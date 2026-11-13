from tests.helpers import *
from tests.test_inventory_guards import _world, _inbound, _ok, _h


def world(client, token):
    w = _world(client)
    _inbound(client, token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    return w


def count(client, token, w):
    return _ok(client.post('/cyclecount/', headers=_h(token), json={'warehouse_id': w['wh'],
        'task_details': [{'goods_id': w['g1'], 'location_id': w['std1']}]}))['id']


def move(client, token, w):
    return client.post('/transfer/', headers=_h(token), json={'goods_id': w['g1'],
        'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 2})


def finish(client, token, ident, actual):
    row = client.get(f'/cyclecount/{ident}/details/', headers=_h(token)).json[0]
    _ok(client.post(f'/cyclecount/{ident}/details-batch-save/', headers=_h(token),
        json={'details': [{'id': row['id'], 'actual_quantity': actual}]}))
    _ok(client.put(f'/cyclecount/{ident}/complete/', headers=_h(token)))


def test_count_freezes_movements_until_zero_variance_completion(client, access_token):
    w = world(client, access_token)
    ident = count(client, access_token, w)
    _ok(client.put(f'/cyclecount/{ident}/process/', headers=_h(access_token)))
    assert move(client, access_token, w).status_code == 409
    finish(client, access_token, ident, 10)
    _ok(move(client, access_token, w))
    with client.application.app_context():
        assert db.session.get(Inventory, (w['g1'], w['wh'])).total_stock == 10


def test_overlap_recount_and_single_post(client, access_token, access_company_admin_token):
    w = world(client, access_token)
    first, other = [count(client, access_token, w) for _ in range(2)]
    h = _h(access_token)
    _ok(client.put(f'/cyclecount/{first}/process/', headers=h))
    assert client.put(f'/cyclecount/{other}/process/', headers=h).status_code == 409
    finish(client, access_token, first, 9)
    old = _ok(client.post(f'/adjustment/create_adjustment_by_cyclecount/{first}', headers=h))['id']
    second = _ok(client.post(f'/cyclecount/{first}/resolve/recount', headers=h, json={'reason': 'Verify shortage'}))['id']
    assert move(client, access_token, w).status_code == 409
    assert client.post(f'/adjustment/create_adjustment_by_cyclecount/{first}', headers=h).status_code == 409
    finish(client, access_token, second, 9)
    adj = _ok(client.post(f'/adjustment/create_adjustment_by_cyclecount/{second}', headers=h))['id']
    for action in ('approve', 'complete'):
        _ok(client.put(f'/adjustment/{adj}/{action}/', headers=_h(access_company_admin_token)))
    with client.application.app_context():
        assert not db.session.get(Adjustment, old).is_active
        assert GoodsLocation.query.filter_by(goods_id=w['g1'], location_id=w['std1']).one().quantity == 9
    _ok(client.put(f'/cyclecount/{other}/process/', headers=h))


def test_cancel_releases_scope_and_disallows_old_results(client, access_token):
    w = world(client, access_token)
    ident = count(client, access_token, w)
    h = _h(access_token)
    _ok(client.put(f'/cyclecount/{ident}/process/', headers=h))
    assert client.post(f'/cyclecount/{ident}/resolve/cancel', headers=h, json={}).status_code == 400
    _ok(client.post(f'/cyclecount/{ident}/resolve/cancel', headers=h, json={'reason': 'Wrong scope'}))
    _ok(move(client, access_token, w))
    assert client.put(f'/cyclecount/{ident}/complete/', headers=h).status_code == 409


def test_concurrent_overlapping_counts_only_one_can_freeze(client,access_token):
    from tests.test_warehouse_custody import race
    w=world(client,access_token);h=_h(access_token)
    first,second=[count(client,access_token,w) for _ in range(2)]
    results=race(client,[lambda c:c.put(f'/cyclecount/{first}/process/',headers=h),lambda c:c.put(f'/cyclecount/{second}/process/',headers=h)])
    assert sorted(r[0] for r in results)==[200,409],results


def test_count_migration_does_not_trust_old_recorded_results():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('count_control_migration',Path(__file__).resolve().parents[1]/'migrations/versions/b2e6f0a7c489_count_control.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with create_engine('sqlite://').begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE cycle_count_tasks (id INTEGER PRIMARY KEY, status VARCHAR(20))'))
        conn.execute(text('CREATE TABLE cycle_count_details (id INTEGER PRIMARY KEY, task_id INTEGER, status VARCHAR(20), operator_id INTEGER)'))
        conn.execute(text("INSERT INTO cycle_count_tasks VALUES (1,'pending'),(2,'in_progress'),(3,'completed'),(4,'pending')"))
        conn.execute(text("INSERT INTO cycle_count_details VALUES (1,4,'pending',1)"))
        migration.op=Operations(MigrationContext.configure(conn));migration.upgrade()
        assert dict(conn.execute(text('SELECT id,control_state FROM cycle_count_tasks')).all())=={1:'draft',2:'legacy',3:'legacy',4:'legacy'}
        migration.downgrade()
        assert conn.execute(text('SELECT count(*) FROM cycle_count_tasks')).scalar()==4
