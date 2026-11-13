from flask import request
from flask_restx import Resource
from extensions.db import db, get_object_or_404
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from extensions.transaction import transactional
from system.common import permission_required
from warehouse.common import warehouse_required, require_actor_user_id, require_warehouse_scope, add_warehouse_filter, require_positive_int, require_bulk_list
from warehouse.common.locks import lock_row, lock_inventory_pairs
from warehouse.common.stock_operations import reason, warehouse_access, event, history
from warehouse.common import clock
from warehouse.warehouse.models import Warehouse
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.location.models import Location
from warehouse.inventory.models import Inventory
from warehouse.inventory.services import InventoryService
from warehouse.stock.service import StockOp, Leg
from warehouse.stock.lots import validated_lot_id, assert_shippable
from .order_models import TransferOrder, TransferOrderLine
from .schemas import api_ns


def scoped_order(ident):
    order = get_object_or_404(TransferOrder, ident)
    scope = add_warehouse_filter({})
    allowed = [scope['warehouse_id']] if scope.get('warehouse_id') else scope.get('warehouse_ids')
    if allowed is not None and not ({order.source_warehouse_id, order.destination_warehouse_id} & set(allowed)):
        raise ForbiddenException('Transfer is outside warehouse scope', 12001)
    return order


def serialize(order):
    from warehouse.stock.models import GoodsLot
    def line(row):
        goods, lot = db.session.get(Goods,row.goods_id), db.session.get(GoodsLot,row.lot_id) if row.lot_id else None
        return dict(id=row.id,goods_id=row.goods_id,goods_code=goods.code,goods_name=goods.name,lot_id=row.lot_id,
            lot_no=lot.lot_no if lot else None,source_location_id=row.source_location_id,
            source_location_code=db.session.get(Location,row.source_location_id).code,quantity=row.quantity,cancelled=row.cancelled,
            shipped=row.shipped,received=row.received,lost=row.lost,outstanding=row.shipped-row.received-row.lost)
    return dict(id=order.id,source_warehouse_id=order.source_warehouse_id,destination_warehouse_id=order.destination_warehouse_id,
        source_warehouse=db.session.get(Warehouse,order.source_warehouse_id).name,
        destination_warehouse=db.session.get(Warehouse,order.destination_warehouse_id).name,status=order.status,
        reason=order.reason,created_at=order.created_at.isoformat(),
        first_shipped_at=order.first_shipped_at.isoformat() if order.first_shipped_at else None,
        transit_days=max(0,(clock.now()-order.first_shipped_at).days) if order.first_shipped_at else 0,
        lines=[line(r) for r in order.lines],events=history('transfer',order.id))


@transactional
def create(body, actor):
    if not isinstance(body, dict):raise BadRequestException('Expected an object')
    from uuid import uuid4
    body = {**body, 'request_id': body.get('request_id') or uuid4().hex}
    src = get_object_or_404(Warehouse, require_positive_int(body.get('source_warehouse_id'),'source_warehouse_id'))
    dst = get_object_or_404(Warehouse, require_positive_int(body.get('destination_warehouse_id'),'destination_warehouse_id'))
    require_warehouse_scope(src.id)
    warehouse_access(dst.id)
    if src.id == dst.id or src.company_id != dst.company_id or not src.is_active or not dst.is_active:
        raise BadRequestException('Choose two active warehouses in the same company')
    lock_row(src)
    created,replay=event('transfer_create',src.id,'create',body,actor)
    if replay:return get_object_or_404(TransferOrder,created.result_id)
    order=TransferOrder(source_warehouse_id=src.id,destination_warehouse_id=dst.id,reason=reason(body),created_by=actor)
    seen=set()
    for item in require_bulk_list(body.get('lines'),'lines'):
        goods=get_object_or_404(Goods,require_positive_int(item.get('goods_id'),'goods_id'))
        loc=get_object_or_404(Location,require_positive_int(item.get('source_location_id'),'source_location_id'))
        if goods.company_id!=src.company_id or loc.warehouse_id!=src.id or loc.location_type!='standard' or not loc.is_active:
            raise BadRequestException('Goods and standard source location must belong to the source warehouse')
        lot=validated_lot_id(goods.id,item.get('lot_id'))
        key=(goods.id,loc.id,lot)
        if key in seen: raise BadRequestException('Duplicate transfer stock line')
        seen.add(key)
        order.lines.append(TransferOrderLine(goods_id=goods.id,lot_id=lot,source_location_id=loc.id,
            quantity=require_positive_int(item.get('quantity'),'quantity')))
    db.session.add(order);db.session.flush()
    created.result_id=order.id
    event('transfer',order.id,'create',body,actor)
    return order


