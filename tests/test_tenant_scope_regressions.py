"""2026-10-04 审计的租户隔离 / 越权回归用例

- B-47 绑定公司、但公司没有启用仓库的 API Key：仓库级列表不能退化成不加过滤（全平台数据）→ 403
- B-48 JWT 建 DN 不能自带 api_key_id；单据来源 Key 必须属于单据公司
- B-51 只有 staff_edit 的员工不能管理权限更高的员工，也不能把仓库范围扩到自己之外
- B-52 统计缓存键带调用方仓库范围，不跨公司串数据
- B-53 调整单 / 盘点单明细的 goods_id 必须属于单据仓库所在公司
"""
import uuid

import pytest
from flask import g

from .helpers import *
from extensions import cache
from extensions.error import ForbiddenException
from system.webhook.models import WebhookEvent
from warehouse.common import add_warehouse_filter, require_source_api_key


def _h(token, wh=None):
    headers = {'Authorization': f'Bearer {token}'}
    if wh is not None:
        headers['X-WAREHOUSE-ID'] = str(wh)
    return headers


def _api_key(company_id, permissions, webhook_url=None):
    plain = uuid.uuid4().hex + uuid.uuid4().hex
    key = APIKey(key=hash_api_key(plain), key_prefix=plain[:8], system_name='sys-' + plain[:6],
                 permissions=permissions)
    key.company_id = company_id
    key.webhook_url = webhook_url
    key.is_active = True
    db.session.add(key)
    db.session.commit()
    return key.id, plain


def _role(name, permissions):
    role = Role(name=name, description=name, is_active=True)
    db.session.add(role)
    for name_ in permissions:
        perm = Permission.query.filter_by(name=name_).first()
        if perm is None:
            perm = Permission(name=name_, description=name_)
            db.session.add(perm)
        role.permissions.append(perm)
    db.session.commit()
    return role


def _staff(user_name, company_id, role, warehouses):
    admin = get_admin_user()
    staff = Staff(user_name=user_name, email=f'{user_name}@test.com', is_active=True,
                  company_id=company_id, created_by=admin.id)
    staff.set_password('password')
    staff.roles.append(role)
    for warehouse in warehouses:
        staff.warehouses.append(warehouse)
    db.session.add(staff)
    db.session.commit()
    return staff


