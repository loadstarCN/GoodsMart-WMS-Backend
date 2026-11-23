from uuid import uuid4

import pytest

from tests.helpers import client, access_token, access_company_admin_token
from tests.test_inventory_guards import _bin, _h, _inbound, _world
from tests.test_warehouse_custody import race
from warehouse.warehouse.models import Warehouse


@pytest.mark.parametrize('kind', ['putaway', 'removal', 'transfer'])
def test_replay_after_lost_response(client, access_token, access_company_admin_token, kind):
    w = _world(client)
    goods, source, dest = w['g1'], w['std1'], w['std2']
    _inbound(client, access_token, w, [(goods, 5)], [] if kind == 'putaway' else [(goods, source, 5)])
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    url = f'/{kind}/requests'
    body = dict(request_id=str(uuid4()), goods_id=goods, quantity=5, location_id=source)
    if kind == 'removal':
        body['reason'] = 'other'
    if kind == 'transfer':
        body['from_location_id'] = body.pop('location_id')
        body['to_location_id'] = dest
    receipt_url = url + '/' + body['request_id']
    assert client.get(receipt_url, headers=headers).json['status'] == 'not_recorded'
    assert client.post(url, headers=headers, json={**body, 'quantity': 6}).status_code == 400
    assert client.get(receipt_url, headers=headers).json['status'] == 'not_recorded'
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 201, first.json
    assert first.json['record_id'] > 0 and first.json['payload'] == body
    replay = client.post(url, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json == first.json
    assert client.get(receipt_url, headers=headers).json == first.json
    assert client.post(url, headers=headers, json={**body, 'quantity': 1}).status_code == 409
    other_actor = {**headers, **_h(access_company_admin_token)}
    assert client.get(receipt_url, headers=other_actor).json['status'] == 'not_recorded'
    assert client.post(url, headers=other_actor, json=body).status_code == 409
    with client.application.app_context():
        other = Warehouse.query.filter(Warehouse.id != w['wh'], Warehouse.is_active.is_(True)).first().id
    other_warehouse = {**headers, 'X-WAREHOUSE-ID': str(other)}
    assert client.get(receipt_url, headers=other_warehouse).json['status'] == 'not_recorded'
    assert client.post(url, headers=other_warehouse, json=body).status_code == 403
    assert _bin(client, goods, source) == (5 if kind == 'putaway' else 0)
    assert _bin(client, goods, dest) == (5 if kind == 'transfer' else 0)


def test_concurrent_identical_bin_requests_post_once(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [(w['g1'], w['std1'], 10)])
    headers = {**_h(access_token), 'X-WAREHOUSE-ID': str(w['wh'])}
    body = dict(request_id=str(uuid4()), goods_id=w['g1'], quantity=3, reason='other', location_id=w['std1'])
    results = race(client, [lambda c: c.post('/removal/requests', json=body, headers=headers)] * 2)
    assert sorted(code for code, _ in results) == [200, 201], results
    assert results[0][1] == results[1][1]
    assert _bin(client, w['g1'], w['std1']) == 7
