from datetime import timedelta
from tests.helpers import *
from tests.test_inventory_guards import _world, _h, _ok
from tests.test_damaged_putaway import _sort_in
from warehouse.putaway.models import ReceiptPendingStock, PutawayRecord
from warehouse.putaway.report import putaway_timing
from warehouse.asn.flow import InboundFlowService
from warehouse.common import clock


def test_putaway_requires_explicit_source_and_removal_does_not_reopen_receipt(client, access_token):
    w = _world(client)
    first = _sort_in(client, access_token, w, w['g1'], 3, 0)
    second = _sort_in(client, access_token, w, w['g1'], 5, 0)
    url = f'/inventory/goods/{w["g1"]}/warehouse/{w["wh"]}/pending-lots'
    sources = _ok(client.get(url, headers=_h(access_token)))['items']
    assert {row['asn_id'] for row in sources} == {first, second}
    body = dict(goods_id=w['g1'], location_id=w['std1'], quantity=3)
    assert client.post('/putaway/', json=body, headers=_h(access_token)).status_code == 400
    source = next(row for row in sources if row['asn_id'] == first)
    body['receipt_source_id'] = source['receipt_source_id']
    record = _ok(client.post('/putaway/', json=body, headers=_h(access_token)))
    assert record['source_asn_id'] == first
    assert client.post('/putaway/', json=body, headers=_h(access_token)).status_code == 400
    _ok(client.post('/removal/', json=dict(goods_id=w['g1'], location_id=w['std1'], quantity=1, reason='transfer'), headers=_h(access_token)))
    with client.application.app_context():
        from warehouse.asn.stage import stage_of
        assert stage_of(db.session.get(ASN, first)) == 'completed'
        assert stage_of(db.session.get(ASN, second)) == 'putaway'
    refreshed = _ok(client.get(url, headers=_h(access_token)))['items']
    assert next(row for row in refreshed if row['receipt_source_id'] == 0)['sorted_stock'] == 1
    body.update(receipt_source_id=0, quantity=1)
    assert _ok(client.post('/putaway/', json=body, headers=_h(access_token)))['source_asn_id'] is None


def test_putaway_timing_weights_units_and_never_uses_unrelated_records(client, access_token):
    w = _world(client)
    asn_id = _sort_in(client, access_token, w, w['g1'], 10, 0)
    body = dict(goods_id=w['g1'], location_id=w['std1'], quantity=9)
    first = _ok(client.post('/putaway/', json=body, headers=_h(access_token)))
    body['quantity'] = 1
    second = _ok(client.post('/putaway/', json=body, headers=_h(access_token)))
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, w['wh'])
        now = clock.now()
        asn = db.session.get(ASN, asn_id)
        asn.completed_at = now-timedelta(days=5)
        db.session.get(PutawayRecord, first['id']).putaway_time = now-timedelta(days=4)
        db.session.get(PutawayRecord, second['id']).putaway_time = now
        legacy = PutawayRecord(goods_id=w['g1'],location_id=w['std1'],quantity=100,operator_id=w['admin'],putaway_time=now)
        db.session.add(legacy); db.session.commit()
        data = putaway_timing(warehouse, clock.local_today(warehouse)-timedelta(days=10), clock.local_today(warehouse))
        assert data['known_quantity'] == 10 and data['unknown_quantity'] == 200  # includes the legacy fixture's 100 units
        assert data['median_days'] == data['p90_days'] == 1 and data['overdue_quantity'] == 1
        assert data['buckets']['day_3_5']['quantity'] == 1
        assert InboundFlowService._last_putaway_at(asn,[w['g1']]) == now
        asn.putaway_tracked = False
        assert InboundFlowService._last_putaway_at(asn,[w['g1']]) is None


def test_newer_receipt_putaway_does_not_age_out_older_pending_source(client, access_token):
    from warehouse.inventory.pending import overdue_summary
    w = _world(client)
    older = _sort_in(client, access_token, w, w['g1'], 4, 0)
    newer = _sort_in(client, access_token, w, w['g1'], 4, 0)
    with client.application.app_context():
        now = clock.now()
        db.session.get(ASN, older).completed_at = now-timedelta(days=5)
        db.session.get(ASN, newer).completed_at = now-timedelta(hours=1)
        source_id = ReceiptPendingStock.query.filter_by(asn_id=newer).one().id
        db.session.commit()
    _ok(client.post('/putaway/', json=dict(goods_id=w['g1'],location_id=w['std1'],quantity=4,
        receipt_source_id=source_id),headers=_h(access_token)))
    with client.application.app_context():
        result = overdue_summary([(w['g1'],w['wh'],4)],3,now)
        assert result['overdue_quantity'] == 4
        assert result['oldest_pending_since'] == now-timedelta(days=5)


def test_putaway_source_migration_does_not_backfill_history():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import pytest
    path = Path(__file__).parents[1]/'migrations/versions/a9d3e7f4c156_putaway_receipt_sources.py'
    spec = importlib.util.spec_from_file_location('putaway_migration',path)
    migration = importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        for table in ('asn','warehouses','goods','goods_lots','putaway_records'):
            connection.execute(sa.text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
            connection.execute(sa.text(f'INSERT INTO {table} (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT putaway_tracked FROM asn')).scalar() == 0
            assert connection.execute(sa.text('SELECT receipt_source_id FROM putaway_records')).scalar() is None
            connection.execute(sa.text('UPDATE asn SET putaway_tracked=true'))
            with pytest.raises(RuntimeError,match='provenance'):
                migration.downgrade()
            connection.execute(sa.text('UPDATE asn SET putaway_tracked=false'))
            migration.downgrade()
