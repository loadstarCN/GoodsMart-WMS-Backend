"""作业流程收尾（审计 2026-10-04 🔵：B-102 / B-103 / B-104 / B-105 / B-107 / B-109）

- B-102 分拣 / 拣货 / 打包追加批次可带 client_batch_id：首次 201；同一任务同 id、明细相同 → 200 返回已存在的批次且不重复记数；
        明细不同 → 409 16121；格式不对 → 400 16123；唯一约束兜底（锁内查重漏掉时）不 500
- B-103 多商品 / 多库位操作统一加锁顺序：库存行按 goods_id 升序、库位库存行按 (goods_id, location_id) 升序，库存行先于库位库存行
- B-104 sorting 统计接口带 sorting_type 不再 500（该字段不存在，参数已去掉）
- B-105 调整单最后增 / 改 / 删明细的人不能审批 → 409 16122（创建人仍 403 16040）
- B-107 新商品首次建库存行 / 库位首次建库位库存行撞唯一键时 savepoint 重读，不再 500
- B-109 移库来源库位没有该商品 → 404 16120（14003 只表示缺仓库 id）
"""
from contextlib import contextmanager
from datetime import date

from sqlalchemy import event, func
from sqlalchemy.orm import Session

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _create_asn, _err, _h, _inbound, _ok, _picked_dn, _task_ids, _world
from tests.test_process_guards import _staff_token
from warehouse.inventory.services import InventoryService
from warehouse.packing.services import PackingTaskService
from warehouse.transfer.services import TransferService


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _count(client, model, **filters):
    with client.application.app_context():
        return model.query.filter_by(**filters).count()


def _sum(client, column, **filters):
    with client.application.app_context():
        return db.session.query(func.coalesce(func.sum(column), 0)).filter_by(**filters).scalar()


def _sorting_task(client, token, w, lines):
    """ASN 收货 → 分拣任务开始，返回 (asn_id, task_id)"""
    asn_id = _create_asn(client, token, w, lines)
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(token)))
    return asn_id, task_id


def _dn(client, token, w, lines):
    return _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': date.today().isoformat(),
        'details': [{'goods_id': g, 'quantity': q} for g, q in lines],
    }, headers=_h(token)))['id']


def _picking_task(client, token, w):
    """入库 g1 / g2 各 10 件（g1 在 std1，g2 在 std2）→ DN 各 5 件 → 拣货任务开始，返回 task_id"""
    _inbound(client, token, w, [(w['g1'], 10), (w['g2'], 10)], [(w['g1'], w['std1'], 10), (w['g2'], w['std2'], 10)])
    dn_id = _dn(client, token, w, [(w['g1'], 5), (w['g2'], 5)])
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(token)))
    task_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{task_id}/process/', headers=_h(token)))
    return task_id


@contextmanager
def _lock_spy():
    """记录 FOR UPDATE 查询里库存行（inv, goods_id, warehouse_id）与库位库存行（gl, goods_id, location_id）的首次加锁顺序。
    SQLite 会忽略 FOR UPDATE，但 ORM 语句上仍带 with_for_update，可以据此检查加锁顺序"""
    seen = []

    def listener(state):
        statement = state.statement
        if not state.is_select or getattr(statement, '_for_update_arg', None) is None:
            return
        entity = statement.column_descriptions[0].get('entity')
        params = statement.compile().params

        def param(prefix):
            return next((v for k, v in params.items() if k.startswith(prefix)), None)

        if entity is Inventory:
            key = ('inv', param('goods_id'), param('warehouse_id'))
        elif entity is GoodsLocation:
            key = ('gl', param('goods_id'), param('location_id'))
        else:
            return
        if key not in seen:
            seen.append(key)

    event.listen(Session, 'do_orm_execute', listener)
    try:
        yield seen
    finally:
        event.remove(Session, 'do_orm_execute', listener)


