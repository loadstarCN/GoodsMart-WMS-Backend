"""库存类守卫回归（审计 2026-10-04：B-54 / B-55 / B-56 / B-61 / B-62 / W-27 后端）

- B-54 少打包可以完成：每个商品「已拣 − 已打包」从 picked_stock 退回 sorted_stock，DN 按实际打包量发货；
  整个任务一件没打包 → 400 16082
- B-55 库位有货时不能停用 / 改类型 / 删除 → 409 16084；上架 / 移库的目标库位必须启用 → 400 16085
- B-56 对外下架接口 reason 白名单，'picking' → 400 16083；拣货流程内部走 from_picking
- B-61 盘点批量保存含已完成明细 → 409 16017 整单不写；operator_id 只写本次实际改动的行
- B-62 同一盘点单只能有一张启用中的调整单 → 409 16086；调整单输出 cyclecount_id
- W-27 received ASN 的分拣任务不能删 → 409 16087
"""
from datetime import date

from sqlalchemy import func

from tests.helpers import *  # noqa: F401,F403
from warehouse.cyclecount.services import CycleCountTaskService


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

STOCK_FIELDS = ('total_stock', 'onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock',
                'dn_stock', 'picked_stock', 'packed_stock', 'delivered_stock')


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _ok(response):
    assert response.status_code in (200, 201), (response.status_code, response.get_json())
    return response.get_json()


def _err(response, status_code, biz_code):
    body = response.get_json()
    assert (response.status_code, body.get('code')) == (status_code, biz_code), (response.status_code, body)
    return body


def _world(client):
    """Warehouse A 下新建两个商品、两个标准库位，返回各 id。"""
    with client.application.app_context():
        admin = get_admin_user()
        warehouse = Warehouse.query.filter_by(name='Warehouse A').first()
        g1 = Goods(code='GRD-1', company_id=warehouse.company_id, name='guard-1', unit='pcs',
                   is_active=True, created_by=admin.id)
        g2 = Goods(code='GRD-2', company_id=warehouse.company_id, name='guard-2', unit='pcs',
                   is_active=True, created_by=admin.id)
        std1 = Location(warehouse_id=warehouse.id, code='GRD-STD1', location_type='standard', created_by=admin.id)
        std2 = Location(warehouse_id=warehouse.id, code='GRD-STD2', location_type='standard', created_by=admin.id)
        db.session.add_all([g1, g2, std1, std2])
        db.session.commit()
        return {
            'wh': warehouse.id, 'g1': g1.id, 'g2': g2.id, 'std1': std1.id, 'std2': std2.id, 'admin': admin.id,
            'supplier': Supplier.query.filter_by(company_id=warehouse.company_id).first().id,
            'recipient': Recipient.query.filter_by(company_id=warehouse.company_id).first().id,
        }


def _inv(client, goods_id, warehouse_id):
    with client.application.app_context():
        inventory = db.session.get(Inventory, (goods_id, warehouse_id))
        return {k: getattr(inventory, k) for k in STOCK_FIELDS}


def _assert_consistent(stock):
    """文档不变量：total_stock = 各实物分量之和，各字段 ≥ 0"""
    assert all(stock[k] >= 0 for k in STOCK_FIELDS), stock
    assert stock['total_stock'] == (stock['onhand_stock'] + stock['damage_stock'] + stock['return_stock']
                                    + stock['sorted_stock'] + stock['picked_stock'] + stock['packed_stock']), stock


def _bin(client, goods_id, location_id):
    with client.application.app_context():
        record = GoodsLocation.query.filter_by(goods_id=goods_id, location_id=location_id).first()
        return record.quantity if record else 0


def _task_ids(client, model, **filters):
    with client.application.app_context():
        return [t.id for t in model.query.filter_by(**filters).order_by(model.id).all()]


def _create_asn(client, token, w, lines):
    return _ok(client.post('/asn/', json={
        'warehouse_id': w['wh'], 'supplier_id': w['supplier'],
        'details': [{'goods_id': g, 'quantity': q} for g, q in lines],
    }, headers=_h(token)))['id']


