import csv
import io
from datetime import timedelta

from tests.helpers import *
from tests.test_inventory_guards import _world, _h, _ok
from tests.test_damaged_putaway import _sort_in
from tests.test_platform_guards import _role, _staff
from warehouse.common import clock
from warehouse.putaway.models import ReceiptPendingStock
from tests.test_isolation_inbound import foreign
from tests.test_lot_operations import receive_lots

URL = '/inventory/pending-putaway/sources'


def test_queue_keeps_receipts_separate_and_unknown_unattributed(client, access_token):
    w = _world(client)
    older = _sort_in(client, access_token, w, w['g1'], 3, 2)
    newer = _sort_in(client, access_token, w, w['g1'], 4, 0)
    with client.application.app_context():
        add_stock(w['g1'], w['wh'], pending=2)
        db.session.get(ASN, older).completed_at = clock.now()-timedelta(days=5)
        db.session.get(ASN, newer).completed_at = clock.now()-timedelta(hours=1)
        db.session.commit()
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    data = _ok(client.get(URL, headers=headers))
    assert data['total'] == 3
    assert data['summary'] == dict(goods_count=1, quantity=11, damaged_quantity=2, unknown_quantity=2)
    assert [row['asn_id'] for row in data['items']] == [older, newer, None]
    assert len({row['id'] for row in data['items']}) == 3
    assert data['items'][-1]['pending_since'] is None
    assert data['items'][0]['good_quantity'] == 3
    for query, count, quantity in [('source=unknown',1,2), ('source=asn',2,9),
                                  ('damaged=true',1,5), ('has_lot=true',0,0),
                                  ('overdue_days=3',1,5), ('overdue_days=7',0,0),
                                  (f'keyword=ASN-{newer}',1,4)]:
        filtered = _ok(client.get(URL+'?'+query, headers=headers))
        assert (filtered['total'], filtered['summary']['quantity']) == (count, quantity)
    assert _ok(client.get(URL+'?page=2&per_page=1', headers=headers))['items'][0]['asn_id'] == newer
    with client.application.app_context():
        db.session.get(Goods, w['g1']).name = '=SUM(1,2)'
        db.session.commit()
    exported = client.get(URL+'/export?page=99&per_page=1', headers=headers)
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert len(rows) == 3 and sum(int(row['total_quantity']) for row in rows) == 11
    assert all(row['goods_name'] == "'=SUM(1,2)" for row in rows)
    assert rows[-1]['asn_id'] == rows[-1]['pending_since_utc'] == ''


def test_queue_validates_scope_filters_permissions_and_inconsistent_sources(client, access_token):
    w = _world(client)
    asn = _sort_in(client, access_token, w, w['g1'], 2, 0)
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    for suffix in ['?source=removal', '?overdue_days=-1', '?overdue_days=366', '?page=0']:
        assert client.get(URL+suffix, headers=headers).status_code == 400
    assert client.get(URL, headers=_h(access_token)).status_code == 400
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, w['wh'])
        stranger = _staff('pending_sources_stranger', _role('pending_sources_stranger', ['goods_read']), [warehouse])
        token = create_access_token(identity=stranger)
    for suffix in ['', '/export']:
        assert client.get(URL+suffix, headers={**_h(token), 'X-WAREHOUSE-ID':str(w['wh'])}).status_code == 403
    with client.application.app_context():
        source = ReceiptPendingStock.query.filter_by(asn_id=asn).one()
        source.good_remaining = source.good_received = 3
        db.session.commit()
    result = client.get(URL, headers=headers)
    assert result.status_code == 400 and result.get_json()['code'] == 16173


def test_queue_lot_removal_stays_unknown_and_foreign_warehouse_is_denied(client, access_token, foreign):
    w = _world(client)
    lots = receive_lots(client, access_token, w)
    _ok(client.post('/removal/', headers=_h(access_token), json=dict(
        goods_id=w['g1'], location_id=w['std1'], lot_id=lots[0], quantity=2, reason='transfer')))
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    result = _ok(client.get(URL+'?has_lot=true&keyword=LOT-A', headers=headers))
    assert result['total'] == 1
    row = result['items'][0]
    assert row['lot_id'] == lots[0] and row['source_type'] == 'unknown'
    assert row['asn_id'] is None and row['pending_since'] is None
    assert row['sorted_stock'] == 2 and row['location_count'] == 1
    assert row['locations'][0]['quantity'] == 3
    assert row['tracks_lots'] and row['remaining_days'] == 30
    from warehouse.stock.models import GoodsLotRules
    with client.application.app_context():
        db.session.add(GoodsLotRules(goods_id=w['g1'],revision=1,overrides={'receive_days':45,'warning_days':60}))
        db.session.commit()
    warned = _ok(client.get(URL+'?has_lot=true&keyword=LOT-A', headers=headers))['items'][0]
    assert warned['expiry_status'] == 'below_acceptance' and warned['accept_days'] == 45
    assert warned['sorted_stock'] == 2
    for suffix in ['', '/export']:
        assert client.get(URL+suffix, headers={**_h(foreign['token']), 'X-WAREHOUSE-ID':str(w['wh'])}).status_code == 403

def test_dn_pending_source_migration_roundtrip_and_loss_guard():
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('pending_migration', Path(__file__).resolve().parents[1]/'migrations/versions/c7f1a5b2d934_pending_dn_sources.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE asn (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE dn (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE receipt_pending_stock (id INTEGER PRIMARY KEY, asn_id INTEGER NOT NULL REFERENCES asn(id))')
        connection.exec_driver_sql('INSERT INTO asn VALUES (1)')
        connection.exec_driver_sql('INSERT INTO dn VALUES (2)')
        connection.exec_driver_sql('INSERT INTO receipt_pending_stock VALUES (1,1)')
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(sa.text('SELECT source_type FROM receipt_pending_stock')).scalar() == 'asn'
        connection.exec_driver_sql("INSERT INTO receipt_pending_stock (id,dn_id,source_type,entered_at) VALUES (2,2,'dn_cancel_return','2026-11-02 00:00:00')")
        import pytest
        with pytest.raises(RuntimeError,match='Cannot discard'):
            migration.downgrade()
        connection.exec_driver_sql('DELETE FROM receipt_pending_stock WHERE id=2')
        migration.downgrade()
        assert {col['name'] for col in sa.inspect(connection).get_columns('receipt_pending_stock')} == {'id','asn_id'}
        assert connection.execute(sa.text('SELECT asn_id FROM receipt_pending_stock')).scalar() == 1
    engine.dispose()
