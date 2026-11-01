"""Live warehouse expiry balances; missing dates stay unknown."""
from sqlalchemy import func, union_all, case, literal
from sqlalchemy.orm import lazyload
from extensions.db import db
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.location.models import Location
from warehouse.common import clock
from .models import GoodsLot, InventoryLotStock
from .lot_policy import effective_rules


def expiry_balances(warehouse, page=1, per_page=50, keyword='', status=None, filters=None, has_lot=None):
    keys = ('onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock',
            'sorted_damage_stock', 'picked_stock', 'packed_stock')
    location_types = ('standard', 'damaged', 'return')
    locations = (db.session.query(GoodsLocation.goods_id.label('goods_id'), GoodsLocation.lot_id.label('lot_id'),
                    GoodsLocation.location_id.label('location_id'),
                    *[case((Location.location_type == kind, GoodsLocation.quantity), else_=0).label(key)
                      for key, kind in zip(keys[:3], location_types)],
                    *[literal(0).label(key) for key in keys[3:]])
                 .join(Location, Location.id == GoodsLocation.location_id)
                 .filter(Location.warehouse_id == warehouse.id, GoodsLocation.quantity > 0))
    pending = (db.session.query(InventoryLotStock.goods_id, InventoryLotStock.lot_id,
                    literal(None).label('location_id'), *[literal(0).label(key) for key in keys[:3]],
                    *[getattr(InventoryLotStock, key).label(key) for key in keys[3:]])
               .filter(InventoryLotStock.warehouse_id == warehouse.id))
    stock = union_all(locations.statement, pending.statement).subquery()
    total = sum(stock.c[key] for key in keys if key != 'sorted_damage_stock')
    query = (db.session.query(Goods, GoodsLot, *[func.sum(stock.c[key]) for key in keys],
                             func.count(func.distinct(stock.c.location_id))).options(lazyload('*'))
             .join(stock, stock.c.goods_id == Goods.id).outerjoin(GoodsLot, GoodsLot.id == stock.c.lot_id)
             .filter(Goods.company_id == warehouse.company_id))
    from warehouse.inventory.analysis_scope import has_scope, goods_query
    if filters and has_scope(filters):
        query = query.filter(Goods.id.in_(goods_query(filters)))
    if keyword:
        query = query.filter(db.or_(Goods.name.ilike('%'+keyword+'%'), Goods.code.ilike('%'+keyword+'%'),
                                   GoodsLot.lot_no.ilike('%'+keyword+'%')))
    balances = query.group_by(Goods.id, GoodsLot.id).having(func.sum(total) > 0).all()
    rules = {row[0].id: effective_rules(row[0], warehouse_id=warehouse.id)['values'] for row in balances}
    tracked = {row[0].id for row in balances if row[1] is not None or rules[row[0].id]['mode'] != 'none'}
    today = clock.local_today(warehouse)
    items = []
    summary = {key: 0 for key in ('normal', 'warning', 'blocked', 'unknown')}
    summary['unassigned'] = 0
    for row in balances:
        goods, lot = row[:2]
        composition = {key: int(value or 0) for key, value in zip(keys, row[2:-1])}
        quantity = sum(value for key, value in composition.items() if key != 'sorted_damage_stock')
        if goods.id not in tracked:
            continue
        rule = rules[goods.id]
        remaining = (lot.expiry_date-today).days if lot and lot.expiry_date else None
        state = 'unknown' if remaining is None else 'blocked' if remaining < rule['ship_days'] else 'warning' if remaining <= rule['warning_days'] else 'normal'
        if lot and lot.status != 'active':
            state = 'blocked'
        if lot is None and rule['mode'] != 'none' and not rule['allow_unassigned']:
            state = 'blocked'
        summary[state] += int(quantity)
        if lot is None:
            summary['unassigned'] += int(quantity)
        items.append(dict(goods_id=goods.id, goods_name=goods.name, goods_code=goods.code,
            lot_id=lot.id if lot else None, lot_no=lot.lot_no if lot else None,
            production_date=lot.production_date.isoformat() if lot and lot.production_date else None,
            location_count=int(row[-1]), **composition,
            expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
            remaining_days=remaining, quantity=int(quantity), status=state,
            ship_days=rule['ship_days'], warning_days=rule['warning_days']))
    items.sort(key=lambda row: (row['goods_code'], row['expiry_date'] or '9999-12-31', row['lot_id'] or 0))
    if status:
        items = [row for row in items if row['status'] == status]
    if has_lot is not None:
        items = [row for row in items if (row['lot_id'] is not None) == has_lot]
    total = len(items)
    return dict(as_of=today.isoformat(), summary=summary, total=total, page=page, per_page=per_page,
                items=items[(page-1)*per_page:page*per_page])
