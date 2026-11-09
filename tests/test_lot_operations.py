from datetime import timedelta
from sqlalchemy import text

from tests.helpers import *
from tests.test_inventory_guards import _world, _ok, _h, _create_asn, _task_ids
from warehouse.common import clock
from warehouse.stock.models import GoodsLot, InventoryLotStock, DNStockAllocation, StockLedger
from tests.test_isolation_inbound import foreign


def receive_lots(client, token, w):
    with client.application.app_context():
        # Legacy shared fixtures contain task details with missing batch parents.
        # Restore those parents before allocating new IDs, so unrelated seed details
        # cannot attach to the batches exercised by this multi-order test.
        for detail_model, batch_model, task_key in (
            (PickingTaskDetail, PickingBatch, 'picking_task_id'),
            (PackingTaskDetail, PackingBatch, 'packing_task_id'),
        ):
            for detail in detail_model.query.all():
                if db.session.get(batch_model, detail.batch_id) is None:
                    db.session.add(batch_model(id=detail.batch_id, **{task_key: getattr(detail, task_key)}, operator_id=w['admin']))
                    db.session.flush()
            # PostgreSQL 的 SERIAL 序列不会随显式插入的 id 前移；补齐历史夹具后同步序列，
            # 避免下一次真实批次 INSERT 与这些父记录冲突（SQLite 会自动前移）。
            if db.engine.dialect.name == 'postgresql':
                table = batch_model.__tablename__
                db.session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"
                ), {'table': table})
        today = clock.local_today(w['wh'])
        a = GoodsLot(goods_id=w['g1'], lot_no='LOT-A', expiry_date=today + timedelta(days=30))
        b = GoodsLot(goods_id=w['g1'], lot_no='LOT-B', expiry_date=today + timedelta(days=90))
        db.session.add_all([a, b]); db.session.commit()
        ids = [a.id, b.id]
    asn = _create_asn(client, token, w, [(w['g1'], 10)])
    _ok(client.put(f'/asn/{asn}/receive/', headers=_h(token)))
    task = _task_ids(client, SortingTask, asn_id=asn, is_active=True)[0]
    _ok(client.put(f'/sorting/{task}/process/', headers=_h(token)))
    body = {'client_batch_id': 'lot-receipt', 'details': [
        {'goods_id': w['g1'], 'lot_id': lot, 'sorted_quantity': 5, 'damage_quantity': 0} for lot in ids]}
    first = _ok(client.post(f'/sorting/{task}/batches/', json=body, headers=_h(token)))
    replay = client.post(f'/sorting/{task}/batches/', json=body, headers=_h(token))
    assert replay.status_code == 200 and replay.json['id'] == first['id']
    body['details'][0]['lot_id'] = ids[1]
    assert client.post(f'/sorting/{task}/batches/', json=body, headers=_h(token)).status_code == 409
    _ok(client.put(f'/sorting/{task}/complete/', headers=_h(token)))
    for lot in ids:
        result = _ok(client.post('/putaway/', json={'goods_id': w['g1'], 'location_id': w['std1'],
                         'lot_id': lot, 'quantity': 5}, headers=_h(token)))
        assert result['lot_id'] == lot
    return ids


def pick_lots(client, token, w, picks):
    dn = _ok(client.post('/dn/', json={'warehouse_id': w['wh'], 'recipient_id': w['recipient'],
        'shipping_address': 'x', 'expected_shipping_date': warehouse_today().isoformat(),
        'details': [{'goods_id': w['g1'], 'quantity': sum(q for _, q in picks)}]}, headers=_h(token)))['id']
    _ok(client.put(f'/dn/{dn}/progress/', headers=_h(token)))
    task = _task_ids(client, PickingTask, dn_id=dn)[0]
    _ok(client.put(f'/picking/{task}/process/', headers=_h(token)))
    body = {'client_batch_id': 'lot-pick', 'details': [
        {'goods_id': w['g1'], 'location_id': w['std1'], 'lot_id': lot, 'picked_quantity': q} for lot, q in picks]}
    _ok(client.post(f'/picking/{task}/batches/', json=body, headers=_h(token)))
    assert client.post(f'/picking/{task}/batches/', json=body, headers=_h(token)).status_code == 200
    _ok(client.put(f'/picking/{task}/complete/', headers=_h(token)))
    return dn, _task_ids(client, PackingTask, dn_id=dn)[0]


