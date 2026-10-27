"""Warehouse ledger balances: include picked and packed until shipment."""
from collections import defaultdict
from datetime import timedelta
from sqlalchemy import func
from warehouse.common import clock
from .models import StockLedger, GoodsLot, IN_WAREHOUSE_STOCK_TYPES
from .queries import ledger_query

BALANCE_TYPES = IN_WAREHOUSE_STOCK_TYPES


def enrich_ledger_lines(lines):
    """Balance at each booking position across all lots/locations of its stock type.

    Detail filters and page boundaries must not cut the history used by the sum.
    The caller supplies already authorized lines; the join also includes company.
    """
    if not lines:
        return
    from sqlalchemy import and_, or_
    from sqlalchemy.orm import aliased
    from extensions.db import db
    history = aliased(StockLedger)
    # Chunk all=true requests as well as normal pages to bound SQL parameter lists.
    for offset in range(0, len(lines), 500):
        chunk = lines[offset:offset + 500]
        balances = dict(db.session.query(StockLedger.id, func.sum(history.quantity)).join(history, and_(
            history.company_id == StockLedger.company_id,
            history.warehouse_id == StockLedger.warehouse_id,
            history.goods_id == StockLedger.goods_id,
            history.stock_type == StockLedger.stock_type,
            or_(history.posted_at < StockLedger.posted_at,
                and_(history.posted_at == StockLedger.posted_at, history.id <= StockLedger.id))
        )).filter(StockLedger.id.in_([line.id for line in chunk])).group_by(StockLedger.id).all())
        lots = {lot.id: lot for lot in GoodsLot.query.filter(GoodsLot.id.in_([line.lot_id for line in chunk if line.lot_id])).all()}
        for line in chunk:
            line.category_balance_after = int(balances.get(line.id, 0))
            lot = lots.get(line.lot_id)
            line.lot_no = lot.lot_no if lot else None


def ledger_overview(warehouse, filters, start, end, page=1, per_page=50):
    # Document/movement filters cannot define an opening or closing stock balance.
    scope = {key: value for key, value in filters.items() if key in (
        'company_id', 'goods_id', 'goods_code', 'keyword', 'lot_id', 'lot_no',
        'location_id', 'location_code', 'category', 'brand', 'category_missing', 'brand_missing')}
    types = filters.get('stock_types') or list(BALANCE_TYPES)
    scope.update(warehouse_id=warehouse.id, stock_types=types)
    base = ledger_query(scope).order_by(None)
    rows = defaultdict(lambda: dict(opening=0, inbound=0, outbound=0, closing=0))
    opening = base.with_entities(StockLedger.goods_id, StockLedger.lot_id, func.sum(StockLedger.quantity)).filter(
        StockLedger.posted_at < start).group_by(StockLedger.goods_id, StockLedger.lot_id)
    for goods_id, lot_id, quantity in opening:
        rows[(goods_id, lot_id)]['opening'] = int(quantity or 0)
        rows[(goods_id, lot_id)]['closing'] = int(quantity or 0)
    deltas = defaultdict(int)
    period = base.with_entities(StockLedger.goods_id, StockLedger.lot_id, StockLedger.quantity,
                               StockLedger.counter_stock_type, StockLedger.posted_at).filter(
        StockLedger.posted_at >= start, StockLedger.posted_at < end)
    location_scope = scope.get('location_id') is not None or bool(scope.get('location_code'))
    for goods_id, lot_id, quantity, counter, posted_at in period.yield_per(1000):
        row = rows[(goods_id, lot_id)]
        row['closing'] += quantity
        deltas[clock.local_date(posted_at, warehouse)] += quantity
        if not location_scope and counter in types:
            continue
        row['inbound' if quantity > 0 else 'outbound'] += abs(quantity)
    totals = {key: sum(row[key] for row in rows.values()) for key in ('opening', 'inbound', 'outbound', 'closing')}
    # Keep zero-closing lots when they had activity in the selected period.
    keys = sorted((key for key, row in rows.items() if any(row.values())), key=lambda k: (k[0], k[1] or 0))
    selected = keys[(page - 1) * per_page:page * per_page]
    from warehouse.goods.models import Goods
    goods = {g.id: g for g in Goods.query.filter(Goods.id.in_([key[0] for key in selected])).all()}
    lots = {lot.id: lot for lot in GoodsLot.query.filter(GoodsLot.id.in_([key[1] for key in selected if key[1]])).all()}
    items = []
    for goods_id, lot_id in selected:
        lot, good = lots.get(lot_id), goods[goods_id]
        items.append(dict(goods_id=goods_id, goods=dict(id=good.id, name=good.name, code=good.code),
                          lot_id=lot_id, lot_no=lot.lot_no if lot else None,
                          expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
                          **rows[(goods_id, lot_id)]))
    running = totals['opening']
    day = clock.local_date(start, warehouse)
    last = clock.local_date(end - timedelta(microseconds=1), warehouse)
    series = []
    while day <= last:
        running += deltas[day]
        series.append(dict(date=day.isoformat(), total=running))
        day += timedelta(days=1)
    return dict(warehouse_id=warehouse.id, timezone=clock.warehouse_timezone_name(warehouse),
                start_time=clock.utc_iso(start), end_time=clock.utc_iso(end-timedelta(microseconds=1)),
                stock_types=list(types), totals=totals, total=len(keys), items=items,
                page=page, per_page=per_page, series=series)
