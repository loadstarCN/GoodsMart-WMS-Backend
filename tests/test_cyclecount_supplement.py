from .helpers import *
from warehouse.stock.models import GoodsLot, StockLedger


def test_supplement_migration_keeps_legacy_rows_unmarked():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('supplement_migration',
        Path(__file__).resolve().parents[1] / 'migrations/versions/d8a2b6c3e045_cyclecount_supplement.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with create_engine('sqlite://').begin() as connection:
        connection.execute(text('CREATE TABLE cycle_count_details (id INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO cycle_count_details (id) VALUES (1)'))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(text('SELECT is_supplemental FROM cycle_count_details')).scalar() == 0
        migration.downgrade()
        assert [column['name'] for column in inspect(connection).get_columns('cycle_count_details')] == ['id']


def _setup(client, token):
    with client.application.app_context():
        task = get_cyclecount_task()
        task.status = 'in_progress'
        goods = task.task_details[0].goods
        location = task.task_details[0].location
        db.session.commit()
        return task.id, {'Authorization': f'Bearer {token}', 'X-Warehouse-ID': str(task.warehouse_id)}, dict(
            goods_code=goods.code, location_code=location.code, lot_no='qa-new-lot',
            expiry_date='2027-06-30')


def test_supplement_records_zero_system_without_changing_stock(client, access_company_admin_token):
    task_id, headers, body = _setup(client, access_company_admin_token)
    with client.application.app_context():
        before = StockLedger.query.count()
    url = f'/cyclecount/{task_id}/supplements/'
    result = client.post(url, headers=headers, json=body)
    assert result.status_code == 201
    row = result.json
    assert row['system_quantity'] == 0 and not row['operator_id']
    assert client.post(url,headers=headers,json={**body,'actual_quantity':2}).status_code==409
    saved=client.post(f'/cyclecount/{task_id}/details-batch-save/',headers=headers,json={'details':[{'id':row['id'],'actual_quantity':2}]})
    assert saved.status_code==201
    assert row['lot_no'] == 'QA-NEW-LOT' and row['is_supplemental'] is True
    assert client.post(url, headers=headers, json=body).status_code == 409
    with client.application.app_context():
        assert StockLedger.query.count() == before
        assert GoodsLocation.query.filter_by(lot_id=row['lot_id']).count() == 0
        assert GoodsLot.query.filter_by(lot_no='QA-NEW-LOT').count() == 1
    assert client.delete(url + str(row['id']), headers=headers).status_code == 200
    with client.application.app_context():
        assert StockLedger.query.count() == before


def test_supplement_scope_quantity_expiry_and_locked_states(client, access_company_admin_token):
    task_id, headers, body = _setup(client, access_company_admin_token)
    url = f'/cyclecount/{task_id}/supplements/'
    for value in (-1, 0.5, True, 'abc'):
        assert client.post(url, headers=headers, json={**body, 'actual_quantity': value}).status_code == 400
    assert client.post(url, headers=headers, json={**body, 'location_code': 'NO-LOCATION'}).status_code == 404
    assert client.post(url, headers=headers, json={**body, 'goods_code': 'NO-GOODS'}).status_code == 404
    assert client.post(url, headers=headers, json={**body, 'expiry_date': '2027-02-30'}).status_code == 400
    assert client.post(url, headers=headers, json={**body, 'lot_no': ''}).status_code == 400
    row = client.post(url, headers=headers, json=body).json
    assert row['actual_quantity'] == 0
    with client.application.app_context():
        task = get_cyclecount_task()
        ordinary = next(line for line in task.task_details if not line.is_supplemental)
        original_id = ordinary.id
    assert client.delete(url + str(original_id), headers=headers).status_code == 409
    assert client.post(url, headers=headers, json={**body, 'expiry_date': '2028-01-01'}).status_code == 409
    with client.application.app_context():
        task = get_cyclecount_task()
        task.status = 'completed'
        db.session.commit()
    assert client.post(url, headers=headers, json={**body, 'lot_no': 'OTHER'}).status_code == 409
    assert client.delete(url + str(row['id']), headers=headers).status_code == 409


def test_supplement_requires_edit_and_rejects_foreign_catalog(client, access_company_admin_token):
    from .test_global_search import _role, _staff_token
    task_id, headers, body = _setup(client, access_company_admin_token)
    url = f'/cyclecount/{task_id}/supplements/'
    with client.application.app_context():
        task = get_cyclecount_task()
        token = _staff_token('count_reader', task.warehouse.company_id,
                             _role('count_reader', ['cycle_count_read']), [task.warehouse])
        foreign = Goods(code='OTHER-COMPANY-COUNT', name='Foreign', company_id=2, unit='pcs', created_by=1)
        db.session.add(foreign)
        db.session.commit()
    restricted = {**headers, 'Authorization': f'Bearer {token}'}
    assert client.post(url, headers=restricted, json=body).status_code == 403
    assert client.delete(url + '1', headers=restricted).status_code == 403
    assert client.post(url, headers=headers, json={**body, 'goods_code': 'OTHER-COMPANY-COUNT'}).status_code == 404


def test_supplement_survives_completed_count_into_pending_adjustment(client, access_company_admin_token):
    _, headers, body = _setup(client, access_company_admin_token)
    with client.application.app_context():
        original = get_cyclecount_task()
        task = CycleCountTask(warehouse_id=original.warehouse_id, status='in_progress',
                              created_by=get_company_admin_user().id, task_name='supplement-only')
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        ledger_count = StockLedger.query.count()
    result = client.post(f'/cyclecount/{task_id}/supplements/', headers=headers, json=body)
    assert result.status_code == 201
    lot_id = result.json['lot_id']
    assert client.put(f'/cyclecount/{task_id}/complete/',headers=headers).status_code==409
    assert client.post(f'/cyclecount/{task_id}/details-batch-save/',headers=headers,json={'details':[{'id':result.json['id'],'actual_quantity':2}]}).status_code==201
    assert client.put(f'/cyclecount/{task_id}/complete/', headers=headers).status_code == 200
    result = client.post(f'/adjustment/create_adjustment_by_cyclecount/{task_id}', headers=headers)
    assert result.status_code == 201
    assert result.json['status'] == 'pending'
    assert len(result.json['details']) == 1
    detail = result.json['details'][0]
    assert detail['lot_id'] == lot_id
    assert detail['system_quantity'] == 0 and detail['actual_quantity'] == 2
    with client.application.app_context():
        assert StockLedger.query.count() == ledger_count
        assert GoodsLocation.query.filter_by(lot_id=lot_id).count() == 0
