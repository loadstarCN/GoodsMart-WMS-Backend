"""作业员权限拆分（system/user/builtin.py、迁移 c4e8a1f6b2d9）

- 新内置权限 asn_receive（收货、刷新到货跟踪）/ cycle_count_execute（开始盘点、录入、完成明细 / 盘点单）；这些接口照旧也接受
  asn_edit / cycle_count_edit
- warehouse_operator 默认权限用新权限替换 asn_edit / cycle_count_edit；warehouse_admin 两套都有
- 作业员能收货、分拣（含规格采集）、盘点录入；不能新建 / 修改 / 关闭 / 取消收货通知，不能新建 / 修改盘点单
- 迁移：只对内置 warehouse_operator，持有旧权限的先授予新权限再移除旧权限；其他角色不动；可重复执行；downgrade 加回旧权限
"""
from .helpers import *  # noqa: F401,F403

import importlib.util
import pathlib

from alembic.migration import MigrationContext
from alembic.operations import Operations

from system.user.builtin import BUILTIN_PERMISSION_NAMES, BUILTIN_ROLE_DEFAULT_PERMISSIONS
from system.user.seed import seed_builtin

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / 'migrations' / 'versions' / 'c4e8a1f6b2d9_operator_receive_and_count_permissions.py'


def _h(token, warehouse_id=None):
    headers = {'Authorization': f'Bearer {token}'}
    if warehouse_id is not None:
        headers['X-WAREHOUSE-ID'] = str(warehouse_id)
    return headers


def _role_permissions(name):
    return {permission.name for permission in Role.query.filter_by(name=name).one().permissions}


def test_builtin_defaults():
    assert {'asn_receive', 'cycle_count_execute'} <= BUILTIN_PERMISSION_NAMES
    operator = set(BUILTIN_ROLE_DEFAULT_PERMISSIONS['warehouse_operator'])
    admin = set(BUILTIN_ROLE_DEFAULT_PERMISSIONS['warehouse_admin'])
    assert {'asn_receive', 'cycle_count_execute', 'sorting_edit'} <= operator
    assert not operator & {'asn_edit', 'cycle_count_edit'}
    assert {'asn_receive', 'asn_edit', 'cycle_count_execute', 'cycle_count_edit'} <= admin


