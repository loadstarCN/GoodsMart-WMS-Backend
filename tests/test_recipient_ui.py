from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.dn.models import DN
from warehouse.dn.customs_services import CustomsService
from datetime import datetime as dt, timezone as tz


def test_recipient_tax_defaults_roundtrip_and_clear(client, access_token):
    headers = _h(access_token)
    response = client.post('/recipient/', headers=headers, json={
        'name': 'Tax defaults', 'country': 'DE', 'company_id': 1,
        'address': 'Manually formatted address', 'tax_id': 'DE123', 'tax_id_type': 'EORI'})
    assert response.status_code == 201, response.get_json()
    rid = response.get_json()['id']
    assert response.get_json()['tax_id'] == 'DE123'
    changed = client.put(f'/recipient/{rid}', headers=headers, json={'phone': '123'})
    assert changed.status_code == 200
    assert changed.get_json()['tax_id'] == 'DE123'
    cleared = client.put(f'/recipient/{rid}', headers=headers, json={'tax_id': None, 'tax_id_type': None})
    assert cleared.status_code == 200
    assert cleared.get_json()['tax_id'] is None
    assert cleared.get_json()['address'] == 'Manually formatted address'
    for field, value in [('tax_id', 'x' * 101), ('tax_id_type', 'x' * 31), ('tax_id', {'invalid': True})]:
        assert client.put(f'/recipient/{rid}', headers=headers, json={field: value}).status_code == 400


def test_recipient_summary_counts_dispatch_and_permissions(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        dn = get_dn()
        rid = dn.recipient_id
        dn.delivered_at = dt.now(tz.utc)
        count = DN.query.filter_by(recipient_id=rid, warehouse_id=wid, is_active=True).count()
        user = _staff('recipient_stats_reader', _role('recipient_stats_reader', ['recipient_read']), [warehouse])
        token = create_access_token(identity=user)
        db.session.commit()
    response = client.get(f'/dn/recipient-summary?recipient_ids={rid},2147483647', headers=_h(access_token, wid))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['items'][0]['total'] == count
    assert response.get_json()['items'][0]['dispatched_at']
    assert client.get(f'/dn/recipient-summary?recipient_ids={rid}', headers=_h(token, wid)).status_code == 403
    for invalid in ['', '0', '-1', '1,x', '2147483648', ','.join(['1'] * 101)]:
        assert client.get('/dn/recipient-summary?recipient_ids=' + invalid, headers=_h(access_token, wid)).status_code == 400


def test_recipient_summary_isolation(client, foreign):
    with client.application.app_context():
        rid = get_dn().recipient_id
        wid = get_warehouse().id
    result = client.get(f'/dn/recipient-summary?recipient_ids={rid}', headers=_h(foreign['token']))
    assert result.status_code == 200
    assert result.get_json() == {'items': []}
    assert client.get(f'/dn/recipient-summary?recipient_ids={rid}', headers=_h(foreign['token'], wid)).status_code == 403


def test_customs_view_exposes_defaults_without_creating_snapshot(client):
    with client.application.app_context():
        dn = get_dn()
        dn.recipient.tax_id = 'DE123'
        dn.recipient.tax_id_type = 'EORI'
        db.session.commit()
        view = CustomsService.build_view(dn)
        assert view['recipient_tax_defaults'] == {'tax_id': 'DE123', 'tax_id_type': 'EORI'}
        assert view['customs'] is None
