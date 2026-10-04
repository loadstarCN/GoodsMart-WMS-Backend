"""任务生命周期规则（2026-10-04 用户决定）

- B-113 拣货一件都没拣不能完成 → 409 16089；修复前留下的「picked 但一件没拣」DN 允许取消
- B-111 DN / ASN 取消后下属任务被停用，停用的任务一律不能再操作 → 409 16088
- B-114 已确认作废（dismissed）的自动运单号与已取消的一样不能再用 → 409 16078
- 分拣任务：已收货 ASN 的最后一个启用中的分拣任务不能删（16087），还有其他启用中的分拣任务时可以删
- 发货任务只由打包完成自动生成：POST /delivery/ 已去掉（见 test_delivery.py / test_delivery_task_guards.py）
"""
from .helpers import *
from .test_customs import _create_dn, _force_packed, _prepare

from warehouse.dn.models import DNCarrierShipment
from warehouse.dn.services import DNService


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _pending_picking_task(client):
    """把种子 DN（pending）推进到 in_progress，返回 (dn_id, 拣货任务 id)"""
    with client.application.app_context():
        dn = get_dn()
        DNService.progress_dn(dn.id)
        task = PickingTask.query.filter_by(dn_id=dn.id, is_active=True).one()
        return dn.id, task.id


# ---------------------------------------------------------------------------
# B-113
# ---------------------------------------------------------------------------

def test_picking_with_nothing_picked_cannot_complete(client, access_token):
    dn_id, task_id = _pending_picking_task(client)
    assert client.put(f'/picking/{task_id}/process/', headers=_h(access_token)).status_code == 200

    response = client.put(f'/picking/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 16089
    with client.application.app_context():
        assert get_picking_task_by_id(task_id).status == 'in_progress'
        assert get_dn_by_id(dn_id).status == 'in_progress'


def test_legacy_picked_nothing_dn_can_be_cancelled(client, access_token):
    """修复前允许拣货 0 件完成：DN 停在 picked、打包任务永远完成不了 → 允许取消"""
    with client.application.app_context():
        admin = get_admin_user()
        dn = get_dn()
        dn_id, warehouse_id = dn.id, dn.warehouse_id
        goods_ids = [d.goods_id for d in dn.details]
        dn.status = 'picked'
        for detail in dn.details:
            detail.picked_quantity = 0
        picking = PickingTask(dn_id=dn_id, status='completed', is_active=True, created_by=admin.id)
        packing = PackingTask(dn_id=dn_id, status='pending', is_active=True, created_by=admin.id)
        db.session.add_all([picking, packing])
        db.session.commit()
        picking_id, packing_id = picking.id, packing.id
        for goods_id in goods_ids:
            InventoryService.update_and_calculate_dn_stock(goods_id, warehouse_id)
        db.session.commit()
        before = {g: get_inventory_by_goods_id_and_warehouse_id(g, warehouse_id).dn_stock for g in goods_ids}

    response = client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'closed'

    with client.application.app_context():
        assert db.session.get(PickingTask, picking_id).is_active is False
        assert db.session.get(PackingTask, packing_id).is_active is False
        # picked 状态本就不再预占，取消前后 dn_stock 不变
        after = {g: get_inventory_by_goods_id_and_warehouse_id(g, warehouse_id).dn_stock for g in goods_ids}
        assert after == before

    # 停用的打包任务不能再操作（B-111）
    response = client.put(f'/packing/{packing_id}/process/', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16088


# ---------------------------------------------------------------------------
# B-111
# ---------------------------------------------------------------------------

def test_tasks_of_cancelled_dn_are_rejected(client, access_token):
    dn_id, task_id = _pending_picking_task(client)
    response = client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()

    for method, url in (('PUT', f'/picking/{task_id}/process/'), ('PUT', f'/picking/{task_id}/complete/'),
                        ('DELETE', f'/picking/{task_id}')):
        response = client.open(url, method=method, headers=_h(access_token))
        assert response.status_code == 409, (url, response.get_json())
        assert response.get_json()['code'] == 16088
        assert response.get_json()['details'] == {'task_id': task_id}


def test_tasks_of_cancelled_asn_are_rejected(client, access_token):
    response = client.post('/asn/', headers=_h(access_token), json={
        'warehouse_id': 1, 'supplier_id': 1, 'details': [{'goods_id': 1, 'quantity': 5}]})
    assert response.status_code == 201, response.get_json()
    asn_id = response.get_json()['id']
    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        task_id = SortingTask.query.filter_by(asn_id=asn_id).one().id
    response = client.put(f'/asn/{asn_id}/cancel/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()

    response = client.put(f'/sorting/{task_id}/process/', headers=_h(access_token))
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 16088


# ---------------------------------------------------------------------------
# 分拣任务删除
# ---------------------------------------------------------------------------

def test_duplicate_sorting_task_of_received_asn_can_be_deleted(client, access_token):
    response = client.post('/asn/', headers=_h(access_token), json={
        'warehouse_id': 1, 'supplier_id': 1, 'details': [{'goods_id': 1, 'quantity': 5}]})
    asn_id = response.get_json()['id']
    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        admin = get_admin_user()
        original_id = SortingTask.query.filter_by(asn_id=asn_id).one().id
        duplicate = SortingTask(asn_id=asn_id, status='pending', is_active=True, created_by=admin.id)
        db.session.add(duplicate)
        db.session.commit()
        duplicate_id = duplicate.id

    response = client.delete(f'/sorting/{duplicate_id}', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()

    # 只剩最后一个：删掉后 ASN 永远停在 received → 16087
    response = client.delete(f'/sorting/{original_id}', headers=_h(access_token))
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 16087


# ---------------------------------------------------------------------------
# B-114
# ---------------------------------------------------------------------------

def test_dismissed_shipment_tracking_number_rejected(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DISMISSED').get_json()['id']
    task_id = _force_packed(client, dn_id)
    with client.application.app_context():
        db.session.add(DNCarrierShipment(dn_id=dn_id, carrier='fedex', status='dismissed', sender_country='JP',
                                         tracking_number='7946 0000 0099'))
        db.session.commit()

    for url, body in ((f'/delivery/{task_id}/tracking', {'tracking_number': '794600000099'}),
                      (f'/delivery/{task_id}', {'tracking_number': '7946-0000-0099'})):
        response = client.put(url, json=body, headers=_h(access_token))
        assert response.status_code == 409, (url, response.get_json())
        assert response.get_json()['code'] == 16078
        details = response.get_json()['details']
        assert details['status'] == 'cancelled' and details['record_status'] == 'dismissed'
        assert details['tracking_number'] is None and details['cancelled_tracking_number'] == '7946 0000 0099'

    # 其它号码照常
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'OTHER-1'}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