def _inbound(client, token, w, lines, putaway):
    """ASN 收货 → 分拣（全部合格）→ 上架。lines: [(goods_id, qty)]，putaway: [(goods_id, location_id, qty)]"""
    asn_id = _create_asn(client, token, w, lines)
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(token)))
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': g, 'sorted_quantity': q, 'damage_quantity': 0} for g, q in lines]}, headers=_h(token)))
    _ok(client.put(f'/sorting/{task_id}/complete/', headers=_h(token)))
    for goods_id, location_id, quantity in putaway:
        _ok(client.post('/putaway/', json={'goods_id': goods_id, 'location_id': location_id, 'quantity': quantity},
                        headers=_h(token)))
    return asn_id


def _picked_dn(client, token, w, lines, picks):
    """建 DN → 开始 → 拣货完成，返回 (dn_id, packing_task_id)。picks: [(goods_id, location_id, qty)]"""
    dn_id = _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': warehouse_today().isoformat(),
        'details': [{'goods_id': g, 'quantity': q} for g, q in lines],
    }, headers=_h(token)))['id']
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(token)))
    picking_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{picking_id}/process/', headers=_h(token)))
    _ok(client.post(f'/picking/{picking_id}/batches/', json={'details': [
        {'goods_id': g, 'location_id': l, 'picked_quantity': q} for g, l, q in picks]}, headers=_h(token)))
    _ok(client.put(f'/picking/{picking_id}/complete/', headers=_h(token)))
    packing_id = _task_ids(client, PackingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/packing/{packing_id}/process/', headers=_h(token)))
    return dn_id, packing_id


def _dn_detail(client, dn_id, goods_id):
    with client.application.app_context():
        d = DNDetail.query.filter_by(dn_id=dn_id, goods_id=goods_id).first()
        return {'picked': d.picked_quantity, 'packed': d.packed_quantity, 'delivered': d.delivered_quantity}


# ---------------------------------------------------------------------------
# B-54 少打包 / 不打包
# ---------------------------------------------------------------------------

def test_short_pack_returns_difference_to_sorted_stock(client, access_token):
    """拣 5 打 3 完成：picked_stock 归零、sorted_stock +2、total_stock 不变；DN 按 3 件发货，2 件可上架放回"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])

    # 拣货下架仍走内部 from_picking：记 reason='picking'，货进 picked_stock 而不是 sorted_stock
    with client.application.app_context():
        record = RemovalRecord.query.filter_by(goods_id=g, location_id=w['std1']).order_by(RemovalRecord.id.desc()).first()
        assert record.reason == 'picking' and record.quantity == 5

    before = _inv(client, g, w['wh'])
    assert (before['picked_stock'], before['sorted_stock'], before['onhand_stock'], before['total_stock']) == (5, 0, 5, 10)

    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': g, 'packed_quantity': 3}]},
                    headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))

    after = _inv(client, g, w['wh'])
    assert after['picked_stock'] == 0
    assert after['sorted_stock'] == before['sorted_stock'] + 2
    assert after['packed_stock'] == 3
    assert after['total_stock'] == before['total_stock']
    _assert_consistent(after)
    assert _dn_detail(client, dn_id, g) == {'picked': 5, 'packed': 3, 'delivered': 0}

    # 发货 / 签收按实际打包量 3 件
    delivery_id = _task_ids(client, DeliveryTask, dn_id=dn_id)[0]
    _ok(client.put(f'/delivery/{delivery_id}/process/', headers=_h(access_token)))
    _ok(client.put(f'/delivery/{delivery_id}/complete/', json={}, headers=_h(access_token)))
    assert _dn_detail(client, dn_id, g)['delivered'] == 3
    _ok(client.put(f'/delivery/{delivery_id}/sign/', json={}, headers=_h(access_token)))

    final = _inv(client, g, w['wh'])
    assert (final['packed_stock'], final['delivered_stock'], final['picked_stock']) == (0, 0, 0)
    assert final['total_stock'] == 7          # 库位 5 + 待上架 2
    _assert_consistent(final)

    # 差额 2 件走上架放回库位
    _ok(client.post('/putaway/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 2},
                    headers=_h(access_token)))
    back = _inv(client, g, w['wh'])
    assert (back['onhand_stock'], back['sorted_stock'], back['total_stock']) == (7, 0, 7)
    assert _bin(client, g, w['std1']) == 7
    _assert_consistent(back)


def test_pack_one_goods_only_returns_other_goods_in_full(client, access_token):
    """按商品逐个结算：只打包了商品 1，商品 2 已拣的全部退回待上架，DN 里商品 2 发 0 件"""
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    _inbound(client, access_token, w, [(g1, 10), (g2, 10)], [(g1, w['std1'], 10), (g2, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g1, 4), (g2, 6)],
                                   [(g1, w['std1'], 4), (g2, w['std1'], 6)])
    before2 = _inv(client, g2, w['wh'])

    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': g1, 'packed_quantity': 4}]},
                    headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))

    s1, s2 = _inv(client, g1, w['wh']), _inv(client, g2, w['wh'])
    assert (s1['picked_stock'], s1['packed_stock'], s1['sorted_stock']) == (0, 4, 0)
    assert (s2['picked_stock'], s2['packed_stock'], s2['sorted_stock']) == (0, 0, before2['sorted_stock'] + 6)
    assert s2['total_stock'] == before2['total_stock']
    _assert_consistent(s1)
    _assert_consistent(s2)
    assert _dn_detail(client, dn_id, g2) == {'picked': 6, 'packed': 0, 'delivered': 0}


def test_pack_nothing_rejected(client, access_token):
    """整个打包任务一件没打包 → 400 16082，任务 / DN / 库存都不动"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])
    before = _inv(client, g, w['wh'])

    _err(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)), 400, 16082)

    # 只有空批次（没有明细）同样拒绝
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': []}, headers=_h(access_token)))
    _err(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)), 400, 16082)

    assert _inv(client, g, w['wh']) == before
    with client.application.app_context():
        assert db.session.get(PackingTask, packing_id).status == 'in_progress'
        assert db.session.get(DN, dn_id).status == 'picked'
        assert DeliveryTask.query.filter_by(dn_id=dn_id).count() == 0


