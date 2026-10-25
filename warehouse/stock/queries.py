"""库存流水、按库位库存的查询（接口见 warehouse/stock/views.py，口径见 doc/库存数值系统说明.md 第十节）。"""
from collections import defaultdict

from sqlalchemy import func, or_

from extensions.db import db
from warehouse.common import clock
from .models import IN_WAREHOUSE_STOCK_TYPES, LOCATION_STOCK_TYPES, STOCK_TYPES, StockLedger

QUERY_INVALID = 15019         # 400 查询参数不合法（field）
EXPORT_TOO_LARGE = 15020      # 400 导出行数超过上限
MAX_EXPORT_ROWS = 50000


def _keyword_goods(query, keyword):
    from warehouse.goods.models import Goods
    like = f"%{keyword.lower()}%"
    return query.filter(or_(func.lower(Goods.code).like(like), func.lower(Goods.name).like(like),
                            func.lower(Goods.manufacturer).like(like), func.lower(Goods.category).like(like),
                            func.lower(Goods.tags).like(like), func.lower(Goods.brand).like(like)))


def ledger_query(filters: dict):
    """流水列表：范围（warehouse_id / warehouse_ids / company_id）+ 筛选，按业务发生时刻（或记账时刻）倒序"""
    from warehouse.goods.models import Goods
    from warehouse.location.models import Location
    query = StockLedger.query
    if filters.get('warehouse_id'):
        query = query.filter(StockLedger.warehouse_id == filters['warehouse_id'])
    if filters.get('warehouse_ids'):
        query = query.filter(StockLedger.warehouse_id.in_(filters['warehouse_ids']))
    if filters.get('company_id'):
        query = query.filter(StockLedger.company_id == filters['company_id'])
    for field in ('goods_id', 'lot_id', 'location_id', 'doc_id', 'operator_id', 'doc_line_id'):
        if filters.get(field) is not None:
            query = query.filter(getattr(StockLedger, field) == filters[field])
    for field, column in (('stock_types', StockLedger.stock_type), ('movement_types', StockLedger.movement_type),
                          ('doc_types', StockLedger.doc_type), ('sources', StockLedger.source)):
        if filters.get(field):
            query = query.filter(column.in_(filters[field]))
    if filters.get('doc_no'):
        query = query.filter(StockLedger.doc_no == filters['doc_no'])
    if filters.get('trace_id'):
        query = query.filter(StockLedger.trace_id == filters['trace_id'])
    if filters.get('lot_no'):
        from .models import GoodsLot
        query = query.join(GoodsLot, GoodsLot.id == StockLedger.lot_id).filter(GoodsLot.lot_no == filters['lot_no'])
    if filters.get('goods_code') or filters.get('keyword'):
        query = query.join(Goods, Goods.id == StockLedger.goods_id)
        if filters.get('goods_code'):
            query = query.filter(Goods.code == filters['goods_code'])
        if filters.get('keyword'):
            query = _keyword_goods(query, filters['keyword'])
    if filters.get('location_code'):
        query = query.join(Location, Location.id == StockLedger.location_id).filter(
            Location.code.ilike(f"%{filters['location_code']}%"))
    column = StockLedger.posted_at if filters.get('time_field') == 'posted' else StockLedger.occurred_at
    query = clock.filter_time_range(query, column, filters.get('start_time'), filters.get('end_time'),
                                    filters.get('warehouse_id'))
    if filters.get('order') == 'asc':
        return query.order_by(column.asc(), StockLedger.id.asc())
    return query.order_by(column.desc(), StockLedger.id.desc())


