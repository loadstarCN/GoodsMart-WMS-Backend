"""公司 / 仓库的出口资料字段（商业发票、装箱单的发货人栏）。

只做格式校验与规范化：去首尾空白、空串视为清空、超长 400、国家代码大写且必须是有效的
ISO 3166-1 alpha-2。内容是否齐全（能否出单证）由 DN 单证侧的 problems 判断，这里不拦。
"""
from extensions.error import BadRequestException
from warehouse.common.countries import is_valid_country

# 字段 → 最大长度（与模型列宽一致）
COMPANY_EXPORT_FIELDS = {
    'legal_name_en': 255,
    'address_en': 500,
    'country_code': 2,
    'tax_id_label': 40,
    'tax_id': 40,
    'export_contact_name': 100,
    'export_signatory_name': 100,
    'export_signatory_title': 100,
}

WAREHOUSE_EXPORT_FIELDS = {
    'address_en': 500,
    'country_code': 2,
    'contact_name_en': 100,
}


def normalize_country_code(value, field='country_code'):
    """None / 空串 → None；否则大写后必须是有效的 ISO 3166-1 alpha-2，不合法 400 14020。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequestException(f"{field} must be an ISO 3166-1 alpha-2 code", 14020, field=field)
    code = value.strip().upper()
    if not code:
        return None
    if not is_valid_country(code):
        raise BadRequestException(f"{field} must be an ISO 3166-1 alpha-2 code", 14020, field=field)
    return code


def normalize_export_profile(data: dict, spec: dict) -> dict:
    """从请求体里挑出 spec 列出的字段并规范化；只返回请求体里出现过的键（未出现 = 不改）。"""
    if not isinstance(data, dict):
        return {}
    result = {}
    for field, max_len in spec.items():
        if field not in data:
            continue
        value = data[field]
        if field == 'country_code':
            result[field] = normalize_country_code(value, field)
            continue
        if value is None:
            result[field] = None
            continue
        if not isinstance(value, str):
            raise BadRequestException(f"{field} must be a string", 14019, field=field)
        value = value.strip()
        if len(value) > max_len:
            raise BadRequestException(f"{field} must not exceed {max_len} characters", 14019, field=field)
        result[field] = value or None
    return result
