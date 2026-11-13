from flask import request
from flask_restx import Resource
from extensions.db import db, get_object_or_404
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from extensions.transaction import transactional
from system.common import permission_required
from warehouse.common import warehouse_required, require_actor_user_id, get_warehouse_owned, add_warehouse_filter, require_positive_int, require_bulk_list, require_warehouse_scope
from warehouse.common.locks import lock_row, lock_inventory_rows
from warehouse.common.stock_operations import reason, event, history, warehouse_access
from warehouse.common import clock
from warehouse.warehouse.models import Warehouse
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.location.models import Location
from warehouse.recipient.models import Recipient
from warehouse.dn.models import DN, DNDetail
from warehouse.stock.models import DNStockAllocation, GoodsLot
from warehouse.stock.lots import validated_lot_id, assert_shippable
from warehouse.stock.service import StockOp, Leg
from warehouse.inventory.services import InventoryService
from .return_models import CustomerReturn, CustomerReturnLine, ReturnHolding, ReturnDisposition
from .schemas import api_ns


def held(goods_id, warehouse_id, location_id=None, lot_id=None, exact_lot=False):
    q=(db.session.query(db.func.coalesce(db.func.sum(ReturnHolding.quantity),0)).join(CustomerReturnLine)
        .join(CustomerReturn).filter(CustomerReturn.warehouse_id==warehouse_id,CustomerReturnLine.goods_id==goods_id))
    if location_id is not None:q=q.filter(ReturnHolding.location_id==location_id)
    if exact_lot:q=q.filter(CustomerReturnLine.lot_id==lot_id)
    return int(q.scalar() or 0)


def guard_quarantine(op, moves):
    from .return_outbound import reserved
    exempt = None
    if op.doc_type == 'picking_task':
        from warehouse.picking.models import PickingTask
        task = db.session.get(PickingTask, op.doc_id)
        exempt = task.dn_id if task else None
    quantities={}
    for move in moves:
        if move.src is not None and move.src.kind==Leg.LOCATION:
            key=(move.goods_id,move.src.location_id,move.src.lot_id)
            quantities[key]=quantities.get(key,0)+move.quantity
    for (gid,lid,lot),qty in quantities.items():
        protected=held(gid,op.warehouse_id,lid,lot,True) + reserved(gid,op.warehouse_id,lid,lot,exempt)
        if not protected:continue
        stock=GoodsLocation.query.filter_by(goods_id=gid,location_id=lid,lot_id=lot).first()
        if not stock or qty>stock.quantity-protected:
            raise ConflictException('Customer returns are quarantined until disposition approval',16230,
                details={'goods_id':gid,'location_id':lid,'lot_id':lot,'quarantined':protected})


def serialize(doc):
    def line(row):
        goods=db.session.get(Goods,row.goods_id);lot=db.session.get(GoodsLot,row.lot_id) if row.lot_id else None
        return dict(id=row.id,goods_id=row.goods_id,goods_code=goods.code,goods_name=goods.name,lot_id=row.lot_id,
            lot_no=lot.lot_no if lot else None,quantity=row.quantity,received=row.received,disposed=row.disposed,
            holdings=[dict(location_id=h.location_id,location_code=db.session.get(Location,h.location_id).code,quantity=h.quantity)
                for h in ReturnHolding.query.filter_by(line_id=row.id).filter(ReturnHolding.quantity>0).all()])
    return dict(id=doc.id,warehouse_id=doc.warehouse_id,original_dn_id=doc.original_dn_id,recipient_id=doc.recipient_id,
        reason=doc.reason,exception_reason=doc.exception_reason,status=doc.status,created_at=doc.created_at.isoformat(),
        lines=[line(r) for r in doc.lines],events=history('return',doc.id),
        dispositions=[dict(id=d.id,line_id=d.line_id,location_id=d.location_id,target_location_id=d.target_location_id,
            action=d.action,quantity=d.quantity,restored_quantity=d.restored_quantity,reason=d.reason,status=d.status,created_by=d.created_by,
            reviewed_by=d.reviewed_by,review_reason=d.review_reason,linked_dn_id=d.linked_dn_id)
            for d in ReturnDisposition.query.filter_by(return_id=doc.id).order_by(ReturnDisposition.id).all()])


@permission_required(['all_access','company_all_access','inventory_edit'])
def require_exception_permission():pass


