"""内置权限 / 内置角色的保护（system/user/builtin.py）

- 内置权限（代码里按名字校验的，seed_permissions.py 按同一清单初始化）：不能改权限代码、不能删（409 11011，
  details {permission, reason}）；描述可以改
- 内置角色（admin / company_admin / warehouse_admin / warehouse_operator）：不能改名、不能停用、不能删（409 11012，
  details {role, reason}）；分配 / 取消权限、改描述照常
- 列表 / 详情带只读的 is_builtin；自建的权限 / 角色照旧能改名、删除
"""
import ast
import pathlib
import re

from tests.helpers import *  # noqa: F401,F403
from system.user.builtin import BUILTIN_PERMISSION_NAMES, BUILTIN_ROLE_NAMES

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _conflict(response, code):
    body = response.get_json()
    assert (response.status_code, body.get('code')) == (409, code), body
    assert body['message'].isascii()
    return body['details']


def test_every_permission_checked_in_code_is_builtin():
    """@permission_required 里出现的权限都在内置清单里（新增权限忘了登记时这里会红）"""
    names = set()
    for path in [*ROOT.joinpath('system').rglob('*.py'), *ROOT.joinpath('warehouse').rglob('*.py')]:
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'permission_required\(\s*(\[[^\]]*\]|"[^"]*"|\'[^\']*\')', text):
            value = ast.literal_eval(match.group(1))
            names.update([value] if isinstance(value, str) else value)
    assert names and names <= BUILTIN_PERMISSION_NAMES, sorted(names - BUILTIN_PERMISSION_NAMES)
    assert {'admin', 'company_admin', 'warehouse_admin', 'warehouse_operator'} == BUILTIN_ROLE_NAMES