def test_operator_receives_sorts_and_counts_but_cannot_edit(client, access_token, access_operator_token):
    with client.application.app_context():
        seed_builtin()
        assert not _role_permissions('warehouse_operator') & {'asn_edit', 'cycle_count_edit'}
        warehouse_id = get_warehouse().id
        location_id = get_location().id
        goods = db.session.get(Goods, 1)
        goods.origin_country = None
        db.session.commit()
    admin = _h(access_token)
    operator = _h(access_operator_token, warehouse_id)

    # 收货通知：作业员不能新建 / 修改 / 加明细 / 关闭 / 删除
    body = {'warehouse_id': warehouse_id, 'supplier_id': 1, 'details': [{'goods_id': 1, 'quantity': 2}],
            'spec_capture': {'fields': ['origin_country']}}
    assert client.post('/asn/', headers=operator, json=body).status_code == 403
    response = client.post('/asn/', headers=admin, json=body)
    assert response.status_code == 201, response.get_json()
    asn_id = response.get_json()['id']
    detail_id = response.get_json()['details'][0]['id']
    assert client.put(f'/asn/{asn_id}', headers=operator, json={'remark': 'x'}).status_code == 403
    assert client.post(f'/asn/{asn_id}/details/', headers=operator,
                       json={'goods_id': 2, 'quantity': 1}).status_code == 403
    assert client.put(f'/asn/{asn_id}/details/{detail_id}', headers=operator, json={'quantity': 9}).status_code == 403
    assert client.put(f'/asn/{asn_id}/close/', headers=operator).status_code == 403
    assert client.delete(f'/asn/{asn_id}', headers=operator).status_code == 403
    # 刷新到货跟踪接受 asn_receive（没有运单号 → 409，不是 403）
    response = client.post(f'/asn/{asn_id}/track', headers=operator)
    assert response.status_code == 409 and response.get_json()['code'] == 16140

    # 收货
    response = client.put(f'/asn/{asn_id}/receive/', headers=operator)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'received'
    assert client.put(f'/asn/{asn_id}/cancel/', headers=operator).status_code == 403

    # 分拣（分拣完成会自动完成收货通知，不需要 asn_edit），含规格采集
    with client.application.app_context():
        task_id = SortingTask.query.filter_by(asn_id=asn_id).one().id
    assert client.put(f'/sorting/{task_id}/process/', headers=operator).status_code == 200
    response = client.post(f'/sorting/{task_id}/batches/', headers=operator,
                           json={'details': [{'goods_id': 1, 'sorted_quantity': 2, 'damage_quantity': 0}]})
    assert response.status_code == 201, response.get_json()
    response = client.put(f'/asn/{asn_id}/details/{detail_id}/spec', headers=operator, json={'origin_country': 'CN'})
    assert response.status_code == 200, response.get_json()
    response = client.put(f'/sorting/{task_id}/complete/', headers=operator)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['asn']['status'] == 'completed'

    # 盘点：作业员不能新建 / 修改 / 加明细 / 改明细
    task_body = {'task_name': 'CC-OP', 'warehouse_id': warehouse_id,
                 'task_details': [{'goods_id': 1, 'location_id': location_id}]}
    assert client.post('/cyclecount/', headers=operator, json=task_body).status_code == 403
    response = client.post('/cyclecount/', headers=admin, json=task_body)
    assert response.status_code == 201, response.get_json()
    cycle_id = response.get_json()['id']
    cycle_detail_id = response.get_json()['task_details'][0]['id']
    assert client.put(f'/cyclecount/{cycle_id}', headers=operator, json={'task_name': 'x'}).status_code == 403
    assert client.post(f'/cyclecount/{cycle_id}/details/', headers=operator,
                       json={'goods_id': 2, 'location_id': location_id}).status_code == 403
    assert client.put(f'/cyclecount/{cycle_id}/details/{cycle_detail_id}', headers=operator,
                      json={'actual_quantity': 1}).status_code == 403

    # 盘点录入：开始 → 录入 → 完成明细 → 完成盘点单
    assert client.put(f'/cyclecount/{cycle_id}/process/', headers=operator).status_code == 200
    response = client.post(f'/cyclecount/{cycle_id}/details-batch-save/', headers=operator,
                           json={'details': [{'id': cycle_detail_id, 'actual_quantity': 98}]})
    assert response.status_code == 201, response.get_json()
    response = client.put(f'/cyclecount/{cycle_id}/details/{cycle_detail_id}/complete/', headers=operator)
    assert response.status_code == 200, response.get_json()
    response = client.put(f'/cyclecount/{cycle_id}/complete/', headers=operator)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'completed'
    with client.application.app_context():
        operator_id = get_operator_user().id
        detail = db.session.get(CycleCountTaskDetail, cycle_detail_id)
        assert (detail.actual_quantity, detail.operator_id) == (98, operator_id)


def test_old_permissions_still_accepted(client, access_token):
    """收货 / 盘点录入接口照旧接受 asn_edit / cycle_count_edit（自建角色、API Key 不受影响）；只有新权限的不能建单"""
    from .test_process_guards import _staff_token
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        location_id = get_location().id
    asn_editor = _staff_token(client, 'asn-editor', ['asn_edit'])
    receiver = _staff_token(client, 'receiver', ['asn_receive'])
    counter = _staff_token(client, 'counter', ['cycle_count_edit'])
    executor = _staff_token(client, 'executor', ['cycle_count_execute'])
    body = {'warehouse_id': warehouse_id, 'supplier_id': 1, 'details': [{'goods_id': 1, 'quantity': 1}]}
    assert client.post('/asn/', headers=receiver, json=body).status_code == 403
    response = client.post('/asn/', headers=asn_editor, json=body)
    assert response.status_code == 201, response.get_json()
    assert client.put(f"/asn/{response.get_json()['id']}/receive/", headers=asn_editor).status_code == 200
    response = client.post('/asn/', headers=asn_editor, json=body)
    assert client.put(f"/asn/{response.get_json()['id']}/receive/", headers=receiver).status_code == 200

    task_body = {'task_name': 'CC-OLD', 'warehouse_id': warehouse_id,
                 'task_details': [{'goods_id': 1, 'location_id': location_id}]}
    assert client.post('/cyclecount/', headers=executor, json=task_body).status_code == 403
    response = client.post('/cyclecount/', headers=counter, json=task_body)
    assert response.status_code == 201, response.get_json()
    assert client.put(f"/cyclecount/{response.get_json()['id']}/process/", headers=counter).status_code == 200
    response = client.post('/cyclecount/', headers=_h(access_token), json=task_body)
    assert client.put(f"/cyclecount/{response.get_json()['id']}/process/", headers=executor).status_code == 200


