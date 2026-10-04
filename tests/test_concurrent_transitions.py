"""B-50：单据 / 任务状态转换先锁单据行（先父单据、后任务），拿到锁后重读状态

PG 读已提交下，第二个并发请求会在行锁上等第一个请求提交后继续执行。修复前它按内存里的旧状态
（in_progress / pending）往下走：拣货完成两次同库位扣两遍、收货两次 received_stock 翻倍、
分拣完成两次多出幻影 sorted_stock……修复后拿到锁 refresh，被各模块现有的状态检查拦下。

SQLite 会忽略 FOR UPDATE，这里用两个 app context（= 两个数据库会话）手工模拟这种交错：
请求 2 先读出单据 / 任务，请求 1 完整执行并提交，请求 2 再拿着内存里的旧对象继续。
"""
from datetime import date

from sqlalchemy import event, func
from sqlalchemy.dialects import postgresql

from tests.helpers import *  # noqa: F401,F403
from extensions.error import APIException
from warehouse.asn.services import ASNService
from warehouse.adjustment.services import AdjustmentService
from warehouse.common.locks import lock_row
from warehouse.cyclecount.models import CycleCountTaskStatusLog
from warehouse.cyclecount.services import CycleCountTaskService
from warehouse.delivery.services import DeliveryTaskService
from warehouse.dn.services import DNService
from warehouse.packing.services import PackingTaskService
from warehouse.picking.services import PickingTaskService
from warehouse.sorting.services import SortingTaskService
import warehouse.picking.services as picking_services
import warehouse.sorting.services as sorting_services


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _ok(response):
    assert response.status_code in (200, 201), (response.status_code, response.get_json())
    return response.get_json()


def _world(client):
    """Warehouse A 下新建一个商品和一个标准库位，返回各 id。"""
    with client.application.app_context():
        admin = get_admin_user()
        warehouse = Warehouse.query.filter_by(name='Warehouse A').first()
        goods = Goods(code='B50-1', company_id=warehouse.company_id, name='b50', unit='pcs',
                      is_active=True, created_by=admin.id)
        location = Location(warehouse_id=warehouse.id, code='B50-STD', location_type='standard',
                            created_by=admin.id)
        db.session.add_all([goods, location])
        db.session.commit()
        return {
            'wh': warehouse.id, 'goods': goods.id, 'std': location.id, 'admin': admin.id,
            'supplier': Supplier.query.filter_by(company_id=warehouse.company_id).first().id,
            'recipient': Recipient.query.filter_by(company_id=warehouse.company_id).first().id,
        }


def _interleave(client, load, first, second):
    """请求 2 先在会话 2 里 load() 读出单据 / 任务；请求 1 在会话 1 里 first() 完整执行并提交；
    请求 2 再 second(旧对象) 继续。返回请求 2 抛出的业务异常（没抛返回 None）。"""
    app = client.application
    ctx2 = app.app_context()
    ctx2.push()
    try:
        stale = load()
        ctx1 = app.app_context()
        ctx1.push()
        try:
            first()
        finally:
            ctx1.pop()
        try:
            second(stale)
        except APIException as exc:
            return exc
        return None
    finally:
        ctx2.pop()


def _assert_rejected(exc, biz_code, status_code=400):
    assert exc is not None, 'second concurrent request was not rejected'
    assert (exc.status_code, exc.biz_code) == (status_code, biz_code), str(exc)


def _inv(client, w):
    with client.application.app_context():
        inventory = db.session.get(Inventory, (w['goods'], w['wh']))
        return {k: getattr(inventory, k) for k in (
            'onhand_stock', 'asn_stock', 'received_stock', 'sorted_stock', 'dn_stock',
            'picked_stock', 'packed_stock', 'delivered_stock')}


def _bin_quantity(client, w):
    with client.application.app_context():
        record = GoodsLocation.query.filter_by(goods_id=w['goods'], location_id=w['std']).first()
        return record.quantity if record else 0


def _task_ids(client, model, **filters):
    with client.application.app_context():
        return [t.id for t in model.query.filter_by(**filters).order_by(model.id).all()]


