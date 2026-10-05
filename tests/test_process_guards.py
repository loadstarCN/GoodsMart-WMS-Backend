"""库存与作业流程守卫（审计 2026-10-04 🟡：B-78 ~ B-86）

- B-78 已审批的调整单：正向调整的库位记录不存在时新建；负向调整没有记录 → 409 16113；
       新增「取消调整单」PUT /adjustment/<id>/cancel/（pending / approved，is_active=False），已取消的单一切操作 → 409 16111
- B-79 盘点系统数在录入实盘时取（批量保存 / 单行完成 / 明细修改），调整单用录入时的系统数
- B-80 盘点完成前每条明细必须已录入（盘到 0 件也要明确提交）→ 否则 409 16114
- B-81 POST /sorting/、/picking/、/packing/ 已去掉（405），任务只由单据流转自动生成
- B-82 删除带明细的分拣 / 拣货 / 打包批次：明细一并删除，不再 500
- B-83 picked / packed（未发货）的 DN 可取消：货退回 sorted_stock，任务停用，单证作废；有未结束的自动运单 → 409 16110
- B-84 库位有历史记录时删除 → 409 44009（改用停用）
- B-85 ASN weight / volume、库位尺寸：空串 → null，非法 / 越界 → 400 16115
- B-86 盘点单 / 调整单列表支持 keyword
"""
from datetime import date, datetime

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import (
    STOCK_FIELDS, _assert_consistent, _bin, _create_asn, _err, _h, _inbound, _inv, _ok, _picked_dn, _task_ids,
    _world,
)
from system.webhook.models import WebhookEvent
from warehouse.cyclecount.services import CycleCountTaskService
from warehouse.dn.models import DNCarrierShipment, DNDocument
from warehouse.packing.services import PackingTaskService
from warehouse.picking.services import PickingTaskService
from warehouse.sorting.services import SortingTaskService


def _staff_token(client, user_name, permissions):
    """Warehouse A 下一个只带指定权限的员工，返回请求头"""
    with client.application.app_context():
        admin = get_admin_user()
        warehouse = Warehouse.query.filter_by(name='Warehouse A').first()
        role = Role(name=f'role-{user_name}', description=user_name, is_active=True)
        db.session.add(role)
        for name in permissions:
            perm = Permission.query.filter_by(name=name).first()
            if perm is None:
                perm = Permission(name=name, description=name)
                db.session.add(perm)
            role.permissions.append(perm)
        staff = Staff(user_name=user_name, email=f'{user_name}@test.com', is_active=True,
                      company_id=warehouse.company_id, created_by=admin.id)
        staff.set_password('password')
        staff.roles.append(role)
        staff.warehouses.append(warehouse)
        db.session.add(staff)
        db.session.commit()
        return {'Authorization': f'Bearer {create_access_token(identity=staff)}',
                'X-WAREHOUSE-ID': str(warehouse.id)}


# ---------------------------------------------------------------------------
# B-78 调整单：完成时新建库位记录 / 负向无记录 409 / 取消
# ---------------------------------------------------------------------------

def _approved_adjustment(client, admin_token, approver_token, w, lines, reason='guard'):
    """admin 建调整单（lines: [(goods_id, location_id, system, actual)]），company_admin 审批，返回 id"""
    adjustment_id = _ok(client.post('/adjustment/', json={
        'warehouse_id': w['wh'], 'adjustment_reason': reason,
        'details': [{'goods_id': g, 'location_id': l, 'system_quantity': s, 'actual_quantity': a}
                    for g, l, s, a in lines],
    }, headers=_h(admin_token)))['id']
    if approver_token is not None:
        _ok(client.put(f'/adjustment/{adjustment_id}/approve/', headers=_h(approver_token)))
    return adjustment_id


def test_positive_adjustment_creates_goods_location(client, access_token, access_company_admin_token):
    """「在新库位发现 3 件」：目标库位还没有该商品的记录 → 完成时新建；商品在仓库还没库存记录时一并建"""
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    adjustment_id = _approved_adjustment(client, access_token, access_company_admin_token, w, [
        (w['g1'], w['std2'], 0, 3),       # g1 在 std2 没有记录
        (w['g2'], w['std2'], 0, 2),       # g2 在仓库连 Inventory 都没有
    ])

    body = _ok(client.put(f'/adjustment/{adjustment_id}/complete/', headers=_h(access_token)))
    assert body['status'] == 'completed'
    assert (_bin(client, w['g1'], w['std1']), _bin(client, w['g1'], w['std2']), _bin(client, w['g2'], w['std2'])) \
        == (10, 3, 2)
    stock = _inv(client, w['g1'], w['wh'])
    assert (stock['onhand_stock'], stock['total_stock']) == (13, 13)
    _assert_consistent(stock)
    stock = _inv(client, w['g2'], w['wh'])
    assert (stock['onhand_stock'], stock['total_stock']) == (2, 2)
    _assert_consistent(stock)


