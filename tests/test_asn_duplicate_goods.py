"""ASN 同一商品多行明细（B-49）

- 新建 / 加明细 / 改明细 / 整单同步：同一 ASN 内同一商品只能一行 → 400 16025（与 DN 一致）
- 存量单据已有重复行时，分拣量只记到该商品第一行，sorted_stock 不重复入账
"""
from .helpers import *


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _inventory(client, goods_id, warehouse_id):
    with client.application.app_context():
        inventory = Inventory.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id).first()
        return {k: getattr(inventory, k) for k in ('sorted_stock', 'received_stock', 'total_stock')}


def _create_asn(client, token, details):
    return client.post('/asn/', headers=_h(token), json={'warehouse_id': 1, 'supplier_id': 1, 'details': details})


def test_create_rejects_duplicate_goods(client, access_token):
    response = _create_asn(client, access_token, [{'goods_id': 1, 'quantity': 30}, {'goods_id': 1, 'quantity': 20}])
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16025


def test_add_and_update_detail_reject_duplicate_goods(client, access_token):
    response = _create_asn(client, access_token, [{'goods_id': 1, 'quantity': 30}, {'goods_id': 2, 'quantity': 5}])
    assert response.status_code == 201, response.get_json()
    asn_id = response.get_json()['id']

    response = client.post(f'/asn/{asn_id}/details/', headers=_h(access_token), json={'goods_id': 1, 'quantity': 20})
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16025

    with client.application.app_context():
        detail_2 = ASNDetail.query.filter_by(asn_id=asn_id, goods_id=2).first().id
    response = client.put(f'/asn/{asn_id}/details/{detail_2}', headers=_h(access_token), json={'goods_id': 1})
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16025


def test_sync_rejects_duplicate_goods(client, access_token):
    response = _create_asn(client, access_token, [{'goods_id': 1, 'quantity': 30}])
    assert response.status_code == 201, response.get_json()
    asn_id = response.get_json()['id']
    detail_id = response.get_json()['details'][0]['id']

    response = client.put(f'/asn/{asn_id}', headers=_h(access_token), json={
        'details': [{'id': detail_id, 'quantity': 30}, {'goods_id': 1, 'quantity': 20}]})
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16025
    with client.application.app_context():
        assert ASNDetail.query.filter_by(asn_id=asn_id).count() == 1


def test_legacy_duplicate_lines_are_counted_once(client, access_token):
    """存量 ASN（修复前已入库的重复行）：分拣 50 件只增加 50 件 sorted_stock"""
    response = _create_asn(client, access_token, [{'goods_id': 1, 'quantity': 30}])
    assert response.status_code == 201, response.get_json()
    asn_id = response.get_json()['id']
    with client.application.app_context():
        admin = get_admin_user()
        db.session.add(ASNDetail(asn_id=asn_id, goods_id=1, quantity=20, created_by=admin.id))
        db.session.commit()

    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        task_id = SortingTask.query.filter_by(asn_id=asn_id).first().id
    assert client.put(f'/sorting/{task_id}/process/', headers=_h(access_token)).status_code == 200
    response = client.post(f'/sorting/{task_id}/batches/', headers=_h(access_token), json={
        'details': [{'goods_id': 1, 'sorted_quantity': 50, 'damage_quantity': 0}]})
    assert response.status_code == 201, response.get_json()

    before = _inventory(client, 1, 1)
    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    after = _inventory(client, 1, 1)

    assert after['sorted_stock'] - before['sorted_stock'] == 50
    assert after['total_stock'] - before['total_stock'] == 50
    with client.application.app_context():
        details = sorted(db.session.get(ASN, asn_id).details, key=lambda d: d.id)
        assert [d.actual_quantity for d in details] == [50, 0]