@transactional
def create(body,actor):
    if not isinstance(body,dict):raise BadRequestException('Expected an object')
    warehouse=get_object_or_404(Warehouse,require_positive_int(body.get('warehouse_id'),'warehouse_id'))
    require_warehouse_scope(warehouse.id)
    if not warehouse.is_active:raise BadRequestException('Warehouse is inactive')
    from uuid import uuid4
    body={**body,'request_id':body.get('request_id') or uuid4().hex}
    lock_row(warehouse)
    created,replay=event('return_create',warehouse.id,'create',body,actor)
    if replay:return get_object_or_404(CustomerReturn,created.result_id)
    original=None;exception=None
    if body.get('original_dn_id'):
        original=lock_row(get_object_or_404(DN,require_positive_int(body['original_dn_id'],'original_dn_id')))
        warehouse_access(original.warehouse_id)
        if original.warehouse.company_id!=warehouse.company_id or original.dn_type!='shipping' or original.status not in ('delivered','completed'):
            raise BadRequestException('Original sales dispatch must have shipped in this company')
        recipient_id=original.recipient_id
    else:
        require_exception_permission()
        exception=reason({'reason':body.get('exception_reason')})
        recipient_id=require_positive_int(body.get('recipient_id'),'recipient_id')
    recipient=get_object_or_404(Recipient,recipient_id)
    if recipient.company_id!=warehouse.company_id:raise BadRequestException('Customer belongs to another company')
    doc=CustomerReturn(warehouse_id=warehouse.id,original_dn_id=original.id if original else None,recipient_id=recipient_id,
        reason=reason(body),exception_reason=exception,created_by=actor)
    seen=set()
    for item in require_bulk_list(body.get('lines'),'lines'):
        goods=get_object_or_404(Goods,require_positive_int(item.get('goods_id'),'goods_id'))
        if goods.company_id!=warehouse.company_id:raise BadRequestException('Goods belongs to another company')
        lot=validated_lot_id(goods.id,item.get('lot_id'));qty=require_positive_int(item.get('quantity'),'quantity')
        if (goods.id,lot) in seen:raise BadRequestException('Duplicate goods and lot')
        seen.add((goods.id,lot))
        if original:
            allocations=DNStockAllocation.query.filter_by(dn_id=original.id,goods_id=goods.id).all()
            if allocations:
                shipped=sum(a.packed_quantity for a in allocations if a.lot_id==lot)
            elif lot is None:
                shipped=sum(d.delivered_quantity or 0 for d in original.details if d.goods_id==goods.id)
            else:shipped=0
            reserved=(db.session.query(db.func.coalesce(db.func.sum(CustomerReturnLine.quantity),0)).join(CustomerReturn)
                .filter(CustomerReturn.original_dn_id==original.id,CustomerReturnLine.goods_id==goods.id,CustomerReturnLine.lot_id==lot).scalar())
            if qty>shipped-reserved:raise ConflictException('Return exceeds shipped quantity less previously authorized returns',16231,
                details={'goods_id':goods.id,'lot_id':lot,'remaining':max(0,shipped-reserved)})
        doc.lines.append(CustomerReturnLine(goods_id=goods.id,lot_id=lot,quantity=qty))
    db.session.add(doc);db.session.flush()
    created.result_id=doc.id
    event('return',doc.id,'create',body,actor)
    return doc


def update_status(doc):
    if all(r.quantity==r.received==r.disposed for r in doc.lines):
        doc.status='completed' if any(r.received for r in doc.lines) else 'cancelled'
    else:doc.status='inspecting' if any(r.received>r.disposed for r in doc.lines) else 'authorized'


