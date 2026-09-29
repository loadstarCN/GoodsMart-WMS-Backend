"""主数据 + 盘点/调整模块的租户 / 仓库隔离用例（B-03 / B-04 / B-07 / B-24 / B-34 / B-39 / B-40）

构造"别家公司员工"：helpers 里已有 Company B，这里再建 Company B 的仓库和 company_admin 员工。
company_admin 持有 company_all_access，能通过 permission_required，但会被归属校验拦下。
"""
import pytest
from flask_jwt_extended import create_access_token

from warehouse.payment.models import Payment
from .helpers import *


def _make_staff(user_name, company_id, role, warehouses=()):
    """在当前 app_context 里新建一个员工并返回其 JWT"""
    admin = get_admin_user()
    staff = Staff(
        user_name=user_name,
        email=f'{user_name}@test.com',
        is_active=True,
        company_id=company_id,
        created_by=admin.id
    )
    staff.set_password('password')
    staff.roles.append(role)
    for warehouse in warehouses:
        staff.warehouses.append(warehouse)
    db.session.add(staff)
    db.session.commit()
    return staff


def _make_role(name, permission_names):
    """新建角色并挂上指定权限（权限不存在则一并创建）"""
    role = Role(name=name, description=name, is_active=True)
    db.session.add(role)
    for perm_name in permission_names:
        perm = Permission.query.filter_by(name=perm_name).first()
        if perm is None:
            perm = Permission(name=perm_name, description=perm_name)
            db.session.add(perm)
        role.permissions.append(perm)
    db.session.commit()
    return role


@pytest.fixture
def company_b(client):
    """Company B 的仓库 + company_admin 员工；返回 dict(company_id, warehouse_id, staff_id, headers)"""
    with client.application.app_context():
        admin = get_admin_user()
        company = Company.query.filter_by(name='Company B').first()
        warehouse = Warehouse(name='Warehouse B', address='B street', company_id=company.id, created_by=admin.id)
        db.session.add(warehouse)
        db.session.commit()

        role = Role.query.filter_by(name='company_admin').first()
        staff = _make_staff('b_admin', company.id, role, [warehouse])
        token = create_access_token(identity=staff)
        return {
            'company_id': company.id,
            'warehouse_id': warehouse.id,
            'staff_id': staff.id,
            'headers': {'Authorization': f'Bearer {token}'},
        }


@pytest.fixture
def admin_headers(access_token):
    return {'Authorization': f'Bearer {access_token}'}


@pytest.fixture
def company_admin_headers(access_company_admin_token):
    return {'Authorization': f'Bearer {access_company_admin_token}'}


# ---------------------------------------------------------------------------
# B-03：公司级主数据 supplier / carrier / goods / recipient
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('prefix,getter', [
    ('/supplier', get_supplier),
    ('/carrier', get_carrier),
    ('/goods', get_goods),
    ('/recipient', get_recipient),
])
def test_cross_company_read_update_delete_forbidden(client, company_b, prefix, getter):
    """别家公司员工读 / 改 / 删 Company A 的主数据 → 403，且数据未被改动"""
    with client.application.app_context():
        record = getter()
        record_id = record.id
        original_name = record.name

    response = client.get(f'{prefix}/{record_id}', headers=company_b['headers'])
    assert response.status_code == 403
    assert response.get_json()['code'] == 12001

    response = client.put(f'{prefix}/{record_id}', headers=company_b['headers'], json={'name': 'hijacked'})
    assert response.status_code == 403

    response = client.delete(f'{prefix}/{record_id}', headers=company_b['headers'])
    assert response.status_code == 403

    with client.application.app_context():
        db.session.expire_all()
        record = getter()
        assert record is not None
        assert record.name == original_name


@pytest.mark.parametrize('prefix,getter', [
    ('/supplier', get_supplier),
    ('/carrier', get_carrier),
    ('/recipient', get_recipient),
    ('/goods', get_goods),
])
def test_update_ignores_company_id(client, admin_headers, prefix, getter):
    """update 里的 company_id 被忽略：即使平台管理员也不能把记录迁到别的公司"""
    with client.application.app_context():
        record = getter()
        record_id, original_company_id = record.id, record.company_id

    response = client.put(f'{prefix}/{record_id}', headers=admin_headers, json={'company_id': 2})
    assert response.status_code == 200
    assert response.get_json()['company_id'] == original_company_id


