"""Physical count scopes. Parent document -> inventory locks; movements never lock count parents."""
from extensions.db import db
from extensions.error import ConflictException, BadRequestException
from extensions.transaction import transactional
from warehouse.common import clock
from warehouse.common.locks import lock_row, lock_inventory_rows
from .models import CycleCountTask, CycleCountTaskDetail


def lock_scope(task, rows=None):
    from warehouse.inventory.services import InventoryService
    rows = list(task.task_details if rows is None else rows)
    for gid in sorted({r.goods_id for r in rows}):
        InventoryService.ensure_inventory(gid, task.warehouse_id)
    lock_inventory_rows([r.goods_id for r in rows], task.warehouse_id)
    return rows


def assert_free(warehouse_id, keys, exclude_task_id=None):
    for gid, lid, lot in sorted(set(keys), key=lambda k: (k[0], k[1], k[2] or 0)):
        hit = (db.session.query(CycleCountTask.id).join(CycleCountTaskDetail)
            .filter(CycleCountTask.warehouse_id == warehouse_id, CycleCountTask.control_state == 'active',
                    CycleCountTask.id != exclude_task_id if exclude_task_id else db.true(),
                    CycleCountTaskDetail.goods_id == gid, CycleCountTaskDetail.location_id == lid,
                    CycleCountTaskDetail.lot_id == lot).first())
        if hit:
            raise ConflictException('Stock is frozen for counting; settle or cancel the count first',
                16200, details={'cyclecount_id': hit[0], 'goods_id': gid, 'location_id': lid, 'lot_id': lot})


def activate(task, rows=None):
    if not task.is_active or task.control_state not in ('draft', 'active'):
        raise ConflictException('Count is resolved or predates stock protection; create a linked recount', 16201)
    rows = lock_scope(task, rows)
    assert_free(task.warehouse_id, [(r.goods_id, r.location_id, r.lot_id) for r in rows], task.id)
    from warehouse.picking.models import PickingTask, PickingTaskDetail
    for row in rows:
        pending_pick = (db.session.query(PickingTaskDetail.id).join(PickingTask)
            .filter(PickingTask.status == 'in_progress', PickingTask.is_active.is_(True),
                PickingTaskDetail.goods_id == row.goods_id, PickingTaskDetail.location_id == row.location_id,
                PickingTaskDetail.lot_id == row.lot_id, PickingTaskDetail.picked_quantity > 0).first())
        if pending_pick:
            raise ConflictException('Finish or cancel active picking before counting this stock', 16202)
    task.control_state = 'active'
    db.session.flush()


def guard_movement(op, keys):
    exempt = None
    if op.doc_type == 'adjustment':
        from warehouse.adjustment.models import Adjustment
        adjustment = db.session.get(Adjustment, op.doc_id)
        exempt = adjustment.cyclecount_id if adjustment else None
    assert_free(op.warehouse_id, keys, exempt)


def require_editable(task):
    if not task.is_active or task.control_state in ('settled', 'cancelled', 'superseded', 'legacy'):
        raise ConflictException('Count is no longer editable; create a linked recount', 16201)


def parent_for_adjustment(adjustment):
    if adjustment.cyclecount_id:
        task = lock_row(db.session.get(CycleCountTask, adjustment.cyclecount_id))
        if task.control_state != 'active':
            raise ConflictException('Count scope is no longer active; recount before adjusting', 16201)
        return task


@transactional
def resolve(task_id, actor, body, recount=False):
    from warehouse.adjustment.models import Adjustment
    from .services import CycleCountTaskService
    task = lock_row(CycleCountTaskService.get_task(task_id))
    reason = body.get('reason') if isinstance(body, dict) else None
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise BadRequestException('A reason of 1-255 characters is required', field='reason')
    if task.control_state in ('cancelled', 'superseded'):
        raise ConflictException('Count already resolved', 16201)
    adjustments = Adjustment.query.filter_by(cyclecount_id=task.id, is_active=True).order_by(Adjustment.id).all()
    for adjustment in adjustments:
        lock_row(adjustment)
        if adjustment.status == 'completed' and not recount:
            raise ConflictException('Posted count cannot be cancelled; create a new count', 16201)
        if adjustment.status != 'completed':
            adjustment.is_active = False
    lock_scope(task)
    if task.control_state != 'settled':
        task.control_state = 'superseded' if recount else 'cancelled'
        task.is_active = False
    task.resolution_reason = reason.strip()
    task.resolved_by = actor
    task.resolved_at = clock.now()
    db.session.flush()
    if not recount:
        return task
    child = CycleCountTaskService.create_task(dict(warehouse_id=task.warehouse_id,
        task_name=f'Recount CC-{task.id}', task_details=[dict(goods_id=r.goods_id,
            location_id=r.location_id, lot_id=r.lot_id) for r in task.task_details]), actor)
    child.recount_of_id = task.id
    db.session.flush()
    # Transfer the freeze without a gap. Recounts of settled counts start a new scope.
    activate(child)
    CycleCountTaskService._update_task_status(child, 'in_progress', actor)
    return child