@transactional
def receive(ident,body,actor,cancel=False):
    doc=get_warehouse_owned(CustomerReturn,ident)
    # Same lock order as authorization: original DN -> return -> inventory.
    if doc.original_dn_id:lock_row(db.session.get(DN,doc.original_dn_id))
    lock_row(doc)
    _,replay=event('return',doc.id,'cancel' if cancel else 'receive',body,actor)
    if replay:return doc
    why=reason(body)
    if doc.status in ('completed','cancelled'):raise ConflictException('Return is already closed',16232)
    if cancel:
        for row in doc.lines:row.quantity=row.received
        update_status(doc);return doc
    rows={r.id:r for r in doc.lines};parsed=[];seen=set()
    for item in require_bulk_list(body.get('lines'),'lines'):
        row=rows.get(item.get('id'))
        if row is None or row.id in seen:raise BadRequestException('Unknown or duplicate return line')
        seen.add(row.id)
        qty=require_positive_int(item.get('quantity'),'quantity')
        if qty>row.quantity-row.received:raise BadRequestException('Receipt exceeds authorized return balance')
        loc=get_object_or_404(Location,require_positive_int(item.get('location_id'),'location_id'))
        if loc.warehouse_id!=doc.warehouse_id or loc.location_type!='return' or not loc.is_active:
            raise BadRequestException('Receive into an active return quarantine location')
        parsed.append((row,qty,loc))
    for gid in sorted({r.goods_id for r,_,_ in parsed}):InventoryService.ensure_inventory(gid,doc.warehouse_id)
    lock_inventory_rows([r.goods_id for r,_,_ in parsed],doc.warehouse_id)
    op=StockOp(doc.warehouse_id,'customer_return_receive',doc_type='customer_return',doc_id=doc.id,operator_id=actor,reason=why)
    for row,qty,loc in parsed:
        op.move(row.goods_id,qty,dst=Leg.at(loc.id,row.lot_id),doc_line_id=row.id)
        holding=ReturnHolding.query.filter_by(line_id=row.id,location_id=loc.id).first()
        if not holding:holding=ReturnHolding(line_id=row.id,location_id=loc.id,quantity=0);db.session.add(holding)
        holding.quantity+=qty;row.received+=qty
    op.commit();update_status(doc);return doc


@transactional
def propose(ident,body,actor):
    doc=lock_row(get_warehouse_owned(CustomerReturn,ident))
    _,replay=event('return',doc.id,'propose',body,actor)
    if replay:return doc
    why=reason(body);action=body.get('action')
    if action not in ('restock','damaged','repair','scrap','supplier_return'):raise BadRequestException('Invalid disposition')
    row=next((r for r in doc.lines if r.id==body.get('line_id')),None)
    if row is None:raise BadRequestException('Unknown return line')
    qty=require_positive_int(body.get('quantity'),'quantity');lid=require_positive_int(body.get('location_id'),'location_id')
    holding=ReturnHolding.query.filter_by(line_id=row.id,location_id=lid).first()
    pending=db.session.query(db.func.coalesce(db.func.sum(ReturnDisposition.quantity),0)).filter_by(line_id=row.id,location_id=lid,status='pending').scalar()
    if not holding or qty>holding.quantity-pending:raise ConflictException('Quantity is unavailable or already awaiting disposition',16233)
    target=None
    if action in ('restock','damaged','repair'):
        target=get_object_or_404(Location,require_positive_int(body.get('target_location_id'),'target_location_id'))
        kind={'restock':'standard','damaged':'damaged','repair':'return'}[action]
        if target.id==lid or target.warehouse_id!=doc.warehouse_id or not target.is_active or target.location_type!=kind:
            raise BadRequestException('Disposition target has the wrong warehouse or stock type')
    db.session.add(ReturnDisposition(return_id=doc.id,line_id=row.id,location_id=lid,target_location_id=target.id if target else None,
        action=action,quantity=qty,reason=why,payload=body,created_by=actor))
    return doc