def test_dn_packed_service_defaults_and_guards(client):
    """InventoryService.dn_packed：不传已拣量时与旧行为一致；打包量超过已拣量 → 16043"""
    from extensions.error import APIException
    w = _world(client)
    with client.application.app_context():
        add_stock(w['g1'], w['wh'], picked=5)
        InventoryService.dn_packed(w['g1'], w['wh'], 2)              # 旧调用：已拣量缺省 = 打包量
        InventoryService.dn_packed(w['g1'], w['wh'], 1, 3)           # 少打包 2 件退回待上架
        inv = db.session.get(Inventory, (w['g1'], w['wh']))
        assert (inv.picked_stock, inv.packed_stock, inv.sorted_stock, inv.total_stock) == (0, 3, 2, 5)
        with pytest.raises(APIException) as exc:
            InventoryService.dn_packed(w['g1'], w['wh'], 4, 3)
        assert exc.value.biz_code == 16043


# ---------------------------------------------------------------------------
# B-55 库位停用 / 改类型 / 删除；上架 / 移库目标库位必须启用
# ---------------------------------------------------------------------------

def test_location_with_stock_cannot_be_deactivated_or_retyped(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 6), (g, w['std2'], 4)])
    url = f"/location/{w['std1']}"

    body = _err(client.put(url, json={'is_active': False}, headers=_h(access_token)), 409, 16084)
    assert body['details'] == {'location_id': w['std1'], 'quantity': 6}
    _err(client.put(url, json={'location_type': 'damaged'}, headers=_h(access_token)), 409, 16084)

    # Web 编辑页整表提交：类型 / 启用状态没变时照常保存
    location = _ok(client.get(url, headers=_h(access_token)))
    updated = _ok(client.put(url, json={**{k: location[k] for k in ('code', 'location_type', 'is_active')},
                                        'description': 'renamed'}, headers=_h(access_token)))
    assert (updated['description'], updated['is_active'], updated['location_type']) == ('renamed', True, 'standard')

    # 移走全部存货后可以停用、改类型
    _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'],
                                        'quantity': 6}, headers=_h(access_token)))
    assert _ok(client.put(url, json={'location_type': 'damaged'}, headers=_h(access_token)))['location_type'] == 'damaged'
    assert _ok(client.put(url, json={'is_active': False}, headers=_h(access_token)))['is_active'] is False

    stock = _inv(client, g, w['wh'])
    assert (stock['onhand_stock'], stock['total_stock']) == (10, 10)
    _assert_consistent(stock)


