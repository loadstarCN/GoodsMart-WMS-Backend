"""Exact product metadata scope shared by inventory analysis panels."""
from sqlalchemy import func
from warehouse.goods.models import Goods

KEYS = ('category', 'brand', 'category_missing', 'brand_missing')


def has_scope(filters):
    return any(filters.get(key) not in (None, '') for key in KEYS)


def goods_query(filters):
    query = Goods.query.with_entities(Goods.id)
    for key in ('category', 'brand'):
        column = getattr(Goods, key)
        if str(filters.get(key+'_missing', '')).lower() in ('1', 'true'):
            query = query.filter(func.coalesce(column, '') == '')
        elif filters.get(key) not in (None, ''):
            query = query.filter(column == filters[key])
    return query
