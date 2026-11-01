"""Explicit, replay-safe lot relabelling; stock quantity never changes."""
from uuid import UUID
from sqlalchemy import func
from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common import require_positive_int, get_company_owned, get_warehouse_owned
from warehouse.common.locks import lock_row, lock_inventory_rows
from warehouse.goods.models import Goods
from warehouse.location.models import Location
from warehouse.warehouse.models import Warehouse
from warehouse.picking.models import PickingTask, PickingTaskDetail
from .models import StockLedger
from .lots import validated_lot_id
from .service import StockOp, Leg, lock_goods_location


@transactional
def relabel(warehouse_id, body):
    if not isinstance(body, dict) or set(body) - {'goods_id', 'location_id', 'from_lot_id', 'to_lot_id', 'quantity', 'reason', 'request_id'}:
        raise BadRequestException('Invalid relabel request')
    goods_id = require_positive_int(body.get('goods_id'), 'goods_id')
    location_id = require_positive_int(body.get('location_id'), 'location_id')
    quantity = require_positive_int(body.get('quantity'), 'quantity')
    reason = body.get('reason')
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise BadRequestException('A reason of 1-255 characters is required', field='reason')
    reason = reason.strip()
    try:
        request_id = str(UUID(body.get('request_id')))
    except (TypeError, ValueError, AttributeError):
        raise BadRequestException('request_id must be a UUID', field='request_id')
    get_company_owned(Goods, goods_id)
    source = validated_lot_id(goods_id, body.get('from_lot_id'))
    target = validated_lot_id(goods_id, body.get('to_lot_id'))
    if target is None or source == target:
        raise BadRequestException('Select a different destination lot', field='to_lot_id')
    # Serialise relabel replay checks within this warehouse, before stock locks.
    lock_row(db.session.get(Warehouse, warehouse_id))
    old = StockLedger.query.filter_by(warehouse_id=warehouse_id, doc_type='lot_relabel', doc_no=request_id).all()
    signature = {(goods_id, location_id, source, -quantity, reason), (goods_id, location_id, target, quantity, reason)}
    if old:
        if {(r.goods_id, r.location_id, r.lot_id, r.quantity, r.remark) for r in old} != signature:
            raise ConflictException('Relabel request was already used with different data', 16121)
        return dict(request_id=request_id, ledger_ids=[r.id for r in old], replayed=True)
    location = get_warehouse_owned(Location, location_id)
    if location.warehouse_id != warehouse_id:
        raise BadRequestException('Location belongs to another warehouse', field='location_id')
    lock_row(location, shared=True)
    lock_inventory_rows([goods_id], warehouse_id)
    locked = {lot_id: lock_goods_location(goods_id, location_id, lot_id)
              for lot_id in sorted((source, target), key=lambda value: value or 0)}
    stock = locked[source]
    reserved = (db.session.query(func.coalesce(func.sum(PickingTaskDetail.picked_quantity), 0))
                .join(PickingTask).filter(PickingTaskDetail.goods_id == goods_id,
                    PickingTaskDetail.location_id == location_id, PickingTaskDetail.lot_id == source,
                    PickingTask.status == 'in_progress', PickingTask.is_active.is_(True)).scalar())
    if stock is None or quantity > stock.quantity - reserved:
        raise ConflictException('Insufficient unreserved stock for relabelling', 16036)
    op = StockOp(warehouse_id, 'lot_relabel', doc_type='lot_relabel', doc_no=request_id,
                 trace_id='relabel-' + request_id, reason='relabel', remark=reason)
    op.move(goods_id, quantity, src=Leg.at(location_id, source), dst=Leg.at(location_id, target))
    rows = op.commit()
    return dict(request_id=request_id, ledger_ids=[r.id for r in rows], replayed=False)