@transactional
def review(ident,decision_id,body,actor):
    doc=lock_row(get_warehouse_owned(CustomerReturn,ident))
    decision=lock_row(get_object_or_404(ReturnDisposition,decision_id))
    if decision.return_id!=doc.id:raise BadRequestException('Disposition belongs to another return')
    _,replay=event('return',doc.id,f'review-{decision_id}',body,actor)
    if replay:return doc
    why=reason(body)
    if type(body.get('approve')) is not bool:raise BadRequestException('Explicit approve boolean is required')
    if decision.status!='pending':raise ConflictException('Disposition already reviewed',16234)
    if actor==decision.created_by:raise ForbiddenException('Another user must review the disposition',16235)
    decision.reviewed_by=actor;decision.reviewed_at=clock.now();decision.review_reason=why
    if not body['approve']:decision.status='rejected';return doc
    row=db.session.get(CustomerReturnLine,decision.line_id)
    lock_inventory_rows([row.goods_id],doc.warehouse_id)
    holding=ReturnHolding.query.filter_by(line_id=row.id,location_id=decision.location_id).one()
    if holding.quantity<decision.quantity:raise ConflictException('Quarantine balance changed',16233)
    if decision.action=='restock':assert_shippable(row.goods_id,row.lot_id,doc.warehouse_id)
    holding.quantity-=decision.quantity
    op=StockOp(doc.warehouse_id,'customer_return_dispose',doc_type='customer_return',doc_id=doc.id,operator_id=actor,reason=why)
    if decision.action=='supplier_return':
        from warehouse.dn.services import DNService
        recipient=get_object_or_404(Recipient,require_positive_int(decision.payload.get('supplier_recipient_id'),'supplier_recipient_id'))
        dn=DNService.create_dn(dict(warehouse_id=doc.warehouse_id,dn_type='return_to_supplier',stock_source='return',
            supplier_id=decision.payload.get('supplier_id'),purpose_reason=decision.reason,recipient_id=recipient.id,
            shipping_address=recipient.address,expected_shipping_date=clock.local_today(doc.warehouse_id).isoformat(),
            order_number=f'RMA-{doc.id}-{decision.id}',details=[dict(goods_id=row.goods_id,quantity=decision.quantity)]),actor)
        decision.linked_dn_id=dn.id
    else:
        dst=None
        if decision.target_location_id:
            target=get_object_or_404(Location,decision.target_location_id)
            kind={'restock':'standard','damaged':'damaged','repair':'return'}[decision.action]
            if not target.is_active or target.warehouse_id!=doc.warehouse_id or target.location_type!=kind:
                raise ConflictException('Disposition target changed; reject and submit again')
            dst=Leg.at(target.id,row.lot_id)
        op.move(row.goods_id,decision.quantity,src=Leg.at(decision.location_id,row.lot_id),dst=dst,doc_line_id=row.id)
        if decision.action=='repair':
            other=ReturnHolding.query.filter_by(line_id=row.id,location_id=decision.target_location_id).first()
            if not other:other=ReturnHolding(line_id=row.id,location_id=decision.target_location_id,quantity=0);db.session.add(other)
            other.quantity+=decision.quantity
    # Flush released holds before the stock guard; all other returns stay protected.
    db.session.flush();op.commit()
    if decision.action!='repair':row.disposed+=decision.quantity
    decision.status='approved';update_status(doc);return doc


@api_ns.route('/returns')
class Returns(Resource):
    @permission_required(['all_access','company_all_access','asn_read'])
    @warehouse_required()
    def get(self):
        q=CustomerReturn.query;scope=add_warehouse_filter({})
        if scope.get('warehouse_id'):q=q.filter_by(warehouse_id=scope['warehouse_id'])
        elif scope.get('warehouse_ids'):q=q.filter(CustomerReturn.warehouse_id.in_(scope['warehouse_ids']))
        from warehouse.common.stock_operations import page
        return page(q, CustomerReturn, serialize)
    @permission_required(['all_access','company_all_access','asn_edit'])
    @warehouse_required()
    def post(self):return serialize(create(request.get_json(),require_actor_user_id())),201


@api_ns.route('/returns/<int:ident>')
class ReturnItem(Resource):
    @permission_required(['all_access','company_all_access','asn_read'])
    @warehouse_required()
    def get(self,ident):return serialize(get_warehouse_owned(CustomerReturn,ident))


@api_ns.route('/returns/<int:ident>/<string:action>')
class ReturnAction(Resource):
    @permission_required(['all_access','company_all_access','asn_edit'])
    @warehouse_required()
    def post(self,ident,action):
        if action not in ('receive','cancel','propose'):raise BadRequestException('Invalid return action')
        if action=='propose':
            require_exception_permission()
            result=propose(ident,request.get_json(),require_actor_user_id())
        else:result=receive(ident,request.get_json(),require_actor_user_id(),action=='cancel')
        return serialize(result)


@api_ns.route('/returns/<int:ident>/dispositions/<int:decision_id>/review')
class ReturnReview(Resource):
    @permission_required(['all_access','company_all_access','adjustment_approve'])
    @warehouse_required()
    def post(self,ident,decision_id):return serialize(review(ident,decision_id,request.get_json(),require_actor_user_id()))
