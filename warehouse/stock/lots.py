"""Lot validation and outbound allocations. Callers hold the parent document lock."""
from datetime import date

from extensions.db import db
from extensions.error import BadRequestException
from warehouse.common import clock, require_positive_int
from .models import GoodsLot, DNStockAllocation


def lot_for_goods(goods_id, lot_id):
    if lot_id is None:
        return None
    lot_id = require_positive_int(lot_id, 'lot_id')
    lot = db.session.get(GoodsLot, lot_id)
    if lot is None or lot.goods_id != goods_id:
        raise BadRequestException('Lot does not belong to this product', 16170, field='lot_id')
    return lot


def validated_lot_id(goods_id, lot_id):
    lot = lot_for_goods(goods_id, lot_id)
    return lot.id if lot else None


def assert_receivable(goods_id, lot_id, warehouse_id, concession_reason=None):
    from warehouse.goods.models import Goods
    from .lot_policy import effective_rules
    rules = effective_rules(db.session.get(Goods, goods_id))['values']
    lot = lot_for_goods(goods_id, lot_id)
    if rules['mode'] != 'none' and lot is None:
        raise BadRequestException('Receiving requires a lot', 16175, field='lot_id')
    if lot and (lot.status != 'active' or
                (rules['mode'] == 'expiry' and lot.expiry_date is None) or
                (lot.expiry_date and (lot.expiry_date - clock.local_today(warehouse_id)).days < rules['receive_days'] and not concession_reason)):
        raise BadRequestException('Lot is held, expired or below the receiving shelf-life limit', 16175, field='lot_id')


def lot_priority(lot, strategy='fefo'):
    # Stable ordering also covers historical lots with no inbound timestamp.
    inbound = lot.first_inbound_at.isoformat() if lot and lot.first_inbound_at else ''
    expiry = lot.expiry_date if lot and lot.expiry_date else date.max
    return (inbound, expiry, lot.id if lot else 0) if strategy == 'fifo' else (expiry, inbound, lot.id if lot else 0)


def assert_shippable(goods_id, lot_id, warehouse_id):
    from warehouse.goods.models import Goods
    from .lot_policy import effective_rules
    rules = effective_rules(db.session.get(Goods, goods_id), warehouse_id=warehouse_id)['values']
    lot = lot_for_goods(goods_id, lot_id)
    if lot is not None:
        db.session.refresh(lot)
    if lot is None and rules['mode'] != 'none' and not rules['allow_unassigned']:
        raise BadRequestException('Unassigned stock must be labelled before shipping', 16171, field='lot_id')
    if lot and (lot.status != 'active' or (lot.expiry_date and
            (lot.expiry_date - clock.local_today(warehouse_id)).days < rules['ship_days'])
            or (rules['mode'] == 'expiry' and lot.expiry_date is None)):
        raise BadRequestException('Lot is held, expired or below the minimum remaining shelf life', 16171, field='lot_id', details={'lot_id': lot.id})


def record_picking(dn, details):
    for detail in details:
        lot_for_goods(detail.goods_id, detail.lot_id)
        row = DNStockAllocation.query.filter_by(dn_id=dn.id, goods_id=detail.goods_id, lot_id=detail.lot_id).first()
        if row is None:
            row = DNStockAllocation(dn_id=dn.id, goods_id=detail.goods_id, lot_id=detail.lot_id,
                                    picked_quantity=0, packed_quantity=0)
            db.session.add(row)
        row.picked_quantity += detail.picked_quantity
        db.session.flush()


def allocated_moves(dn, detail, op, stage, packed_lots=None):
    """Return False for historical orders without allocations; never infer their lots."""
    from .service import Leg
    from warehouse.dn.stock_policy import return_leg, assert_lot
    from warehouse.asn.return_outbound import restore
    rows = DNStockAllocation.query.filter_by(dn_id=dn.id, goods_id=detail.goods_id).all()
    if not rows:
        return False
    from warehouse.goods.models import Goods
    from .lot_policy import effective_rules
    strategy = effective_rules(db.session.get(Goods, detail.goods_id))['values']['strategy']
    rows.sort(key=lambda row: lot_priority(db.session.get(GoodsLot, row.lot_id) if row.lot_id else None, strategy))
    if stage == 'pack':
        remaining = detail.packed_quantity or 0
        if packed_lots is None and len([row for row in rows if row.picked_quantity>0])>1 and 0<remaining<sum(row.picked_quantity for row in rows):
            raise BadRequestException('Select actual packed lots for partial mixed-lot packing',16172,field='lot_allocations')
        if sum(row.picked_quantity for row in rows) != (detail.picked_quantity or 0):
            raise BadRequestException('Picked lot allocations do not match the order', 16172)
        for row in rows:
            quantity = packed_lots[row.id] if packed_lots is not None else min(row.picked_quantity, remaining)
            row.packed_quantity = quantity
            remaining -= quantity
            op.move(detail.goods_id, quantity, src=Leg.picked(row.lot_id), dst=Leg.packed(row.lot_id), doc_line_id=detail.id)
            destination = return_leg(dn, detail.goods_id, row.lot_id)
            op.move(detail.goods_id, row.picked_quantity - quantity, src=Leg.picked(row.lot_id),
                    dst=destination, movement_type='pack_short_return', doc_line_id=detail.id)
            restore(dn, row.picked_quantity - quantity, destination.location_id)
        if remaining:
            raise BadRequestException('Packed quantity exceeds picked lot allocations', 16172)
    else:
        for row in rows:
            if stage == 'ship':
                if row.packed_quantity:
                    assert_lot(dn, row.goods_id, row.lot_id)
                src, dst, quantity = Leg.packed(row.lot_id), Leg.in_transit(row.lot_id), row.packed_quantity
            elif stage == 'sign':
                src, dst, quantity = Leg.in_transit(row.lot_id), None, row.packed_quantity
            elif stage == 'cancel_picked':
                src, dst, quantity = Leg.picked(row.lot_id), return_leg(dn, detail.goods_id, row.lot_id), row.picked_quantity
            else:
                src, dst, quantity = Leg.packed(row.lot_id), return_leg(dn, detail.goods_id, row.lot_id), row.packed_quantity
            op.move(detail.goods_id, quantity, src=src, dst=dst, doc_line_id=detail.id)
            if stage in ('cancel_picked', 'cancel_packed'):
                restore(dn, quantity, dst.location_id)
    return True
