"""商品列表的筛选参数（GET /goods/ 的查询参数与导出任务 POST /goods/exports 的 filters 共用一套解析）。

导出任务把筛选条件以 JSON 存下（日期 / 时刻存成 ISO 文字），后台生成与「按原条件重新导出」时再解析成 list_goods 的 filters。
"""
from datetime import date, datetime

from flask import g
from flask_restx import inputs

from warehouse.common.spec_capture import SPEC_CAPTURE_FIELDS

IMPORT_ACTIONS = ('create', 'update')

# 文字筛选（部分匹配 / 等于，原样交给 list_goods）
TEXT_FILTERS = ('code', 'name', 'manufacturer', 'category', 'tags', 'currency', 'brand', 'keyword')
NUMBER_FILTERS = ('price_min', 'price_max', 'discount_price_min', 'discount_price_max')
DATE_FILTERS = ('expiration_date_min', 'expiration_date_max', 'production_date_min', 'production_date_max')
TIME_FILTERS = ('created_from', 'created_to', 'updated_from', 'updated_to')
BOOL_FILTERS = ('origin_missing', 'image_missing', 'has_stock')
# 导出任务的 filters 里认的键（另有 is_active、goods_codes、spec_missing、company_id、import_id、import_action、warehouse_id）
EXPORT_FILTER_KEYS = (*TEXT_FILTERS, *NUMBER_FILTERS, *DATE_FILTERS, *TIME_FILTERS, *BOOL_FILTERS,
                      'is_active', 'goods_codes', 'spec_missing', 'company_id', 'import_id', 'import_action',
                      'warehouse_id')


def active_arg(value):
    """is_active：true / false，或 all（启用和停用的都要）；不传 = 只看启用的"""
    if isinstance(value, str) and value.strip().lower() == 'all':
        return 'all'
    if isinstance(value, bool):
        return value
    return inputs.boolean(value)


def time_bound(value):
    """创建 / 更新时间的筛选值：YYYY-MM-DD（默认时区当天；结束日含当天）或 ISO 8601 时刻（带偏移按偏移）"""
    if isinstance(value, (date, datetime)):
        return value
    text = str(value).strip()
    if len(text) == 10:
        return date.fromisoformat(text)
    if text[-1:] in ('Z', 'z'):
        text = text[:-1] + '+00:00'
    return datetime.fromisoformat(text)


def codes_arg(value):
    """goods_codes：逗号分隔的文字或列表 → 去空白、去空的列表"""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).split(',')
    return [str(code).strip() for code in items if str(code).strip()]


def spec_missing_arg(value):
    """spec_missing=weight,dimensions：逗号分隔（或列表）的采集项，不认识的 → ValueError"""
    names = codes_arg(value)
    unknown = [name for name in names if name not in SPEC_CAPTURE_FIELDS]
    if unknown:
        raise ValueError(f"unknown spec field(s): {', '.join(unknown)}; use {', '.join(SPEC_CAPTURE_FIELDS)}")
    return names


def import_action_arg(value):
    text = str(value).strip().lower()
    if text not in IMPORT_ACTIONS:
        raise ValueError(f"import_action must be one of {', '.join(IMPORT_ACTIONS)}")
    return text


def stock_scope():
    """库存范围（「有 / 无库存」筛选、导出的库存列）：当前仓库 → [它]；否则调用方可访问的仓库；平台用户 / 超级密钥 None（不限）。
    须在 @warehouse_required() 之后调用"""
    warehouse_id = getattr(g, 'warehouse_id', None)
    if warehouse_id:
        return [warehouse_id]
    accessible = getattr(g, 'accessible_warehouses', None)
    if accessible is not None:
        return [warehouse.id for warehouse in accessible]
    return None


def _number(value):
    if isinstance(value, bool):
        raise ValueError('must be a number')
    return float(value)


def _int(value):
    if isinstance(value, bool):
        raise ValueError('must be an integer')
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError('must be an integer')
    return int(text)


def _bool(value):
    if isinstance(value, bool):
        return value
    return inputs.boolean(value)


def normalize_stored_filters(raw) -> dict:
    """导出任务的 filters（JSON 对象）→ 规范化后可存库的 dict（只留有值的键；日期 / 时刻为 ISO 文字）。
    不认识的键或值不合法 → ValueError(键名, 原因)"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('filters', 'must be a JSON object')
    result = {}
    for key, value in raw.items():
        if key not in EXPORT_FILTER_KEYS:
            raise ValueError(key, 'unknown filter')
        if value is None or (isinstance(value, str) and not value.strip()) or value == []:
            continue
        try:
            if key in TEXT_FILTERS:
                if not isinstance(value, str):
                    raise ValueError('must be a string')
                result[key] = value.strip()
            elif key in NUMBER_FILTERS:
                result[key] = _number(value)
            elif key in DATE_FILTERS:
                result[key] = inputs.date_from_iso8601(str(value)).isoformat()
            elif key in TIME_FILTERS:
                result[key] = time_bound(value).isoformat()
            elif key in BOOL_FILTERS:
                result[key] = _bool(value)
            elif key == 'is_active':
                result[key] = active_arg(value)
            elif key == 'goods_codes':
                codes = codes_arg(value)
                if codes:
                    result[key] = codes
            elif key == 'spec_missing':
                names = spec_missing_arg(value)
                if names:
                    result[key] = names
            elif key in ('company_id', 'import_id', 'warehouse_id'):
                result[key] = _int(value)
            elif key == 'import_action':
                result[key] = import_action_arg(value)
        except (TypeError, ValueError) as exc:
            reason = exc.args[0] if exc.args and isinstance(exc.args[0], str) else 'invalid value'
            raise ValueError(key, reason) from None
    return result


def list_filters(stored: dict, company_id, stock_warehouse_ids) -> dict:
    """存下的 filters → GoodsService.list_goods 的 filters"""
    filters = {}
    for key, value in (stored or {}).items():
        if key in ('warehouse_id', 'company_id'):
            continue
        if key in DATE_FILTERS:
            filters[key] = date.fromisoformat(value)
        elif key in TIME_FILTERS:
            filters[key] = time_bound(value)
        else:
            filters[key] = value
    filters['company_id'] = company_id
    filters['stock_warehouse_ids'] = stock_warehouse_ids
    return filters