def ledger_summary(filters: dict, start, end, stock_types=None, group_by='goods') -> list:
    """进销存：[start, end) 内（按记账时刻）的期初 / 入 / 出 / 期末。
    stock_types：统计哪些类别（默认仓内实物 = total_stock 口径）；入 / 出只算跨过这组类别边界的变动（如上架是
    待上架 → 库位，两边都在仓内，不算入也不算出），按变动类型分别给出。
    group_by：goods（按商品）/ goods_stock_type（按商品 × 类别，此时各类别各自为边界）。
    返回 [{warehouse_id, goods_id, stock_type?, opening, inbound, outbound, closing, inbound_by_movement,
    outbound_by_movement}]"""
    stock_types = tuple(stock_types or IN_WAREHOUSE_STOCK_TYPES)
    opening_rows = (_scope_summary(
        db.session.query(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.stock_type,
                         func.sum(StockLedger.quantity))
        .filter(StockLedger.stock_type.in_(stock_types)), filters)
        .filter(StockLedger.posted_at < start)
        .group_by(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.stock_type).all())
    per_type = group_by == 'goods_stock_type'

    def key(row):
        return (row[0], row[1], row[2]) if per_type else (row[0], row[1])

    result = defaultdict(lambda: {'opening': 0, 'inbound': 0, 'outbound': 0,
                                  'inbound_by_movement': defaultdict(int), 'outbound_by_movement': defaultdict(int)})
    for row in opening_rows:
        result[key(row)]['opening'] += int(row[3] or 0)
    # 期间内按行拆成入 / 出（同一组变动里可能有正有负）
    period_lines = (_scope_summary(
        db.session.query(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.stock_type,
                         StockLedger.counter_stock_type, StockLedger.movement_type, StockLedger.quantity)
        .filter(StockLedger.stock_type.in_(stock_types)), filters)
        .filter(StockLedger.posted_at >= start, StockLedger.posted_at < end).all())
    for row in period_lines:
        entry = result[key(row)]
        quantity = int(row[5])
        # 另一侧也在统计范围里 = 仓内搬动（只筛了一个库位时，进出这个库位都算）
        inside = (row[3] is not None and filters.get('location_id') is None
                  and (row[3] == row[2] if per_type else row[3] in stock_types))
        if inside:
            entry['_net'] = entry.get('_net', 0) + quantity
            continue
        if quantity > 0:
            entry['inbound'] += quantity
            entry['inbound_by_movement'][row[4]] += quantity
        else:
            entry['outbound'] += -quantity
            entry['outbound_by_movement'][row[4]] += -quantity
    items = []
    for k, entry in result.items():
        closing = entry['opening'] + entry['inbound'] - entry['outbound'] + entry.pop('_net', 0)
        item = {'warehouse_id': k[0], 'goods_id': k[1], 'opening': entry['opening'], 'inbound': entry['inbound'],
                'outbound': entry['outbound'], 'closing': closing,
                'inbound_by_movement': dict(entry['inbound_by_movement']),
                'outbound_by_movement': dict(entry['outbound_by_movement'])}
        if per_type:
            item['stock_type'] = k[2]
        if any((item['opening'], item['inbound'], item['outbound'], item['closing'])):
            items.append(item)
    items.sort(key=lambda i: (i['warehouse_id'], i['goods_id'], STOCK_TYPES.index(i.get('stock_type', 'onhand'))))
    return items


def _scope_summary(query, filters):
    from warehouse.goods.models import Goods
    if filters.get('warehouse_id'):
        query = query.filter(StockLedger.warehouse_id == filters['warehouse_id'])
    if filters.get('warehouse_ids'):
        query = query.filter(StockLedger.warehouse_id.in_(filters['warehouse_ids']))
    if filters.get('company_id'):
        query = query.filter(StockLedger.company_id == filters['company_id'])
    if filters.get('goods_id') is not None:
        query = query.filter(StockLedger.goods_id == filters['goods_id'])
    if filters.get('goods_ids'):
        query = query.filter(StockLedger.goods_id.in_(filters['goods_ids']))
    if filters.get('location_id') is not None:
        query = query.filter(StockLedger.location_id == filters['location_id'])
    if filters.get('lot_id') is not None:
        query = query.filter(StockLedger.lot_id == filters['lot_id'])
    if filters.get('keyword') or filters.get('goods_code'):
        query = query.join(Goods, Goods.id == StockLedger.goods_id)
        if filters.get('goods_code'):
            query = query.filter(Goods.code == filters['goods_code'])
        if filters.get('keyword'):
            query = _keyword_goods(query, filters['keyword'])
    return query


def daily_flows(warehouse_id, date_from, date_to, goods_ids=None) -> list:
    """每日入库 / 出库量（趋势图）：按业务发生时刻的仓库当地日期。入库 = 分拣完成（sort_in）、盘盈（adjust_gain）；
    出库 = 发货完成（ship）；另给盘亏（adjust_loss）。流水覆盖之前的日子用单据补（flows.py）"""
    from datetime import timedelta
    from .flows import inbound_events, outbound_events
    start = clock.local_midnight(date_from, warehouse_id)
    end = clock.local_midnight(date_to + timedelta(days=1), warehouse_id)
    days = {}
    day = date_from
    while day <= date_to:
        days[day] = {'date': day.isoformat(), 'inbound': 0, 'sort_in': 0, 'adjust_gain': 0, 'outbound': 0,
                     'adjust_loss': 0}
        day += timedelta(days=1)
    for _goods, moment, quantity, movement in inbound_events(warehouse_id, end, goods_ids, start=start):
        entry = days.get(clock.local_date(moment, warehouse_id))
        if entry is not None:
            entry['inbound'] += quantity
            entry[movement] += quantity
    for _goods, moment, quantity, _dn_type in outbound_events(warehouse_id, start, end, goods_ids):
        entry = days.get(clock.local_date(moment, warehouse_id))
        if entry is not None:
            entry['outbound'] += quantity
    loss = (db.session.query(StockLedger.occurred_at, StockLedger.quantity)
            .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type == 'adjust_loss',
                    StockLedger.occurred_at >= start, StockLedger.occurred_at < end))
    if goods_ids is not None:
        loss = loss.filter(StockLedger.goods_id.in_(list(goods_ids)))
    for moment, quantity in loss.all():
        entry = days.get(clock.local_date(moment, warehouse_id))
        if entry is not None:
            entry['adjust_loss'] += -int(quantity)
    return list(days.values())


