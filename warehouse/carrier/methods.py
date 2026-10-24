"""承运商每项能力的对接方式（doc/shipping-flow.md §2）：运单 waybill_method、揽收 pickup_method、跟踪 tracking_method。

没有 API 合同也能完整走流程：每项能力各自选方式。
- waybill_method：api（插件建单）/ file（按承运商软件的格式导出 CSV、导回运单号；要设了文件预设或模板，
  见 warehouse/carrier/file_settings.py）/ manual（手填 / 扫码运单号）
- pickup_method：api（插件预约）/ manual（电话 / 网站约好后在 WMS 记一笔）/ scheduled（定期集荷，不用逐票预约）/
  dropoff（自送网点）/ none（不管揽收）
- tracking_method：api（插件定时查：揽收后自动交承运商、送达后自动签收）/ link（只给公开查询页链接）/ none
- api 只有在承运商的在线对接已启用、且插件声明了对应能力（supports_waybill / supports_pickup / supports_tracking）时
  才能选（available_methods）；改成不可选的值 → 400 16135（field 指明哪一项）。没改的值不重新校验
  （对接停用后再保存承运商的其它资料不报错；停用期间已建的运单照样能跟踪 / 取消）。
- 设置在线对接时自动跟上：新建 / 重新启用 / 换插件后，还是默认值（manual / none / link）的项改成 api（插件支持的）；
  删掉对接时 api 的项退回默认值。
- 公开查询页：承运商设置的 tracking_url_template，没填用插件内置的，再没有用文件模板（预设）的；
  {tracking_number} 换成运单号（URL 编码）。
"""
import re
from datetime import datetime
from urllib.parse import quote

from extensions.error import BadRequestException

from . import file_settings
from .integrations import adapter_class

METHOD_INVALID = 16135          # 400 对接方式 / 揽收安排 / 查询页 / 自动签收天数不合法（field）

DEFAULT_METHODS = {'waybill': 'manual', 'pickup': 'none', 'tracking': 'link'}
# 不用 API 也能选的方式（file 另要有文件预设 / 模板）
BASE_METHODS = {
    'waybill': ('manual',),
    'pickup': ('manual', 'scheduled', 'dropoff', 'none'),
    'tracking': ('link', 'none'),
}
# api 方式要求插件声明的能力
CAPABILITY = {'waybill': 'supports_waybill', 'pickup': 'supports_pickup', 'tracking': 'supports_tracking'}
FIELDS = {'waybill': 'waybill_method', 'pickup': 'pickup_method', 'tracking': 'tracking_method'}

AUTO_SIGN_MIN_DAYS = 1
AUTO_SIGN_MAX_DAYS = 90
TRACKING_PLACEHOLDER = '{tracking_number}'
_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)


def _invalid(message, field):
    return BadRequestException(message, METHOD_INVALID, field=field)


def plugin_of(carrier):
    """承运商在线对接的插件类（没有对接 / 插件没装为 None）"""
    integration = getattr(carrier, 'integration', None) if carrier is not None else None
    return adapter_class(integration.adapter) if integration is not None else None


def integration_enabled(carrier) -> bool:
    integration = getattr(carrier, 'integration', None) if carrier is not None else None
    return bool(integration is not None and integration.enabled and adapter_class(integration.adapter) is not None)


def available_methods(carrier, file_configured=None) -> dict:
    """{waybill: [...], pickup: [...], tracking: [...]}：现在能选的方式（api 在前，运单再 file）。
    file_configured：保存时按请求里的文件设置算（None = 按承运商现在的设置）"""
    cls = plugin_of(carrier) if integration_enabled(carrier) else None
    if file_configured is None:
        file_configured = file_settings.configured(carrier)
    result = {}
    for kind, base in BASE_METHODS.items():
        api = cls is not None and bool(getattr(cls, CAPABILITY[kind], False))
        extra = ['file'] if kind == 'waybill' and file_configured else []
        result[kind] = (['api'] if api else []) + extra + list(base)
    return result