@pytest.mark.parametrize('prefix,payload', [
    ('/supplier', {'name': 'B Supplier', 'company_id': 1}),
    ('/carrier', {'name': 'B Carrier', 'company_id': 1}),
    ('/recipient', {'name': 'B Recipient', 'country': 'jp', 'company_id': 1}),
    ('/goods', {'code': 'BG001', 'name': 'B Goods', 'company_id': 1}),
])
def test_create_forces_actor_company(client, company_b, prefix, payload):
    """非平台管理员 POST 时 company_id 被强制为本公司（请求体里的 1 被覆盖）"""
    response = client.post(f'{prefix}/', headers=company_b['headers'], json=payload)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['company_id'] == company_b['company_id']


def test_goods_create_forces_company_for_staff(client, company_admin_headers):
    """goods POST 对 staff 也强制公司（以前只对 API Key 注入）"""
    response = client.post('/goods/', headers=company_admin_headers, json={
        'code': 'CA001', 'name': 'Company Admin Goods', 'company_id': 2
    })
    assert response.status_code == 201
    assert response.get_json()['company_id'] == 1


def test_master_data_list_scoped_to_company(client, company_b):
    """列表：别家员工看不到 Company A 的供应商 / 承运商 / 商品 / 收件人 / 仓库"""
    for prefix in ('/supplier', '/carrier', '/goods', '/recipient', '/warehouse'):
        response = client.get(f'{prefix}/?page=1&per_page=50&company_id=1', headers=company_b['headers'])
        assert response.status_code == 200, prefix
        data = response.get_json()
        for item in data['items']:
            assert item['company_id'] == company_b['company_id'], prefix


def test_goods_list_with_company_filter_ignored_for_staff(client, company_admin_headers):
    """Company A 的员工带 company_id=2 查询仍只看到自家商品"""
    response = client.get('/goods/?page=1&per_page=50&company_id=2', headers=company_admin_headers)
    assert response.status_code == 200
    data = response.get_json()
    assert len(data['items']) > 0
    assert all(item['company_id'] == 1 for item in data['items'])


def test_supplier_email_unique_per_company(client, admin_headers):
    """B-39：邮箱改为 (company_id, email) 联合唯一 → 别家公司可以用同一个邮箱"""
    with client.application.app_context():
        email = get_supplier().email

    response = client.post('/supplier/', headers=admin_headers, json={
        'name': 'Other Company Same Email', 'email': email, 'company_id': 2
    })
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['email'] == email

    # 同公司仍然唯一
    response = client.post('/supplier/', headers=admin_headers, json={
        'name': 'Same Company Same Email', 'email': email, 'company_id': 1
    })
    assert response.status_code != 201


# ---------------------------------------------------------------------------
# B-03：payment（归属跟随 delivery → dn → warehouse → company）
# ---------------------------------------------------------------------------

