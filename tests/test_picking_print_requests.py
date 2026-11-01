from uuid import uuid4
import pytest
from tests.helpers import *
from tests.test_picking_location_type import _setup
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_outbound import _make_company_b
from warehouse.picking.models import PickingPrintRequest


def test_print_requests_replay_filter_history_and_no_stock_change(client, access_token):
    _, _, task_id = _setup(client, access_token)
    headers = _h(access_token)
    before = _ok(client.get(f'/picking/{task_id}', headers=headers))
    payload = dict(task_ids=[task_id], request_id=str(uuid4()), language='ja')
    url = '/picking/print-requests'
    assert client.post(url, headers=headers, json=payload).status_code == 201
    assert client.post(url, headers=headers, json=payload).status_code == 200
    task = _ok(client.get(f'/picking/{task_id}', headers=headers))
    assert task['print_count'] == 1 and task['last_print_requested_at']
    assert task['print_requests'][0]['language'] == 'ja'
    assert task['print_requests'][0]['operator']['id']
    assert task['status'] == before['status'] and task['total_picked_quantity'] == before['total_picked_quantity']
    assert task_id in [r['id'] for r in _ok(client.get('/picking/?print_requested=true', headers=headers))['items']]
    assert task_id not in [r['id'] for r in _ok(client.get('/picking/?print_requested=false', headers=headers))['items']]
    assert client.post(url, headers=headers, json={**payload, 'language':'en'}).status_code == 409
    assert client.post(url, headers=headers, json={**payload, 'request_id':str(uuid4())}).status_code == 201
    assert _ok(client.get(f'/picking/{task_id}', headers=headers))['print_count'] == 2


def test_print_request_batch_failure_rolls_back_and_foreign_scope_denied(client, access_token):
    _, _, task_id = _setup(client, access_token)
    payload = dict(task_ids=[task_id, 999999], request_id=str(uuid4()), language='zh')
    assert client.post('/picking/print-requests', headers=_h(access_token), json=payload).status_code == 404
    foreign, _ = _make_company_b(client, company_admin=True)
    assert client.post('/picking/print-requests', headers=foreign, json={**payload,'task_ids':[task_id]}).status_code in (403,404)
    with client.application.app_context():
        assert PickingPrintRequest.query.count() == 0


def test_print_request_requires_picking_read(client, access_operator_token):
    response = client.post('/picking/print-requests', headers=_h(access_operator_token),
        json=dict(task_ids=[1],request_id=str(uuid4()),language='en'))
    assert response.status_code == 403


def test_print_request_multiple_tasks_are_independent(client, access_token):
    _, _, task_id = _setup(client, access_token)
    with client.application.app_context():
        other_id = PickingTask.query.filter(PickingTask.id != task_id).first().id
    payload = dict(task_ids=[other_id, task_id], request_id=str(uuid4()), language='en')
    result = _ok(client.post('/picking/print-requests', headers=_h(access_token), json=payload))
    assert result['created'] == 2
    assert _ok(client.post('/picking/print-requests', headers=_h(access_token), json=payload))['created'] == 0
    for i in [other_id, task_id]:
        task = _ok(client.get(f'/picking/{i}', headers=_h(access_token)))
        assert task['print_count'] == 1
    with client.application.app_context():
        assert PickingPrintRequest.query.count() == 2


def test_today_filter_uses_warehouse_day_and_excludes_previous_day(client, access_token, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from warehouse.common import clock
    from warehouse.warehouse.models import Warehouse
    _, _, task_id = _setup(client, access_token)
    instant = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: instant)
    with client.application.app_context():
        task = db.session.get(PickingTask, task_id)
        task.dn.warehouse.timezone = 'Asia/Tokyo'
        task.created_at = instant
        db.session.commit()
    url = '/picking/?created_today=true'
    assert task_id in [r['id'] for r in _ok(client.get(url, headers=_h(access_token)))['items']]
    with client.application.app_context():
        task = db.session.get(PickingTask, task_id)
        task.created_at = instant - timedelta(hours=2)  # Same UTC day, previous Tokyo day.
        db.session.commit()
    assert task_id not in [r['id'] for r in _ok(client.get(url, headers=_h(access_token)))['items']]


@pytest.mark.parametrize('patch', [dict(task_ids=[]),dict(task_ids=[True]),dict(task_ids=[1,1]),dict(task_ids=list(range(1,52))),dict(request_id='bad'),dict(language='xx')])
def test_print_request_rejects_invalid_payload(client, access_token, patch):
    payload = dict(task_ids=[1], request_id=str(uuid4()), language='en')
    assert client.post('/picking/print-requests', headers=_h(access_token), json={**payload,**patch}).status_code == 400