def _assert_lock_order(seen, *, expect_inv=True, expect_gl=False):
    inv = [k for k in seen if k[0] == 'inv']
    gl = [k for k in seen if k[0] == 'gl']
    assert bool(inv) == expect_inv and bool(gl) == expect_gl, seen
    assert inv == sorted(inv), seen
    assert gl == sorted(gl), seen
    if inv and gl:
        # 库存行全部先于库位库存行
        assert seen.index(gl[0]) > seen.index(inv[-1]), seen


# ---------------------------------------------------------------------------
# B-102 批次追加幂等
# ---------------------------------------------------------------------------

def test_sorting_batch_client_batch_id_replay_and_conflict(client, access_token):
    w = _world(client)
    _, task_id = _sorting_task(client, access_token, w, [(w['g1'], 10), (w['g2'], 10)])
    url = f'/sorting/{task_id}/batches/'
    body = {'client_batch_id': 'sort-1', 'remark': 'first', 'details': [
        {'goods_id': w['g1'], 'sorted_quantity': 3, 'damage_quantity': 1},
        {'goods_id': w['g2'], 'sorted_quantity': 2, 'damage_quantity': 0},
    ]}
    first = client.post(url, json=body, headers=_h(access_token))
    assert first.status_code == 201, first.get_json()
    created = first.get_json()
    assert created['client_batch_id'] == 'sort-1' and len(created['details']) == 2

    # 重放（明细换了顺序、备注 / 操作时间不同也算相同）→ 200，返回同一批次，不重复记数
    replay = client.post(url, json={
        'client_batch_id': 'sort-1', 'remark': 'retry', 'operation_time': '2026-10-05T10:00:00',
        'details': list(reversed(body['details'])),
    }, headers=_h(access_token))
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['id'] == created['id']
    assert len(replay.get_json()['details']) == 2
    assert _count(client, SortingBatch, sorting_task_id=task_id) == 1
    assert _sum(client, SortingTaskDetail.sorted_quantity, sorting_task_id=task_id) == 5
    assert _sum(client, SortingTaskDetail.damage_quantity, sorting_task_id=task_id) == 1

    # 同一个 id、明细不同 → 409 16121
    conflict = _err(client.post(url, json={'client_batch_id': 'sort-1', 'details': [
        {'goods_id': w['g1'], 'sorted_quantity': 4, 'damage_quantity': 0},
    ]}, headers=_h(access_token)), 409, 16121)
    assert conflict['details'] == {'client_batch_id': 'sort-1', 'batch_id': created['id']}

    # 不带 client_batch_id：行为不变，每次都新增
    for _ in range(2):
        response = client.post(url, json={'details': [
            {'goods_id': w['g1'], 'sorted_quantity': 1, 'damage_quantity': 0}]}, headers=_h(access_token))
        assert response.status_code == 201
    assert _count(client, SortingBatch, sorting_task_id=task_id) == 3
    assert _sum(client, SortingTaskDetail.sorted_quantity, sorting_task_id=task_id) == 7

    # 唯一性只在同一任务内：别的任务可以用同一个 id
    _, other_task_id = _sorting_task(client, access_token, w, [(w['g1'], 5)])
    other = client.post(f'/sorting/{other_task_id}/batches/', json={'client_batch_id': 'sort-1', 'details': [
        {'goods_id': w['g1'], 'sorted_quantity': 1, 'damage_quantity': 0}]}, headers=_h(access_token))
    assert other.status_code == 201 and other.get_json()['id'] != created['id']

    # 任务完成后，迟到的重放仍按已存在的批次返回 200（不会报「任务不在进行中」）
    _ok(client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token)))
    late = client.post(url, json=body, headers=_h(access_token))
    assert late.status_code == 200 and late.get_json()['id'] == created['id']