def methods_of(carrier) -> dict:
    """{waybill, pickup, tracking}：承运商现在设的方式（没有承运商为默认值）"""
    if carrier is None:
        return dict(DEFAULT_METHODS)
    return {kind: getattr(carrier, field, None) or DEFAULT_METHODS[kind] for kind, field in FIELDS.items()}


def tracking_url(carrier, tracking_number, adapter_code=None):
    """公开查询页：承运商的 tracking_url_template → 插件内置的（adapter_code 指定插件时优先用它的）；
    没有模板 / 没有运单号为 None"""
    number = str(tracking_number or '').strip()
    if not number:
        return None
    template = tracking_url_template(carrier, adapter_code)
    if not template:
        return None
    return template.replace(TRACKING_PLACEHOLDER, quote(number, safe=''))


def tracking_url_template(carrier, adapter_code=None):
    """查询页模板：承运商设置的 → 插件内置的（adapter_code 指定插件时优先用它的）→ 文件模板（预设）的；没有为 None"""
    template = (getattr(carrier, 'tracking_url_template', None) or '').strip() if carrier is not None else ''
    if not template:
        cls = adapter_class(adapter_code) if adapter_code else None
        cls = cls or plugin_of(carrier)
        template = (getattr(cls, 'tracking_url_template', None) or '') if cls is not None else ''
    if not template and carrier is not None:
        file_template = file_settings.template_of(carrier)
        template = (file_template or {}).get('tracking_url_template') or ''
    return template or None


# ------------------------------------------------------------------
# 校验（POST / PUT /carrier）
# ------------------------------------------------------------------

def _hhmm(value, field):
    text = str(value or '').strip() if isinstance(value, str) else ''
    try:
        return datetime.strptime(text, '%H:%M').strftime('%H:%M')
    except ValueError:
        raise _invalid(f"{field} must be HH:MM", field) from None