def test_builtin_permission_cannot_be_renamed_or_deleted(client, access_token):
    headers = _h(access_token)
    with client.application.app_context():
        builtin_id = Permission.query.filter_by(name='all_access').one().id

    detail = client.get(f'/user/permissions/{builtin_id}', headers=headers).get_json()
    assert detail['is_builtin'] is True
    details = _conflict(client.put(f'/user/permissions/{builtin_id}', headers=headers,
                                   json={'name': 'everything'}), 11011)
    assert details == {'permission': 'all_access', 'reason': 'RENAME'}
    details = _conflict(client.delete(f'/user/permissions/{builtin_id}', headers=headers), 11011)
    assert details == {'permission': 'all_access', 'reason': 'DELETE'}

    # 描述可以改；整表提交原名不算改名
    response = client.put(f'/user/permissions/{builtin_id}', headers=headers,
                          json={'name': 'all_access', 'description': 'Platform super admin'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['description'] == 'Platform super admin'
    with client.application.app_context():
        assert db.session.get(Permission, builtin_id).name == 'all_access'

    # 自建的权限照旧能改名、删除
    created = client.post('/user/permissions', headers=headers, json={'name': 'custom_perm', 'description': 'x'})
    assert created.status_code == 201, created.get_json()
    assert created.get_json()['is_builtin'] is False
    custom_id = created.get_json()['id']
    response = client.put(f'/user/permissions/{custom_id}', headers=headers, json={'name': 'custom_perm_2'})
    assert response.status_code == 200 and response.get_json()['name'] == 'custom_perm_2'
    assert client.delete(f'/user/permissions/{custom_id}', headers=headers).status_code == 200

    items = client.get('/user/permissions?per_page=100', headers=headers).get_json()['items']
    flags = {item['name']: item['is_builtin'] for item in items}
    assert flags['all_access'] is True and flags['company_all_access'] is True
    # 测试库里的 operator / warehouse_all_access 不是代码里校验的权限
    assert flags['operator'] is False


def test_builtin_role_cannot_be_renamed_deactivated_or_deleted(client, access_token):
    headers = _h(access_token)
    with client.application.app_context():
        role = Role.query.filter_by(name='warehouse_operator').one()
        role_id = role.id
        unused = Role.query.filter_by(name='warehouse_admin').one().id
        permission_id = Permission.query.filter_by(name='company_all_access').one().id

    assert client.get(f'/user/roles/{role_id}', headers=headers).get_json()['is_builtin'] is True
    assert _conflict(client.put(f'/user/roles/{role_id}', headers=headers, json={'name': 'picker'}), 11012) \
        == {'role': 'warehouse_operator', 'reason': 'RENAME'}
    assert _conflict(client.put(f'/user/roles/{role_id}', headers=headers, json={'is_active': False}), 11012) \
        == {'role': 'warehouse_operator', 'reason': 'DEACTIVATE'}
    # 没人用的内置角色也不能删
    assert _conflict(client.delete(f'/user/roles/{unused}', headers=headers), 11012) \
        == {'role': 'warehouse_admin', 'reason': 'DELETE'}
    with client.application.app_context():
        role = db.session.get(Role, role_id)
        assert (role.name, role.is_active) == ('warehouse_operator', True)

    # 分配 / 取消权限、改描述照常；整表提交原名 + 启用不算违规
    response = client.put(f'/user/roles/{role_id}', headers=headers, json={
        'name': 'warehouse_operator', 'is_active': True, 'description': 'Warehouse staff',
        'permissions': [permission_id]})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['permissions'] == ['company_all_access']
    response = client.put(f'/user/roles/{role_id}', headers=headers, json={'permissions': []})
    assert response.status_code == 200 and response.get_json()['permissions'] == []

    # 自建角色照旧能改名、停用、删除
    created = client.post('/user/roles', headers=headers, json={'name': 'night_shift'})
    assert created.status_code == 201 and created.get_json()['is_builtin'] is False
    custom_id = created.get_json()['id']
    response = client.put(f'/user/roles/{custom_id}', headers=headers, json={'name': 'late_shift', 'is_active': False})
    assert response.status_code == 200
    assert (response.get_json()['name'], response.get_json()['is_active']) == ('late_shift', False)
    assert client.delete(f'/user/roles/{custom_id}', headers=headers).status_code == 200

    items = client.get('/user/roles?per_page=100', headers=headers).get_json()['items']
    assert {item['name']: item['is_builtin'] for item in items} == {
        'admin': True, 'company_admin': True, 'warehouse_admin': True, 'warehouse_operator': True}


# ---------------------------------------------------------------------------
# 种子：内置角色的默认权限只补缺、不删除，可重复运行
# ---------------------------------------------------------------------------

def _role_permissions(name):
    role = Role.query.filter_by(name=name).one()
    return {permission.name for permission in role.permissions}


def test_seed_tops_up_builtin_role_permissions_without_removing(client, access_operator_token):
    from system.user.builtin import BUILTIN_PERMISSIONS, BUILTIN_ROLE_DEFAULT_PERMISSIONS
    from system.user.seed import seed_builtin
    with client.application.app_context():
        # 模拟线上：warehouse_operator 只有 goods_read / location_read（另有测试库里手工加的 operator），
        # warehouse_admin 只有手工加的 warehouse_all_access
        for name in ('goods_read', 'location_read'):
            db.session.add(Permission(name=name, description='pre-existing'))
        db.session.flush()
        operator_role = Role.query.filter_by(name='warehouse_operator').one()
        operator_role.permissions.extend(Permission.query.filter(Permission.name.in_(['goods_read', 'location_read'])))
        db.session.commit()

        result = seed_builtin()
        assert set(result['added_permissions']) == {name for name, _d in BUILTIN_PERMISSIONS} - {
            'all_access', 'company_all_access', 'goods_read', 'location_read'}
        assert result['created_roles'] == []
        assert set(result['granted']) == {'warehouse_admin', 'warehouse_operator'}
        assert 'goods_read' not in result['granted']['warehouse_operator']      # 已有的不重复加
        # 已有描述不改
        assert Permission.query.filter_by(name='goods_read').one().description == 'pre-existing'

        operator_defaults = set(BUILTIN_ROLE_DEFAULT_PERMISSIONS['warehouse_operator'])
        admin_defaults = set(BUILTIN_ROLE_DEFAULT_PERMISSIONS['warehouse_admin'])
        assert _role_permissions('warehouse_operator') == operator_defaults | {'operator'}
        assert _role_permissions('warehouse_admin') == admin_defaults | {'warehouse_all_access'}
        assert _role_permissions('company_admin') == {'company_all_access'}
        assert _role_permissions('admin') == {'all_access'}
        # 作业员：不给删除 / 审批 / 主数据编辑 / 系统类权限；仓库经理：不给公司 / 密钥 / 推送 / 角色权限 / 平台级
        assert not {p for p in operator_defaults if p.endswith('_delete')}
        assert not operator_defaults & {'adjustment_approve', 'goods_edit', 'goods_add', 'location_edit',
                                        'carrier_edit', 'recipient_edit', 'supplier_edit', 'staff_read',
                                        'company_read', 'api_keys_read', 'webhook_read', 'role_read'}
        assert not admin_defaults & {'all_access', 'company_all_access', 'company_read', 'company_edit',
                                     'api_keys_read', 'api_keys_edit', 'webhook_read', 'webhook_edit', 'role_read',
                                     'role_edit', 'permission_read', 'permission_edit', 'user_read', 'user_edit'}
        assert {'adjustment_approve', 'location_edit', 'location_delete', 'dn_delete', 'asn_delete',
                'staff_read'} <= admin_defaults

        # 管理员手工给作业员加的权限，再跑种子也保留；第二次运行什么都不改
        operator_role = Role.query.filter_by(name='warehouse_operator').one()
        operator_role.permissions.append(Permission.query.filter_by(name='adjustment_read').one())
        db.session.commit()
        assert seed_builtin() == {'added_permissions': [], 'created_roles': [], 'granted': {}}
        assert 'adjustment_read' in _role_permissions('warehouse_operator')

        # 内置的仓库角色不存在时新建（admin / company_admin 不新建）
        role = Role.query.filter_by(name='warehouse_admin').one()
        for user in role.users.all():
            user.roles.remove(role)
        db.session.delete(role)
        db.session.commit()
        result = seed_builtin()
        assert result['created_roles'] == ['warehouse_admin']
        assert _role_permissions('warehouse_admin') == admin_defaults

    # 作业员拿到的权限能用：看库存、收货通知；不能审批调整单
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        adjustment_id = Adjustment.query.first().id
    headers = {**_h(access_operator_token), 'X-WAREHOUSE-ID': str(warehouse_id)}
    assert client.get('/inventory/', headers=headers).status_code == 200
    assert client.get('/asn/', headers=headers).status_code == 200
    assert client.put(f'/adjustment/{adjustment_id}/approve/', headers=headers).status_code == 403
