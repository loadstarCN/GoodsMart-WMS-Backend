from uuid import uuid4
import pytest
from tests.helpers import *
from tests.test_picking_location_type import _setup
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_outbound import _make_company_b


def test_dn_print_replay_history_scope_and_no_fulfillment_change(client, access_token):
    _, dn_id, _ = _setup(client, access_token)
    headers = _h(access_token)
    before = _ok(client.get(f'/dn/{dn_id}', headers=headers))
    url = f'/dn/{dn_id}/print-requests'
    payload = dict(request_id=str(uuid4()), language='ja')
    assert client.post(url, headers=headers, json=payload).status_code == 201
    assert client.post(url, headers=headers, json=payload).status_code == 200
    assert client.post(url, headers=headers, json={**payload, 'language': 'en'}).status_code == 409
    result = _ok(client.get(url, headers=headers))
    assert len(result['print_requests']) == 1
    assert result['print_requests'][0]['operator']['id']
    assert result['print_requests'][0]['requested_at']
    assert result['print_requests'][0]['language'] == 'ja'
    assert client.post(url, headers=headers, json={**payload, 'request_id': str(uuid4())}).status_code == 201
    assert len(_ok(client.get(url, headers=headers))['print_requests']) == 2
    after = _ok(client.get(f'/dn/{dn_id}', headers=headers))
    for key in ('status', 'total_picked_quantity', 'total_packed_quantity', 'total_delivered_quantity'):
        assert after[key] == before[key]
    foreign, _ = _make_company_b(client, company_admin=True)
    assert client.get(url, headers=foreign).status_code in (403, 404)
    assert client.post(url, headers=foreign, json=payload).status_code in (403, 404)


@pytest.mark.parametrize('payload', [None, [], {}, {'request_id':'bad','language':'en'}, {'request_id':str(uuid4()),'language':'xx'}])
def test_dn_print_invalid_payload(client, access_token, payload):
    assert client.post('/dn/1/print-requests', headers=_h(access_token), json=payload).status_code in (400, 415)


def test_dn_print_requires_read(client, access_operator_token):
    headers = _h(access_operator_token)
    assert client.get('/dn/1/print-requests', headers=headers).status_code == 403
    assert client.post('/dn/1/print-requests', headers=headers, json={'request_id': str(uuid4()), 'language':'en'}).status_code == 403


def test_dn_print_uses_live_waybill_and_ignores_cancelled_service(client, access_token):
    from warehouse.dn.models import DNCarrierShipment
    _, dn_id, _ = _setup(client, access_token)
    with client.application.app_context():
        shipment = DNCarrierShipment(dn_id=dn_id, source='file', status='active',
            tracking_number='LIVE-PRINT-001', carrier_data={'service_name': 'Express next day'})
        db.session.add(shipment)
        db.session.commit()
        shipment_id = shipment.id
    url = f'/dn/{dn_id}/print-requests'
    result = _ok(client.get(url, headers=_h(access_token)))['shipping']
    assert result['tracking_number'] == 'LIVE-PRINT-001'
    assert result['service_name'] == 'Express next day'
    with client.application.app_context():
        db.session.get(DNCarrierShipment, shipment_id).status = 'cancelled'
        db.session.commit()
    result = _ok(client.get(url, headers=_h(access_token)))['shipping']
    assert result['tracking_number'] is None and result['service_name'] is None
