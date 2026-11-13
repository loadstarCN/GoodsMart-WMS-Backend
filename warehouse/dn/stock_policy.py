"""Outbound purpose selects a stock pool; quality identity survives cancellation and short packing."""
from extensions.db import db
from extensions.error import BadRequestException

RESERVED = {'standard': 'dn_stock', 'damaged': 'damaged_dn_stock', 'return': 'return_dn_stock'}
PHYSICAL = {'standard': 'onhand_stock', 'damaged': 'damage_stock', 'return': 'return_stock'}


def source(dn):
    return dn.stock_source or ('damaged' if dn.dn_type == 'damage_to_supplier' else 'standard')


def parse(data, company_id):
    kind = data.get('dn_type') or 'shipping'
    if kind == 'transfer':
        raise BadRequestException('Create a linked warehouse transfer order via /transfer/orders', field='dn_type')
    pool = data.get('stock_source') or ('damaged' if kind == 'damage_to_supplier' else 'standard')
    if pool not in RESERVED or (kind in ('shipping', 'transfer') and pool != 'standard') or (kind == 'damage_to_supplier' and pool != 'damaged'):
        raise BadRequestException('Invalid stock source for this outbound purpose', field='stock_source')
    supplier, reason = data.get('supplier_id'), data.get('purpose_reason')
    if kind in ('return_to_supplier', 'damage_to_supplier'):
        from warehouse.supplier.models import Supplier
        row = db.session.get(Supplier, supplier) if type(supplier) is int else None
        if row is None or row.company_id != company_id:
            raise BadRequestException('A supplier in this company is required', field='supplier_id')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
            raise BadRequestException('A return reason of 1-255 characters is required', field='purpose_reason')
    elif supplier is not None:
        raise BadRequestException('Supplier is only supported for supplier returns', field='supplier_id')
    return dict(stock_source=pool, supplier_id=supplier, purpose_reason=reason.strip() if isinstance(reason, str) else None)


def assert_lot(dn, goods_id, lot_id):
    if dn.dn_type in ('return_to_supplier', 'damage_to_supplier'):
        # Explicit supplier-return purpose permits held/expired goods, never another product's lot.
        from warehouse.stock.lots import lot_for_goods
        lot_for_goods(goods_id, lot_id)
        from warehouse.asn.return_models import ReturnDisposition, CustomerReturnLine
        decision = ReturnDisposition.query.filter_by(linked_dn_id=dn.id).first()
        if decision:
            line = db.session.get(CustomerReturnLine, decision.line_id)
            if goods_id != line.goods_id or lot_id != line.lot_id:
                raise BadRequestException('Supplier return must use the authorized customer-return lot', field='lot_id')
    else:
        from warehouse.stock.lots import assert_shippable
        assert_shippable(goods_id, lot_id, dn.warehouse_id)


def assert_movement_lot(op, goods_id, lot_id):
    """Resolve purpose from the owning document, never from caller-supplied flags."""
    from warehouse.dn.models import DN
    dn = db.session.get(DN, op.doc_id) if op.doc_type == 'dn' else None
    if op.doc_type == 'picking_task':
        from warehouse.picking.models import PickingTask
        task = db.session.get(PickingTask, op.doc_id)
        dn = task.dn if task else None
    if dn is not None and dn.warehouse_id == op.warehouse_id:
        assert_lot(dn, goods_id, lot_id)
    else:
        from warehouse.stock.lots import assert_shippable
        assert_shippable(goods_id, lot_id, op.warehouse_id)


def return_leg(dn, goods_id, lot_id=None):
    from warehouse.stock.service import Leg
    pool = source(dn)
    if pool != 'return':
        return Leg.pending(lot_id, damaged=pool == 'damaged')
    from warehouse.location.models import Location
    from sqlalchemy.exc import IntegrityError
    row = Location.query.filter_by(warehouse_id=dn.warehouse_id, location_type='return', is_active=True).order_by(Location.id).first()
    if row is None:
        code = 'SYSTEM-RETURN-HOLD'
        try:
            with db.session.begin_nested():
                row = Location(warehouse_id=dn.warehouse_id, code=code, location_type='return', created_by=dn.created_by)
                db.session.add(row)
                db.session.flush()
        except IntegrityError:
            row = Location.query.filter_by(warehouse_id=dn.warehouse_id, code=code).one()
            if not row.is_active or row.location_type != 'return':
                raise BadRequestException('Enable a return location before cancelling this return', field='location_id')
    return Leg.at(row.id, lot_id)