def test_client_batch_id_format_rejected(client, access_token):
    w = _world(client)
    _, task_id = _sorting_task(client, access_token, w, [(w['g1'], 10)])
    for bad in ('', 'x' * 65, 123, ['a'], True):
        _err(client.post(f'/sorting/{task_id}/batches/', json={'client_batch_id': bad, 'details': []},
                         headers=_h(access_token)), 400, 16123)
    # 64 个字符可以；null 等同不带
    assert client.post(f'/sorting/{task_id}/batches/', json={'client_batch_id': 'x' * 64, 'details': []},
                       headers=_h(access_token)).status_code == 201
    assert client.post(f'/sorting/{task_id}/batches/', json={'client_batch_id': None, 'details': []},
                       headers=_h(access_token)).status_code == 201
    assert _count(client, SortingBatch, sorting_task_id=task_id) == 2


def test_picking_batch_client_batch_id_replay_and_conflict(client, access_token):
    w = _world(client)
    task_id = _picking_task(client, access_token, w)
    url = f'/picking/{task_id}/batches/'
    details = [
        {'goods_id': w['g1'], 'location_id': w['std1'], 'picked_quantity': 2},
        {'goods_id': w['g2'], 'location_id': w['std2'], 'picked_quantity': 1},
    ]
    first = client.post(url, json={'client_batch_id': 'pick-1', 'details': details}, headers=_h(access_token))
    assert first.status_code == 201, first.get_json()
    batch_id = first.get_json()['id']

    replay = client.post(url, json={'client_batch_id': 'pick-1', 'details': details[::-1]}, headers=_h(access_token))
    assert replay.status_code == 200 and replay.get_json()['id'] == batch_id
    assert replay.get_json()['client_batch_id'] == 'pick-1'
    assert _count(client, PickingBatch, picking_task_id=task_id) == 1
    assert _sum(client, PickingTaskDetail.picked_quantity, picking_task_id=task_id) == 3

    # 拣货还比库位：同商品同数量换了库位也算不同
    _err(client.post(url, json={'client_batch_id': 'pick-1', 'details': [
        {'goods_id': w['g1'], 'location_id': w['std2'], 'picked_quantity': 2},
        {'goods_id': w['g2'], 'location_id': w['std2'], 'picked_quantity': 1},
    ]}, headers=_h(access_token)), 409, 16121)
    _err(client.post(url, json={'client_batch_id': 'pick-1', 'details': details[:1]}, headers=_h(access_token)),
         409, 16121)
    assert _sum(client, PickingTaskDetail.picked_quantity, picking_task_id=task_id) == 3


