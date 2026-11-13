import pytest
from tests.helpers import *
from tests.test_tenant_scope_regressions import _role, _staff, _h, _api_key
from warehouse.stock.models import GoodsLot


def _count(client, token):
    with client.application.app_context():
        stock = GoodsLocation.query.filter(GoodsLocation.quantity > 2).first()
        line = dict(goods_id=stock.goods_id, location_id=stock.location_id, lot_id=stock.lot_id)
        wh, before = stock.location.warehouse_id, stock.quantity
    h = _h(token)
    response = client.post('/cyclecount/', headers=h,
        json=dict(warehouse_id=wh, task_details=[line]))
    assert response.status_code == 201, response.json
    return response.json['id'], line, wh, before


@pytest.mark.parametrize('string_ids', [False, True])
def test_count_duplicate_create_add_update_and_distinct_lots(client, access_token, string_ids):
    task, line, wh, _ = _count(client, access_token)
    h = _h(access_token)
    with client.application.app_context():
        before = CycleCountTask.query.count()
    duplicate = {**line, 'goods_id': str(line['goods_id']), 'location_id': str(line['location_id'])} if string_ids else line
    assert client.post('/cyclecount/', headers=h, json=dict(
        warehouse_id=wh, task_details=[line, duplicate])).status_code == 409
    with client.application.app_context():
        assert CycleCountTask.query.count() == before
        lot = GoodsLot(goods_id=line['goods_id'], lot_no='AUDIT-DISTINCT')
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    assert client.post(f'/cyclecount/{task}/details/', headers=h, json=duplicate).status_code == 409
    added = client.post(f'/cyclecount/{task}/details/', headers=h, json={**line, 'lot_id': lot_id})
    assert added.status_code == 201, added.json
    url = f"/cyclecount/{task}/details/{added.json['id']}"
    assert client.put(url, headers=h, json={'lot_id': line['lot_id']}).status_code == 409
    assert client.get(url, headers=h).json['lot_id'] == lot_id
    assert client.put(url, headers=h, json={'actual_quantity': 1}).status_code == 409


@pytest.mark.parametrize('status,endpoint', [
    ('pending', 'process'), ('in_progress', 'complete'), ('completed', 'adjustment')])
def test_legacy_duplicate_count_cannot_advance(client, access_token, status, endpoint):
    task, line, _, _ = _count(client, access_token)
    with client.application.app_context():
        count = db.session.get(CycleCountTask, task)
        count.status = status
        count.task_details.append(CycleCountTaskDetail(**line, actual_quantity=0))
        db.session.commit()
    h = _h(access_token)
    result = (client.post(f'/adjustment/create_adjustment_by_cyclecount/{task}', headers=h)
              if endpoint == 'adjustment' else client.put(f'/cyclecount/{task}/{endpoint}/', headers=h))
    assert result.status_code == 409, result.json
    with client.application.app_context():
        assert db.session.get(CycleCountTask, task).status == status
        assert Adjustment.query.filter_by(cyclecount_id=task).count() == 0


def test_single_count_adjustment_changes_stock_once(client, access_token, access_company_admin_token):
    task, line, _, before = _count(client, access_token)
    h = _h(access_token)
    assert client.put(f'/cyclecount/{task}/process/', headers=h).status_code == 200
    details = client.get(f'/cyclecount/{task}/details/', headers=h).json
    assert client.post(f'/cyclecount/{task}/details-batch-save/', headers=h,
        json={'details': [{'id': details[0]['id'], 'actual_quantity': before - 1}]}).status_code == 201
    assert client.put(f'/cyclecount/{task}/complete/', headers=h).status_code == 200
    adjustment = client.post(f'/adjustment/create_adjustment_by_cyclecount/{task}', headers=h)
    assert adjustment.status_code == 201, adjustment.json
    ident = adjustment.json['id']
    for action in ('approve', 'complete'):
        result = client.put(f'/adjustment/{ident}/{action}/', headers=_h(access_company_admin_token))
        assert result.status_code == 200, result.json
    with client.application.app_context():
        assert GoodsLocation.query.filter_by(**line).one().quantity == before - 1


@pytest.mark.parametrize('api_key', [False, True])
@pytest.mark.parametrize('assigned', [False, True])
def test_warehouse_reads_follow_staff_scope(client, api_key, assigned):
    with client.application.app_context():
        company = get_company()
        warehouse = get_warehouse()
        own = warehouse.id
        other = next(w.id for w in company.warehouses if w.id != own)
        staff = _staff('audit_reader', company.id, _role('audit_reader', ['warehouse_read']),
                       [warehouse] if assigned else [])
        headers = _h(create_access_token(identity=staff))
        if api_key:
            key_id, plain = _api_key(company.id, ['warehouse_read'])
            db.session.get(APIKey, key_id).user_id = staff.id
            db.session.commit()
            headers = {'X-API-KEY': plain}
    response = client.get('/warehouse/?all=true', headers=headers)
    assert response.status_code == 200, response.json
    assert {w['id'] for w in response.json['items']} == ({own} if assigned else set())
    assert client.get(f'/warehouse/{own}', headers=headers).status_code == (200 if assigned else 403)
    assert client.get(f'/warehouse/{other}', headers=headers).status_code == 403


def test_company_admin_keeps_unassigned_warehouse_access(client, access_company_admin_token):
    with client.application.app_context():
        own = {w.id for w in get_company().warehouses if w.is_active}
    response = client.get('/warehouse/?all=true', headers=_h(access_company_admin_token))
    assert {w['id'] for w in response.json['items']} == own


def test_legacy_duplicate_adjustment_cannot_change_stock(client, access_token):
    from warehouse.adjustment.services import AdjustmentService
    task, line, _, before = _count(client, access_token)
    assert client.put(f'/cyclecount/{task}/process/', headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        count = db.session.get(CycleCountTask, task)
        count.status = 'completed'
        detail = count.task_details[0]
        detail.system_quantity = before
        detail.actual_quantity = before - 1
        detail.difference = -1
        detail.operator_id = get_admin_user().id
        db.session.commit()
        adjustment = AdjustmentService.create_adjustment_from_cyclecount(task, get_admin_user().id)
        adjustment.status = 'approved'
        adjustment.details.append(AdjustmentDetail(**line, system_quantity=before,
            actual_quantity=before - 1, adjustment_quantity=-1))
        db.session.commit()
        ident = adjustment.id
    response = client.put(f'/adjustment/{ident}/complete/', headers=_h(access_token))
    assert response.status_code == 409, response.json
    with client.application.app_context():
        assert db.session.get(Adjustment, ident).status == 'approved'
        assert GoodsLocation.query.filter_by(**line).one().quantity == before