def _create_asn(client, token, w, quantity):
    return _ok(client.post('/asn/', json={
        'warehouse_id': w['wh'], 'supplier_id': w['supplier'],
        'details': [{'goods_id': w['goods'], 'quantity': quantity}],
    }, headers=_h(token)))['id']


def _received_asn_in_sorting(client, token, w, quantity):
    """收货并开始分拣，返回 (asn_id, sorting_task_id)。"""
    asn_id = _create_asn(client, token, w, quantity)
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(token)))
    return asn_id, task_id


def _inbound(client, token, w, quantity):
    """入库全流程：收货 → 分拣 → 上架到标准库位。"""
    _, task_id = _received_asn_in_sorting(client, token, w, quantity)
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': w['goods'], 'sorted_quantity': quantity, 'damage_quantity': 0}]}, headers=_h(token)))
    _ok(client.put(f'/sorting/{task_id}/complete/', headers=_h(token)))
    _ok(client.post('/putaway/', json={'goods_id': w['goods'], 'location_id': w['std'], 'quantity': quantity},
                    headers=_h(token)))


def _create_dn(client, token, w, quantity):
    return _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'B50',
        'expected_shipping_date': date.today().isoformat(),
        'details': [{'goods_id': w['goods'], 'quantity': quantity}],
    }, headers=_h(token)))['id']


def _dn_in_picking(client, token, w, quantity):
    """DN 开始作业、拣货任务开始，返回 (dn_id, picking_task_id)。"""
    dn_id = _create_dn(client, token, w, quantity)
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(token)))
    task_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{task_id}/process/', headers=_h(token)))
    return dn_id, task_id


def _pick(client, token, w, task_id, quantity):
    _ok(client.post(f'/picking/{task_id}/batches/', json={'details': [
        {'goods_id': w['goods'], 'location_id': w['std'], 'picked_quantity': quantity}]}, headers=_h(token)))


