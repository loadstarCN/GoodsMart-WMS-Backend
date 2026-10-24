"""运单服务选项（doc/shipping-flow.md §6.4）：DN.shipping_options（如配送日 delivery_date、时间带
delivery_time_slot、送り状种类 invoice_type、代收货款 cod_amount、冷藏 cool_type）。

- 选项的声明（schema）看 DN 当前的承运商（发货任务的，没有用 DN 的）：运单方式 file → 文件模板的
  service_options；否则在线对接插件的 CarrierAdapter.service_options；再没有用文件模板（设了的话）的；都没有为 []。
  每项 {key, kind: select | date | money | text, label{语言}, values?[{value, label}], default?, help?, max_length?}。
- 来源系统创建 DN 时可以传（parse_for_create：只查结构与已声明键的取值，承运商以后还可能换，没声明的键照收）；
  流程的运单步骤里改用 PUT /warehouse/dn/<id>/shipping-options（parse_strict：没声明的键 400、select 值不在列表 400、
  日期 / 金额格式不对 400，都是 16139，field = options.<key>）。
- 建运单 / 导出后锁定（有 api 运单或 file 运单 —— exported / active —— 时 409 16076，reason SHIPPING_OPTIONS_LOCKED）；
  DN 已发货 409 16065。手填的运单号（manual）不锁。
- 建单时交给插件（shipment['options']，只含插件声明了的键）；导出时作为 {options.<key>}（没设的用 default）。
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional

from .common import is_locked, lock_dn

OPTIONS_INVALID = 16139
MAX_OPTIONS = 30
TEXT_MAX = 200
MONEY_MAX = Decimal('10000000000')
_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
LOCK_REASON = 'SHIPPING_OPTIONS_LOCKED'


def _invalid(message, field):
    return BadRequestException(message, OPTIONS_INVALID, field=field)


def _carrier_of(dn):
    from warehouse.dn.carrier_services import CarrierShipmentService
    return CarrierShipmentService.carrier_of(dn)


def schema_for(carrier) -> list:
    """承运商声明的服务选项（规范化后的列表）"""
    if carrier is None:
        return []
    from warehouse.carrier import file_settings
    from warehouse.carrier.file_templates import TemplateError, validate_service_options
    from warehouse.carrier.methods import methods_of, plugin_of
    template = file_settings.template_of(carrier)
    if methods_of(carrier)['waybill'] == 'file' and template is not None:
        return template['service_options']
    cls = plugin_of(carrier)
    if cls is not None and cls.service_options:
        try:
            return validate_service_options(cls.service_options, f'{cls.code}.service_options')
        except TemplateError:
            return []
    return template['service_options'] if template is not None else []


def dn_schema(dn) -> list:
    return schema_for(_carrier_of(dn))


def defaults(schema) -> dict:
    """各选项的默认值（声明了 default 且不为空的）"""
    return {o['key']: o['default'] for o in schema or [] if o.get('default') not in (None, '')}


def effective(dn, schema=None) -> dict:
    """导出 / 建单用的选项值：DN 设的覆盖默认值"""
    schema = dn_schema(dn) if schema is None else schema
    return {**defaults(schema), **(dn.shipping_options or {})}


def for_plugin(dn, schema) -> dict:
    """交给插件的 shipment['options']：只含声明了的键（没设的用默认值）"""
    declared = {o['key'] for o in schema or []}
    return {k: v for k, v in effective(dn, schema).items() if k in declared}


# ------------------------------------------------------------------
# 校验
# ------------------------------------------------------------------

def _value(option: dict, raw, field):
    """按选项类型校验一个值 → 规范化后的值（None = 去掉这个键）"""
    if raw is None or (isinstance(raw, str) and not raw.strip() and option['kind'] != 'select'):
        return None
    kind = option['kind']
    if kind == 'select':
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            raise _invalid(f"{field} must be one of the listed values", field)
        value = str(raw).strip()
        allowed = [v['value'] for v in option.get('values') or []]
        if value not in allowed:
            if value == '':
                return None
            raise _invalid(f"{field} must be one of {', '.join(repr(v) for v in allowed)}", field)
        return value or None
    if kind == 'date':
        if isinstance(raw, datetime):
            return raw.date().isoformat()
        if isinstance(raw, date):
            return raw.isoformat()
        try:
            return datetime.strptime(str(raw).strip(), '%Y-%m-%d').date().isoformat()
        except ValueError:
            raise _invalid(f"{field} must be a date in YYYY-MM-DD format", field) from None
    if kind == 'money':
        if isinstance(raw, bool):
            raise _invalid(f"{field} must be a number", field)
        try:
            number = Decimal(str(raw).strip().replace(',', ''))
        except InvalidOperation:
            raise _invalid(f"{field} must be a number", field) from None
        if not number.is_finite() or number < 0 or number > MONEY_MAX:
            raise _invalid(f"{field} must be a number from 0 to {MONEY_MAX:,}", field)
        if number != number.quantize(Decimal('0.01')):
            raise _invalid(f"{field} must have at most 2 decimal places", field)
        return int(number) if number == number.to_integral_value() else float(number)
    # text
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise _invalid(f"{field} must be a text", field)
    text = str(raw).strip()
    limit = option.get('max_length') or 100
    if len(text) > limit:
        raise _invalid(f"{field} must not exceed {limit} characters", field)
    return text or None


def _structure(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid("options must be an object {key: value}", 'options')
    if len(value) > MAX_OPTIONS:
        raise _invalid(f"options must not have more than {MAX_OPTIONS} keys", 'options')
    for key in value:
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise _invalid("option keys are lower-case letters, digits and _ (max 40)", f'options.{key}')
    return value


def parse_strict(value, schema) -> dict | None:
    """PUT shipping-options：每个键都要是承运商声明了的，值按类型校验；null / 空串去掉该键。→ dict 或 None（空）"""
    data = _structure(value)
    declared = {o['key']: o for o in schema or []}
    result = {}
    for key, raw in data.items():
        option = declared.get(key)
        field = f'options.{key}'
        if option is None:
            known = ', '.join(sorted(declared)) or 'none'
            raise _invalid(f"Unknown shipping option {key} for this carrier (known: {known})", field)
        normalized = _value(option, raw, field)
        if normalized is not None:
            result[key] = normalized
    return result or None


def parse_for_create(value, schema) -> dict | None:
    """创建 / 修改 DN 时来源系统传的 shipping_options：结构 + 已声明键的取值按类型校验；没声明的键照收
    （文字 / 数字，最长 200）。→ dict 或 None"""
    data = _structure(value)
    declared = {o['key']: o for o in schema or []}
    result = {}
    for key, raw in data.items():
        field = f'shipping_options.{key}'
        option = declared.get(key)
        if option is not None:
            try:
                normalized = _value(option, raw, field)
            except BadRequestException as exc:
                exc.field = field
                raise
        elif raw is None or (isinstance(raw, str) and not raw.strip()):
            normalized = None
        elif isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise _invalid(f"{field} must be a text or a number", field)
        else:
            normalized = raw.strip() if isinstance(raw, str) else raw
            if isinstance(normalized, str) and len(normalized) > TEXT_MAX:
                raise _invalid(f"{field} must not exceed {TEXT_MAX} characters", field)
        if normalized is not None:
            result[key] = normalized
    return result or None


def problems(dn, schema=None) -> list:
    """DN 上已存的选项与承运商声明对不上的（导出前逐行检查用）：[{key, code: OPTION_INVALID, message}]"""
    schema = dn_schema(dn) if schema is None else schema
    declared = {o['key']: o for o in schema or []}
    result = []
    for key, raw in (dn.shipping_options or {}).items():
        option = declared.get(key)
        if option is None:
            continue
        try:
            _value(option, raw, f'options.{key}')
        except BadRequestException as exc:
            result.append({'key': key, 'code': 'OPTION_INVALID', 'message': exc.message})
    return result


# ------------------------------------------------------------------
# 锁定 / 读写
# ------------------------------------------------------------------

def lock_of(dn):
    """选项现在不能改的原因 → {reason_code, message, carrier_shipment?}；能改为 None。
    已发货 → DN_SHIPPED；有 api 运单（任一未结束状态）或 file 运单（exported / active）→ WAYBILL_EXISTS"""
    if is_locked(dn):
        return {'reason_code': 'DN_SHIPPED', 'message': "The DN has been shipped; shipping options are locked"}
    from .waybills import current_waybill
    record = current_waybill(dn)
    if record is not None and record.source in ('api', 'file'):
        what = "exported to the carrier software" if record.status == 'exported' else \
            "created through the carrier integration" if record.source == 'api' else "imported from the carrier file"
        return {
            'reason_code': 'WAYBILL_EXISTS',
            'message': (f"The waybill has been {what} ({record.status}"
                        f"{' ' + record.tracking_number if record.tracking_number else ''}); shipping options are "
                        "locked — cancel the waybill (or discard the export) first"),
            'carrier': record.carrier,
            'carrier_shipment': {'id': record.id, 'source': record.source, 'status': record.status,
                                 'tracking_number': record.tracking_number},
        }
    return None


def payload(dn, schema=None) -> dict:
    """{values, schema, locked, locked_reason, locked_message}（流程汇总的运单步骤 summary.options）"""
    schema = dn_schema(dn) if schema is None else schema
    lock = lock_of(dn)
    return {
        'values': dict(dn.shipping_options or {}),
        'schema': schema,
        'defaults': defaults(schema),
        'locked': lock is not None,
        'locked_reason': lock['reason_code'] if lock else None,
        'locked_message': lock['message'] if lock else None,
    }


@transactional
def update(dn, value) -> dict:
    """PUT /warehouse/dn/<id>/shipping-options：锁 DN → 锁定检查 → 按声明校验 → 存。→ {options, schema, locked, ...}"""
    dn = lock_dn(dn)
    lock = lock_of(dn)
    if lock is not None and lock['reason_code'] == 'DN_SHIPPED':
        raise ConflictException(lock['message'], 16065)
    if lock is not None:
        record = lock['carrier_shipment']
        raise ConflictException(lock['message'], 16076, details={
            'reason': LOCK_REASON, 'status': record['status'], 'carrier': lock['carrier'], 'carrier_shipment': record})
    schema = dn_schema(dn)
    dn.shipping_options = parse_strict(value, schema)
    db.session.flush()
    result = payload(dn, schema)
    return {'options': result['values'], **result}
