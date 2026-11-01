from tests.helpers import *
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_inbound import foreign
from system.third_party.models import APICallLog


def test_call_history_records_get_errors_and_scopes_without_secrets(client, access_company_admin_token, foreign):
    headers = _h(access_company_admin_token)
    key = _ok(client.post('/api-keys/api-keys', headers=headers, json={'system_name':'History', 'permissions':['goods_read']}))
    key_headers = {'X-API-KEY':key['key'], 'X-WAREHOUSE-ID':'1'}
    response = client.get('/goods/?secret=never-store-this', headers=key_headers)
    assert response.status_code == 200, response.json
    assert client.get('/staff/', headers=key_headers).status_code == 403
    assert client.get('/unknown/never-store-this?token=secret', headers=key_headers).status_code == 404
    url = f'/api-keys/api-keys/{key["id"]}/calls'
    result = _ok(client.get(url, headers=headers))
    assert result['total'] == 3
    assert {r['status_code'] for r in result['items']} == {200,403,404}
    assert all(r['duration_ms'] >= 0 for r in result['items'])
    assert 'never-store-this' not in str(result) and key['key'] not in str(result)
    assert _ok(client.get(url+'?status=4xx&per_page=1',headers=headers))['total']==2
    assert client.get(url+'?status=bad',headers=headers).status_code==400
    assert client.get(url+'?from=2026-02-02&before=2026-01-01',headers=headers).status_code==400
    assert client.get(url,headers=_h(foreign['token'])).status_code==403
    summary='/api-keys/api-keys/call-summary?api_key_ids='+str(key['id'])
    assert _ok(client.get(summary,headers=headers))['items'][0]['last_called_at']
    assert _ok(client.get(summary,headers=_h(foreign['token'])))['items']==[]
    assert _ok(client.get(url,headers=headers))['total']==3  # JWT reads aren't key calls.


def test_history_never_commits_business_session(client):
    app=client.application
    @app.route('/history-uncommitted')
    def uncommitted():
        db.session.add(Permission(name='must_not_commit',description='uncommitted'))
        db.session.flush()
        return {}, 500
    response=client.get('/history-uncommitted',headers={'X-API-KEY':TEST_API_KEY_PLAIN})
    assert response.status_code==500
    with app.app_context():
        assert Permission.query.filter_by(name='must_not_commit').first() is None
        assert APICallLog.query.filter_by(route='/history-uncommitted',status_code=500).count()==1


def test_api_history_migration_has_no_backfill_and_protects_records():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import pytest
    path=Path(__file__).parents[1]/'migrations/versions/d2a6b0c7f489_api_call_history.py'
    spec=importlib.util.spec_from_file_location('api_history_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with sa.create_engine('sqlite://').begin() as connection:
        connection.execute(sa.text('CREATE TABLE api_keys (id INTEGER PRIMARY KEY)'))
        connection.execute(sa.text('INSERT INTO api_keys VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT COUNT(*) FROM api_call_logs')).scalar()==0
            connection.execute(sa.text("INSERT INTO api_call_logs VALUES (1,1,'2026-10-31','GET','/goods/',200,1)"))
            with pytest.raises(RuntimeError,match='history'):
                migration.downgrade()
            connection.execute(sa.text('DELETE FROM api_call_logs'))
            migration.downgrade()