def test_positive_adjustment_into_inactive_location_rejected(client, access_token, access_company_admin_token):
    w = _world(client)
    adjustment_id = _approved_adjustment(client, access_token, access_company_admin_token, w,
                                         [(w['g1'], w['std2'], 0, 3)])
    _ok(client.put(f"/location/{w['std2']}", json={'is_active': False}, headers=_h(access_token)))
    _err(client.put(f'/adjustment/{adjustment_id}/complete/', headers=_h(access_token)), 400, 16085)
    with client.application.app_context():
        assert db.session.get(Adjustment, adjustment_id).status == 'approved'
    assert _bin(client, w['g1'], w['std2']) == 0


def test_negative_adjustment_without_record_conflict_then_cancel(client, access_token, access_company_admin_token):
    """审批后库位被清空：负向调整完成 → 409 16113（单据不变）；取消后一切操作 → 409 16111"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])
    adjustment_id = _approved_adjustment(client, access_token, access_company_admin_token, w, [(g, w['std1'], 5, 2)])
    # 审批后货被整批移走，std1 上的库位记录随之删除
    _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'],
                                        'quantity': 5}, headers=_h(access_token)))
    assert _bin(client, g, w['std1']) == 0

    body = _err(client.put(f'/adjustment/{adjustment_id}/complete/', headers=_h(access_token)), 409, 16113)
    assert body['details']['location_id'] == w['std1'] and body['details']['adjustment_quantity'] == -3
    with client.application.app_context():
        adjustment = db.session.get(Adjustment, adjustment_id)
        assert (adjustment.status, adjustment.is_active, adjustment.completed_at) == ('approved', True, None)

    # 取消（需要 adjustment_approve）：is_active=False，status 保持 approved，库存不动
    body = _ok(client.put(f'/adjustment/{adjustment_id}/cancel/', headers=_h(access_company_admin_token)))
    assert (body['status'], body['is_active']) == ('approved', False)
    assert _bin(client, g, w['std2']) == 5

    with client.application.app_context():
        detail_id = AdjustmentDetail.query.filter_by(adjustment_id=adjustment_id).first().id
    for method, url, payload in (
        ('PUT', f'/adjustment/{adjustment_id}/complete/', None),
        ('PUT', f'/adjustment/{adjustment_id}/approve/', None),
        ('PUT', f'/adjustment/{adjustment_id}/cancel/', None),
        ('PUT', f'/adjustment/{adjustment_id}', {'adjustment_reason': 'x'}),
        ('DELETE', f'/adjustment/{adjustment_id}', None),
        ('POST', f'/adjustment/{adjustment_id}/details/', {'goods_id': g, 'location_id': w['std2'],
                                                            'system_quantity': 5, 'actual_quantity': 4}),
        ('PUT', f'/adjustment/{adjustment_id}/details/{detail_id}', {'actual_quantity': 1}),
        ('DELETE', f'/adjustment/{adjustment_id}/details/{detail_id}', None),
    ):
        response = client.open(url, method=method, json=payload, headers=_h(access_company_admin_token))
        _err(response, 409, 16111)
        assert response.get_json()['details'] == {'adjustment_id': adjustment_id}

    # 默认列表只显示启用中的单；is_active=false 可以查到取消的单
    listed = _ok(client.get('/adjustment/?all=true', headers=_h(access_token)))
    assert adjustment_id not in [a['id'] for a in listed['items']]
    listed = _ok(client.get('/adjustment/?all=true&is_active=false', headers=_h(access_token)))
    assert adjustment_id in [a['id'] for a in listed['items']]


def test_cancel_pending_adjustment_and_completed_rejected(client, access_token, access_company_admin_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 5)], [(w['g1'], w['std1'], 5)])
    pending_id = _approved_adjustment(client, access_token, None, w, [(w['g1'], w['std1'], 5, 4)])
    body = _ok(client.put(f'/adjustment/{pending_id}/cancel/', headers=_h(access_company_admin_token)))
    assert (body['status'], body['is_active']) == ('pending', False)
    with client.application.app_context():
        assert db.session.get(Adjustment, pending_id).operator_id == get_company_admin_user().id

    done_id = _approved_adjustment(client, access_token, access_company_admin_token, w, [(w['g1'], w['std1'], 5, 4)])
    _ok(client.put(f'/adjustment/{done_id}/complete/', headers=_h(access_token)))
    body = _err(client.put(f'/adjustment/{done_id}/cancel/', headers=_h(access_company_admin_token)), 409, 16112)
    assert body['details'] == {'adjustment_id': done_id, 'status': 'completed'}
    assert _bin(client, w['g1'], w['std1']) == 4


def test_cancel_adjustment_requires_approve_permission(client, access_token):
    w = _world(client)
    adjustment_id = _approved_adjustment(client, access_token, None, w, [(w['g1'], w['std1'], 0, 1)])
    editor = _staff_token(client, 'adj_editor', ['adjustment_read', 'adjustment_edit'])
    approver = _staff_token(client, 'adj_approver', ['adjustment_read', 'adjustment_approve'])
    assert client.put(f'/adjustment/{adjustment_id}/cancel/', headers=editor).status_code == 403
    body = _ok(client.put(f'/adjustment/{adjustment_id}/cancel/', headers=approver))
    assert body['is_active'] is False


# ---------------------------------------------------------------------------
# B-79 / B-80 盘点：录入时取系统数；完成前必须全部录入
# ---------------------------------------------------------------------------

def _cycle_task(client, w, lines, start=True):
    """盘点单（lines: [(goods_id, location_id)]），默认开始盘点，返回 (task_id, [detail_id...])"""
    with client.application.app_context():
        task = CycleCountTaskService.create_task({
            'task_name': 'process-guard', 'warehouse_id': w['wh'],
            'task_details': [{'goods_id': g, 'location_id': l} for g, l in lines],
        }, w['admin'])
        db.session.commit()
        task_id = task.id
        if start:
            CycleCountTaskService.process_task(task_id, w['admin'])
        ids = sorted(d.id for d in db.session.get(CycleCountTask, task_id).task_details)
        return task_id, ids


def _cc(client, detail_id):
    with client.application.app_context():
        d = db.session.get(CycleCountTaskDetail, detail_id)
        return {'system': d.system_quantity, 'actual': d.actual_quantity, 'difference': d.difference,
                'operator': d.operator_id, 'status': d.status}


def test_cycle_count_system_quantity_taken_when_counted(client, access_token, access_company_admin_token):
    """开始盘点时快照 100；之后正常拣走 20（库位 80）；实盘 75 → 差异按录入时算 −5，调整后库位 75（旧逻辑 55）"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 100)], [(g, w['std1'], 100)])
    task_id, (d1,) = _cycle_task(client, w, [(g, w['std1'])])
    assert _cc(client, d1)['system'] == 100

    _picked_dn(client, access_token, w, [(g, 20)], [(g, w['std1'], 20)])
    assert _bin(client, g, w['std1']) == 80

    _ok(client.post(f'/cyclecount/{task_id}/details-batch-save/', json={'details': [{'id': d1, 'actual_quantity': 75}]},
                    headers=_h(access_token)))
    assert _cc(client, d1)['system'] == 80 and _cc(client, d1)['difference'] == -5
    _ok(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)))

    adjustment = _ok(client.post(f'/adjustment/create_adjustment_by_cyclecount/{task_id}', headers=_h(access_token)))
    (line,) = adjustment['details']
    assert (line['system_quantity'], line['actual_quantity'], line['adjustment_quantity']) == (80, 75, -5)
    _ok(client.put(f"/adjustment/{adjustment['id']}/approve/", headers=_h(access_company_admin_token)))
    _ok(client.put(f"/adjustment/{adjustment['id']}/complete/", headers=_h(access_token)))
    assert _bin(client, g, w['std1']) == 75
    _assert_consistent(_inv(client, g, w['wh']))