@transactional
def operate(ident, action, body, actor):
    order=lock_row(scoped_order(ident))
    require_warehouse_scope(order.destination_warehouse_id if action=='receive' else order.source_warehouse_id)
    receipt,replay=event('transfer',order.id,action,body,actor)
    if replay:return order
    why=reason(body)
    if order.status in ('completed','cancelled'):raise ConflictException('Transfer is already closed',16221)
    lines={r.id:r for r in order.lines}
    if action=='cancel':
        # Cancel only the unshipped balance; shipped stock must still be received or settled as lost.
        for row in lines.values():row.cancelled=row.quantity-row.shipped
        order.status='in_transit' if any(r.shipped>r.received+r.lost for r in lines.values()) else 'cancelled'
        return order
    parsed=[];seen=set()
    for item in require_bulk_list(body.get('lines'),'lines'):
        row=lines.get(item.get('id'))
        if row is None or row.id in seen:raise BadRequestException('Unknown or duplicate transfer line')
        seen.add(row.id);qty=require_positive_int(item.get('quantity'),'quantity')
        loc=None
        if action=='receive':
            loc=get_object_or_404(Location,require_positive_int(item.get('location_id'),'location_id'))
            if loc.warehouse_id!=order.destination_warehouse_id or not loc.is_active or loc.location_type not in ('standard','damaged','return'):
                raise BadRequestException('Choose an active destination location')
        parsed.append((row,qty,loc))
    pairs={(r.goods_id,w) for r,_,_ in parsed for w in (order.source_warehouse_id,order.destination_warehouse_id)}
    for gid,wid in sorted(pairs):InventoryService.ensure_inventory(gid,wid)
    lock_inventory_pairs(pairs)
    if action == 'receive':
        for row, _, loc in parsed:
            if loc.location_type == 'standard':
                assert_shippable(row.goods_id, row.lot_id, order.destination_warehouse_id)
    outgoing=StockOp(order.source_warehouse_id,'transfer_ship' if action=='ship' else 'transfer_receive' if action=='receive' else 'transfer_loss',
        doc_type='warehouse_transfer',doc_id=order.id,operator_id=actor,reason=why)
    incoming=StockOp(order.destination_warehouse_id,'transfer_receive',doc_type='warehouse_transfer',doc_id=order.id,operator_id=actor,reason=why)
    for row,qty,loc in parsed:
        if action=='ship':
            if qty>row.quantity-row.shipped-row.cancelled:raise BadRequestException('Shipment exceeds remaining plan')
            assert_shippable(row.goods_id,row.lot_id,order.source_warehouse_id)
            inv=db.session.get(Inventory,(row.goods_id,order.source_warehouse_id))
            # Group demand is checked again after each queued line.
            prior=sum(q for r,q,_ in parsed if r.goods_id==row.goods_id)
            if prior>inv.available_stock_for_sale:raise ConflictException('Stock is reserved for other orders',16222)
            source_location=db.session.get(Location,row.source_location_id)
            if not source_location.is_active or source_location.location_type!='standard':raise ConflictException('Source location is no longer pickable')
            from warehouse.picking.models import PickingTask,PickingTaskDetail
            reserved=(db.session.query(db.func.coalesce(db.func.sum(PickingTaskDetail.picked_quantity),0)).join(PickingTask)
                .filter(PickingTask.status=='in_progress',PickingTask.is_active.is_(True),PickingTaskDetail.goods_id==row.goods_id,
                    PickingTaskDetail.location_id==row.source_location_id,PickingTaskDetail.lot_id==row.lot_id).scalar())
            stock=GoodsLocation.query.filter_by(goods_id=row.goods_id,location_id=row.source_location_id,lot_id=row.lot_id).first()
            if not stock or qty>stock.quantity-reserved:raise ConflictException('Source stock is unavailable or being picked',16222)
            outgoing.move(row.goods_id,qty,src=Leg.at(row.source_location_id,row.lot_id),dst=Leg.in_transit(row.lot_id),doc_line_id=row.id)
            row.shipped+=qty
            order.first_shipped_at=order.first_shipped_at or clock.now()
        else:
            if qty>row.shipped-row.received-row.lost:raise BadRequestException('Quantity exceeds outstanding shipment')
            outgoing.move(row.goods_id,qty,src=Leg.in_transit(row.lot_id),doc_line_id=row.id)
            if action=='receive':
                incoming.move(row.goods_id,qty,dst=Leg.at(loc.id,row.lot_id),doc_line_id=row.id)
                row.received+=qty
            else:row.lost+=qty
    outgoing.commit()
    if action=='receive':incoming.commit()
    order.status='completed' if all(r.quantity==r.shipped+r.cancelled and r.shipped==r.received+r.lost for r in lines.values()) else 'in_transit'
    return order


@api_ns.route('/orders')
class Orders(Resource):
    @permission_required(['all_access','company_all_access','transfer_read'])
    @warehouse_required()
    def get(self):
        scope=add_warehouse_filter({});q=TransferOrder.query
        ids=[scope['warehouse_id']] if scope.get('warehouse_id') else scope.get('warehouse_ids')
        if ids is not None:q=q.filter(db.or_(TransferOrder.source_warehouse_id.in_(ids),TransferOrder.destination_warehouse_id.in_(ids)))
        from warehouse.common.stock_operations import page
        return page(q, TransferOrder, serialize)
    @permission_required(['all_access','company_all_access','transfer_edit'])
    @warehouse_required()
    def post(self):return serialize(create(request.get_json(),require_actor_user_id())),201


@api_ns.route('/orders/<int:ident>')
class Order(Resource):
    @permission_required(['all_access','company_all_access','transfer_read'])
    @warehouse_required()
    def get(self,ident):return serialize(scoped_order(ident))


@permission_required(['all_access','company_all_access','adjustment_approve'])
def require_loss_permission():pass


@api_ns.route('/orders/<int:ident>/<string:action>')
class OrderAction(Resource):
    @permission_required(['all_access','company_all_access','transfer_edit'])
    @warehouse_required()
    def post(self,ident,action):
        if action not in ('ship','receive','loss','cancel'):raise BadRequestException('Invalid transfer action')
        if action=='loss':require_loss_permission()
        return serialize(operate(ident,action,request.get_json(),require_actor_user_id()))
