from tests.helpers import *
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_inbound import foreign
from tests.test_platform_guards import _role, _staff


def permission_ids(client):
    with client.application.app_context():
        for name in ('goods_read','inventory_edit','company_roles_read','company_roles_edit'):
            if not Permission.query.filter_by(name=name).first():
                db.session.add(Permission(name=name,description=name))
        db.session.commit()
        return {row.name:row.id for row in Permission.query.all()}


def test_platform_legacy_role_routes_cannot_bypass_company_revision_and_grant_rules(client, access_token, access_company_admin_token):
    ids = permission_ids(client)
    body = dict(display_name='Company reader',permissions=[ids['goods_read']],is_active=True)
    role = _ok(client.post('/company/roles',headers=_h(access_company_admin_token),json=body))
    url = f'/user/roles/{role["id"]}'
    assert client.get(url,headers=_h(access_token)).status_code == 200
    assert client.put(url,headers=_h(access_token),json={'name':'renamed','permissions':[ids['all_access']]}).status_code == 409
    assert client.delete(url,headers=_h(access_token)).status_code == 409
    unchanged = _ok(client.get(url,headers=_h(access_token)))
    assert unchanged['name'] == role['name'] and unchanged['revision'] == 1
    assert unchanged['permissions'] == ['goods_read']
    updated = _ok(client.put(f'/company/roles/{role["id"]}',headers=_h(access_company_admin_token),json={**body,'revision':1,'display_name':'Updated reader'}))
    assert updated['revision'] == 2


def test_company_role_create_assign_disable_revision_and_tenant_isolation(client, access_company_admin_token, foreign):
    ids=permission_ids(client)
    url='/company/roles'
    body=dict(display_name='Inventory reader',description='Read only',permissions=[ids['goods_read']],is_active=True)
    role=_ok(client.post(url,json=body,headers=_h(access_company_admin_token)))
    assert role['display_name']=='Inventory reader' and not role['readonly'] and role['revision']==1
    assert client.post(url,json=body,headers=_h(access_company_admin_token)).status_code==409
    assert role['id'] not in [r['id'] for r in _ok(client.get(url,headers=_h(foreign['token'])))['items']]
    assert client.put(url+f'/{role["id"]}',json={**body,'revision':1},headers=_h(foreign['token'])).status_code==403
    assert client.delete(url+f'/{role["id"]}',json={'revision':1},headers=_h(foreign['token'])).status_code==403
    other=_ok(client.post(url,json=body,headers=_h(foreign['token'])))
    assert other['name']!=role['name']
    with client.application.app_context():
        operator=get_operator_user();operator_id=operator.id
        operator_token=create_access_token(identity=operator)
        warehouse_id=get_warehouse().id
    assigned=_ok(client.put(f'/staff/{operator_id}',json={'roles':[role['name']]},headers=_h(access_company_admin_token)))
    assert assigned['role_labels'][role['name']]=='Inventory reader'
    response=client.get('/goods/',headers={**_h(operator_token),'X-WAREHOUSE-ID':str(warehouse_id)})
    assert response.status_code==200, response.json
    assert client.put(f'/staff/{operator_id}',json={'roles':[other['name']]},headers=_h(access_company_admin_token)).status_code==403
    assert client.delete(url+f'/{role["id"]}',json={'revision':1},headers=_h(access_company_admin_token)).status_code==409
    changed=_ok(client.put(url+f'/{role["id"]}',json={**body,'display_name':'Renamed reader','is_active':False,'revision':1},headers=_h(access_company_admin_token)))
    assert changed['name']==role['name'] and changed['revision']==2
    response=client.get('/goods/',headers={**_h(operator_token),'X-WAREHOUSE-ID':str(warehouse_id)})
    assert response.status_code==403, response.json
    assert client.put(url+f'/{role["id"]}',json={**body,'revision':1},headers=_h(access_company_admin_token)).status_code==409
    _ok(client.put(f'/staff/{operator_id}',json={'roles':['warehouse_operator']},headers=_h(access_company_admin_token)))
    _ok(client.delete(url+f'/{role["id"]}',json={'revision':2},headers=_h(access_company_admin_token)))


def test_company_roles_cannot_escalate_or_modify_global_roles(client, access_company_admin_token):
    ids=permission_ids(client)
    with client.application.app_context():
        manager=_staff('role_manager',_role('limited_role_manager', ['company_roles_edit','goods_read']),[get_warehouse()])
        token=create_access_token(identity=manager)
        builtin_id=Role.query.filter_by(name='warehouse_operator').one().id
    url='/company/roles'
    for forbidden in ('all_access','company_all_access','inventory_edit'):
        assert client.post(url,json=dict(display_name='Escalation',permissions=[ids[forbidden]]),headers=_h(token)).status_code==403
    assert client.put(url+f'/{builtin_id}',json=dict(display_name='Changed',revision=1,permissions=[]),headers=_h(access_company_admin_token)).status_code==403
    assert client.delete(url+f'/{builtin_id}',json={'revision':1},headers=_h(access_company_admin_token)).status_code==403
    permissions=_ok(client.get(url+'/permissions',headers=_h(token)))['items']
    assert not {'all_access','company_all_access','role_edit'} & {p['name'] for p in permissions}
    assert next(p for p in permissions if p['name']=='inventory_edit')['grantable'] is False
    _ok(client.post(url,json=dict(display_name='Allowed reader',permissions=[ids['goods_read']]),headers=_h(token)))


def test_company_role_migration_preserves_global_roles_and_blocks_lossy_downgrade():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import pytest
    path=Path(__file__).parents[1]/'migrations/versions/c1f5a9b6e378_company_owned_roles.py'
    spec=importlib.util.spec_from_file_location('company_role_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE companies (id INTEGER PRIMARY KEY)'))
        connection.execute(sa.text('CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT)'))
        connection.execute(sa.text("INSERT INTO roles VALUES (1,'company_admin')"))
        connection.execute(sa.text('CREATE TABLE permissions (name TEXT PRIMARY KEY, description TEXT)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT name,company_id,revision FROM roles')).one()==('company_admin',None,1)
            connection.execute(sa.text("INSERT INTO roles (id,name,company_id,display_name) VALUES (2,'custom',1,'Reader')"))
            with pytest.raises(RuntimeError,match='ownership'):
                migration.downgrade()
            connection.execute(sa.text('DELETE FROM roles WHERE id=2'))
            migration.downgrade()
