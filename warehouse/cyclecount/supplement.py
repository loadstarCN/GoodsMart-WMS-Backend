"""Record unexpected physical stock; only an approved adjustment changes balances."""
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from extensions.transaction import transactional
from warehouse.common import require_fields, require_non_negative_int
from warehouse.common.locks import lock_row
from warehouse.goods.models import Goods
from warehouse.location.models import Location
from warehouse.stock.models import GoodsLot
from .models import CycleCountTask, CycleCountTaskDetail


@transactional
def add_supplement(task_id, body, actor):
    from warehouse.goods.lots import normalize, parse_date
    from .services import CycleCountTaskService
    task = lock_row(db.session.get(CycleCountTask, task_id))
    from . import control
    control.require_editable(task)
    if task.status != 'in_progress':
        raise ConflictException('Count must be in progress')
    require_fields(body, 'goods_code', 'location_code')
    if 'actual_quantity' in body:
        require_non_negative_int(body['actual_quantity'], 'actual_quantity')
        raise ConflictException('Add and freeze the supplemental scope before recording physical quantities', 16201)
    if not isinstance(body['goods_code'], str) or not isinstance(body['location_code'], str):
        raise BadRequestException('Invalid goods or location code')
    goods = Goods.query.filter_by(company_id=task.warehouse.company_id, code=body['goods_code'].strip()).first()
    location = Location.query.filter_by(warehouse_id=task.warehouse_id, code=body['location_code'].strip()).first()
    if not goods or not location:
        raise NotFoundException('Goods or location not found in count scope')
    goods = lock_row(goods)
    lot = None
    if body.get('lot_no'):
        lot_no = normalize(body['lot_no'])
        expiry = parse_date(body, 'expiry_date')
        lot = GoodsLot.query.filter_by(goods_id=goods.id, lot_no=lot_no).first()
        if lot and expiry and lot.expiry_date != expiry:
            raise ConflictException('Expiry differs from the existing lot', field='expiry_date')
        if not lot:
            lot = GoodsLot(goods_id=goods.id, lot_no=lot_no, expiry_date=expiry,
                           source='cyclecount', created_by=actor)
            db.session.add(lot)
            db.session.flush()
    elif body.get('expiry_date'):
        raise BadRequestException('Expiry requires a lot number', field='lot_no')
    lot_id = lot.id if lot else None
    if CycleCountTaskDetail.query.filter_by(task_id=task.id, goods_id=goods.id,
                                           location_id=location.id, lot_id=lot_id).first():
        raise ConflictException('This goods, location and lot already exists in the count')
    row = CycleCountTaskDetail(task_id=task.id, goods_id=goods.id, location_id=location.id,
                               lot_id=lot_id, is_supplemental=True, actual_quantity=0)
    db.session.add(row)
    db.session.flush()
    control.activate(task, list(task.task_details) + [row])
    from warehouse.goods.services import GoodsLocationService
    row.system_quantity = GoodsLocationService.get_quantity(row.goods_id, row.location_id, row.lot_id)
    return row


@transactional
def remove_supplement(task_id, detail_id):
    from .services import CycleCountTaskService
    task = lock_row(db.session.get(CycleCountTask, task_id))
    from . import control
    control.require_editable(task)
    row = CycleCountTaskService.get_task_detail(task_id, detail_id)
    if task.status != 'in_progress' or not row.is_supplemental or row.status == 'completed':
        raise ConflictException('Only unfinished supplements can be removed during counting')
    control.lock_scope(task)
    db.session.delete(row)