def _company_b_admin(client):
    """Company B 的 company_admin + 仓库 B，返回 JWT"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        warehouse_b = Warehouse(name='Warehouse B', address='B', phone='1', zip_code='1',
                                company_id=company_b.id, created_by=admin.id)
        db.session.add(warehouse_b)
        db.session.flush()
        role = Role.query.filter_by(name='company_admin').first()
        staff = _staff('b_admin', company_b.id, role, [warehouse_b])
        return create_access_token(identity=staff)


# ---------------------------------------------------------------------------
# B-47
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/inventory/', '/asn/', '/dn/', '/delivery/', '/asn/status-overview-stats'])
def test_api_key_of_company_without_warehouses_is_rejected(client, path):
    with client.application.app_context():
        company_b = Company.query.filter_by(name='Company B').first()
        assert not [w for w in company_b.warehouses if w.is_active]
        _, plain = _api_key(company_b.id, ['inventory_read', 'asn_read', 'dn_read', 'delivery_read'])

    response = client.get(path, headers={'X-API-KEY': plain})
    assert response.status_code == 403, response.get_json()
    assert response.get_json()['code'] == 12001


def test_api_key_of_company_whose_warehouses_are_all_inactive_is_rejected(client):
    with client.application.app_context():
        company = get_company()
        for warehouse in company.warehouses:
            warehouse.is_active = False
        db.session.commit()
        _, plain = _api_key(company.id, ['inventory_read'])

    response = client.get('/inventory/', headers={'X-API-KEY': plain})
    assert response.status_code == 403
    assert response.get_json()['code'] == 12001


def test_api_key_of_company_with_warehouses_still_lists_own_data(client):
    with client.application.app_context():
        company = get_company()
        warehouse_ids = [w.id for w in company.warehouses if w.is_active]
        own_total = Inventory.query.filter(Inventory.warehouse_id.in_(warehouse_ids)).count()
        _, plain = _api_key(company.id, ['inventory_read'])

    response = client.get('/inventory/?per_page=100', headers={'X-API-KEY': plain})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['total'] == own_total > 0


def test_add_warehouse_filter_fails_closed_on_empty_scope(client):
    with client.application.test_request_context('/'):
        g.accessible_warehouses = []
        with pytest.raises(ForbiddenException):
            add_warehouse_filter({})


def test_add_warehouse_filter_unscoped_caller_is_unfiltered(client):
    with client.application.test_request_context('/'):
        assert add_warehouse_filter({}) == {}


# ---------------------------------------------------------------------------
# B-48
# ---------------------------------------------------------------------------

def _create_dn(client, token, **extra):
    body = {
        'recipient_id': 1, 'shipping_address': 'X', 'expected_shipping_date': warehouse_today().isoformat(),
        'warehouse_id': 1, 'details': [{'goods_id': 2, 'quantity': 1}],
    }
    body.update(extra)
    response = client.post('/dn/', json=body, headers=_h(token))
    assert response.status_code == 201, response.get_json()
    return response.get_json()['id']


def test_jwt_dn_cannot_carry_api_key_id(client, access_company_admin_token):
    with client.application.app_context():
        company_b = Company.query.filter_by(name='Company B').first()
        other_key_id, _ = _api_key(company_b.id, ['dn_read'], webhook_url='https://b.example.com/hook')

    dn_id = _create_dn(client, access_company_admin_token, api_key_id=other_key_id, order_number='B-ORDER-1')
    with client.application.app_context():
        assert db.session.get(DN, dn_id).api_key_id is None

    response = client.put(f'/dn/{dn_id}/progress/', headers=_h(access_company_admin_token))
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        assert WebhookEvent.query.filter_by(api_key_id=other_key_id).count() == 0


def test_source_api_key_must_belong_to_document_company(client):
    with client.application.app_context():
        company_a = get_company()
        company_b = Company.query.filter_by(name='Company B').first()
        key_b, _ = _api_key(company_b.id, ['dn_read'])
        key_a, _ = _api_key(company_a.id, ['dn_read'])
        platform_key, _ = _api_key(None, ['dn_read'])

        with pytest.raises(ForbiddenException):
            require_source_api_key(key_b, company_a.id)
        with pytest.raises(ForbiddenException):
            require_source_api_key(999999, company_a.id)
        assert require_source_api_key(key_a, company_a.id) == key_a
        assert require_source_api_key(platform_key, company_a.id) == platform_key
        assert require_source_api_key(None, company_a.id) is None


# ---------------------------------------------------------------------------
# B-51
# ---------------------------------------------------------------------------

def _hr_clerk(client):
    """只有 staff_read / staff_edit / staff_delete、只绑仓库 A 的员工。返回 (token, ids)"""
    with client.application.app_context():
        company = get_company()
        warehouse_a = get_warehouse()
        other = next(w for w in company.warehouses if w.id != warehouse_a.id)
        role = _role('hr_clerk', ['staff_read', 'staff_edit', 'staff_delete'])
        hr = _staff('hr1', company.id, role, [warehouse_a])
        peer = _staff('hr2', company.id, role, [warehouse_a])
        company_admin_id = User.query.filter_by(user_name='company_admin').first().id
        ids = dict(hr=hr.id, peer=peer.id, company_admin=company_admin_id,
                   warehouse_a=warehouse_a.id, other_warehouse=other.id)
        return create_access_token(identity=hr), ids


def test_staff_edit_holder_cannot_take_over_company_admin(client):
    token, ids = _hr_clerk(client)

    response = client.put(f"/staff/{ids['company_admin']}", headers=_h(token), json={'password': 'pwned-123456'})
    assert response.status_code == 403, response.get_json()
    assert response.get_json()['code'] == 12007

    response = client.put(f"/staff/{ids['company_admin']}", headers=_h(token), json={'roles': []})
    assert response.status_code == 403

    response = client.delete(f"/staff/{ids['company_admin']}", headers=_h(token))
    assert response.status_code == 403

    response = client.post('/user/login', json={'account': 'company_admin', 'password': 'pwned-123456'})
    assert response.status_code != 200


def test_staff_edit_holder_cannot_widen_warehouse_scope(client):
    token, ids = _hr_clerk(client)

    response = client.put(f"/staff/{ids['hr']}", headers=_h(token),
                          json={'warehouse_ids': [ids['warehouse_a'], ids['other_warehouse']]})
    assert response.status_code == 403, response.get_json()
    assert response.get_json()['code'] == 12001

    response = client.post('/staff/', headers=_h(token), json={
        'user_name': 'new_hire', 'email': 'new_hire@test.com', 'password': 'password123',
        'warehouse_ids': [ids['other_warehouse']],
    })
    assert response.status_code == 403, response.get_json()


def test_staff_edit_holder_can_still_manage_peers_in_scope(client):
    token, ids = _hr_clerk(client)

    response = client.put(f"/staff/{ids['peer']}", headers=_h(token),
                          json={'position': 'Picker', 'warehouse_ids': [ids['warehouse_a']]})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['position'] == 'Picker'


def test_company_admin_can_still_widen_staff_scope(client, access_company_admin_token):
    _, ids = _hr_clerk(client)

    response = client.put(f"/staff/{ids['hr']}", headers=_h(access_company_admin_token),
                          json={'warehouse_ids': [ids['warehouse_a'], ids['other_warehouse']]})
    assert response.status_code == 200, response.get_json()


# ---------------------------------------------------------------------------
# B-52
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/asn/status-overview-stats', '/dn/status-overview-stats'])
def test_stats_cache_is_scoped_per_caller(client, access_company_admin_token, path):
    app = client.application
    app.config['CACHE_TYPE'] = 'SimpleCache'
    cache.init_app(app)
    token_b = _company_b_admin(client)

    response_a = client.get(path, headers=_h(access_company_admin_token))
    assert response_a.status_code == 200
    assert any(row['current_month'] for row in response_a.get_json())

    response_b = client.get(path, headers=_h(token_b))
    assert response_b.status_code == 200
    assert all(row['current_month'] == 0 for row in response_b.get_json())


# ---------------------------------------------------------------------------
# B-53
# ---------------------------------------------------------------------------

def _foreign_goods(client):
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        goods = Goods(code='SECRET-B', company_id=company_b.id, name='B secret product', unit='pcs',
                      price=123.45, brand='BrandB', is_active=True, created_by=admin.id)
        db.session.add(goods)
        db.session.commit()
        return goods.id


def test_adjustment_detail_rejects_foreign_goods(client, access_company_admin_token):
    foreign_id = _foreign_goods(client)
    with client.application.app_context():
        adjustment = Adjustment.query.filter_by(status='pending').first()
        adjustment_id = adjustment.id
        detail_id = adjustment.details[0].id if adjustment.details else None
        location_id = Location.query.filter_by(warehouse_id=adjustment.warehouse_id).first().id

    headers = _h(access_company_admin_token)
    response = client.post(f'/adjustment/{adjustment_id}/details/', headers=headers, json={
        'goods_id': foreign_id, 'location_id': location_id, 'system_quantity': 0, 'actual_quantity': 0})
    assert response.status_code == 403, response.get_json()
    assert 'SECRET-B' not in response.get_data(as_text=True)

    response = client.post('/adjustment/', headers=headers, json={
        'warehouse_id': 1, 'adjustment_reason': 'x',
        'details': [{'goods_id': foreign_id, 'location_id': location_id, 'actual_quantity': 0}]})
    assert response.status_code == 403, response.get_json()

    if detail_id is not None:
        response = client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=headers,
                              json={'goods_id': foreign_id})
        assert response.status_code == 403, response.get_json()


def test_cyclecount_rejects_foreign_goods(client, access_company_admin_token):
    foreign_id = _foreign_goods(client)
    with client.application.app_context():
        location_id = Location.query.filter_by(warehouse_id=1).first().id

    headers = _h(access_company_admin_token)
    response = client.post('/cyclecount/', headers=headers, json={
        'warehouse_id': 1, 'task_name': 'leak', 'task_details': [{'goods_id': foreign_id, 'location_id': location_id}]})
    assert response.status_code == 403, response.get_json()
    assert 'SECRET-B' not in response.get_data(as_text=True)

    with client.application.app_context():
        goods_location = GoodsLocation.query.join(Location).filter(Location.warehouse_id == 1).first()
        own_goods_id, own_location_id = goods_location.goods_id, goods_location.location_id
    response = client.post('/cyclecount/', headers=headers, json={
        'warehouse_id': 1, 'task_name': 'own',
        'task_details': [{'goods_id': own_goods_id, 'location_id': own_location_id}]})
    assert response.status_code == 201, response.get_json()
    task_id = response.get_json()['id']
    with client.application.app_context():
        detail_id = CycleCountTask.query.get(task_id).task_details[0].id

    response = client.put(f'/cyclecount/{task_id}/details/{detail_id}', headers=headers,
                          json={'goods_id': foreign_id})
    assert response.status_code == 403, response.get_json()
