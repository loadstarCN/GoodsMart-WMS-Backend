"""审计修复的回归测试：token 吊销 / 停用账号 / 租户越权 / 拣货库存双计"""
import time
import datetime

from flask_jwt_extended import create_access_token

from .helpers import *
from warehouse.removal.services import RemovalService


# ---------------------------------------------------------------------------
# B-08：停用用户 / 过期公司的 JWT 立即失效；登出与改密吊销 token
# ---------------------------------------------------------------------------

def test_inactive_user_token_rejected(client):
    with client.application.app_context():
        user = get_operator_user()
        token = create_access_token(identity=user)
        user.is_active = False
        db.session.commit()
        warehouse_id = get_warehouse().id

    response = client.get('/asn/', headers={'Authorization': f'Bearer {token}', 'X-WAREHOUSE-ID': str(warehouse_id)})
    assert response.status_code == 401


def test_expired_company_token_rejected(client):
    with client.application.app_context():
        user = get_company_admin_user()
        token = create_access_token(identity=user)
        user.company.expired_at = datetime.datetime.now() - datetime.timedelta(days=1)
        db.session.commit()

    response = client.get('/company/', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401


def test_logout_revokes_token(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    assert client.get('/user/users', headers=headers).status_code == 200

    response = client.post('/user/logout', headers=headers)
    assert response.status_code == 200

    assert client.get('/user/users', headers=headers).status_code == 401


def test_password_change_revokes_existing_tokens(client):
    login = client.post('/user/login', json={'account': 'operator', 'password': 'password'})
    assert login.status_code == 200
    token = login.get_json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}

    # 用户级吊销以秒为粒度，改密要晚于签发至少 1 秒
    time.sleep(1.1)
    response = client.put('/user/change-password', headers=headers,
                          json={'old_password': 'password', 'new_password': 'new-password-1'})
    assert response.status_code == 200

    with client.application.app_context():
        warehouse_id = get_warehouse().id
    response = client.get('/asn/', headers={**headers, 'X-WAREHOUSE-ID': str(warehouse_id)})
    assert response.status_code == 401

    # 新密码可以登录
    assert client.post('/user/login', json={'account': 'operator', 'password': 'new-password-1'}).status_code == 200


# ---------------------------------------------------------------------------
# B-02 / B-03：公司管理员不能提权、不能跨公司、不能给自己续期
# ---------------------------------------------------------------------------

def test_company_admin_cannot_assign_admin_role(client, access_company_admin_token):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    with client.application.app_context():
        staff_id = get_company_admin_user().id

    response = client.put(f'/staff/{staff_id}', headers=headers, json={'roles': ['admin']})
    assert response.status_code == 403
    assert response.get_json()['code'] == 12007

    # 授予自己已有的角色是允许的
    response = client.put(f'/staff/{staff_id}', headers=headers, json={'roles': ['company_admin']})
    assert response.status_code == 200


def test_company_admin_cannot_touch_other_company_staff(client, access_company_admin_token):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    with client.application.app_context():
        company_b = db.session.query(Company).filter_by(name='Company B').first()
        other = Staff(user_name='other_company_staff', email='other@b.com', is_active=True,
                      company_id=company_b.id, created_by=get_admin_user().id)
        other.set_password('password')
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        company_b_id = company_b.id
        own_company_id = get_company_admin_user().company_id

    assert client.get(f'/staff/{other_id}', headers=headers).status_code == 403
    assert client.put(f'/staff/{other_id}', headers=headers, json={'password': 'hacked'}).status_code == 403
    assert client.delete(f'/staff/{other_id}', headers=headers).status_code == 403

    # 尝试在别的公司创建员工：company_id 被强制为自己公司
    response = client.post('/staff/', headers=headers, json={
        'user_name': 'planted', 'email': 'planted@b.com', 'password': 'password', 'company_id': company_b_id,
    })
    assert response.status_code == 201
    assert response.get_json()['company_id'] == own_company_id

    # 列表里看不到别的公司的员工
    response = client.get('/staff/', headers=headers)
    assert response.status_code == 200
    assert all(item['company_id'] == own_company_id for item in response.get_json()['items'])


def test_company_admin_cannot_extend_own_company(client, access_company_admin_token):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    with client.application.app_context():
        company_id = get_company_admin_user().company_id
        company = get_company_by_id(company_id)
        company.expired_at = datetime.datetime(2030, 1, 1)
        db.session.commit()

    response = client.put(f'/company/{company_id}', headers=headers,
                          json={'name': 'Renamed Co', 'expired_at': '2099-12-31', 'is_active': False})
    assert response.status_code == 200

    with client.application.app_context():
        company = get_company_by_id(company_id)
        assert company.name == 'Renamed Co'
        assert company.expired_at == datetime.datetime(2030, 1, 1)
        assert company.is_active is True

    # 不能创建公司、列表只看到自己公司
    assert client.post('/company/', headers=headers, json={'name': 'Evil Co'}).status_code == 403
    response = client.get('/company/', headers=headers)
    assert [item['id'] for item in response.get_json()['items']] == [company_id]


def test_admin_can_set_company_expired_at_in_iso_and_date(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        company_id = get_company().id

    assert client.put(f'/company/{company_id}', headers=headers, json={'expired_at': '2031-05-06'}).status_code == 200
    assert client.put(f'/company/{company_id}', headers=headers, json={'expired_at': '2031-05-06T00:00:00'}).status_code == 200
    assert client.put(f'/company/{company_id}', headers=headers, json={'expired_at': None}).status_code == 200
    with client.application.app_context():
        assert get_company_by_id(company_id).expired_at is None
    assert client.put(f'/company/{company_id}', headers=headers, json={'expired_at': 'not-a-date'}).status_code == 400


def test_delete_company_with_related_records_returns_409(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        company_id = get_company().id  # Company A 有员工 / 仓库 / 商品
    response = client.delete(f'/company/{company_id}', headers=headers)
    assert response.status_code == 409
    assert response.get_json()['code'] == 44003


# ---------------------------------------------------------------------------
# B-21：用户停用 / 自我保护 / 角色使用中
# ---------------------------------------------------------------------------

def test_admin_can_deactivate_user_but_not_self(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        admin_id = get_admin_user().id
        operator_id = get_operator_user().id

    response = client.put(f'/user/users/{operator_id}', headers=headers, json={'is_active': False})
    assert response.status_code == 200
    assert response.get_json()['is_active'] is False
    assert client.post('/user/login', json={'account': 'operator', 'password': 'password'}).status_code == 401

    response = client.put(f'/user/users/{admin_id}', headers=headers, json={'is_active': False})
    assert response.status_code == 400
    assert client.delete(f'/user/users/{admin_id}', headers=headers).status_code == 400


def test_delete_role_in_use_returns_409(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        role_id = get_admin_role().id
    response = client.delete(f'/user/roles/{role_id}', headers=headers)
    assert response.status_code == 409


def test_create_permission_works(client, access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.post('/user/permissions', headers=headers, json={'name': 'new_perm', 'description': 'x'})
    assert response.status_code == 201
    assert response.get_json()['name'] == 'new_perm'


# ---------------------------------------------------------------------------
# B-06：拣货下架不得进入 sorted_stock（否则 total_stock 双计、可再次上架）
# ---------------------------------------------------------------------------

def test_picking_removal_does_not_inflate_sorted_stock(client):
    with client.application.app_context():
        goods = get_goods()
        location = get_location()
        warehouse_id = location.warehouse_id
        admin_id = get_admin_user().id
        before = get_inventory_by_goods_id_and_warehouse_id(goods.id, warehouse_id)
        sorted_before, onhand_before, total_before = before.sorted_stock, before.onhand_stock, before.total_stock

        RemovalService.create_removal_record({
            'goods_id': goods.id, 'location_id': location.id, 'quantity': 5, 'reason': 'picking',
        }, admin_id)

        after = get_inventory_by_goods_id_and_warehouse_id(goods.id, warehouse_id)
        assert after.sorted_stock == sorted_before
        assert after.onhand_stock == onhand_before - 5
        assert after.total_stock == total_before - 5

        # 普通下架仍进入待上架区，总量不变
        RemovalService.create_removal_record({
            'goods_id': goods.id, 'location_id': location.id, 'quantity': 5, 'reason': 'damage',
        }, admin_id)
        again = get_inventory_by_goods_id_and_warehouse_id(goods.id, warehouse_id)
        assert again.sorted_stock == sorted_before + 5
        assert again.total_stock == total_before - 5