# ---------------------------------------------------------------------------
# 迁移 c4e8a1f6b2d9
# ---------------------------------------------------------------------------

def _load_migration():
    spec = importlib.util.spec_from_file_location('operator_permission_migration', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(step):
    """在测试库（同一个会话连接）上跑迁移的 upgrade / downgrade"""
    context = MigrationContext.configure(db.session.connection())
    with Operations.context(context):
        getattr(_load_migration(), step)()
    db.session.commit()
    db.session.expire_all()


def _grant(role_name, *names):
    role = Role.query.filter_by(name=role_name).one()
    for name in names:
        permission = Permission.query.filter_by(name=name).first()
        if permission is None:
            permission = Permission(name=name, description=name)
            db.session.add(permission)
        role.permissions.append(permission)
    db.session.commit()


def test_migration_moves_operator_permissions(client):
    with client.application.app_context():
        # 线上的样子：种子刚给作业员补上 asn_edit / cycle_count_edit；仓库经理、自建角色也有 asn_edit
        _grant('warehouse_operator', 'asn_edit', 'cycle_count_edit', 'sorting_edit')
        _grant('warehouse_admin', 'asn_edit', 'cycle_count_edit')
        db.session.add(Role(name='receiving_clerk', description='custom', is_active=True))
        db.session.commit()
        _grant('receiving_clerk', 'asn_edit')
        assert Permission.query.filter_by(name='asn_receive').first() is None

        _run('upgrade')
        assert _role_permissions('warehouse_operator') == {
            'operator', 'sorting_edit', 'asn_receive', 'cycle_count_execute'}
        assert _role_permissions('warehouse_admin') == {'warehouse_all_access', 'asn_edit', 'cycle_count_edit'}
        assert _role_permissions('receiving_clerk') == {'asn_edit'}
        descriptions = {p.name: p.description for p in Permission.query.filter(
            Permission.name.in_(['asn_receive', 'cycle_count_execute']))}
        assert set(descriptions) == {'asn_receive', 'cycle_count_execute'} and all(descriptions.values())

        # 可重复执行
        _run('upgrade')
        assert Permission.query.filter_by(name='asn_receive').count() == 1
        assert _role_permissions('warehouse_operator') == {
            'operator', 'sorting_edit', 'asn_receive', 'cycle_count_execute'}

        # 部署时随后跑的种子不会把旧权限加回去
        seed_builtin()
        operator = _role_permissions('warehouse_operator')
        assert operator == set(BUILTIN_ROLE_DEFAULT_PERMISSIONS['warehouse_operator']) | {'operator'}
        assert not operator & {'asn_edit', 'cycle_count_edit'}

        # downgrade：加回旧权限，新权限保留
        _run('downgrade')
        assert {'asn_edit', 'cycle_count_edit', 'asn_receive', 'cycle_count_execute'} <= \
            _role_permissions('warehouse_operator')
        assert Permission.query.filter_by(name='asn_receive').count() == 1


def test_migration_only_replaces_what_the_operator_holds(client):
    """作业员没有 cycle_count_edit（管理员手工去掉了）：不授予 cycle_count_execute，只换 asn_edit"""
    with client.application.app_context():
        _grant('warehouse_operator', 'asn_edit')
        _run('upgrade')
        assert _role_permissions('warehouse_operator') == {'operator', 'asn_receive'}
        assert Permission.query.filter_by(name='cycle_count_execute').count() == 1