def _create_payment_as_admin(client, admin_headers):
    with client.application.app_context():
        delivery_id = get_delivery_task().id
        carrier_id = get_carrier().id
    response = client.post('/payment/', headers=admin_headers, json={
        'delivery_id': delivery_id, 'carrier_id': carrier_id,
        'amount': '100.00', 'currency': 'JPY', 'payment_method': 'cash',
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()['id'], delivery_id


def test_payment_cross_company_forbidden(client, admin_headers, company_b):
    payment_id, delivery_id = _create_payment_as_admin(client, admin_headers)

    for method, url in (
        ('get', f'/payment/{payment_id}'),
        ('put', f'/payment/{payment_id}'),
        ('delete', f'/payment/{payment_id}'),
        ('put', f'/payment/{payment_id}/process/'),
        ('put', f'/payment/{payment_id}/cancel/'),
    ):
        kwargs = {'json': {'remark': 'x'}} if method == 'put' and url.endswith(str(payment_id)) else {}
        response = getattr(client, method)(url, headers=company_b['headers'], **kwargs)
        assert response.status_code == 403, (method, url)

    # 列表看不到
    response = client.get('/payment/?page=1&per_page=50', headers=company_b['headers'])
    assert response.status_code == 200
    assert response.get_json()['total'] == 0

    # 平台管理员能看到
    response = client.get('/payment/?page=1&per_page=50', headers=admin_headers)
    assert response.get_json()['total'] == 1

    # 别家公司不能给 Company A 的发货单建支付记录
    response = client.post('/payment/', headers=company_b['headers'], json={
        'delivery_id': delivery_id, 'carrier_id': 1,
        'amount': '1.00', 'payment_method': 'cash',
    })
    assert response.status_code == 403

    with client.application.app_context():
        db.session.expire_all()
        assert db.session.get(Payment, payment_id).status == 'pending'


def test_payment_create_ignores_status_and_checks_carrier_company(client, admin_headers):
    """create 不接受客户端 status；承运商必须与发货单同公司"""
    with client.application.app_context():
        delivery_id = get_delivery_task().id
        carrier_id = get_carrier().id
        admin = get_admin_user()
        other_carrier = Carrier(name='B Carrier', company_id=2, created_by=admin.id)
        db.session.add(other_carrier)
        db.session.commit()
        other_carrier_id = other_carrier.id

    response = client.post('/payment/', headers=admin_headers, json={
        'delivery_id': delivery_id, 'carrier_id': carrier_id,
        'amount': '100.00', 'payment_method': 'cash', 'status': 'paid',
    })
    assert response.status_code == 201
    assert response.get_json()['status'] == 'pending'

    response = client.post('/payment/', headers=admin_headers, json={
        'delivery_id': delivery_id, 'carrier_id': other_carrier_id,
        'amount': '100.00', 'payment_method': 'cash',
    })
    assert response.status_code == 400
    assert response.get_json()['code'] == 16027


# ---------------------------------------------------------------------------
# B-03 / B-07 / B-24：仓库级 location / goods_locations / inventory
# ---------------------------------------------------------------------------

def test_location_cross_warehouse_forbidden(client, company_b):
    with client.application.app_context():
        location = get_location()
        location_id, original_code = location.id, location.code
        warehouse_a_id = get_warehouse().id

    headers = company_b['headers']
    assert client.get(f'/location/{location_id}', headers=headers).status_code == 403
    assert client.put(f'/location/{location_id}', headers=headers, json={'code': 'X'}).status_code == 403
    assert client.delete(f'/location/{location_id}', headers=headers).status_code == 403

    # 不能在别家仓库建库位
    response = client.post('/location/', headers=headers, json={
        'warehouse_id': warehouse_a_id, 'code': 'B-LOC', 'location_type': 'standard'
    })
    assert response.status_code == 403

    # 在自己仓库可以
    response = client.post('/location/', headers=headers, json={
        'warehouse_id': company_b['warehouse_id'], 'code': 'B-LOC', 'location_type': 'standard'
    })
    assert response.status_code == 201
    assert response.get_json()['warehouse_id'] == company_b['warehouse_id']

    with client.application.app_context():
        db.session.expire_all()
        assert get_location_by_id(location_id).code == original_code


def test_location_update_ignores_warehouse_id(client, admin_headers):
    with client.application.app_context():
        location = get_location()
        location_id, warehouse_id = location.id, location.warehouse_id

    response = client.put(f'/location/{location_id}', headers=admin_headers,
                          json={'warehouse_id': 2, 'description': 'moved?'})
    assert response.status_code == 200
    assert response.get_json()['warehouse_id'] == warehouse_id


def test_goods_location_readonly_and_scoped(client, company_b):
    with client.application.app_context():
        goods_location_id = get_goods_location().id

    headers = company_b['headers']
    assert client.get(f'/goods/locations/{goods_location_id}', headers=headers).status_code == 403
    response = client.get('/goods/locations/?page=1&per_page=50', headers=headers)
    assert response.status_code == 200
    assert response.get_json()['total'] == 0

    assert client.post('/goods/locations/', headers=headers,
                       json={'goods_id': 1, 'location_id': 1, 'quantity': 1}).status_code == 405
    assert client.put(f'/goods/locations/{goods_location_id}', headers=headers,
                      json={'quantity': 1}).status_code == 405
    assert client.delete(f'/goods/locations/{goods_location_id}', headers=headers).status_code == 405


def test_inventory_detail_cross_warehouse_forbidden(client, company_b):
    with client.application.app_context():
        inventory = get_inventory()
        url = f'/inventory/goods/{inventory.goods_id}/warehouse/{inventory.warehouse_id}'
    response = client.get(url, headers=company_b['headers'])
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# B-24：warehouse
# ---------------------------------------------------------------------------

def test_warehouse_cross_company_forbidden(client, company_b):
    with client.application.app_context():
        warehouse_id = get_warehouse().id
    headers = company_b['headers']
    assert client.get(f'/warehouse/{warehouse_id}', headers=headers).status_code == 403
    assert client.put(f'/warehouse/{warehouse_id}', headers=headers, json={'name': 'X'}).status_code == 403
    assert client.delete(f'/warehouse/{warehouse_id}', headers=headers).status_code == 403


def test_warehouse_create_forces_company_for_company_admin(client, company_b):
    response = client.post('/warehouse/', headers=company_b['headers'], json={
        'name': 'B Second Warehouse', 'company_id': 1
    })
    assert response.status_code == 201
    assert response.get_json()['company_id'] == company_b['company_id']


def test_warehouse_update_manager_checks_warehouse_company_and_unbinds_old(client, admin_headers, company_b):
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse_id = warehouse.id
        first_manager_id = get_warehouse_admin_user().id
        second_manager_id = get_operator_user().id

    # 别家公司的员工不能当 manager（即便请求体里谎报 company_id）
    response = client.put(f'/warehouse/{warehouse_id}', headers=admin_headers,
                          json={'manager_id': company_b['staff_id'], 'company_id': company_b['company_id']})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16027

    response = client.put(f'/warehouse/{warehouse_id}', headers=admin_headers,
                          json={'manager_id': first_manager_id, 'company_id': 2})
    assert response.status_code == 200
    assert response.get_json()['manager_id'] == first_manager_id
    assert response.get_json()['company_id'] == 1

    response = client.put(f'/warehouse/{warehouse_id}', headers=admin_headers,
                          json={'manager_id': second_manager_id})
    assert response.status_code == 200
    assert response.get_json()['manager_id'] == second_manager_id

    with client.application.app_context():
        db.session.expire_all()
        warehouse = get_warehouse_by_id(warehouse_id)
        assert warehouse not in db.session.get(Staff, first_manager_id).warehouses   # 旧 manager 已解绑
        assert warehouse in db.session.get(Staff, second_manager_id).warehouses


# ---------------------------------------------------------------------------
# B-04 / B-40：cyclecount
# ---------------------------------------------------------------------------

def test_cyclecount_cross_warehouse_forbidden(client, company_b):
    with client.application.app_context():
        task = get_cyclecount_task()
        task_id = task.id
        detail_id = task.task_details[0].id
        warehouse_a_id = task.warehouse_id
        original_name = task.task_name

    headers = company_b['headers']
    for method, url, kwargs in (
        ('get', f'/cyclecount/{task_id}', {}),
        ('put', f'/cyclecount/{task_id}', {'json': {'task_name': 'X'}}),
        ('delete', f'/cyclecount/{task_id}', {}),
        ('put', f'/cyclecount/{task_id}/process/', {}),
        ('put', f'/cyclecount/{task_id}/complete/', {}),
        ('get', f'/cyclecount/{task_id}/details/', {}),
        ('post', f'/cyclecount/{task_id}/details/', {'json': {'goods_id': 1, 'location_id': 1}}),
        ('get', f'/cyclecount/{task_id}/details/{detail_id}', {}),
        ('put', f'/cyclecount/{task_id}/details/{detail_id}', {'json': {'actual_quantity': 1}}),
        ('delete', f'/cyclecount/{task_id}/details/{detail_id}', {}),
        ('put', f'/cyclecount/{task_id}/details/{detail_id}/complete/', {}),
        ('post', f'/cyclecount/{task_id}/details-batch-save/',
         {'json': {'task_id': task_id, 'details': [{'id': detail_id, 'actual_quantity': 1}]}}),
    ):
        response = getattr(client, method)(url, headers=headers, **kwargs)
        assert response.status_code == 403, (method, url, response.get_json())

    # 不能在别家仓库建盘点任务
    response = client.post('/cyclecount/', headers=headers, json={'task_name': 'B', 'warehouse_id': warehouse_a_id})
    assert response.status_code == 403

    with client.application.app_context():
        db.session.expire_all()
        assert get_cyclecount_task_by_id(task_id).task_name == original_name


def test_cyclecount_detail_location_must_be_in_task_warehouse(client, admin_headers, company_b):
    """明细库位必须与任务同仓库（即便是平台管理员）"""
    with client.application.app_context():
        task_id = get_cyclecount_task().id
        admin = get_admin_user()
        location_b = Location(warehouse_id=company_b['warehouse_id'], code='B-LOC-1',
                              location_type='standard', created_by=admin.id)
        db.session.add(location_b)
        db.session.commit()
        location_b_id = location_b.id

    response = client.post(f'/cyclecount/{task_id}/details/', headers=admin_headers,
                           json={'goods_id': 1, 'location_id': location_b_id})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# B-04 / B-34：adjustment
# ---------------------------------------------------------------------------

def test_adjustment_cross_warehouse_forbidden(client, company_b):
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id
        detail_id = adjustment.details[0].id
        warehouse_a_id = adjustment.warehouse_id
        cyclecount_id = get_cyclecount_task().id

    headers = company_b['headers']
    for method, url, kwargs in (
        ('get', f'/adjustment/{adjustment_id}', {}),
        ('put', f'/adjustment/{adjustment_id}', {'json': {'adjustment_reason': 'X'}}),
        ('delete', f'/adjustment/{adjustment_id}', {}),
        ('put', f'/adjustment/{adjustment_id}/approve/', {}),
        ('put', f'/adjustment/{adjustment_id}/complete/', {}),
        ('get', f'/adjustment/{adjustment_id}/details/', {}),
        ('post', f'/adjustment/{adjustment_id}/details/',
         {'json': {'goods_id': 1, 'location_id': 1, 'system_quantity': 1, 'actual_quantity': 2}}),
        ('get', f'/adjustment/{adjustment_id}/details/{detail_id}', {}),
        ('put', f'/adjustment/{adjustment_id}/details/{detail_id}', {'json': {'actual_quantity': 1}}),
        ('delete', f'/adjustment/{adjustment_id}/details/{detail_id}', {}),
        ('post', f'/adjustment/create_adjustment_by_cyclecount/{cyclecount_id}', {}),
    ):
        response = getattr(client, method)(url, headers=headers, **kwargs)
        assert response.status_code == 403, (method, url, response.get_json())

    response = client.post('/adjustment/', headers=headers, json={'warehouse_id': warehouse_a_id})
    assert response.status_code == 403

    with client.application.app_context():
        db.session.expire_all()
        assert get_adjustment_by_id(adjustment_id).status == 'pending'


def test_adjustment_approve_requires_approve_permission(client):
    """B-34：approve 需要 adjustment_approve；只有 adjustment_edit 的员工 → 403"""
    with client.application.app_context():
        warehouse = get_warehouse()
        editor_role = _make_role('adjust_editor', ['adjustment_read', 'adjustment_edit'])
        approver_role = _make_role('adjust_approver', ['adjustment_read', 'adjustment_approve'])
        editor = _make_staff('adj_editor', 1, editor_role, [warehouse])
        approver = _make_staff('adj_approver', 1, approver_role, [warehouse])
        editor_headers = {'Authorization': f'Bearer {create_access_token(identity=editor)}',
                          'X-WAREHOUSE-ID': str(warehouse.id)}
        approver_headers = {'Authorization': f'Bearer {create_access_token(identity=approver)}',
                            'X-WAREHOUSE-ID': str(warehouse.id)}
        adjustment_id = get_adjustment().id

    response = client.put(f'/adjustment/{adjustment_id}/approve/', headers=editor_headers)
    assert response.status_code == 403

    # 有 edit 权限的员工可以改单（说明 403 来自 approve 权限而非仓库归属）
    response = client.put(f'/adjustment/{adjustment_id}', headers=editor_headers, json={'adjustment_reason': 'ok'})
    assert response.status_code == 200

    response = client.put(f'/adjustment/{adjustment_id}/approve/', headers=approver_headers)
    assert response.status_code == 200
    assert response.get_json()['status'] == 'approved'