def test_packing_batch_client_batch_id_replay_and_conflict(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    _, task_id = _picked_dn(client, access_token, w, [(w['g1'], 5)], [(w['g1'], w['std1'], 5)])
    url = f'/packing/{task_id}/batches/'
    # 测试种子数据里有一条挂在 batch_id=1 上的孤儿打包明细（SQLite 不查外键）：先占掉 1 号批次，免得混进来
    _ok(client.post(url, json={'details': []}, headers=_h(access_token)))
    first = client.post(url, json={'client_batch_id': 'pack-1', 'details': [
        {'goods_id': w['g1'], 'packed_quantity': 2, 'packing_time': '2026-10-05T09:00:00'}]}, headers=_h(access_token))
    assert first.status_code == 201, first.get_json()
    batch_id = first.get_json()['id']

    # packing_time 不比
    replay = client.post(url, json={'client_batch_id': 'pack-1', 'details': [
        {'goods_id': w['g1'], 'packed_quantity': 2, 'packing_time': '2026-10-05T09:05:00'}]}, headers=_h(access_token))
    assert replay.status_code == 200 and replay.get_json()['id'] == batch_id
    assert _count(client, PackingBatch, packing_task_id=task_id) == 2
    assert _sum(client, PackingTaskDetail.packed_quantity, packing_task_id=task_id) == 2

    _err(client.post(url, json={'client_batch_id': 'pack-1', 'details': [
        {'goods_id': w['g1'], 'packed_quantity': 3}]}, headers=_h(access_token)), 409, 16121)
    assert _sum(client, PackingTaskDetail.packed_quantity, packing_task_id=task_id) == 2

    # 服务层：create_batch 重放时返回已存在的批次
    with client.application.app_context():
        batch = PackingTaskService.create_batch(task_id, {'client_batch_id': 'pack-1', 'details': [
            {'goods_id': w['g1'], 'packed_quantity': 2}]}, w['admin'])
        assert batch.id == batch_id
    assert _count(client, PackingBatch, packing_task_id=task_id) == 2


def test_batch_unique_constraint_fallback_replays_instead_of_500(client, access_token, monkeypatch):
    """锁内查重漏掉（模拟并发的另一请求刚提交了同一个 client_batch_id）：插入撞唯一约束 → savepoint 回滚后重放 / 409"""
    import warehouse.picking.services as picking_services
    import warehouse.sorting.services as sorting_services

    w = _world(client)
    task_id = _picking_task(client, access_token, w)
    url = f'/picking/{task_id}/batches/'
    details = [{'goods_id': w['g1'], 'location_id': w['std1'], 'picked_quantity': 1}]
    batch_id = _ok(client.post(url, json={'client_batch_id': 'race-1', 'details': details},
                               headers=_h(access_token)))['id']

    monkeypatch.setattr(picking_services, 'find_batch', lambda *args, **kwargs: None)
    replay = client.post(url, json={'client_batch_id': 'race-1', 'details': details}, headers=_h(access_token))
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['id'] == batch_id
    _err(client.post(url, json={'client_batch_id': 'race-1', 'details': [
        {'goods_id': w['g1'], 'location_id': w['std1'], 'picked_quantity': 2}]}, headers=_h(access_token)), 409, 16121)
    assert _count(client, PickingBatch, picking_task_id=task_id) == 1
    assert _sum(client, PickingTaskDetail.picked_quantity, picking_task_id=task_id) == 1

    # 分拣同样
    _, sorting_task_id = _sorting_task(client, access_token, w, [(w['g1'], 10)])
    sort_url = f'/sorting/{sorting_task_id}/batches/'
    sort_details = [{'goods_id': w['g1'], 'sorted_quantity': 2, 'damage_quantity': 0}]
    sort_batch_id = _ok(client.post(sort_url, json={'client_batch_id': 'race-2', 'details': sort_details},
                                    headers=_h(access_token)))['id']
    monkeypatch.setattr(sorting_services, 'find_batch', lambda *args, **kwargs: None)
    replay = client.post(sort_url, json={'client_batch_id': 'race-2', 'details': sort_details},
                         headers=_h(access_token))
    assert replay.status_code == 200 and replay.get_json()['id'] == sort_batch_id
    assert _count(client, SortingBatch, sorting_task_id=sorting_task_id) == 1


# ---------------------------------------------------------------------------
# B-103 统一加锁顺序
# ---------------------------------------------------------------------------

def test_dn_create_and_progress_lock_inventory_in_goods_order(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10), (w['g2'], 10)], [(w['g1'], w['std1'], 10), (w['g2'], w['std2'], 10)])
    assert w['g1'] < w['g2']
    with _lock_spy() as seen:
        dn_id = _dn(client, access_token, w, [(w['g2'], 2), (w['g1'], 2)])   # 请求里商品倒序
    _assert_lock_order(seen)
    assert [k[1] for k in seen] == [w['g1'], w['g2']]

    with _lock_spy() as seen:
        _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(access_token)))
    _assert_lock_order(seen)
    assert [k[1] for k in seen] == [w['g1'], w['g2']]


def test_picking_complete_locks_inventory_before_bins(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10), (w['g2'], 10)], [(w['g1'], w['std2'], 10), (w['g2'], w['std1'], 10)])
    dn_id = _dn(client, access_token, w, [(w['g2'], 3), (w['g1'], 3)])
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(access_token)))
    task_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{task_id}/process/', headers=_h(access_token)))
    with _lock_spy() as seen:
        _ok(client.post(f'/picking/{task_id}/batches/', json={'details': [
            {'goods_id': w['g2'], 'location_id': w['std1'], 'picked_quantity': 3},
            {'goods_id': w['g1'], 'location_id': w['std2'], 'picked_quantity': 3},
        ]}, headers=_h(access_token)))
    _assert_lock_order(seen, expect_inv=False, expect_gl=True)

    with _lock_spy() as seen:
        _ok(client.put(f'/picking/{task_id}/complete/', headers=_h(access_token)))
    _assert_lock_order(seen, expect_gl=True)
    assert [k[1:] for k in seen if k[0] == 'gl'] == [(w['g1'], w['std2']), (w['g2'], w['std1'])]


