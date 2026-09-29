"""输入校验辅助：数量 / 批量列表。

restx 的 expect(model) 没有开启 validate，所以 service 层必须自己把关；
统一在这里做，避免各模块各写一套（并且都忘了 bool 是 int 的子类）。
"""
from extensions.error import BadRequestException

MAX_BULK_ITEMS = 500


def _to_int(value, field, code):
    # bool 是 int 的子类，True/False 不能当数量
    if isinstance(value, bool):
        raise BadRequestException(f"{field} must be an integer", code)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    raise BadRequestException(f"{field} must be an integer", code)


def require_positive_int(value, field='quantity', code=16033):
    """数量必须是正整数（>0）。缺失、小数、负数、字符串、布尔值一律 400。"""
    if value is None:
        raise BadRequestException(f"{field} is required", code)
    number = _to_int(value, field, code)
    if number <= 0:
        raise BadRequestException(f"{field} must be greater than 0", code)
    return number


def require_non_negative_int(value, field='quantity', code=16033, default=0):
    """数量必须是非负整数（>=0）；None 取默认值。"""
    if value is None:
        return default
    number = _to_int(value, field, code)
    if number < 0:
        raise BadRequestException(f"{field} must not be negative", code)
    return number


def require_bulk_list(items, field='items', max_items=MAX_BULK_ITEMS, allow_empty=False, code=16015):
    """批量接口的列表校验：必须是 list（默认非空），且不超过 max_items 条。"""
    if not isinstance(items, list):
        raise BadRequestException(f"{field} must be a list", code)
    if not items and not allow_empty:
        raise BadRequestException(f"{field} must not be empty", code)
    if len(items) > max_items:
        raise BadRequestException(f"{field} must not exceed {max_items} items", code)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise BadRequestException(f"{field}[{index}] must be an object", code)
    return items


def require_fields(data, *fields, code=40000):
    """必填字段检查：缺失或为 None 时 400，而不是 KeyError 500。"""
    if not isinstance(data, dict):
        raise BadRequestException("Request body must be a JSON object", code)
    missing = [f for f in fields if data.get(f) is None]
    if missing:
        raise BadRequestException(f"Missing required field(s): {', '.join(missing)}", code)
    return data
