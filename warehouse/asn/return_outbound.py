"""Keep approved supplier returns linked to their original quarantine on reversal."""
from extensions.db import db
from extensions.error import ConflictException
from warehouse.common.locks import lock_row
from .return_models import CustomerReturn, CustomerReturnLine, ReturnDisposition, ReturnHolding


def decision_for(dn):
    return ReturnDisposition.query.filter_by(linked_dn_id=dn.id).first() if dn.id else None


def lock_parent(dn):
    decision = decision_for(dn)
    if decision:
        lock_row(db.session.get(CustomerReturn, decision.return_id))


def require_independent(dn):
    if decision_for(dn):
        raise ConflictException('This outbound is approved by a customer return; cancel it and submit a new disposition to change quantities or destination')


def reserved(goods_id, warehouse_id, location_id, lot_id, exclude_dn=None):
    """Approved returns awaiting picking remain reserved at their inspected location."""
    from warehouse.dn.models import DN
    query = (db.session.query(db.func.coalesce(db.func.sum(
        ReturnDisposition.quantity - ReturnDisposition.restored_quantity), 0))
        .join(CustomerReturnLine, ReturnDisposition.line_id == CustomerReturnLine.id)
        .join(DN, ReturnDisposition.linked_dn_id == DN.id)
        .filter(DN.warehouse_id == warehouse_id, DN.status.in_(('pending','in_progress')),
                DN.is_active.is_(True), ReturnDisposition.status == 'approved',
                CustomerReturnLine.goods_id == goods_id, CustomerReturnLine.lot_id == lot_id,
                ReturnDisposition.location_id == location_id))
    if exclude_dn is not None:
        query = query.filter(DN.id != exclude_dn)
    return int(query.scalar())


def assert_source(dn, location_id):
    decision = decision_for(dn)
    if decision and decision.location_id != location_id:
        raise ConflictException('Pick this supplier return from its inspected quarantine location')


def restore(dn, quantity, location_id):
    if not quantity:
        return
    decision = decision_for(dn)
    if not decision:
        return
    if quantity > decision.quantity - decision.restored_quantity:
        raise ConflictException('Supplier-return reversal exceeds its approved balance')
    line = db.session.get(CustomerReturnLine, decision.line_id)
    holding = ReturnHolding.query.filter_by(line_id=line.id, location_id=location_id).first()
    if holding is None:
        holding = ReturnHolding(line_id=line.id, location_id=location_id, quantity=0)
        db.session.add(holding)
    holding.quantity += quantity
    line.disposed -= quantity
    decision.restored_quantity += quantity
    from .returns import update_status
    update_status(db.session.get(CustomerReturn, decision.return_id))
    db.session.flush()


def restore_unpicked(dn, cancelled=False):
    decision = decision_for(dn)
    if not decision:
        return
    remaining = decision.quantity if cancelled else sum(d.quantity - (d.picked_quantity or 0) for d in dn.details)
    restore(dn, remaining - decision.restored_quantity, decision.location_id)
