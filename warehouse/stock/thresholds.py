"""补货阈值批量设置（inventory.low_stock_threshold / high_stock_threshold，-1 = 不启用；doc/库存数值系统说明.md 第十二节）。

- 逐个商品设：PUT /inventory/thresholds {items: [{goods_id | goods_code, low_stock_threshold?, high_stock_threshold?}]}
- 按范围设：PUT /inventory/thresholds/by-filter {filter: {keyword, goods_ids, category, brand, below_low_only}, …}：
  统一设固定值，或按出库量给建议值（suggest）
- 建议值预览：GET /inventory/thresholds/suggest（metrics.suggest_thresholds）
校验沿用 set_low / set_high_stock_threshold：low ≥ -1（15013）；high 为 -1 或 ≥ 0（15014）且 > low（15015）；
商品必须属于仓库所在公司（16030 找不到商品）；一次最多 MAX_ITEMS 个商品。有任何一行不合法 → 400 15017
details {errors: [{index, goods_id, goods_code, field, code, message}]}，全部不改。
商品在该仓库还没有库存行时建一个全 0 行（ensure_inventory）。阈值变更由请求日志（system/logs）记录，不记库存流水。
"""
from sqlalchemy import func, or_

from extensions.db import db
from extensions.error import BadRequestException
from warehouse.goods.models import Goods
from warehouse.inventory.models import Inventory

MAX_ITEMS = 1000
THRESHOLD_BATCH_INVALID = 15017
FIELDS = ('low_stock_threshold', 'high_stock_threshold')


def _int_or_error(value, field, errors, base):
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append({**base, 'field': field, 'code': THRESHOLD_BATCH_INVALID,
                       'message': f"{field} must be an integer"})
        return None
    return value


def _check_pair(low, high, errors, base):
    if low is not None and low < -1:
        errors.append({**base, 'field': 'low_stock_threshold', 'code': 15013,
                       'message': "Low stock threshold must be non-negative or -1 to disable."})
        return False
    if high is not None and high < 0 and high != -1:
        errors.append({**base, 'field': 'high_stock_threshold', 'code': 15014,
                       'message': "High stock threshold must be non-negative or -1 to disable."})
        return False
    if high is not None and high != -1 and low is not None and high <= low:
        errors.append({**base, 'field': 'high_stock_threshold', 'code': 15015,
                       'message': "High stock threshold must be greater than low stock threshold."})
        return False
    return True


def _raise(errors):
    errors.sort(key=lambda error: error['index'])
    raise BadRequestException(f"{len(errors)} threshold item(s) are invalid; nothing was changed.",
                              THRESHOLD_BATCH_INVALID, details={'errors': errors})