def parse_pickup_schedule(value):
    """{weekdays: [1..7], ready_time: 'HH:MM', close_time: 'HH:MM'} → 规范化（星期去重排序）；null → None"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("pickup_schedule must be an object {weekdays, ready_time, close_time}", 'pickup_schedule')
    weekdays = value.get('weekdays')
    if not isinstance(weekdays, list) or not weekdays:
        raise _invalid("pickup_schedule.weekdays must be a non-empty list of ISO weekdays (1 = Monday … 7 = Sunday)",
                       'pickup_schedule.weekdays')
    days = set()
    for day in weekdays:
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 7:
            raise _invalid("pickup_schedule.weekdays must be integers 1 to 7", 'pickup_schedule.weekdays')
        days.add(day)
    ready = _hhmm(value.get('ready_time'), 'pickup_schedule.ready_time')
    close = _hhmm(value.get('close_time'), 'pickup_schedule.close_time')
    if close <= ready:
        raise _invalid("pickup_schedule.close_time must be later than ready_time", 'pickup_schedule.close_time')
    return {'weekdays': sorted(days), 'ready_time': ready, 'close_time': close}


def _text(value, field, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string", field)
    text = value.strip()
    if len(text) > limit:
        raise _invalid(f"{field} must not exceed {limit} characters", field)
    return text or None


def parse_tracking_url_template(value):
    template = _text(value, 'tracking_url_template', 500)
    if template is None:
        return None
    if not _URL_RE.match(template) or TRACKING_PLACEHOLDER not in template:
        raise _invalid("tracking_url_template must be an http(s) URL containing {tracking_number}",
                       'tracking_url_template')
    return template


def parse_auto_sign_days(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) \
            or not AUTO_SIGN_MIN_DAYS <= value <= AUTO_SIGN_MAX_DAYS:
        raise _invalid(f"auto_sign_days must be an integer from {AUTO_SIGN_MIN_DAYS} to {AUTO_SIGN_MAX_DAYS} "
                       "(null = off)", 'auto_sign_days')
    return value


def parse_settings(data: dict, carrier=None, file_configured=None) -> dict:
    """请求体里的对接方式相关字段 → 要写入的值（只含请求里带了的键）。
    carrier 为现在的承运商（新建时为 None）：方式没改时不重新校验 api 是否可选。
    file_configured：保存后有没有文件模板（同一个请求里设预设并选 file 也行；None = 按承运商现在的）。
    运单方式保存后是 file 却没有文件模板 → 400 16135（field file_preset）。"""
    if not isinstance(data, dict):
        return {}
    available = available_methods(carrier, file_configured)
    current = methods_of(carrier)
    values = {}
    for kind, field in FIELDS.items():
        if field not in data:
            continue
        value = data[field]
        if value is None:
            raise _invalid(f"{field} must not be null", field)
        if not isinstance(value, str) or value.strip().lower() not in _all_methods(kind):
            raise _invalid(f"{field} must be one of {' / '.join(_all_methods(kind))}", field)
        value = value.strip().lower()
        if value != current[kind] or carrier is None:
            if value not in available[kind]:
                raise _invalid(_unavailable_message(kind, value, carrier), field)
        values[field] = value
    waybill = values.get('waybill_method', current['waybill'])
    touched = any(key in data for key in ('waybill_method', 'file_preset', 'file_template'))
    if waybill == 'file' and file_configured is False and touched:
        raise _invalid("waybill_method file needs a file preset or template; it cannot be removed while the "
                       "waybill method is file", 'file_preset')
    if 'pickup_schedule' in data:
        values['pickup_schedule'] = parse_pickup_schedule(data['pickup_schedule'])
    if 'pickup_contact' in data:
        values['pickup_contact'] = _text(data['pickup_contact'], 'pickup_contact', 255)
    if 'tracking_url_template' in data:
        values['tracking_url_template'] = parse_tracking_url_template(data['tracking_url_template'])
    if 'auto_sign_days' in data:
        values['auto_sign_days'] = parse_auto_sign_days(data['auto_sign_days'])
    return values


def _all_methods(kind):
    from .models import Carrier
    return {'waybill': Carrier.WAYBILL_METHODS, 'pickup': Carrier.PICKUP_METHODS,
            'tracking': Carrier.TRACKING_METHODS}[kind]


def _unavailable_message(kind, value, carrier) -> str:
    if value == 'file':
        return ("waybill_method file needs a file preset or template: set file_preset "
                "(GET /warehouse/carrier/file-presets) or file_template")
    if value == 'api':
        what = {'waybill': 'create shipments', 'pickup': 'book pickups', 'tracking': 'track shipments'}[kind]
        if plugin_of(carrier) is None:
            return (f"{FIELDS[kind]} api needs an online integration that can {what}: set up the integration "
                    "of this carrier first")
        if not integration_enabled(carrier):
            return f"{FIELDS[kind]} api needs the online integration of this carrier to be turned on"
        return f"The online integration of this carrier cannot {what}"
    return f"{FIELDS[kind]} {value} is not available"


# ------------------------------------------------------------------
# 在线对接保存 / 删除时跟上（CarrierIntegrationService.save 调用）
# ------------------------------------------------------------------

def adopt_integration(carrier, cls):
    """对接新建 / 重新启用 / 换插件后：还是默认值的项改成 api（插件声明了对应能力的）"""
    for kind, field in FIELDS.items():
        if getattr(cls, CAPABILITY[kind], False) and (getattr(carrier, field, None) or DEFAULT_METHODS[kind]) \
                == DEFAULT_METHODS[kind]:
            setattr(carrier, field, 'api')


def release_integration(carrier, cls=None):
    """删掉对接（或换成不支持该能力的插件）后：api 的项退回默认值"""
    for kind, field in FIELDS.items():
        if getattr(carrier, field, None) == 'api' and not (cls is not None and getattr(cls, CAPABILITY[kind], False)):
            setattr(carrier, field, DEFAULT_METHODS[kind])