def test_adjustment_complete_and_bulk_putaway_lock_order(client, access_token, access_company_admin_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10), (w['g2'], 10)], [(w['g1'], w['std2'], 5), (w['g2'], w['std1'], 5)])

    # 批量上架：请求里 (g2, std2) 在前，加锁仍按库存行 g1 < g2、再库位库存行 (g1, std1) < (g2, std2)
    with _lock_spy() as seen:
        _ok(client.post('/putaway/bulk', json=[
            {'goods_id': w['g2'], 'location_id': w['std2'], 'quantity': 2},
            {'goods_id': w['g1'], 'location_id': w['std1'], 'quantity': 2},
        ], headers=_h(access_token)))
    _assert_lock_order(seen, expect_gl=True)

    adjustment_id = _ok(client.post('/adjustment/', json={
        'warehouse_id': w['wh'], 'adjustment_reason': 'lock order',
        'details': [
            {'goods_id': w['g2'], 'location_id': w['std1'], 'system_quantity': 5, 'actual_quantity': 4},
            {'goods_id': w['g1'], 'location_id': w['std2'], 'system_quantity': 5, 'actual_quantity': 6},
        ],
    }, headers=_h(access_token)))['id']
    _ok(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(access_company_admin_token)))
    with _lock_spy() as seen:
        _ok(client.put(f'/adjustment/{adjustment_id}/complete/', headers=_h(access_token)))
    _assert_lock_order(seen, expect_gl=True)
    assert [k[1:] for k in seen if k[0] == 'gl'] == [(w['g1'], w['std2']), (w['g2'], w['std1'])]


def test_asn_receive_and_bulk_transfer_lock_order(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10), (w['g2'], 10)], [(w['g1'], w['std2'], 5), (w['g2'], w['std1'], 5)])
    asn_id = _create_asn(client, access_token, w, [(w['g2'], 1), (w['g1'], 1)])
    with _lock_spy() as seen:
        _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)))
    _assert_lock_order(seen)

    with _lock_spy() as seen:
        _ok(client.post('/transfer/bulk', json=[
            {'goods_id': w['g2'], 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1},
            {'goods_id': w['g1'], 'from_location_id': w['std2'], 'to_location_id': w['std1'], 'quantity': 1},
        ], headers=_h(access_token)))
    _assert_lock_order(seen, expect_gl=True)


# ---------------------------------------------------------------------------
# B-104 sorting 统计接口
# ---------------------------------------------------------------------------

def test_stats_with_sorting_type_param_no_longer_500(client, access_token):
    w = _world(client)
    _sorting_task(client, access_token, w, [(w['g1'], 3)])
    for url in ('/sorting/monthly-stats', '/sorting/status-overview-stats', '/picking/monthly-stats',
                '/packing/monthly-stats', '/delivery/monthly-stats'):
        response = client.get(f'{url}?sorting_type=pending&months=3', headers=_h(access_token))
        assert response.status_code == 200, (url, response.get_json())
    overview = client.get('/sorting/status-overview-stats?sorting_type=pending', headers=_h(access_token)).get_json()
    in_progress = next(row for row in overview if row['name'] == 'in_progress')
    assert in_progress['current_month'] >= 1   # 参数被忽略，不再按它过滤


# ---------------------------------------------------------------------------
# B-105 调整单职责分离
# ---------------------------------------------------------------------------

def _adjustment(client, token, w):
    body = _ok(client.post('/adjustment/', json={
        'warehouse_id': w['wh'], 'adjustment_reason': 'sod',
        'details': [{'goods_id': w['g1'], 'location_id': w['std1'], 'system_quantity': 5, 'actual_quantity': 4}],
    }, headers=_h(token)))
    with client.application.app_context():
        detail_id = AdjustmentDetail.query.filter_by(adjustment_id=body['id']).first().id
    return body['id'], detail_id