# ---------------------------------------------------------------------------
# 按库位查看库存（库位 × 商品；二期起 × 批次）
# ---------------------------------------------------------------------------
def shelf_of(code: str, separator='-', levels=1, prefix_length=None) -> str:
    """库位编码 → 货架（库区）：给了 prefix_length 取前 N 个字符；否则按分隔符取前 levels 段（没有分隔符 = 整个编码）"""
    code = code or ''
    if prefix_length:
        return code[:prefix_length]
    if separator and separator in code:
        return separator.join(code.split(separator)[:max(1, levels)])
    return code


def location_stock(filters: dict):
    """库位与其库存：[(Location, [(GoodsLocation, Goods)])]；filters：warehouse_id / warehouse_ids / location_id /
    location_type / keyword（库位编码、商品编码 / 名称）/ include_empty / include_inactive"""
    from warehouse.goods.models import Goods, GoodsLocation
    from warehouse.location.models import Location
    query = Location.query
    if filters.get('warehouse_id'):
        query = query.filter(Location.warehouse_id == filters['warehouse_id'])
    if filters.get('warehouse_ids'):
        query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))
    if filters.get('location_id'):
        query = query.filter(Location.id == filters['location_id'])
    if filters.get('location_type'):
        query = query.filter(Location.location_type == filters['location_type'])
    if not filters.get('include_inactive'):
        query = query.filter(Location.is_active == True)
    locations = query.order_by(Location.code, Location.id).all()
    if not locations:
        return []
    rows = (db.session.query(GoodsLocation, Goods).join(Goods, Goods.id == GoodsLocation.goods_id)
            .filter(GoodsLocation.location_id.in_([l.id for l in locations]), GoodsLocation.quantity > 0)
            .order_by(Goods.code, GoodsLocation.id).all())
    by_location = defaultdict(list)
    for stock, goods in rows:
        by_location[stock.location_id].append((stock, goods))
    keyword = (filters.get('keyword') or '').strip().lower()
    result = []
    for location in locations:
        items = by_location.get(location.id, [])
        if keyword:
            if keyword not in (location.code or '').lower():
                items = [(s, g) for s, g in items if keyword in (g.code or '').lower()
                         or keyword in (g.name or '').lower()]
                if not items:
                    continue
        if not items and not filters.get('include_empty', True):
            continue
        result.append((location, items))
    return result


def location_volume_m3(location):
    """库位容积（立方米）：capacity，没设时按 宽 × 深 × 高（厘米）算；都没有为 None"""
    if location.capacity:
        return float(location.capacity)
    if location.width and location.depth and location.height:
        return float(location.width) * float(location.depth) * float(location.height) / 1_000_000
    return None


def goods_volume_m3(goods):
    """商品单件体积（立方米，长宽高毫米）；缺尺寸为 None"""
    if goods.length and goods.width and goods.height:
        return float(goods.length) * float(goods.width) * float(goods.height) / 1_000_000_000
    return None


def location_summary(location, items) -> dict:
    quantity = sum(int(s.quantity) for s, _g in items)
    capacity = location_volume_m3(location)
    used = None
    unknown = 0
    if capacity:
        used = 0.0
        for stock, goods in items:
            volume = goods_volume_m3(goods)
            if volume is None:
                unknown += int(stock.quantity)
            else:
                used += volume * int(stock.quantity)
    return {
        'location': location, 'goods_count': len({g.id for _s, g in items}), 'quantity': quantity,
        'lot_count': len({s.lot_id for s, _g in items if s.lot_id is not None}),
        'is_empty': quantity == 0,
        'stock_type': LOCATION_STOCK_TYPES.get(location.location_type),
        'capacity_m3': round(capacity, 6) if capacity else None,
        'used_volume_m3': round(used, 6) if used is not None else None,
        'volume_utilization': round(used / capacity, 4) if capacity and used is not None else None,
        'volume_unknown_quantity': unknown if capacity else None,
        'items': items,
    }


def occupancy(summaries) -> dict:
    """库位占用：默认按「有库存的库位占比」；设了容积的库位另给容积利用率（只算设了容积的库位）"""
    total = len(summaries)
    occupied = sum(1 for s in summaries if not s['is_empty'])
    sized = [s for s in summaries if s['capacity_m3']]
    capacity = sum(s['capacity_m3'] for s in sized)
    used = sum(s['used_volume_m3'] or 0 for s in sized)
    return {
        'location_count': total, 'occupied_count': occupied, 'empty_count': total - occupied,
        'occupancy_rate': round(occupied / total, 4) if total else None,
        'quantity': sum(s['quantity'] for s in summaries),
        'sized_location_count': len(sized),
        'capacity_m3': round(capacity, 6) if sized else None,
        'used_volume_m3': round(used, 6) if sized else None,
        'volume_utilization': round(used / capacity, 4) if capacity else None,
    }
