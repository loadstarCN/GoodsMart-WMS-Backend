"""承运商的文件方式设置（doc/shipping-flow.md §6.1）：carriers.file_preset / file_template / file_account。

- file_preset：预设代码（如 jp.yamato_b2cloud，见 warehouse/carrier/presets）
- file_template：在预设基础上改的部分（json，按 file_templates.merge_template 合并：对象逐键合并、列表整个替换、
  值为 null 删掉该键）；没有预设时是完整模板；为空 = 完全用预设
- file_account：模板 account_fields 声明的账号项 {key: 文字}（如ヤマト的請求先顧客コード）
waybill_method = file 要求有预设或模板（available_methods 据此给 file）。保存时校验合并后的模板，
不合法 400 16135（field = file_preset / file_template.<位置> / file_account.<key>）。
"""
import copy
import json
import logging

from extensions.error import BadRequestException

from .file_templates import TemplateError, merge_template, validate_template
from .presets import raw_preset

logger = logging.getLogger(__name__)

METHOD_INVALID = 16135
FILE_FIELDS = ('file_preset', 'file_template', 'file_account')
ACCOUNT_VALUE_MAX = 100
_UNSET = object()


def _invalid(message, field):
    return BadRequestException(message, METHOD_INVALID, field=field)


_cache = {}
_CACHE_MAX = 64


def build_template(preset_code, override):
    """预设 + 改动 → 规范化后的模板；两者都没有为 None。不合法抛 TemplateError（预设代码不存在时 path = file_preset）。
    同一组（预设, 改动）的结果缓存在进程里（流程汇总 / 承运商列表一次请求会用到好几次），返回副本。"""
    try:
        key = (preset_code or None, json.dumps(override, sort_keys=True, ensure_ascii=False) if override else None)
    except (TypeError, ValueError):
        key = None
    if key is not None and key in _cache:
        cached = _cache[key]
        if isinstance(cached, TemplateError):
            raise TemplateError(cached.path, cached.message)
        return copy.deepcopy(cached)
    try:
        template = _build_template(preset_code, override)
    except TemplateError as exc:
        if key is not None and len(_cache) < _CACHE_MAX:
            _cache[key] = exc
        raise
    if key is not None:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = template
    return copy.deepcopy(template)


def _build_template(preset_code, override):
    base = None
    if preset_code:
        base = raw_preset(preset_code)
        if base is None:
            raise TemplateError('file_preset', f"unknown file preset {preset_code}")
    if base is None and not override:
        return None
    merged = merge_template(base, override) if override else base
    return validate_template(merged)


def template_of(carrier):
    """承运商现在的文件模板（规范化后）；没设 / 不合法（如预设被删了）为 None"""
    if carrier is None:
        return None
    preset = getattr(carrier, 'file_preset', None)
    override = getattr(carrier, 'file_template', None)
    if not preset and not override:
        return None
    try:
        return build_template(preset, override)
    except TemplateError as exc:
        logger.warning("carrier %s: file template is invalid: %s", getattr(carrier, 'id', None), exc)
        return None


def template_error(carrier):
    """承运商的文件模板不合法时的说明（没设或合法为 None）"""
    preset = getattr(carrier, 'file_preset', None)
    override = getattr(carrier, 'file_template', None)
    if not preset and not override:
        return None
    try:
        build_template(preset, override)
    except TemplateError as exc:
        return str(exc)
    return None


def configured(carrier) -> bool:
    """有可用的文件模板（waybill_method 可以选 file）"""
    return template_of(carrier) is not None


def missing_account_fields(template, account) -> list:
    """模板声明为必填、但 file_account 里没有值的账号项"""
    account = account or {}
    return [f['key'] for f in (template or {}).get('account_fields') or []
            if f.get('required') and not str(account.get(f['key']) or '').strip()]