def test_cycle_count_detail_complete_and_update_record_current_system(client, access_token):
    """单行完成（没批量保存过）、pending 时改明细实盘数，都按录入那一刻取系统数并记录录入人"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])

    # pending 时修改明细给了实盘数 = 录入；开始盘点不覆盖已录入明细的系统数
    task_id, (d1, d2) = _cycle_task(client, w, [(g, w['std1']), (w['g2'], w['std1'])], start=False)
    _ok(client.put(f'/cyclecount/{task_id}/details/{d1}', json={'actual_quantity': 9}, headers=_h(access_token)))
    recorded = _cc(client, d1)
    assert (recorded['system'], recorded['actual'], recorded['difference']) == (10, 9, -1)
    assert recorded['operator'] is not None
    _ok(client.post('/removal/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 2, 'reason': 'damaged'},
                    headers=_h(access_token)))
    _ok(client.put(f'/cyclecount/{task_id}/process/', headers=_h(access_token)))
    assert (_cc(client, d1)['system'], _cc(client, d1)['difference']) == (10, -1)
    assert _cc(client, d2)['operator'] is None        # 未录入的只做展示快照

    # 单行完成一条没录入过的：按当前实盘（默认 0）录入，系统数取此刻（std1 没有商品 2）
    _ok(client.put(f'/cyclecount/{task_id}/details/{d2}/complete/', headers=_h(access_token)))
    done = _cc(client, d2)
    assert (done['system'], done['actual'], done['difference'], done['status']) == (0, 0, 0, 'completed')
    assert done['operator'] is not None
    _ok(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)))


def test_cycle_count_complete_requires_all_details_recorded(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 4)], [(w['g1'], w['std1'], 4)])
    task_id, (d1, d2) = _cycle_task(client, w, [(w['g1'], w['std1']), (w['g2'], w['std1'])])

    body = _err(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)), 409, 16114)
    assert body['details'] == {'detail_ids': [d1, d2]}

    # 盘到 0 件：值与默认 0 相同，明确提交了也算已录入（系统数 4、差异 −4）
    _ok(client.post(f'/cyclecount/{task_id}/details-batch-save/', json={'details': [{'id': d1, 'actual_quantity': 0}]},
                    headers=_h(access_token)))
    counted = _cc(client, d1)
    assert (counted['system'], counted['actual'], counted['difference']) == (4, 0, -4)
    assert counted['operator'] is not None and counted['status'] == 'pending'

    body = _err(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)), 409, 16114)
    assert body['details'] == {'detail_ids': [d2]}
    with client.application.app_context():
        assert db.session.get(CycleCountTask, task_id).status == 'in_progress'

    _ok(client.put(f'/cyclecount/{task_id}/details/{d2}/complete/', headers=_h(access_token)))
    assert _ok(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)))['status'] == 'completed'


def test_cancelled_adjustment_releases_cycle_count(client, access_token, access_company_admin_token):
    """由盘点单生成的调整单取消后，同一盘点单可以重新生成（16086 只看启用中的调整单）"""
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 6)], [(w['g1'], w['std1'], 6)])
    task_id, (d1,) = _cycle_task(client, w, [(w['g1'], w['std1'])])
    _ok(client.post(f'/cyclecount/{task_id}/details-batch-save/', json={'details': [{'id': d1, 'actual_quantity': 5}]},
                    headers=_h(access_token)))
    _ok(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)))
    url = f'/adjustment/create_adjustment_by_cyclecount/{task_id}'
    first = _ok(client.post(url, headers=_h(access_token)))
    _err(client.post(url, headers=_h(access_token)), 409, 16086)
    _ok(client.put(f"/adjustment/{first['id']}/cancel/", headers=_h(access_company_admin_token)))
    second = _ok(client.post(url, headers=_h(access_token)))
    assert second['id'] != first['id'] and second['cyclecount_id'] == task_id


# ---------------------------------------------------------------------------
# B-81 手工新建任务接口已去掉
# ---------------------------------------------------------------------------

def test_manual_task_creation_endpoints_removed(client, access_token):
    with client.application.app_context():
        asn_id, dn_id = get_asn().id, get_dn().id
        before = (SortingTask.query.count(), PickingTask.query.count(), PackingTask.query.count())
    for url, body in (('/sorting/', {'asn_id': asn_id}), ('/picking/', {'dn_id': dn_id}),
                      ('/packing/', {'dn_id': dn_id})):
        response = client.post(url, json=body, headers=_h(access_token))
        assert response.status_code == 405, (url, response.status_code, response.get_json())
        # 列表查询照旧
        assert client.get(url, headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        assert (SortingTask.query.count(), PickingTask.query.count(), PackingTask.query.count()) == before
    for service in (SortingTaskService, PickingTaskService, PackingTaskService):
        assert not hasattr(service, 'create_task')
    # 自动生成的函数保留
    assert hasattr(SortingTaskService, 'create_sorting_task_from_asn')
    assert hasattr(PickingTaskService, 'create_picking_task_from_dn')
    assert hasattr(PackingTaskService, 'create_packing_task_from_dn')


# ---------------------------------------------------------------------------
# B-82 删除带明细的批次
# ---------------------------------------------------------------------------

def test_delete_picking_batch_with_details(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id = _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': date.today().isoformat(), 'details': [{'goods_id': g, 'quantity': 4}],
    }, headers=_h(access_token)))['id']
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(access_token)))
    picking_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{picking_id}/process/', headers=_h(access_token)))
    batch = _ok(client.post(f'/picking/{picking_id}/batches/', json={'details': [
        {'goods_id': g, 'location_id': w['std1'], 'picked_quantity': 4}]}, headers=_h(access_token)))

    _ok(client.delete(f"/picking/{picking_id}/batches/{batch['id']}", headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(PickingBatch, batch['id']) is None
        assert PickingTaskDetail.query.filter_by(picking_task_id=picking_id).count() == 0
    # 删掉后计划量 / 库位预占随之释放：可以重新拣满 4 件并完成，DN 能取消（拣货未开始）也恢复
    _ok(client.post(f'/picking/{picking_id}/batches/', json={'details': [
        {'goods_id': g, 'location_id': w['std1'], 'picked_quantity': 4}]}, headers=_h(access_token)))
    _ok(client.put(f'/picking/{picking_id}/complete/', headers=_h(access_token)))
    stock = _inv(client, g, w['wh'])
    assert (stock['onhand_stock'], stock['picked_stock'], stock['total_stock']) == (6, 4, 10)


def test_delete_packing_and_sorting_batch_with_details(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 4)], [(g, w['std1'], 4)])
    batch = _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [
        {'goods_id': g, 'packed_quantity': 3}]}, headers=_h(access_token)))
    _ok(client.delete(f"/packing/{packing_id}/batches/{batch['id']}", headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(PackingBatch, batch['id']) is None
        assert PackingTaskDetail.query.filter_by(packing_task_id=packing_id).count() == 0
    # 已打包量随之清零：可以重新打满 4 件
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [
        {'goods_id': g, 'packed_quantity': 4}]}, headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))

    # 分拣批次
    asn_id = _create_asn(client, access_token, w, [(w['g2'], 5)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)))
    sorting_id = _task_ids(client, SortingTask, asn_id=asn_id)[0]
    _ok(client.put(f'/sorting/{sorting_id}/process/', headers=_h(access_token)))
    batch = _ok(client.post(f'/sorting/{sorting_id}/batches/', json={'details': [
        {'goods_id': w['g2'], 'sorted_quantity': 5, 'damage_quantity': 0}]}, headers=_h(access_token)))
    _ok(client.delete(f"/sorting/{sorting_id}/batches/{batch['id']}", headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(SortingBatch, batch['id']) is None
        assert SortingTaskDetail.query.filter_by(sorting_task_id=sorting_id).count() == 0
    # 累计分拣量随之清零：可以重新分拣满 5 件
    _ok(client.post(f'/sorting/{sorting_id}/batches/', json={'details': [
        {'goods_id': w['g2'], 'sorted_quantity': 5, 'damage_quantity': 0}]}, headers=_h(access_token)))


# ---------------------------------------------------------------------------
# B-83 已拣 / 已打包（未发货）的 DN 取消：货退回待上架
# ---------------------------------------------------------------------------

def _outbound(client, token, w, pick=4, pack=None, complete_packing=False):
    """g1 入库 10 件到 std1；DN 4 件拣 pick 件；pack 不为 None 时打一个批次（complete_packing 时完成打包）。
    返回 (dn_id, packing_task_id)"""
    g = w['g1']
    _inbound(client, token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, token, w, [(g, 4)], [(g, w['std1'], pick)])
    if pack:
        _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [
            {'goods_id': g, 'packed_quantity': pack}]}, headers=_h(token)))
    if complete_packing:
        _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(token)))
    return dn_id, packing_id


def _assert_cancelled_back_to_sorted(client, token, w, dn_id, before):
    """取消后：picked / packed 归零，退回的件数全部进 sorted_stock，total / onhand 不变，不变量成立"""
    g = w['g1']
    body = _ok(client.put(f'/dn/{dn_id}/cancel/', headers=_h(token)))
    assert body['status'] == 'closed'
    after = _inv(client, g, w['wh'])
    returned = before['picked_stock'] + before['packed_stock']
    assert after['picked_stock'] == 0 and after['packed_stock'] == 0
    assert after['sorted_stock'] == before['sorted_stock'] + returned
    assert (after['total_stock'], after['onhand_stock'], after['dn_stock'], after['delivered_stock']) == \
        (before['total_stock'], before['onhand_stock'], 0, before['delivered_stock'])
    _assert_consistent(after)

    with client.application.app_context():
        for model in (PickingTask, PackingTask, DeliveryTask):
            assert all(t.is_active is False for t in model.query.filter_by(dn_id=dn_id).all()), model
    # 退回的货可以上架放回库位
    _ok(client.post('/putaway/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': after['sorted_stock']},
                    headers=_h(token)))
    final = _inv(client, g, w['wh'])
    assert (final['onhand_stock'], final['sorted_stock'], final['total_stock']) == (10, 0, 10)
    _assert_consistent(final)
    return after


def test_cancel_picked_dn_returns_picked_stock(client, access_token):
    """已拣未打包：picked_stock 4 → sorted_stock 4；停用的打包任务不能再操作（16088）"""
    w = _world(client)
    dn_id, packing_id = _outbound(client, access_token, w)
    before = _inv(client, w['g1'], w['wh'])
    assert (before['onhand_stock'], before['picked_stock'], before['sorted_stock'], before['total_stock']) == (6, 4, 0, 10)

    _assert_cancelled_back_to_sorted(client, access_token, w, dn_id, before)
    _err(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': w['g1'], 'packed_quantity': 1}]},
                     headers=_h(access_token)), 409, 16088)


def test_cancel_partially_packed_dn(client, access_token):
    """打包进行中（打了 2 / 4 件、未完成，DN 仍 picked）：打包未完成不动库存，已拣的 4 件全部退回"""
    w = _world(client)
    dn_id, packing_id = _outbound(client, access_token, w, pack=2)
    before = _inv(client, w['g1'], w['wh'])
    assert (before['picked_stock'], before['packed_stock']) == (4, 0)
    after = _assert_cancelled_back_to_sorted(client, access_token, w, dn_id, before)
    assert after['sorted_stock'] == 4
    _err(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)), 409, 16088)


def test_cancel_short_packed_dn(client, access_token):
    """少打包完成（拣 4 打 3）：差额 1 件打包时已退回 sorted；取消时 packed_stock 3 件再退回，合计 sorted 4"""
    w = _world(client)
    dn_id, _ = _outbound(client, access_token, w, pack=3, complete_packing=True)
    before = _inv(client, w['g1'], w['wh'])
    assert (before['picked_stock'], before['packed_stock'], before['sorted_stock']) == (0, 3, 1)
    after = _assert_cancelled_back_to_sorted(client, access_token, w, dn_id, before)
    assert after['sorted_stock'] == 4


def test_cancel_fully_packed_dn_voids_documents_and_emits_webhook(client, access_token):
    """全部打包：packed_stock 4 → sorted_stock 4；发货任务停用（16088）；有效单证作废；推 dn.cancelled"""
    w = _world(client)
    dn_id, _ = _outbound(client, access_token, w, pack=4, complete_packing=True)
    with client.application.app_context():
        key = APIKey(key=hash_api_key(uuid.uuid4().hex), key_prefix='pg-hook', system_name='process-guard',
                     permissions=['dn_read'])
        key.company_id = get_company().id
        key.webhook_url = 'https://example.com/hook'
        key.is_active = True
        db.session.add(key)
        db.session.flush()
        dn = db.session.get(DN, dn_id)
        dn.api_key_id = key.id
        for doc_type, status in (('commercial_invoice', 'issued'), ('packing_list', 'issued'),
                                 ('commercial_invoice', 'void')):
            db.session.add(DNDocument(
                dn_id=dn_id, doc_type=doc_type, version=1 if status == 'issued' else 0, document_number='INV-1',
                invoice_date=date.today(), status=status, sha256='0' * 64, data_sha256='0' * 64, size_bytes=1,
                file_name=f'{doc_type}.pdf', content=b'x'))
        db.session.commit()
        key_id = key.id
        delivery_id = DeliveryTask.query.filter_by(dn_id=dn_id, is_active=True).one().id

    before = _inv(client, w['g1'], w['wh'])
    assert (before['picked_stock'], before['packed_stock'], before['sorted_stock']) == (0, 4, 0)
    _assert_cancelled_back_to_sorted(client, access_token, w, dn_id, before)

    with client.application.app_context():
        documents = DNDocument.query.filter_by(dn_id=dn_id).all()
        assert all(d.status == 'void' for d in documents)
        assert sorted(d.void_reason for d in documents if d.void_reason) == ['dn_cancelled', 'dn_cancelled']
        event = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.cancelled').one()
        assert event.payload['dn_id'] == dn_id and event.payload['previous_status'] == 'packed'
        assert event.payload['details'][0]['packed_quantity'] == 4
    _err(client.put(f'/delivery/{delivery_id}/process/', headers=_h(access_token)), 409, 16088)


def test_cancel_dn_with_open_carrier_shipment_rejected(client, access_token):
    w = _world(client)
    dn_id, _ = _outbound(client, access_token, w, pack=4, complete_packing=True)
    before = _inv(client, w['g1'], w['wh'])
    for status in DNCarrierShipment.OPEN_STATUSES:
        with client.application.app_context():
            DNCarrierShipment.query.filter_by(dn_id=dn_id).delete()
            record = DNCarrierShipment(dn_id=dn_id, carrier='fedex', status=status, sender_country='JP',
                                       tracking_number='7946 1111 0001')
            db.session.add(record)
            db.session.commit()
            record_id = record.id
        body = _err(client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token)), 409, 16110)
        assert body['details'] == {'shipment_id': record_id, 'status': status, 'tracking_number': '7946 1111 0001'}
        with client.application.app_context():
            assert db.session.get(DN, dn_id).status == 'packed'
        assert _inv(client, w['g1'], w['wh']) == before

    # 运单取消后可以取消 DN
    with client.application.app_context():
        DNCarrierShipment.query.filter_by(dn_id=dn_id).update({'status': 'cancelled'})
        db.session.commit()
    _assert_cancelled_back_to_sorted(client, access_token, w, dn_id, before)


def test_cancel_shipped_dn_still_rejected(client, access_token):
    w = _world(client)
    dn_id, _ = _outbound(client, access_token, w, pack=4, complete_packing=True)
    with client.application.app_context():
        task_id = DeliveryTask.query.filter_by(dn_id=dn_id, is_active=True).one().id
    _ok(client.put(f'/delivery/{task_id}/process/', headers=_h(access_token)))
    _ok(client.put(f'/delivery/{task_id}/complete/', json={'tracking_number': 'TRK-1', 'shipping_cost': 0},
                   headers=_h(access_token)))
    _err(client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token)), 409, 16052)


# ---------------------------------------------------------------------------
# B-84 库位有历史记录时不能删
# ---------------------------------------------------------------------------

def test_location_with_history_cannot_be_deleted(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 3)], [(g, w['std1'], 3)])
    _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'],
                                        'quantity': 3}, headers=_h(access_token)))
    assert _bin(client, g, w['std1']) == 0

    body = _err(client.delete(f"/location/{w['std1']}", headers=_h(access_token)), 409, 44009)
    assert body['details'] == {'entity': 'location', 'id': w['std1'],
                               'references': {'putaway_records': 1, 'transfer_records': 1}}
    with client.application.app_context():
        assert db.session.get(Location, w['std1']) is not None
    # 有货仍是 16084
    _err(client.delete(f"/location/{w['std2']}", headers=_h(access_token)), 409, 16084)
    # 停用照常
    _ok(client.put(f"/location/{w['std1']}", json={'is_active': False}, headers=_h(access_token)))


def test_location_without_history_deleted_with_empty_stock_rows(client, access_token):
    w = _world(client)
    with client.application.app_context():
        db.session.add(GoodsLocation(goods_id=w['g1'], location_id=w['std2'], quantity=0))
        db.session.commit()
    _ok(client.delete(f"/location/{w['std2']}", headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(Location, w['std2']) is None
        assert GoodsLocation.query.filter_by(location_id=w['std2']).count() == 0


# ---------------------------------------------------------------------------
# B-85 数值字段：空串 → null，非法 / 越界 → 400 16115
# ---------------------------------------------------------------------------

def test_asn_weight_volume_validation(client, access_token):
    w = _world(client)

    def create(**extra):
        return client.post('/asn/', json={'warehouse_id': w['wh'], 'supplier_id': w['supplier'],
                                          'details': [{'goods_id': w['g1'], 'quantity': 2, **extra}]},
                           headers=_h(access_token))

    # 新建时空串按「没填」处理（列默认值 0.0），不再把 "" 写进 Float 列
    asn = _ok(create(weight='', volume='  '))
    with client.application.app_context():
        detail = ASNDetail.query.filter_by(asn_id=asn['id']).one()
        assert (detail.weight, detail.volume) == (0.0, 0.0)
        detail_id = detail.id
    asn2 = _ok(create(weight='1.5', volume=0))
    with client.application.app_context():
        detail = ASNDetail.query.filter_by(asn_id=asn2['id']).one()
        assert (detail.weight, detail.volume) == (1.5, 0.0)

    for bad in ({'weight': 'abc'}, {'weight': -1}, {'volume': True}, {'volume': 'NaN'}, {'weight': [1]}):
        body = _err(create(**bad), 400, 16115)
        assert body.get('field') in ('weight', 'volume') or 'weight' in body['message'] or 'volume' in body['message']

    # 明细修改 / 整单同步也一样
    _ok(client.put(f"/asn/{asn['id']}/details/{detail_id}", json={'weight': '2.25', 'volume': ''},
                   headers=_h(access_token)))
    with client.application.app_context():
        detail = db.session.get(ASNDetail, detail_id)
        assert (detail.weight, detail.volume) == (2.25, None)
    _err(client.put(f"/asn/{asn['id']}/details/{detail_id}", json={'weight': 'x'}, headers=_h(access_token)),
         400, 16115)
    _ok(client.put(f"/asn/{asn['id']}", json={'details': [{'id': detail_id, 'quantity': 2, 'weight': '', 'volume': '3'}]},
                   headers=_h(access_token)))
    with client.application.app_context():
        detail = db.session.get(ASNDetail, detail_id)
        assert (detail.weight, detail.volume) == (None, 3.0)
    _err(client.put(f"/asn/{asn['id']}", json={'details': [{'id': detail_id, 'quantity': 2, 'volume': -0.5}]},
                    headers=_h(access_token)), 400, 16115)


def test_location_dimension_validation(client, access_token):
    w = _world(client)
    base = {'warehouse_id': w['wh'], 'location_type': 'standard'}
    created = _ok(client.post('/location/', json={**base, 'code': 'NUM-1', 'width': '', 'depth': '12.5',
                                                  'height': 30, 'capacity': None}, headers=_h(access_token)))
    assert (created.get('width'), created['depth'], created['height'], created.get('capacity')) == (None, 12.5, 30.0, None)

    for i, bad in enumerate(({'width': 0}, {'depth': -1}, {'height': 'abc'}, {'capacity': 0}, {'capacity': False})):
        body = _err(client.post('/location/', json={**base, 'code': f'NUM-BAD-{i}', **bad}, headers=_h(access_token)),
                    400, 16115)
        assert body.get('field') == next(iter(bad))
    with client.application.app_context():
        assert Location.query.filter(Location.code.like('NUM-BAD-%')).count() == 0

    location_id = created['id']
    updated = _ok(client.put(f'/location/{location_id}', json={'depth': '', 'width': '8'}, headers=_h(access_token)))
    assert (updated.get('depth'), updated['width'], updated['height']) == (None, 8.0, 30.0)
    _err(client.put(f'/location/{location_id}', json={'height': 0}, headers=_h(access_token)), 400, 16115)
    with client.application.app_context():
        assert db.session.get(Location, location_id).height == 30.0


# ---------------------------------------------------------------------------
# B-86 盘点单 / 调整单列表 keyword
# ---------------------------------------------------------------------------

def test_adjustment_and_cycle_count_list_keyword(client, access_token):
    w = _world(client)
    first = _approved_adjustment(client, access_token, None, w, [(w['g1'], w['std1'], 0, 1)], reason='Found in aisle')
    second = _approved_adjustment(client, access_token, None, w, [(w['g2'], w['std2'], 0, 2)], reason='Recount')

    def ids(keyword):
        listed = _ok(client.get('/adjustment/', query_string={'keyword': keyword, 'all': 'true'},
                                headers=_h(access_token)))
        return {a['id'] for a in listed['items']}

    assert ids('aisle') == {first}                 # 调整原因
    assert ids('GRD-2') == {second}                # 明细商品编码
    assert ids('guard-1') == {first}               # 明细商品名
    assert ids('GRD-STD2') == {second}             # 明细库位编码
    assert ids(str(second)) >= {second}            # 单号
    assert ids('no-such-thing') == set()

    task_id, _ = _cycle_task(client, w, [(w['g1'], w['std1'])], start=False)
    with client.application.app_context():
        db.session.get(CycleCountTask, task_id).task_name = 'Quarterly aisle count'
        db.session.commit()
    listed = _ok(client.get('/cyclecount/', query_string={'keyword': 'quarterly', 'all': 'true'},
                            headers=_h(access_token)))
    assert [t['id'] for t in listed['items']] == [task_id]
    listed = _ok(client.get('/cyclecount/', query_string={'keyword': 'GRD-STD1', 'all': 'true'},
                            headers=_h(access_token)))
    assert task_id in [t['id'] for t in listed['items']]
    listed = _ok(client.get('/cyclecount/', query_string={'keyword': 'zzz-none', 'all': 'true'},
                            headers=_h(access_token)))
    assert listed['items'] == []