def test_location_with_stock_cannot_be_deleted(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 3)], [(g, w['std1'], 3)])

    _err(client.delete(f"/location/{w['std1']}", headers=_h(access_token)), 409, 16084)
    with client.application.app_context():
        assert db.session.get(Location, w['std1']) is not None
    assert _bin(client, g, w['std1']) == 3

    # 空库位照常删除
    _ok(client.delete(f"/location/{w['std2']}", headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(Location, w['std2']) is None


def test_putaway_and_transfer_into_inactive_location_rejected(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 8)])   # 2 件留在待上架
    _ok(client.put(f"/location/{w['std2']}", json={'is_active': False}, headers=_h(access_token)))
    before = _inv(client, g, w['wh'])

    _err(client.post('/putaway/', json={'goods_id': g, 'location_id': w['std2'], 'quantity': 2},
                     headers=_h(access_token)), 400, 16085)
    _err(client.post('/putaway/bulk', json=[{'goods_id': g, 'location_id': w['std2'], 'quantity': 2}],
                     headers=_h(access_token)), 400, 16085)
    _err(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'],
                                         'quantity': 3}, headers=_h(access_token)), 400, 16085)
    _err(client.post('/transfer/bulk', json=[{'goods_id': g, 'from_location_id': w['std1'],
                                              'to_location_id': w['std2'], 'quantity': 3}],
                     headers=_h(access_token)), 400, 16085)

    assert _inv(client, g, w['wh']) == before
    assert (_bin(client, g, w['std1']), _bin(client, g, w['std2'])) == (8, 0)

    # 从启用库位移出 / 上架到启用库位不受影响
    _ok(client.post('/putaway/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 2},
                    headers=_h(access_token)))
    _assert_consistent(_inv(client, g, w['wh']))


def test_putaway_rechecks_location_after_concurrent_deactivation(client, access_token):
    """上架先锁目标库位并重读：会话 2 内存里的库位仍是启用状态，会话 1 已停用并提交 → 400 16085"""
    from extensions.error import APIException
    from warehouse.location.services import LocationService
    from warehouse.putaway.services import PutawayService
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 5)], [])          # 5 件在待上架区

    app = client.application
    ctx2 = app.app_context()
    ctx2.push()
    try:
        assert db.session.get(Location, w['std2']).is_active     # 会话 2 先读到启用中的库位
        ctx1 = app.app_context()
        ctx1.push()
        try:
            LocationService.update_location(w['std2'], {'is_active': False})
        finally:
            ctx1.pop()
        with pytest.raises(APIException) as exc:
            PutawayService.create_putaway_record({'goods_id': g, 'location_id': w['std2'], 'quantity': 5}, w['admin'])
        assert (exc.value.status_code, exc.value.biz_code) == (400, 16085)
    finally:
        ctx2.pop()
    assert _bin(client, g, w['std2']) == 0
    assert _inv(client, g, w['wh'])['sorted_stock'] == 5


# ---------------------------------------------------------------------------
# B-56 下架 reason 白名单
# ---------------------------------------------------------------------------

def test_removal_api_rejects_picking_reason(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    before = _inv(client, g, w['wh'])

    for reason in ('picking', 'PICKING', 'unknown', 123, ['damage']):
        _err(client.post('/removal/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 4, 'reason': reason},
                         headers=_h(access_token)), 400, 16083)
    # bulk 里混一条 picking：整单拒绝，前面合法的一条也不落库
    _err(client.post('/removal/bulk', json=[
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 1, 'reason': 'damage'},
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 4, 'reason': 'picking'},
    ], headers=_h(access_token)), 400, 16083)

    assert _inv(client, g, w['wh']) == before
    assert _bin(client, g, w['std1']) == 10
    with client.application.app_context():
        assert RemovalRecord.query.filter_by(goods_id=g).count() == 0