def _dn_in_packing(client, token, w, quantity):
    """拣货完成、打包任务开始并已打包，返回 (dn_id, packing_task_id)。"""
    dn_id, picking_id = _dn_in_picking(client, token, w, quantity)
    _pick(client, token, w, picking_id, quantity)
    _ok(client.put(f'/picking/{picking_id}/complete/', headers=_h(token)))
    task_id = _task_ids(client, PackingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/packing/{task_id}/process/', headers=_h(token)))
    _ok(client.post(f'/packing/{task_id}/batches/', json={'details': [
        {'goods_id': w['goods'], 'packed_quantity': quantity}]}, headers=_h(token)))
    return dn_id, task_id


# ---------------------------------------------------------------------------
# 出库线
# ---------------------------------------------------------------------------

def test_picking_complete_twice_deducts_bin_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    _create_dn(client, access_token, w, 8)                    # 另一张 DN 的预占
    dn_id, task_id = _dn_in_picking(client, access_token, w, 5)
    _pick(client, access_token, w, task_id, 5)

    def load():
        task = PickingTaskService.get_task(task_id)
        _ = (task.status, task.dn.status, [d.quantity for d in task.dn.details], list(task.task_details))
        return task

    exc = _interleave(client, load,
                      lambda: PickingTaskService.complete_task(task_id, w['admin']),
                      lambda task: PickingTaskService.complete_task(task, w['admin']))
    _assert_rejected(exc, 16008)
    assert _bin_quantity(client, w) == 15                      # 实拣 5，只扣一次
    inventory = _inv(client, w)
    assert inventory['picked_stock'] == 5
    assert inventory['dn_stock'] == 8                          # 另一张 DN 的预占没被吃掉
    assert len(_task_ids(client, PackingTask, dn_id=dn_id)) == 1
    with client.application.app_context():
        assert RemovalRecord.query.filter_by(goods_id=w['goods'], reason='picking').count() == 1


def test_picking_complete_locks_dn_before_task(client, access_token, monkeypatch):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id, task_id = _dn_in_picking(client, access_token, w, 5)
    _pick(client, access_token, w, task_id, 5)
    calls = []

    def spy(obj):
        calls.append((type(obj).__name__, obj.id))
        return lock_row(obj)

    monkeypatch.setattr(picking_services, 'lock_row', spy)
    _ok(client.put(f'/picking/{task_id}/complete/', headers=_h(access_token)))
    assert calls[:2] == [('DN', dn_id), ('PickingTask', task_id)]


def test_picking_batch_append_concurrent_cannot_exceed_plan(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    _, task_id = _dn_in_picking(client, access_token, w, 5)
    body = {'details': [{'goods_id': w['goods'], 'location_id': w['std'], 'picked_quantity': 5}]}

    def load():
        task = PickingTaskService.get_task(task_id)
        _ = (task.status, list(task.task_details), [d.quantity for d in task.dn.details])
        return task

    exc = _interleave(client, load,
                      lambda: PickingTaskService.create_batch(task_id, dict(body), w['admin']),
                      lambda task: PickingTaskService.create_batch(task, dict(body), w['admin']))
    _assert_rejected(exc, 16029)
    with client.application.app_context():
        assert PickingBatch.query.filter_by(picking_task_id=task_id).count() == 1
        picked = db.session.query(func.sum(PickingTaskDetail.picked_quantity)) \
            .filter(PickingTaskDetail.picking_task_id == task_id).scalar()
        assert picked == 5


def test_packing_complete_twice_records_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id, task_id = _dn_in_packing(client, access_token, w, 5)

    def load():
        task = PackingTaskService.get_task(task_id)
        _ = (task.status, task.dn.status, list(task.task_details))
        return task

    exc = _interleave(client, load,
                      lambda: PackingTaskService.complete_task(task_id, w['admin']),
                      lambda task: PackingTaskService.complete_task(task, w['admin']))
    _assert_rejected(exc, 16008)
    inventory = _inv(client, w)
    assert (inventory['picked_stock'], inventory['packed_stock']) == (0, 5)
    assert len(_task_ids(client, DeliveryTask, dn_id=dn_id)) == 1


def test_delivery_sign_twice_completes_dn_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id, packing_id = _dn_in_packing(client, access_token, w, 5)
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))
    delivery_id = _task_ids(client, DeliveryTask, dn_id=dn_id)[0]
    _ok(client.put(f'/delivery/{delivery_id}/process/', headers=_h(access_token)))
    _ok(client.put(f'/delivery/{delivery_id}/complete/', json={}, headers=_h(access_token)))

    def load():
        task = DeliveryTaskService.get_task(delivery_id)
        _ = (task.status, task.dn.status, [d.quantity for d in task.dn.details])
        return task

    exc = _interleave(client, load,
                      lambda: DeliveryTaskService.sign_task(delivery_id, {}, w['admin']),
                      lambda task: DeliveryTaskService.sign_task(task, {}, w['admin']))
    _assert_rejected(exc, 16024)
    assert _inv(client, w)['delivered_stock'] == 0
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'completed'


def test_dn_progress_twice_creates_one_picking_task(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id = _create_dn(client, access_token, w, 5)

    def load():
        dn = DNService.get_dn(dn_id)
        _ = (dn.status, [d.quantity for d in dn.details])
        return dn

    exc = _interleave(client, load,
                      lambda: DNService.progress_dn(dn_id),
                      lambda dn: DNService.progress_dn(dn))
    _assert_rejected(exc, 16000)
    assert len(_task_ids(client, PickingTask, dn_id=dn_id)) == 1


def test_dn_cancel_twice_conflicts(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id = _create_dn(client, access_token, w, 5)
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(access_token)))

    exc = _interleave(client, lambda: DNService.get_dn(dn_id),
                      lambda: DNService.cancel_dn(dn_id),
                      lambda dn: DNService.cancel_dn(dn))
    _assert_rejected(exc, 16052, 409)
    assert _inv(client, w)['dn_stock'] == 0


def test_dn_cancel_after_concurrent_progress_stops_picking(client, access_token):
    """取消时 DN 已被并发 progress：按 in_progress 取消（停用拣货任务），不能按旧的 pending 直接关单。"""
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id = _create_dn(client, access_token, w, 5)

    exc = _interleave(client, lambda: DNService.get_dn(dn_id),
                      lambda: DNService.progress_dn(dn_id),
                      lambda dn: DNService.cancel_dn(dn))
    assert exc is None, str(exc)
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'closed'
        tasks = PickingTask.query.filter_by(dn_id=dn_id).all()
        assert tasks and all(not t.is_active for t in tasks)
    assert _inv(client, w)['dn_stock'] == 0


