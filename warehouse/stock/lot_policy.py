"""Field-wise product > category > company lot rules, with explicit provenance."""
from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from .models import CompanyLotRules, GoodsLotRules, GoodsLot, InventoryLotStock

DEFAULTS = dict(mode='none', source='manual', shelf_life_days=None, warning_days=0,
                receive_days=0, ship_days=0, strategy='fefo', allow_unassigned=True)
ENUMS = dict(mode=('none', 'lot', 'expiry'), source=('manual', 'auto'), strategy=('fifo', 'fefo'))


def validate_rules(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise BadRequestException('Invalid lot rules', field='rules')
    for key, val in value.items():
        if key in ENUMS:
            valid = isinstance(val, str) and val in ENUMS[key]
        elif key == 'allow_unassigned':
            valid = type(val) is bool
        else:
            valid = (key == 'shelf_life_days' and val is None) or (type(val) is int and 0 <= val <= 36500)
        if not valid:
            raise BadRequestException('Invalid lot rule value', field=key)
    return dict(value)


def effective_rules(goods, overrides=None, company_rules=None, warehouse_id=None):
    company = company_rules if company_rules is not None else db.session.get(CompanyLotRules, goods.company_id)
    own = db.session.get(GoodsLotRules, goods.id) if goods.id else None
    layers = [('company', company.defaults if company else {}),
              ('category', (company.categories or {}).get(goods.category, {}) if company else {}),
              ('goods', overrides if overrides is not None else (own.overrides if own else {}))]
    values, sources = dict(DEFAULTS), {key: 'default' for key in DEFAULTS}
    for source, layer in layers:
        for key, val in (layer or {}).items():
            values[key], sources[key] = val, source
    if warehouse_id is not None:
        from warehouse.warehouse.models import Warehouse
        warehouse = db.session.get(Warehouse, warehouse_id)
        configured = (warehouse.operations or {}).get('allow_unassigned') if warehouse else None
        if warehouse and warehouse.company_id == goods.company_id and configured is not None:
            values['allow_unassigned'], sources['allow_unassigned'] = configured, 'warehouse'
    return dict(values=values, sources=sources)


def has_lot_stock(goods_id):
    from warehouse.goods.models import GoodsLocation
    return (GoodsLocation.query.filter(GoodsLocation.goods_id == goods_id, GoodsLocation.lot_id.isnot(None),
                                      GoodsLocation.quantity > 0).first() is not None
            or InventoryLotStock.query.filter(InventoryLotStock.goods_id == goods_id,
                InventoryLotStock.lot_id.isnot(None),
                InventoryLotStock.sorted_stock + InventoryLotStock.picked_stock + InventoryLotStock.packed_stock
                + InventoryLotStock.delivered_stock > 0).first() is not None)


def assert_can_disable(goods, effective):
    if effective['values']['mode'] == 'none' and effective_rules(goods)['values']['mode'] != 'none' and has_lot_stock(goods.id):
        raise ConflictException('Cannot disable lot management while lot stock exists', 16174)


def revision_of(body):
    if not isinstance(body, dict) or type(body.get('revision')) is not int or body['revision'] < 0:
        raise BadRequestException('Invalid revision', field='revision')
    return body['revision']


def blocked_onhand(goods_id, warehouse_id):
    from datetime import timedelta
    from sqlalchemy import or_, and_
    from warehouse.goods.models import Goods, GoodsLocation
    from warehouse.location.models import Location
    from warehouse.common import clock
    goods = db.session.get(Goods, goods_id)
    if goods is None:
        return 0
    rules = effective_rules(goods, warehouse_id=warehouse_id)['values']
    cutoff = clock.local_today(warehouse_id) + timedelta(days=rules['ship_days'])
    blocked = [GoodsLot.status != 'active', GoodsLot.expiry_date < cutoff]
    if rules['mode'] == 'expiry':
        blocked.append(and_(GoodsLot.id.isnot(None), GoodsLot.expiry_date.is_(None)))
    if rules['mode'] != 'none' and not rules['allow_unassigned']:
        blocked.append(GoodsLocation.lot_id.is_(None))
    return int(db.session.query(db.func.coalesce(db.func.sum(GoodsLocation.quantity), 0))
        .join(Location, Location.id == GoodsLocation.location_id)
        .outerjoin(GoodsLot, GoodsLot.id == GoodsLocation.lot_id)
        .filter(GoodsLocation.goods_id == goods_id, Location.warehouse_id == warehouse_id,
                Location.is_active.is_(True), Location.location_type == 'standard', or_(*blocked)).scalar() or 0)


def blocked_stock_map(pairs):
    """Batch calculation for exports, summary and threshold filtering."""
    from sqlalchemy import tuple_
    from warehouse.goods.models import Goods, GoodsLocation
    from warehouse.location.models import Location
    from warehouse.common import clock
    pairs = list(set(pairs))
    result, rules_by_goods, today_by_warehouse = {}, {}, {}
    for start in range(0, len(pairs), 300):
        rows = (db.session.query(GoodsLocation, Location.warehouse_id)
                .join(Location, Location.id == GoodsLocation.location_id)
                .filter(tuple_(GoodsLocation.goods_id, Location.warehouse_id).in_(pairs[start:start+300]),
                        Location.is_active.is_(True), Location.location_type == 'standard', GoodsLocation.quantity > 0))
        for row, warehouse_id in rows:
            pair = (row.goods_id, warehouse_id)
            if pair not in rules_by_goods:
                rules_by_goods[pair] = effective_rules(db.session.get(Goods, row.goods_id), warehouse_id=warehouse_id)['values']
            if warehouse_id not in today_by_warehouse:
                today_by_warehouse[warehouse_id] = clock.local_today(warehouse_id)
            rules, lot = rules_by_goods[pair], row.lot
            blocked = ((lot is None and rules['mode'] != 'none' and not rules['allow_unassigned']) or
                       (lot is not None and (lot.status != 'active' or
                        (rules['mode'] == 'expiry' and lot.expiry_date is None) or
                        (lot.expiry_date and (lot.expiry_date-today_by_warehouse[warehouse_id]).days < rules['ship_days']))))
            if blocked:
                key = (row.goods_id, warehouse_id)
                result[key] = result.get(key, 0) + row.quantity
    return result