def test_putaway_locations_prioritize_same_lot_and_explain_mixing(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    url = f'/goods/{w["g1"]}/putaway-locations'
    first = _ok(client.get(url + f'?lot_id={a}&per_page=1', headers=headers))
    assert first['items'][0]['id'] == w['std1']
    assert first['items'][0]['same_lot_quantity'] == 5
    assert first['items'][0]['other_lot_quantity'] == 5
    assert all(row['warehouse_id'] == w['wh'] for row in _ok(client.get(url, headers=headers))['items'])
    unassigned = next(row for row in _ok(client.get(url, headers=headers))['items'] if row['id'] == w['std1'])
    assert unassigned['same_lot_quantity'] == 0 and unassigned['other_lot_quantity'] == 10
    with client.application.app_context():
        db.session.get(Location, w['std1']).is_active = False
        db.session.commit()
    assert w['std1'] not in [row['id'] for row in _ok(client.get(url, headers=headers))['items']]
    assert client.get(url + '?lot_id=999999', headers=headers).status_code == 404
    assert client.get(url + '?lot_id=-1', headers=headers).status_code == 400
    assert client.get(url + '?code=DOES-NOT-EXIST', headers=headers).json['items'] == []


def test_goods_lots_filter_stock_before_pagination(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    with client.application.app_context():
        empty = GoodsLot(goods_id=w['g1'], lot_no='EMPTY')
        db.session.add(empty); db.session.commit()
        empty_id = empty.id
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    url = f'/goods/{w["g1"]}/lots'
    active = _ok(client.get(url+'?in_stock=true&per_page=1', headers=headers))
    assert active['total'] == 2 and active['items'][0]['id'] in (a, b)
    all_rows = _ok(client.get(url+'?in_stock=false', headers=headers))
    assert all_rows['total'] == 3 and all_rows['items'][-1]['id'] == empty_id
    assert client.get(url+'?in_stock=invalid', headers=headers).status_code == 400


def test_lots_survive_short_pack_ship_sign_without_cross_order_stock(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    balances = _ok(client.get(f'/inventory/lot-balances?goods_id={w["g1"]}&lot_ids={a},{b}', headers=_h(access_token)))['items']
    assert {row['lot_id']: row['total_stock'] for row in balances} == {None: 0, a: 5, b: 5}
    dn, pack = pick_lots(client, access_token, w, [(a, 3), (b, 3)])
    other, _ = pick_lots(client, access_token, w, [(b, 2)])
    _ok(client.put(f'/packing/{pack}/process/', headers=_h(access_token)))
    _ok(client.post(f'/packing/{pack}/batches/', json={'details': [{'goods_id': w['g1'], 'packed_quantity': 4}]}, headers=_h(access_token)))
    choices=_ok(client.get(f'/packing/{pack}/lot-allocations',headers=_h(access_token)))
    assert choices['required']
    _ok(client.put(f'/packing/{pack}/complete/', json={'lot_allocations':[{'id':row['id'],'quantity':3 if row['lot_id']==a else 1} for row in choices['items']]}, headers=_h(access_token)))
    with client.application.app_context():
        allocations = DNStockAllocation.query.filter_by(dn_id=dn).all()
        assert {r.lot_id: (r.picked_quantity, r.packed_quantity) for r in allocations} == {a: (3, 3), b: (3, 1)}
        bins = InventoryLotStock.query.filter_by(warehouse_id=w['wh'], goods_id=w['g1']).all()
        assert {r.lot_id: (r.sorted_stock, r.picked_stock, r.packed_stock) for r in bins} == {a: (0, 0, 3), b: (2, 2, 1)}
        assert db.session.get(GoodsLot, a).first_inbound_at is not None
    delivery = _task_ids(client, DeliveryTask, dn_id=dn)[0]
    _ok(client.put(f'/delivery/{delivery}/process/', headers=_h(access_token)))
    _ok(client.put(f'/delivery/{delivery}/complete/', json={}, headers=_h(access_token)))
    with client.application.app_context():
        from warehouse.dn.services import DNService
        DNService.complete_dn(dn)
        rows = StockLedger.query.filter_by(doc_type='dn', doc_id=dn, movement_type='ship', stock_type='in_transit').all()
        assert {r.lot_id: r.quantity for r in rows} == {a: 3, b: 1}
        bins = InventoryLotStock.query.filter_by(warehouse_id=w['wh'], goods_id=w['g1']).all()
        assert {r.lot_id: (r.sorted_stock, r.picked_stock, r.packed_stock, r.delivered_stock) for r in bins} == {b: (2, 2, 0, 0)}
    _ok(client.put(f'/dn/{other}/cancel/', json={}, headers=_h(access_token)))
    with client.application.app_context():
        assert InventoryLotStock.query.filter_by(warehouse_id=w['wh'], goods_id=w['g1'], lot_id=b).one().sorted_stock == 4
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    queue = _ok(client.get('/inventory/pending-putaway/sources', headers=headers))
    returned = [row for row in queue['items'] if row['goods_id'] == w['g1']]
    assert {(row['source_type'],row['dn_id'],row['sorted_stock']) for row in returned} == {
        ('pack_short_return',dn,2), ('dn_cancel_return',other,2)}
    assert all(row['pending_since'] and row['lot_id']==b for row in returned)
    assert _ok(client.get(f'/inventory/pending-putaway/sources?keyword=DN-{other}',headers=headers))['total']==1
    source = next(row for row in returned if row['dn_id']==other)
    put = _ok(client.post('/putaway/',json=dict(goods_id=w['g1'],location_id=w['std1'],lot_id=b,
        quantity=1,receipt_source_id=source['receipt_source_id']),headers=headers))
    assert put['receipt_source_id']==source['receipt_source_id']
    remaining = _ok(client.get('/inventory/pending-putaway/sources',headers=headers))['items']
    assert next(row for row in remaining if row['dn_id']==other)['sorted_stock']==1
    assert next(row for row in remaining if row['dn_id']==dn)['sorted_stock']==2



def test_partial_mixed_lot_pack_requires_actual_selection_and_returns_the_correct_lot(client,access_token,foreign):
    w=_world(client)
    a,b=receive_lots(client,access_token,w)
    dn,pack=pick_lots(client,access_token,w,[(a,3),(b,3)])
    _ok(client.put(f'/packing/{pack}/process/',headers=_h(access_token)))
    _ok(client.post(f'/packing/{pack}/batches/',json={'details':[{'goods_id':w['g1'],'packed_quantity':3}]},headers=_h(access_token)))
    url=f'/packing/{pack}/lot-allocations'
    assert client.get(url,headers=_h(foreign['token'])).status_code in (403,404)
    choices=_ok(client.get(url,headers=_h(access_token)))
    assert choices['required'] and all(row['packed_quantity'] is None for row in choices['items'])
    payload=[{'id':row['id'],'quantity':0 if row['lot_id']==a else 3} for row in choices['items']]
    endpoint=f'/packing/{pack}/complete/'
    for body in ({},{'lot_allocations':payload[:1]},{'lot_allocations':payload+payload[:1]},
                 {'lot_allocations':[{'id':999999,'quantity':3}]},
                 {'lot_allocations':[{**row,'quantity':4} for row in payload]},
                 {'lot_allocations':[{**row,'quantity':True} for row in payload]}):
        response=client.put(endpoint,json=body,headers=_h(access_token))
        assert response.status_code==400
    with client.application.app_context():
        assert db.session.get(DN,dn).status=='picked'
    _ok(client.put(endpoint,json={'lot_allocations':payload},headers=_h(access_token)))
    with client.application.app_context():
        allocation={row.lot_id:row.packed_quantity for row in DNStockAllocation.query.filter_by(dn_id=dn)}
        assert allocation=={a:0,b:3}
        returned=StockLedger.query.filter_by(doc_type='packing_task',doc_id=pack,movement_type='pack_short_return',stock_type='pending_putaway').all()
        assert all(row.lot_id==a for row in returned)
        assert InventoryLotStock.query.filter_by(warehouse_id=w['wh'],goods_id=w['g1'],lot_id=a).one().sorted_stock==3
        assert InventoryLotStock.query.filter_by(warehouse_id=w['wh'],goods_id=w['g1'],lot_id=b).one().packed_stock==3


def test_lot_expiry_and_product_ownership_reject_before_stock_change(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    bad = client.post('/putaway/', json={'goods_id': w['g2'], 'location_id': w['std1'], 'lot_id': a, 'quantity': 1}, headers=_h(access_token))
    assert bad.status_code == 400 and bad.json['code'] == 16170
    dn, pack = pick_lots(client, access_token, w, [(a, 2)])
    _ok(client.put(f'/packing/{pack}/process/', headers=_h(access_token)))
    _ok(client.post(f'/packing/{pack}/batches/', json={'details': [{'goods_id': w['g1'], 'packed_quantity': 2}]}, headers=_h(access_token)))
    _ok(client.put(f'/packing/{pack}/complete/', headers=_h(access_token)))
    with client.application.app_context():
        db.session.get(GoodsLot, a).expiry_date = clock.local_today(w['wh']) - timedelta(days=1)
        db.session.commit()
    delivery = _task_ids(client, DeliveryTask, dn_id=dn)[0]
    _ok(client.put(f'/delivery/{delivery}/process/', headers=_h(access_token)))
    failed = client.put(f'/delivery/{delivery}/complete/', json={}, headers=_h(access_token))
    assert failed.status_code == 400 and failed.json['code'] == 16171
    with client.application.app_context():
        assert db.session.get(DN, dn).status == 'packed'
        assert InventoryLotStock.query.filter_by(goods_id=w['g1'], warehouse_id=w['wh'], lot_id=a).one().packed_stock == 2
        assert not StockLedger.query.filter_by(doc_type='dn', doc_id=dn, movement_type='ship').count()


def test_lot_rules_inherit_per_field_and_block_unshippable_stock(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    with client.application.app_context():
        goods = db.session.get(Goods, w['g1'])
        company_id = goods.company_id
        goods.category = 'FOOD'
        db.session.commit()
    company_url = f'/company/{company_id}/lot-rules'
    company = _ok(client.get(company_url, headers=_h(access_token)))
    company['defaults']['mode'] = 'lot'
    company['categories'] = {'FOOD': {'mode': 'expiry', 'ship_days': 60}}
    _ok(client.put(company_url, json=company, headers=_h(access_token)))
    assert client.put(company_url, json=company, headers=_h(access_token)).status_code == 409
    url = f'/goods/{w["g1"]}/lot-rules'
    rules = _ok(client.get(url, headers=_h(access_token)))
    assert rules['values']['ship_days'] == 60 and rules['sources']['ship_days'] == 'category'
    assert rules['sources']['strategy'] == 'company'
    with client.application.app_context():
        inventory = db.session.get(Inventory, (w['g1'], w['wh']))
        assert inventory.onhand_stock == 10 and inventory.available_stock_for_sale == 5
    changed = _ok(client.put(url, json={'revision': 0, 'overrides': {'ship_days': 10}}, headers=_h(access_token)))
    assert changed['values']['mode'] == 'expiry' and changed['sources']['ship_days'] == 'goods'
    assert client.put(url, json={'revision': 1, 'overrides': {'mode': 'none'}}, headers=_h(access_token)).status_code == 409
    assert client.put(url, json={'revision': 1, 'overrides': {'ship_days': True}}, headers=_h(access_token)).status_code == 400
    _ok(client.put(url, json={'revision': 1, 'overrides': {}}, headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(Inventory, (w['g1'], w['wh'])).available_stock_for_sale == 5


def test_receiving_policy_auto_number_and_pending_lot_selection(client, access_token):
    w = _world(client)
    rules_url = f'/goods/{w["g1"]}/lot-rules'
    _ok(client.put(rules_url, json={'revision': 0, 'overrides': {
        'mode': 'expiry', 'source': 'auto', 'shelf_life_days': 90, 'receive_days': 30}}, headers=_h(access_token)))
    today = warehouse_today()
    lot = _ok(client.post(f'/goods/{w["g1"]}/lots', json={'production_date': today.isoformat()}, headers=_h(access_token)))
    assert lot['source'] == 'auto' and lot['expiry_date'] == (today + timedelta(days=90)).isoformat()
    asn = _create_asn(client, access_token, w, [(w['g1'], 4)])
    _ok(client.put(f'/asn/{asn}/receive/', headers=_h(access_token)))
    task = _task_ids(client, SortingTask, asn_id=asn, is_active=True)[0]
    _ok(client.put(f'/sorting/{task}/process/', headers=_h(access_token)))
    body = {'client_batch_id': 'policy-receive', 'details': [{'goods_id': w['g1'], 'sorted_quantity': 4, 'damage_quantity': 0}]}
    bad = client.post(f'/sorting/{task}/batches/', json=body, headers=_h(access_token))
    assert bad.status_code == 400 and bad.json['code'] == 16175
    body['details'][0]['lot_id'] = lot['id']
    _ok(client.post(f'/sorting/{task}/batches/', json=body, headers=_h(access_token)))
    _ok(client.put(f'/sorting/{task}/complete/', headers=_h(access_token)))
    pending_url = f'/inventory/goods/{w["g1"]}/warehouse/{w["wh"]}/pending-lots'
    pending = _ok(client.get(pending_url, headers=_h(access_token)))['items']
    assert len(pending) == 1 and pending[0]['receipt_source_id'] > 0 and pending[0]['asn_id'] == asn
    assert {key: pending[0][key] for key in ('lot_id','lot_no','expiry_date','sorted_stock','sorted_damage_stock')} == dict(lot_id=lot['id'], lot_no=lot['lot_no'], expiry_date=lot['expiry_date'], sorted_stock=4, sorted_damage_stock=0)


def test_lot_migration_preserves_old_operations_and_rejects_lossy_downgrade():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'migrations/versions/e7b1c5d2a934_lot_operation_provenance.py'
    spec = importlib.util.spec_from_file_location('lot_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        for table in ('companies', 'goods', 'goods_lots', 'dn', *migration.TABLES):
            connection.execute(sa.text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
            connection.execute(sa.text(f'INSERT INTO {table} (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT id, lot_id FROM sorting_task_details')).one() == (1, None)
            connection.execute(sa.text('UPDATE sorting_task_details SET lot_id=1'))
            import pytest
            with pytest.raises(RuntimeError, match='provenance'):
                migration.downgrade()
            connection.execute(sa.text('UPDATE sorting_task_details SET lot_id=NULL'))
            migration.downgrade()
            assert connection.execute(sa.text('SELECT id FROM sorting_task_details')).scalar() == 1


def test_relabel_is_atomic_replay_safe_and_keeps_total(client, access_token):
    from uuid import uuid4
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    body = dict(goods_id=w['g1'], location_id=w['std1'], from_lot_id=a, to_lot_id=b,
                quantity=2, reason='Correct supplier lot label', request_id=str(uuid4()))
    first = _ok(client.post('/inventory/lot-relabel', json=body, headers={**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}))
    again = _ok(client.post('/inventory/lot-relabel', json=body, headers={**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}))
    assert again['replayed'] and first['ledger_ids'] == again['ledger_ids']
    assert client.post('/inventory/lot-relabel', json={**body, 'quantity': 1}, headers={**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}).status_code == 409
    with client.application.app_context():
        assert db.session.get(Inventory, (w['g1'], w['wh'])).total_stock == 10
        rows = GoodsLocation.query.filter_by(goods_id=w['g1'], location_id=w['std1']).all()
        assert {r.lot_id: r.quantity for r in rows} == {a: 3, b: 7}
        assert sum(r.quantity for r in StockLedger.query.filter_by(doc_type='lot_relabel')) == 0
    assert client.post('/inventory/lot-relabel', json={**body, 'quantity': 4, 'request_id': str(uuid4())}, headers={**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}).status_code == 409


def test_lot_map_counts_expiry_and_enforces_company_scope(client, access_token, foreign):
    from tests.test_platform_guards import _role, _staff
    from warehouse.stock.models import GoodsLotRules
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    with client.application.app_context():
        db.session.add(GoodsLotRules(goods_id=w['g1'], revision=1, overrides={'warning_days': 45}))
        db.session.commit()
        reader = _staff('lot_stock_reader', _role('lot_stock_reader', ['inventory_read']), [db.session.get(Warehouse, w['wh'])])
        token = create_access_token(identity=reader)
    headers = {**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}
    reader_headers = {**_h(token), 'X-Warehouse-ID': str(w['wh'])}
    items = _ok(client.get(f'/inventory/by-location/items?goods_id={w["g1"]}', headers=reader_headers))['items']
    assert {item['lot']['lot_no']: item['quantity'] for item in items} == {'LOT-A': 5, 'LOT-B': 5}
    assert all(item['lot']['id'] == item['lot_id'] and item['lot']['expiry_date'] for item in items)
    grouped = _ok(client.get(f'/inventory/by-location?location_id={w["std1"]}&include_items=true', headers=reader_headers))
    assert {item['lot']['id'] for item in grouped['items'][0]['items']} == {a, b}
    assert client.get('/inventory/by-location/items', headers={**_h(foreign['token']), 'X-Warehouse-ID': str(w['wh'])}).status_code in (403, 404)
    rows = _ok(client.get('/inventory/location-health', headers=headers))['items']
    row = next(r for r in rows if r['location_id'] == w['std1'])
    assert (row['quantity'], row['expiring'], row['expired'], row['unknown_expiry']) == (10, 5, 0, 0)
    with client.application.app_context():
        db.session.get(GoodsLot, a).expiry_date = warehouse_today()-timedelta(days=1)
        db.session.commit()
    rows = _ok(client.get('/inventory/location-health', headers=headers))['items']
    row = next(r for r in rows if r['location_id'] == w['std1'])
    assert row['expired'] == 5 and row['expiring'] == 0
    expiry = _ok(client.get('/inventory/expiry', headers=headers))
    assert expiry['summary'] == dict(normal=5, warning=0, blocked=5, unknown=0, unassigned=0)
    assert {r['lot_id']: r['quantity'] for r in expiry['items']} == {a: 5, b: 5}
    filtered = _ok(client.get('/inventory/expiry?status=blocked&per_page=1', headers=headers))
    assert filtered['total'] == 1 and filtered['items'][0]['lot_id'] == a
    assert client.get('/inventory/expiry', headers={**_h(foreign['token']), 'X-Warehouse-ID': str(w['wh'])}).status_code in (403, 404)
    assert client.get('/inventory/location-health', headers={**_h(foreign['token']), 'X-Warehouse-ID': str(w['wh'])}).status_code in (403, 404)
    assert client.get(f'/inventory/lot-balances?goods_id={w["g1"]}&lot_ids={a}', headers=_h(foreign['token'])).status_code in (403, 404)
    assert client.post('/inventory/lot-relabel', json={}, headers={**_h(token), 'X-Warehouse-ID': str(w['wh'])}).status_code == 403


def test_expiry_composition_conserves_lots_after_short_pack(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    dn, pack = pick_lots(client, access_token, w, [(a, 3), (b, 3)])
    headers = {**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}
    before = _ok(client.get('/inventory/expiry', headers=headers))
    assert sum(row['quantity'] for row in before['items']) == 10
    assert sum(row['onhand_stock'] for row in before['items']) == 4
    assert sum(row['picked_stock'] for row in before['items']) == 6
    assert all(row['location_count'] == 1 for row in before['items'])
    _ok(client.put(f'/packing/{pack}/process/', headers=_h(access_token)))
    _ok(client.post(f'/packing/{pack}/batches/', json={'details': [
        {'goods_id': w['g1'], 'packed_quantity': 3}]}, headers=_h(access_token)))
    choices = _ok(client.get(f'/packing/{pack}/lot-allocations', headers=_h(access_token)))
    allocations = [{'id': row['id'], 'quantity': 0 if row['lot_id'] == a else 3} for row in choices['items']]
    _ok(client.put(f'/packing/{pack}/complete/', json={'lot_allocations': allocations}, headers=_h(access_token)))
    after = _ok(client.get('/inventory/expiry', headers=headers))
    assert sum(row['quantity'] for row in after['items']) == 10
    assert sum(row['onhand_stock'] for row in after['items']) == 4
    assert sum(row['sorted_stock'] for row in after['items']) == 3
    assert sum(row['packed_stock'] for row in after['items']) == 3
    assert sum(row['picked_stock'] for row in after['items']) == 0
    for row in after['items']:
        assert row['quantity'] == sum(row[key] for key in (
            'onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock', 'picked_stock', 'packed_stock'))
        assert row['production_date'] is None
    filtered = _ok(client.get('/inventory/expiry?keyword=LOT-A', headers=headers))
    assert filtered['total'] == 1 and filtered['items'][0]['lot_id'] == a


def test_expiry_composition_excludes_transit_and_does_not_double_pending_damage(client, access_token):
    from warehouse.stock.service import StockOp, Leg
    w = _world(client)
    with client.application.app_context():
        lot = GoodsLot(goods_id=w['g1'], lot_no='DAMAGE-LOT')
        db.session.add(lot)
        db.session.flush()
        op = StockOp(w['wh'], 'reconcile', operator_id=w['admin'])
        op.move(w['g1'], 5, dst=Leg.at(w['std1'], lot.id))
        op.move(w['g1'], 3, dst=Leg.pending(lot.id))
        op.move(w['g1'], 2, dst=Leg.pending(lot.id, damaged=True))
        op.move(w['g1'], 7, dst=Leg.in_transit(lot.id))
        op.commit()
        db.session.commit()
    headers = {**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}
    result = _ok(client.get('/inventory/expiry', headers=headers))
    row = result['items'][0]
    assert result['total'] == 1
    assert row['quantity'] == 10
    assert row['onhand_stock'] == 5
    assert row['sorted_stock'] == 5 and row['sorted_damage_stock'] == 2
    assert row['status'] == 'unknown' and row['remaining_days'] is None
    assert row['location_count'] == 1


def test_expiry_unassigned_filter_distinguishes_missing_lot_from_missing_date(client, access_token):
    from warehouse.stock.service import StockOp, Leg
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    with client.application.app_context():
        db.session.get(GoodsLot, a).expiry_date = None
        op = StockOp(w['wh'], 'reconcile', operator_id=w['admin'])
        op.move(w['g1'], 3, dst=Leg.pending())
        op.commit()
        db.session.commit()
    headers = {**_h(access_token), 'X-Warehouse-ID': str(w['wh'])}
    all_rows = _ok(client.get('/inventory/expiry', headers=headers))
    assert all_rows['summary']['unknown'] == 8
    assert all_rows['summary']['unassigned'] == 3
    missing = _ok(client.get('/inventory/expiry?has_lot=false', headers=headers))
    assert missing['total'] == 1
    assert missing['items'][0]['lot_id'] is None
    assert missing['items'][0]['quantity'] == 3
    known = _ok(client.get('/inventory/expiry?has_lot=true&status=unknown', headers=headers))
    assert known['total'] == 1 and known['items'][0]['lot_id'] == a