def test_detail_modifier_cannot_approve(client, access_token, access_company_admin_token):
    w = _world(client)
    with client.application.app_context():
        company_admin_id = get_company_admin_user().id
        admin_id = get_admin_user().id
    approver = _staff_token(client, 'sod-approver', ['adjustment_approve', 'adjustment_read'])

    # admin 建单，公司管理员改明细 → 公司管理员不能审批（409 16122）
    adjustment_id, detail_id = _adjustment(client, access_token, w)
    assert _ok(client.get(f'/adjustment/{adjustment_id}', headers=_h(access_token)))['details_modified_by'] == admin_id
    _ok(client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', json={'actual_quantity': 50},
                   headers=_h(access_company_admin_token)))
    assert _ok(client.get(f'/adjustment/{adjustment_id}',
                          headers=_h(access_token)))['details_modified_by'] == company_admin_id
    body = _err(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(access_company_admin_token)), 409, 16122)
    assert body['details'] == {'adjustment_id': adjustment_id, 'details_modified_by': company_admin_id}
    # 创建人仍是 403 16040
    _err(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(access_token)), 403, 16040)
    with client.application.app_context():
        assert db.session.get(Adjustment, adjustment_id).status == 'pending'
    # 第三个人可以审批
    approved = _ok(client.put(f'/adjustment/{adjustment_id}/approve/', headers=approver))
    assert approved['status'] == 'approved'

    # 加明细 / 删明细同样记修改人
    adjustment_id, detail_id = _adjustment(client, access_token, w)
    _ok(client.post(f'/adjustment/{adjustment_id}/details/', json={
        'goods_id': w['g2'], 'location_id': w['std1'], 'system_quantity': 0, 'actual_quantity': 1,
    }, headers=_h(access_company_admin_token)))
    _err(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(access_company_admin_token)), 409, 16122)

    adjustment_id, detail_id = _adjustment(client, access_token, w)
    _ok(client.delete(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=_h(access_company_admin_token)))
    _err(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(access_company_admin_token)), 409, 16122)

    # 只看最后修改人：公司管理员改过之后创建人又改了一次 → 公司管理员可以审批
    adjustment_id, detail_id = _adjustment(client, access_token, w)
    _ok(client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', json={'actual_quantity': 3},
                   headers=_h(access_company_admin_token)))
    _ok(client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', json={'actual_quantity': 2},
                   headers=_h(access_token)))
    assert _ok(client.put(f'/adjustment/{adjustment_id}/approve/',
                          headers=_h(access_company_admin_token)))['status'] == 'approved'


def test_legacy_adjustment_without_modifier_only_checks_creator(client, access_token, access_company_admin_token):
    """存量调整单 details_modified_by 为 NULL：只按创建人判断（与原来一致）"""
    w = _world(client)
    adjustment_id, _ = _adjustment(client, access_token, w)
    with client.application.app_context():
        db.session.get(Adjustment, adjustment_id).details_modified_by = None
        db.session.commit()
    assert _ok(client.put(f'/adjustment/{adjustment_id}/approve/',
                          headers=_h(access_company_admin_token)))['status'] == 'approved'


# ---------------------------------------------------------------------------
# B-107 先查后插的库存行 / 库位库存行
# ---------------------------------------------------------------------------

