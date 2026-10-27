from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.common.clock import local_today
from warehouse.dn.models import DN
from warehouse.asn.models import ASN


def test_warehouse_overview_date_counts_and_isolation(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        today = local_today(warehouse)
        dn = get_dn()
        dn.expected_shipping_date = today
        dn.status = 'pending'
        asn = get_asn()
        asn.expected_arrival_date = today
        asn.status = 'pending'
        db.session.commit()
        expected = DN.query.filter(DN.warehouse_id == wid, DN.is_active.is_(True), DN.expected_shipping_date == today, DN.status.in_(['pending', 'in_progress', 'picked', 'packed'])).count()
    result = client.get(f'/warehouse/{wid}/overview', headers=_h(access_token))
    assert result.status_code == 200, result.get_json()
    assert result.get_json()['date'] == today.isoformat()
    assert result.get_json()['dispatch_today'] == expected
    assert result.get_json()['receive_today'] >= 1
    assert result.get_json()['locations'] >= 1
    assert result.get_json()['onhand'] >= 0
    assert client.get(f'/warehouse/{wid}/overview', headers=_h(foreign['token'])).status_code in (403, 404)


def test_warehouse_overview_hides_unauthorized_metrics(client):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        reader = _staff('overview_reader', _role('overview_reader', ['warehouse_read']), [warehouse])
        outsider = _staff('overview_outsider', _role('overview_outsider', ['warehouse_read']), [])
        token = create_access_token(identity=reader)
        other_token = create_access_token(identity=outsider)
    result = client.get(f'/warehouse/{wid}/overview', headers=_h(token))
    assert result.status_code == 200, result.get_json()
    assert set(result.get_json()) == {'date'}
    assert client.get(f'/warehouse/{wid}/overview', headers=_h(other_token)).status_code == 403


def test_location_latest_count_uses_completion_time_not_id(client, access_token):
    from warehouse.cyclecount.models import CycleCountTaskDetail
    from datetime import datetime, timedelta, timezone
    with client.application.app_context():
        rows = CycleCountTaskDetail.query.order_by(CycleCountTaskDetail.id).limit(2).all()
        assert len(rows) == 2
        now = datetime.now(timezone.utc)
        rows[0].status = rows[1].status = 'completed'
        rows[0].completed_at = now
        rows[1].completed_at = now - timedelta(days=2)
        expected = rows[0].id
        db.session.commit()
    response = client.get('/cyclecount/details/search/?status=completed&order_by=completed_at&per_page=1', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['items'][0]['id'] == expected