def apply_thresholds(warehouse, items, dry_run=False) -> dict:
    """逐个商品设阈值（只改给出的字段）：全部合法才改。返回 {updated, created, unchanged, dry_run, items}"""
    if not isinstance(items, list) or not items:
        raise BadRequestException("items must be a non-empty list", THRESHOLD_BATCH_INVALID, field='items')
    if len(items) > MAX_ITEMS:
        raise BadRequestException(f"At most {MAX_ITEMS} items per request", THRESHOLD_BATCH_INVALID, field='items',
                                  details={'max_items': MAX_ITEMS})
    errors = []
    resolved = []
    seen = {}
    codes = {item.get('goods_code') for item in items if isinstance(item, dict) and item.get('goods_code')}
    by_code = {g.code: g for g in Goods.query.filter(Goods.company_id == warehouse.company_id,
                                                     Goods.code.in_(codes)).all()} if codes else {}
    for index, item in enumerate(items):
        base = {'index': index, 'goods_id': None, 'goods_code': None}
        if not isinstance(item, dict):
            errors.append({**base, 'field': None, 'code': THRESHOLD_BATCH_INVALID, 'message': 'item must be an object'})
            continue
        goods = None
        if item.get('goods_id') is not None:
            goods = db.session.get(Goods, item['goods_id']) if isinstance(item['goods_id'], int) else None
            if goods is not None and goods.company_id != warehouse.company_id:
                goods = None
        elif item.get('goods_code'):
            goods = by_code.get(item['goods_code'])
        base = {'index': index, 'goods_id': item.get('goods_id'), 'goods_code': item.get('goods_code')}
        if goods is None:
            errors.append({**base, 'field': 'goods_id' if item.get('goods_id') is not None else 'goods_code',
                           'code': 16030, 'message': 'Goods not found in this company'})
            continue
        base = {'index': index, 'goods_id': goods.id, 'goods_code': goods.code}
        if goods.id in seen:
            errors.append({**base, 'field': 'goods_id', 'code': THRESHOLD_BATCH_INVALID,
                           'message': f'duplicate goods (also at index {seen[goods.id]})'})
            continue
        seen[goods.id] = index
        values = {}
        for field in FIELDS:
            if field in item:
                value = _int_or_error(item[field], field, errors, base)
                if value is None:
                    break
                values[field] = value
        else:
            if not values:
                errors.append({**base, 'field': None, 'code': THRESHOLD_BATCH_INVALID,
                               'message': 'give low_stock_threshold and / or high_stock_threshold'})
                continue
            resolved.append((goods, values, base))
    inventories = {inv.goods_id: inv for inv in Inventory.query.filter(
        Inventory.warehouse_id == warehouse.id, Inventory.goods_id.in_([g.id for g, _v, _b in resolved])).all()} \
        if resolved else {}
    plans = []
    for goods, values, base in resolved:
        inventory = inventories.get(goods.id)
        before_low = inventory.low_stock_threshold if inventory is not None else -1
        before_high = inventory.high_stock_threshold if inventory is not None else -1
        low = values.get('low_stock_threshold', before_low)
        high = values.get('high_stock_threshold', before_high)
        if _check_pair(low, high, errors, base):
            plans.append((goods, inventory, (before_low, before_high), (low, high)))
    if errors:
        _raise(errors)
    return _apply(warehouse, plans, dry_run)


def _apply(warehouse, plans, dry_run):
    from warehouse.inventory.services import InventoryService
    result = {'updated': 0, 'created': 0, 'unchanged': 0, 'dry_run': bool(dry_run), 'items': []}
    for goods, inventory, before, after in plans:
        changed = before != after
        if inventory is None and changed:
            result['created'] += 1
        elif changed:
            result['updated'] += 1
        else:
            result['unchanged'] += 1
        result['items'].append({'goods_id': goods.id, 'goods_code': goods.code, 'goods_name': goods.name,
                                'before': {'low': before[0], 'high': before[1]},
                                'after': {'low': after[0], 'high': after[1]}, 'changed': changed})
        if dry_run or not changed:
            continue
        if inventory is None:
            inventory = InventoryService.ensure_inventory(goods.id, warehouse.id)
        inventory.low_stock_threshold, inventory.high_stock_threshold = after
    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
    return result


def goods_in_filter(warehouse, flt: dict):
    """按范围（keyword / goods_ids / category / brand / below_low_only）取商品：公司内启用的商品"""
    query = Goods.query.filter(Goods.company_id == warehouse.company_id, Goods.is_active == True)
    if flt.get('goods_ids') is not None:
        ids = flt['goods_ids']
        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
            raise BadRequestException("filter.goods_ids must be a list of integers", THRESHOLD_BATCH_INVALID,
                                      field='filter.goods_ids')
        query = query.filter(Goods.id.in_(ids))
    for field in ('category', 'brand'):
        if flt.get(field):
            query = query.filter(func.lower(getattr(Goods, field)) == str(flt[field]).strip().lower())
    if flt.get('keyword'):
        like = f"%{str(flt['keyword']).lower()}%"
        query = query.filter(or_(func.lower(Goods.code).like(like), func.lower(Goods.name).like(like),
                                 func.lower(Goods.manufacturer).like(like), func.lower(Goods.category).like(like),
                                 func.lower(Goods.tags).like(like), func.lower(Goods.brand).like(like)))
    goods = query.order_by(Goods.id).all()
    if flt.get('below_low_only'):
        inventories = {inv.goods_id: inv for inv in Inventory.query.filter(
            Inventory.warehouse_id == warehouse.id, Inventory.goods_id.in_([g.id for g in goods])).all()}
        goods = [g for g in goods if g.id in inventories and inventories[g.id].low_stock_threshold != -1
                 and inventories[g.id].available_stock_for_sale < inventories[g.id].low_stock_threshold]
    return goods


