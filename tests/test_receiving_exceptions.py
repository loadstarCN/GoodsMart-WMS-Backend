from datetime import timedelta
from tests.helpers import *
from tests.test_inventory_guards import _world, _ok, _h, _create_asn, _task_ids
from warehouse.stock.models import GoodsLot, InventoryLotStock
from warehouse.common import clock


def task_for(client, token, w, quantity):
    asn_id = _create_asn(client, token, w, [(w['g1'],quantity)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(token)))
    return asn_id, task_id


def test_rejected_units_are_audited_but_never_stocked_or_double_counted_as_short(client, access_token):
    w = _world(client)
    asn_id, task_id = task_for(client, access_token, w, 10)
    endpoint = f'/sorting/{task_id}/batches/'
    line = dict(goods_id=w['g1'], sorted_quantity=7, damage_quantity=1, rejected_quantity=2)
    body = dict(client_batch_id='receipt-exception',details=[line])
    assert client.post(endpoint,json=body,headers=_h(access_token)).status_code == 400
    line['rejection_reason'] = 'Packaging contamination: returned to supplier'
    first = _ok(client.post(endpoint,json=body,headers=_h(access_token)))
    assert first['details'][0]['rejected_quantity'] == 2
    assert _ok(client.post(endpoint,json=body,headers=_h(access_token)))['id'] == first['id']
    line['rejection_reason'] = 'Changed reason'
    assert client.post(endpoint,json=body,headers=_h(access_token)).status_code == 409
    line.update(rejected_quantity=3)
    assert client.post(endpoint,json=dict(details=[line]),headers=_h(access_token)).status_code == 400
    _ok(client.put(f'/sorting/{task_id}/complete/',headers=_h(access_token)))
    detail = _ok(client.get(f'/asn/{asn_id}', headers=_h(access_token)))
    assert detail['total_rejected_quantity'] == 2
    with client.application.app_context():
        asn = db.session.get(ASN,asn_id)
        assert (asn.details[0].actual_quantity, asn.details[0].rejected_quantity) == (8,2)
        inventory = db.session.get(Inventory,(w['g1'],w['wh']))
        assert inventory.total_stock == inventory.sorted_stock == 8
        asn.expected_arrival_date = clock.local_today(w['wh'])
        db.session.commit()
    result = _ok(client.get('/asn/report',headers={**_h(access_token),'X-WAREHOUSE-ID':str(w['wh'])}))
    difference = result['receipt_differences']
    assert difference['types']['rejected']['quantity'] == 2
    assert difference['types']['short']['quantity'] == 0
    assert difference['types']['damage']['quantity'] == 1


def test_asn_rejected_total_preserves_unknown_history(client, access_token):
    w = _world(client)
    asn_id = _create_asn(client, access_token, w, [(w['g1'], 2)])
    with client.application.app_context():
        line = db.session.get(ASN, asn_id).details[0]
        line.rejected_quantity = None
        db.session.commit()
    assert _ok(client.get(f'/asn/{asn_id}', headers=_h(access_token)))['total_rejected_quantity'] is None
    with client.application.app_context():
        db.session.get(ASN, asn_id).details[0].rejected_quantity = 0
        db.session.commit()
    assert _ok(client.get(f'/asn/{asn_id}', headers=_h(access_token)))['total_rejected_quantity'] == 0


def test_shelf_life_concession_is_recorded_and_does_not_override_hold_or_missing_lot(client, access_token):
    w = _world(client)
    _ok(client.put(f'/goods/{w["g1"]}/lot-rules',json=dict(revision=0,overrides={'mode':'expiry','receive_days':30}),headers=_h(access_token)))
    with client.application.app_context():
        lot = GoodsLot(goods_id=w['g1'],lot_no='SHORT-LIFE',expiry_date=clock.local_today(w['wh'])+timedelta(days=5))
        db.session.add(lot);db.session.commit();lot_id=lot.id
    _, task_id = task_for(client,access_token,w,2)
    endpoint = f'/sorting/{task_id}/batches/'
    line = dict(goods_id=w['g1'],sorted_quantity=2,damage_quantity=0,lot_id=lot_id)
    assert client.post(endpoint,json={'details':[line]},headers=_h(access_token)).status_code == 400
    line['concession_reason'] = 'Supplier approved short-life receipt for immediate allocation'
    saved = _ok(client.post(endpoint,json={'details':[line]},headers=_h(access_token)))
    assert saved['details'][0]['concession_reason'] == line['concession_reason']
    _ok(client.put(f'/sorting/{task_id}/complete/',headers=_h(access_token)))
    with client.application.app_context():
        assert InventoryLotStock.query.filter_by(goods_id=w['g1'],lot_id=lot_id).one().sorted_stock == 2
        db.session.get(GoodsLot,lot_id).status = 'held';db.session.commit()
    _, held_task = task_for(client,access_token,w,2)
    assert client.post(f'/sorting/{held_task}/batches/',json={'details':[line]},headers=_h(access_token)).status_code == 400
    line['lot_id'] = None
    assert client.post(f'/sorting/{held_task}/batches/',json={'details':[line]},headers=_h(access_token)).status_code == 400
    line.update(sorted_quantity=0,rejected_quantity=2,rejection_reason='Missing required batch')
    _ok(client.post(f'/sorting/{held_task}/batches/',json={'details':[line]},headers=_h(access_token)))


def test_receiving_exception_migration_preserves_unknown_history():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import pytest
    path=Path(__file__).parents[1]/'migrations/versions/b0e4f8a5d267_receiving_exceptions.py'
    spec=importlib.util.spec_from_file_location('receipt_exception_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        for name in ('sorting_task_details','asn_details'):
            connection.execute(sa.text(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY)'))
            connection.execute(sa.text(f'INSERT INTO {name} VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT rejected_quantity FROM asn_details')).scalar() is None
            connection.execute(sa.text('UPDATE sorting_task_details SET rejected_quantity=1'))
            with pytest.raises(RuntimeError,match='exceptions'):
                migration.downgrade()
            connection.execute(sa.text('UPDATE sorting_task_details SET rejected_quantity=0'))
            migration.downgrade()