def test_ensure_inventory_recovers_from_concurrent_insert(client, access_token, monkeypatch):
    """同一新商品第一次被并发建 ASN：本请求查的时候还没有库存行，插入时对方已提交 → 撞主键后 savepoint 重读"""
    w = _world(client)
    with client.application.app_context():
        # 模拟并发的另一请求刚建好的库存行
        db.session.add(Inventory(goods_id=w['g1'], warehouse_id=w['wh']))
        db.session.commit()

    original_get = db.session.get
    misses = {'left': 1}

    def stale_get(entity, ident, *args, **kwargs):
        if entity is Inventory and misses['left']:
            misses['left'] -= 1
            return None   # 查的时候还没看到
        return original_get(entity, ident, *args, **kwargs)

    monkeypatch.setattr(db.session, 'get', stale_get)
    asn_id = _create_asn(client, access_token, w, [(w['g1'], 4)])
    monkeypatch.undo()
    assert misses['left'] == 0
    with client.application.app_context():
        assert Inventory.query.filter_by(goods_id=w['g1'], warehouse_id=w['wh']).count() == 1
        assert db.session.get(Inventory, (w['g1'], w['wh'])).asn_stock == 4
        assert db.session.get(ASN, asn_id) is not None


def test_ensure_inventory_service_creates_once(client):
    w = _world(client)
    with client.application.app_context():
        first = InventoryService.ensure_inventory(w['g2'], w['wh'])
        second = InventoryService.ensure_inventory(w['g2'], w['wh'])
        assert first is second
        assert Inventory.query.filter_by(goods_id=w['g2'], warehouse_id=w['wh']).count() == 1


def test_first_putaway_into_bin_recovers_from_concurrent_insert(client, access_token, monkeypatch):
    """同一 (商品, 库位) 第一次被并发上架：加锁读的时候还没有行，插入撞 uix_goods_location → savepoint 后重新加锁读取"""
    import warehouse.common.locks as locks

    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 6)])
    original = locks.lock_goods_location
    misses = {'left': 1}

    def stale_lock(goods_id, location_id):
        if misses['left']:
            misses['left'] -= 1
            return None
        return original(goods_id, location_id)

    monkeypatch.setattr(locks, 'lock_goods_location', stale_lock)
    _ok(client.post('/putaway/', json={'goods_id': w['g1'], 'location_id': w['std1'], 'quantity': 3},
                    headers=_h(access_token)))
    monkeypatch.undo()
    assert misses['left'] == 0
    with client.application.app_context():
        rows = GoodsLocation.query.filter_by(goods_id=w['g1'], location_id=w['std1']).all()
        assert [r.quantity for r in rows] == [9]
        assert db.session.get(Inventory, (w['g1'], w['wh'])).sorted_stock == 1


def test_first_transfer_into_bin_creates_row(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    _ok(client.post('/transfer/', json={'goods_id': w['g1'], 'from_location_id': w['std1'],
                                        'to_location_id': w['std2'], 'quantity': 4}, headers=_h(access_token)))
    with client.application.app_context():
        assert GoodsLocation.query.filter_by(goods_id=w['g1'], location_id=w['std2']).one().quantity == 4
        assert GoodsLocation.query.filter_by(goods_id=w['g1'], location_id=w['std1']).one().quantity == 6


# ---------------------------------------------------------------------------
# B-109 移库来源库位没有该商品 → 16120
# ---------------------------------------------------------------------------

def test_transfer_goods_not_in_source_location_is_16120(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    single = _err(client.post('/transfer/', json={
        'goods_id': w['g2'], 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1,
    }, headers=_h(access_token)), 404, 16120)
    assert single['details'] == {'goods_id': w['g2'], 'from_location_id': w['std1']}
    _err(client.post('/transfer/bulk', json=[
        {'goods_id': w['g1'], 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1},
        {'goods_id': w['g1'], 'from_location_id': w['std2'], 'to_location_id': w['std1'], 'quantity': 1},
    ], headers=_h(access_token)), 404, 16120)

    # 服务层（接口预检之后库位被并发清空）同样是 16120
    with client.application.app_context():
        try:
            TransferService.create_transfer_record({
                'goods_id': w['g2'], 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1,
            }, w['admin'])
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, 'biz_code', None) == 16120, exc
        else:
            raise AssertionError('expected 16120')
        db.session.rollback()


def test_missing_warehouse_still_14003(client, access_token):
    staff = _staff_token(client, 'no-wh-staff', ['transfer_read'])
    staff.pop('X-WAREHOUSE-ID')
    _err(client.get('/transfer/', headers=staff), 400, 14003)