def apply_by_filter(warehouse, flt, low=None, high=None, suggest=None, dry_run=False) -> dict:
    """按范围统一设固定值（low / high，只改给出的），或按建议值（suggest = {window_days, cover_days, overstock_days,
    min_quantity, apply: low | high | both}，近 90 天没有出库的商品跳过）。返回同 apply_thresholds，另带 skipped"""
    from .metrics import suggest_thresholds
    if not isinstance(flt, dict):
        raise BadRequestException("filter must be an object", THRESHOLD_BATCH_INVALID, field='filter')
    goods = goods_in_filter(warehouse, flt)
    if len(goods) > MAX_ITEMS:
        raise BadRequestException(f"The filter matches {len(goods)} goods; at most {MAX_ITEMS} per request",
                                  THRESHOLD_BATCH_INVALID, field='filter', details={'max_items': MAX_ITEMS,
                                                                                    'matched': len(goods)})
    skipped = []
    items = []
    if suggest is not None:
        options = suggest if isinstance(suggest, dict) else {}
        target = options.get('apply', 'both')
        params = suggest_params(options)
        suggestions = suggest_thresholds(warehouse.id, [g.id for g in goods], **params)
        for g in goods:
            entry = suggestions.get(g.id) or {}
            if entry.get('suggested_low') is None:
                skipped.append({'goods_id': g.id, 'goods_code': g.code, 'reason': 'NO_OUTBOUND_90D'})
                continue
            item = {'goods_id': g.id}
            if target in ('low', 'both'):
                item['low_stock_threshold'] = entry['suggested_low']
            if target in ('high', 'both'):
                item['high_stock_threshold'] = entry['suggested_high']
            items.append(item)
    else:
        if low is None and high is None:
            raise BadRequestException("give low_stock_threshold and / or high_stock_threshold, or suggest",
                                      THRESHOLD_BATCH_INVALID, field='low_stock_threshold')
        for g in goods:
            item = {'goods_id': g.id}
            if low is not None:
                item['low_stock_threshold'] = low
            if high is not None:
                item['high_stock_threshold'] = high
            items.append(item)
    if not items:
        return {'updated': 0, 'created': 0, 'unchanged': 0, 'dry_run': bool(dry_run), 'items': [], 'skipped': skipped}
    result = apply_thresholds(warehouse, items, dry_run=dry_run)
    result['skipped'] = skipped
    return result


def suggest_params(options: dict) -> dict:
    """建议参数：window_days（日均出库的窗口，默认 30）、cover_days（补货覆盖，默认 14）、overstock_days（积压，默认 90）、
    min_quantity（默认 1）；各为 1–365（min_quantity 0–100000）的整数，否则 400 15017"""
    from .metrics import SUGGEST_COVER_DAYS, SUGGEST_MIN_QUANTITY, SUGGEST_OVERSTOCK_DAYS, SUGGEST_WINDOW_DAYS
    defaults = {'window_days': SUGGEST_WINDOW_DAYS, 'cover_days': SUGGEST_COVER_DAYS,
                'overstock_days': SUGGEST_OVERSTOCK_DAYS, 'min_quantity': SUGGEST_MIN_QUANTITY}
    params = {}
    for key, default in defaults.items():
        value = options.get(key, default)
        if value is None:
            value = default
        upper = 100000 if key == 'min_quantity' else 365
        lower = 0 if key == 'min_quantity' else 1
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise BadRequestException(f"suggest.{key} must be an integer {lower}-{upper}", THRESHOLD_BATCH_INVALID,
                                      field=f'suggest.{key}')
        params[key] = value
    return params