def test_removal_api_accepts_web_and_app_reasons(client, access_token):
    """Web（damaged / returned / transfer / picked / expired / lost / other）与 APP（return / damage / transfer）的取值"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 20)], [(g, w['std1'], 20)])
    reasons = ['damaged', 'returned', 'transfer', 'picked', 'expired', 'lost', 'other', 'return', 'damage']

    _ok(client.post('/removal/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 1, 'reason': reasons[0]},
                    headers=_h(access_token)))
    _ok(client.post('/removal/bulk', json=[
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 1, 'reason': r} for r in reasons[1:]
    ], headers=_h(access_token)))

    stock = _inv(client, g, w['wh'])
    # 普通下架进待上架区，总量不变
    assert (stock['onhand_stock'], stock['sorted_stock'], stock['total_stock']) == (11, 9, 20)
    _assert_consistent(stock)


# ---------------------------------------------------------------------------
# B-61 盘点批量保存
# ---------------------------------------------------------------------------

def _counting_task(client, w):
    """盘点单（两行：商品 1 / 商品 2 @ std1）开始盘点，返回 (task_id, detail_id_1, detail_id_2)"""
    with client.application.app_context():
        task = CycleCountTaskService.create_task({
            'task_name': 'guard', 'warehouse_id': w['wh'],
            'task_details': [{'goods_id': w['g1'], 'location_id': w['std1']},
                             {'goods_id': w['g2'], 'location_id': w['std1']}],
        }, w['admin'])
        task_id = task.id
        CycleCountTaskService.process_task(task_id, w['admin'])
        ids = sorted(d.id for d in db.session.get(CycleCountTask, task_id).task_details)
        return task_id, ids[0], ids[1]


def _cc_detail(client, detail_id):
    with client.application.app_context():
        d = db.session.get(CycleCountTaskDetail, detail_id)
        return {'actual': d.actual_quantity, 'difference': d.difference, 'operator': d.operator_id, 'status': d.status}


def test_batch_save_with_completed_detail_rejected(client, access_token):
    w = _world(client)
    task_id, d1, d2 = _counting_task(client, w)
    url = f'/cyclecount/{task_id}/details-batch-save/'
    _ok(client.post(url, json={'details': [{'id': d1, 'actual_quantity': 5}]}, headers=_h(access_token)))
    _ok(client.put(f'/cyclecount/{task_id}/details/{d1}/complete/', headers=_h(access_token)))
    snapshot = (_cc_detail(client, d1), _cc_detail(client, d2))

    # 含已完成明细 → 整单 409，未完成的那一行也不写
    body = _err(client.post(url, json={'details': [{'id': d2, 'actual_quantity': 7}, {'id': d1, 'actual_quantity': 9}]},
                            headers=_h(access_token)), 409, 16017)
    assert body['details'] == {'detail_ids': [d1]}
    assert (_cc_detail(client, d1), _cc_detail(client, d2)) == snapshot

    # 值没变也算包含已完成明细
    _err(client.post(url, json={'details': [{'id': d1, 'actual_quantity': 5}]}, headers=_h(access_token)), 409, 16017)

    # 重复完成同一明细 → 409 16017
    _err(client.put(f'/cyclecount/{task_id}/details/{d1}/complete/', headers=_h(access_token)), 409, 16017)

    # 只提交未完成的行照常保存
    _ok(client.post(url, json={'details': [{'id': d2, 'actual_quantity': 7}]}, headers=_h(access_token)))
    assert _cc_detail(client, d2)['actual'] == 7


def test_batch_save_writes_operator_only_on_changed_rows(client):
    w = _world(client)
    task_id, d1, d2 = _counting_task(client, w)
    with client.application.app_context():
        operator_b = get_operator_user().id
        assert operator_b != w['admin']
        # A 录了第 1 行
        CycleCountTaskService.batch_save_task_details(task_id, [{'id': d1, 'actual_quantity': 5}], w['admin'])
        # B 打开时第 1 行已是 5，整单提交：第 1 行没变，只有第 2 行是 B 的改动
        CycleCountTaskService.batch_save_task_details(
            task_id, [{'id': d1, 'actual_quantity': 5}, {'id': d2, 'actual_quantity': 7}], operator_b)

    first, second = _cc_detail(client, d1), _cc_detail(client, d2)
    assert (first['actual'], first['operator']) == (5, w['admin'])
    assert (second['actual'], second['operator']) == (7, operator_b)
    assert second['difference'] == 7 - 0      # std1 上没有商品 2，账面 0


# ---------------------------------------------------------------------------
# B-62 同一盘点单重复生成调整单
# ---------------------------------------------------------------------------

def test_adjustment_from_same_cyclecount_only_once(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    task_id, d1, d2 = _counting_task(client, w)
    # 盘点完成要求每行都已录入（B-80）：第 2 行盘到 0 件也要明确提交
    _ok(client.post(f'/cyclecount/{task_id}/details-batch-save/', json={'details': [
        {'id': d1, 'actual_quantity': 8}, {'id': d2, 'actual_quantity': 0}]}, headers=_h(access_token)))
    _ok(client.put(f'/cyclecount/{task_id}/complete/', headers=_h(access_token)))

    url = f'/adjustment/create_adjustment_by_cyclecount/{task_id}'
    first = _ok(client.post(url, headers=_h(access_token)))
    assert first['cyclecount_id'] == task_id

    body = _err(client.post(url, headers=_h(access_token)), 409, 16086)
    assert body['details'] == {'adjustment_id': first['id']}
    with client.application.app_context():
        assert Adjustment.query.filter_by(cyclecount_id=task_id).count() == 1

    # 详情 / 列表输出 cyclecount_id；手工建的调整单为 null（列表项 skip_none，null 时不出现该键）
    detail = _ok(client.get(f"/adjustment/{first['id']}", headers=_h(access_token)))
    assert detail['cyclecount_id'] == task_id
    listed = _ok(client.get('/adjustment/?per_page=100', headers=_h(access_token)))['items']
    assert {a['id']: a.get('cyclecount_id') for a in listed}[first['id']] == task_id
    assert any(a.get('cyclecount_id') is None for a in listed)

    # 删掉（仍 pending 的）调整单后可以重新生成
    _ok(client.delete(f"/adjustment/{first['id']}", headers=_h(access_token)))
    again = _ok(client.post(url, headers=_h(access_token)))
    assert again['cyclecount_id'] == task_id
    with client.application.app_context():
        assert Adjustment.query.filter_by(cyclecount_id=task_id).count() == 1


def test_adjustment_cyclecount_id_not_writable_by_client(client, access_token):
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        cyclecount_id = get_cyclecount_task().id
    created = _ok(client.post('/adjustment/', json={'warehouse_id': warehouse_id, 'adjustment_reason': 'manual',
                                                    'cyclecount_id': cyclecount_id}, headers=_h(access_token)))
    assert created['cyclecount_id'] is None


# ---------------------------------------------------------------------------
# W-27 分拣任务删除
# ---------------------------------------------------------------------------

def test_sorting_task_of_received_asn_cannot_be_deleted(client, access_token):
    w = _world(client)
    asn_id = _create_asn(client, access_token, w, [(w['g1'], 5)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]

    body = _err(client.delete(f'/sorting/{task_id}', headers=_h(access_token)), 409, 16087)
    assert body['details'] == {'asn_id': asn_id}
    with client.application.app_context():
        assert db.session.get(SortingTask, task_id) is not None
        assert db.session.get(ASN, asn_id).status == 'received'

    # 分拣开始后仍是原来的 400 16002
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(access_token)))
    _err(client.delete(f'/sorting/{task_id}', headers=_h(access_token)), 400, 16002)


def test_sorting_task_of_pending_asn_can_be_deleted(client, access_token):
    """收货前手工建的分拣任务（ASN 仍 pending；手工新建接口已去掉，这里直接写库模拟存量数据）照常可删"""
    w = _world(client)
    asn_id = _create_asn(client, access_token, w, [(w['g1'], 5)])
    with client.application.app_context():
        task = SortingTask(asn_id=asn_id, status='pending', is_active=True, created_by=w['admin'])
        db.session.add(task)
        db.session.commit()
        task_id = task.id
    _ok(client.delete(f'/sorting/{task_id}', headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(SortingTask, task_id) is None
