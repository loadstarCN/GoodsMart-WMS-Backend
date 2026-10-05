"""GET /staff/current：当前员工信息、可访问仓库与生效权限（W-42 前端按权限显示菜单 / 按钮）

- 只要求登录：没有 staff_read 的员工也能取到自己的信息与仓库列表
- permissions 只含启用中角色的权限；is_company_admin 按启用中的角色判断
"""
from .helpers import *


def _role(name, permissions, is_active=True):
    role = Role(name=name, description=name, is_active=is_active)
    db.session.add(role)
    for perm_name in permissions:
        perm = Permission.query.filter_by(name=perm_name).first()
        if perm is None:
            perm = Permission(name=perm_name, description=perm_name)
            db.session.add(perm)
        role.permissions.append(perm)
    db.session.commit()
    return role


def _staff_token(client, roles, warehouses=True):
    with client.application.app_context():
        admin = get_admin_user()
        staff = Staff(user_name='picker1', email='picker1@test.com', is_active=True,
                      company_id=get_company().id, created_by=admin.id)
        staff.set_password('password')
        for role in roles():
            staff.roles.append(role)
        if warehouses:
            staff.warehouses.append(get_warehouse())
        db.session.add(staff)
        db.session.commit()
        return create_access_token(identity=staff), get_warehouse().id


def test_staff_without_staff_read_gets_own_info(client):
    token, warehouse_id = _staff_token(client, lambda: [_role('picker', ['picking_read', 'picking_edit'])])

    response = client.get('/staff/current', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['user_name'] == 'picker1'
    assert [w['id'] for w in data['warehouses']] == [warehouse_id]
    assert data['permissions'] == ['picking_edit', 'picking_read']
    assert data['is_company_admin'] is False


def test_inactive_role_permissions_are_not_listed(client):
    token, _ = _staff_token(client, lambda: [
        _role('picker', ['picking_read']),
        _role('approver_disabled', ['adjustment_approve'], is_active=False),
    ])

    data = client.get('/staff/current', headers={'Authorization': f'Bearer {token}'}).get_json()
    assert data['permissions'] == ['picking_read']


def test_company_admin_flag(client, access_company_admin_token):
    data = client.get('/staff/current', headers={'Authorization': f'Bearer {access_company_admin_token}'}).get_json()
    assert data['is_company_admin'] is True
    assert 'company_all_access' in data['permissions']


def test_requires_login(client):
    assert client.get('/staff/current').status_code == 401