def test_lock_row_on_concurrently_deleted_row_is_404(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    dn_id = _create_dn(client, access_token, w, 5)

    exc = _interleave(client, lambda: DNService.get_dn(dn_id),
                      lambda: DNService.delete_dn(dn_id),
                      lambda dn: DNService.progress_dn(dn))
    _assert_rejected(exc, 13001, 404)
    assert _task_ids(client, PickingTask, dn_id=dn_id) == []


def test_lock_row_locks_primary_key_only(client, access_token):
    """PG 上不能对带 joined 关系的模型做 FOR UPDATE（outer join → FeatureNotSupported）：
    加锁语句只查主键、不带 JOIN。"""
    with client.application.app_context():
        dn = DN.query.first()
        session = db.session()
        statements = []

        def capture(orm_execute_state):
            statements.append(str(orm_execute_state.statement.compile(dialect=postgresql.dialect())))

        event.listen(session, 'do_orm_execute', capture)
        try:
            lock_row(dn)
        finally:
            event.remove(session, 'do_orm_execute', capture)

    locking = [s for s in statements if 'FOR UPDATE' in s]
    assert len(locking) == 1, statements
    assert 'JOIN' not in locking[0].upper()
    assert locking[0].split('FROM')[0].strip().upper() == 'SELECT DN.ID AS DN_ID'


# ---------------------------------------------------------------------------
# 入库线
# ---------------------------------------------------------------------------

def test_receive_asn_twice_records_once(client, access_token):
    w = _world(client)
    _create_asn(client, access_token, w, 10)                   # 同商品另一张待收货 ASN
    asn_id = _create_asn(client, access_token, w, 10)

    def load():
        asn = ASNService.get_asn(asn_id)
        _ = (asn.status, [d.quantity for d in asn.details])
        return asn

    exc = _interleave(client, load,
                      lambda: ASNService.receive_asn(asn_id),
                      lambda asn: ASNService.receive_asn(asn))
    _assert_rejected(exc, 16021)
    inventory = _inv(client, w)
    assert (inventory['asn_stock'], inventory['received_stock']) == (10, 10)
    assert len(_task_ids(client, SortingTask, asn_id=asn_id)) == 1


def test_asn_cancel_after_concurrent_receive_rolls_back_received(client, access_token):
    """取消时 ASN 已被并发收货：按 received 取消（回滚签收库存、停用分拣任务），不能按旧的 pending 直接关单。"""
    w = _world(client)
    asn_id = _create_asn(client, access_token, w, 10)

    exc = _interleave(client, lambda: ASNService.get_asn(asn_id),
                      lambda: ASNService.receive_asn(asn_id),
                      lambda asn: ASNService.cancel_asn(asn))
    assert exc is None, str(exc)
    inventory = _inv(client, w)
    assert (inventory['asn_stock'], inventory['received_stock']) == (0, 0)
    assert _task_ids(client, SortingTask, asn_id=asn_id, is_active=True) == []
    with client.application.app_context():
        assert db.session.get(ASN, asn_id).status == 'closed'


def test_sorting_complete_twice_no_phantom_sorted_stock(client, access_token):
    w = _world(client)
    other = _create_asn(client, access_token, w, 10)
    _ok(client.put(f'/asn/{other}/receive/', headers=_h(access_token)))   # 同商品另一张已收货 ASN
    _, task_id = _received_asn_in_sorting(client, access_token, w, 10)
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': w['goods'], 'sorted_quantity': 10, 'damage_quantity': 0}]}, headers=_h(access_token)))

    def load():
        task = SortingTaskService.get_task(task_id)
        _ = (task.status, task.asn.status, [d.quantity for d in task.asn.details])
        return task

    exc = _interleave(client, load,
                      lambda: SortingTaskService.complete_task(task_id, w['admin']),
                      lambda task: SortingTaskService.complete_task(task, w['admin']))
    _assert_rejected(exc, 16008)
    inventory = _inv(client, w)
    assert (inventory['sorted_stock'], inventory['received_stock']) == (10, 10)


def test_sorting_batch_append_locks_asn_and_task_before_plan_check(client, access_token, monkeypatch):
    """分拣的「不超计划量」是按整张 ASN 的聚合查询：两个请求同时查、都通过、各自提交就会超量。
    SQLite 上模拟不出这个窗口，这里校验加锁（先 ASN、后任务）发生在校验之前。"""
    w = _world(client)
    asn_id, task_id = _received_asn_in_sorting(client, access_token, w, 10)
    calls = []
    original_check = SortingTaskService._assert_within_planned

    def lock_spy(obj):
        calls.append((type(obj).__name__, obj.id))
        return lock_row(obj)

    def check_spy(task, *args, **kwargs):
        calls.append(('check', task.id))
        return original_check(task, *args, **kwargs)

    monkeypatch.setattr(sorting_services, 'lock_row', lock_spy)
    monkeypatch.setattr(SortingTaskService, '_assert_within_planned', staticmethod(check_spy))
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': w['goods'], 'sorted_quantity': 10, 'damage_quantity': 0}]}, headers=_h(access_token)))
    assert calls[:3] == [('ASN', asn_id), ('SortingTask', task_id), ('check', task_id)]


def test_sorting_batch_append_concurrent_cannot_exceed_plan(client, access_token):
    w = _world(client)
    _, task_id = _received_asn_in_sorting(client, access_token, w, 10)
    body = {'details': [{'goods_id': w['goods'], 'sorted_quantity': 10, 'damage_quantity': 0}]}

    def load():
        task = SortingTaskService.get_task(task_id)
        _ = (task.status, list(task.task_details), [d.quantity for d in task.asn.details])
        return task

    exc = _interleave(client, load,
                      lambda: SortingTaskService.create_batch(task_id, dict(body), w['admin']),
                      lambda task: SortingTaskService.create_batch(task, dict(body), w['admin']))
    _assert_rejected(exc, 16062)
    with client.application.app_context():
        assert SortingBatch.query.filter_by(sorting_task_id=task_id).count() == 1


# ---------------------------------------------------------------------------
# 调整单 / 盘点
# ---------------------------------------------------------------------------

def test_adjustment_complete_twice_adjusts_bin_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    with client.application.app_context():
        creator = get_company_admin_user()                      # 创建人与审批人分离
        adjustment = Adjustment(warehouse_id=w['wh'], adjustment_reason='B-50', status='approved',
                                is_active=True, created_by=creator.id, approved_by=w['admin'])
        adjustment.details.append(AdjustmentDetail(
            goods_id=w['goods'], location_id=w['std'],
            system_quantity=20, actual_quantity=25, adjustment_quantity=5,
        ))
        db.session.add(adjustment)
        db.session.commit()
        adjustment_id = adjustment.id

    def load():
        adjustment = AdjustmentService.get_adjustment(adjustment_id)
        _ = (adjustment.status, list(adjustment.details))
        return adjustment

    exc = _interleave(client, load,
                      lambda: AdjustmentService.complete_adjustment(adjustment_id, w['admin']),
                      lambda adjustment: AdjustmentService.complete_adjustment(adjustment, w['admin']))
    _assert_rejected(exc, 16019)
    assert _bin_quantity(client, w) == 25
    assert _inv(client, w)['onhand_stock'] == 25


def test_cycle_count_complete_twice_logs_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, 20)
    with client.application.app_context():
        task = CycleCountTask(task_name='B-50', warehouse_id=w['wh'], status='pending',
                              is_active=True, created_by=w['admin'])
        task.task_details.append(CycleCountTaskDetail(goods_id=w['goods'], location_id=w['std'],
                                                      actual_quantity=0))
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        CycleCountTaskService.process_task(task_id, w['admin'])

    def load():
        task = CycleCountTaskService.get_task(task_id)
        _ = (task.status, list(task.task_details))
        return task

    exc = _interleave(client, load,
                      lambda: CycleCountTaskService.complete_task(task_id, w['admin']),
                      lambda task: CycleCountTaskService.complete_task(task, w['admin']))
    _assert_rejected(exc, 16008)
    with client.application.app_context():
        assert CycleCountTaskStatusLog.query.filter_by(task_id=task_id, new_status='completed').count() == 1