def payload(carrier) -> dict:
    """承运商接口里的 file_mode（只读）：{configured, ready, preset: {code, name, verified} | null, error,
    service_options, account_fields, missing_account_fields, can_import, tracking_url_template,
    tracking_url_fallback, encoding}"""
    template = template_of(carrier)
    preset_code = getattr(carrier, 'file_preset', None)
    preset = None
    if preset_code:
        raw = raw_preset(preset_code)
        preset = {'code': preset_code, 'name': (raw or {}).get('name'),
                  'verified': bool((raw or {}).get('verified', False)), 'exists': raw is not None}
    missing = missing_account_fields(template, getattr(carrier, 'file_account', None)) if template else []
    return {
        'configured': template is not None,
        'ready': template is not None and not missing,
        'preset': preset,
        'error': template_error(carrier),
        'service_options': template['service_options'] if template else [],
        'account_fields': template['account_fields'] if template else [],
        'missing_account_fields': missing,
        'can_import': bool(template and template.get('import')),
        'tracking_url_template': template.get('tracking_url_template') if template else None,
        'tracking_url_fallback': template.get('tracking_url_fallback') if template else None,
        'encoding': template['export']['encoding'] if template else None,
    }


def _parse_account(value, template):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("file_account must be an object {key: text}", 'file_account')
    declared = {f['key'] for f in (template or {}).get('account_fields') or []}
    result = {}
    for key, raw in value.items():
        field = f'file_account.{key}'
        if template is None:
            raise _invalid("file_account needs a file_preset or file_template first", field)
        if key not in declared:
            raise _invalid(f"{key} is not an account field of this file template "
                           f"(known: {', '.join(sorted(declared)) or 'none'})", field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            raise _invalid(f"{field} must be a string", field)
        text = str(raw).strip()
        if len(text) > ACCOUNT_VALUE_MAX:
            raise _invalid(f"{field} must not exceed {ACCOUNT_VALUE_MAX} characters", field)
        result[key] = text
    return result or None


def parse(data: dict, carrier=None) -> dict:
    """请求体里的文件方式字段 → 要写入的值（只含请求里带了的键），并给出合并后的模板：
    → {'values': {...}, 'template': 规范化后的模板 | None（保存后的）}。carrier 为现在的承运商（新建为 None）。"""
    if not isinstance(data, dict):
        data = {}
    values = {}
    preset = getattr(carrier, 'file_preset', None) if carrier is not None else None
    override = getattr(carrier, 'file_template', None) if carrier is not None else None
    account = getattr(carrier, 'file_account', None) if carrier is not None else None
    if 'file_preset' in data:
        raw = data['file_preset']
        if raw is not None and not isinstance(raw, str):
            raise _invalid("file_preset must be a preset code (GET /warehouse/carrier/file-presets) or null",
                           'file_preset')
        preset = (raw or '').strip() or None
        if preset is not None and raw_preset(preset) is None:
            raise _invalid(f"Unknown file preset {preset} (GET /warehouse/carrier/file-presets)", 'file_preset')
        values['file_preset'] = preset
    if 'file_template' in data:
        raw = data['file_template']
        if raw is not None and not isinstance(raw, dict):
            raise _invalid("file_template must be an object (the parts changed from the preset) or null",
                           'file_template')
        override = raw or None
        values['file_template'] = override
    template = None
    if preset or override:
        try:
            template = build_template(preset, override)
        except TemplateError as exc:
            field = 'file_preset' if exc.path == 'file_preset' or not override else \
                f"file_template.{exc.path}" if exc.path else 'file_template'
            raise _invalid(f"The file template is invalid: {exc}", field) from None
    if 'file_account' in data:
        values['file_account'] = _parse_account(data['file_account'], template)
    elif account and ('file_preset' in data or 'file_template' in data):
        # 换了模板：新模板没有声明的账号项去掉（模板去掉了就全清）
        declared = {f['key'] for f in (template or {}).get('account_fields') or []}
        kept = {k: v for k, v in account.items() if k in declared}
        if kept != account:
            values['file_account'] = kept or None
    return {'values': values, 'template': template}
