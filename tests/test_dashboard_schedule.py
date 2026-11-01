import pytest
from datetime import timedelta
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.common import clock
from warehouse.cyclecount.models import CycleCountTask
from warehouse.dn.models import CarrierPickup


def test_schedule_local_week_permissions_and_scope(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.timezone = 'Asia/Tokyo'
        wid = warehouse.id
        today = clock.local_today(warehouse)
        asn = get_asn()
        asn.expected_arrival_date = today
        asn.status = 'pending'
        asn.is_active = True
        user = _staff('schedule_reader', _role('schedule_reader', ['cycle_count_read']), [warehouse])
        outsider = _staff('schedule_outsider', _role('schedule_outsider', ['cycle_count_read']), [])
        token = create_access_token(identity=user)
        outside_token = create_access_token(identity=outsider)
        for day, active, status in [(0, True, 'pending'), (6, True, 'pending'),
                                    (7, True, 'pending'), (0, False, 'pending'), (0, True, 'completed')]:
            db.session.add(CycleCountTask(warehouse_id=wid, created_by=user.id,
                scheduled_date=clock.local_midnight(today+timedelta(days=day), warehouse),
                is_active=active, status=status))
        for day, status in [(0, 'scheduled'), (6, 'scheduled'), (7, 'scheduled'), (0, 'cancelled')]:
            db.session.add(CarrierPickup(warehouse_id=wid, source='manual', status=status,
                pickup_date=today+timedelta(days=day), ready_time='09:00', close_time='17:00',
                package_count=1, total_weight_kg=1))
        db.session.commit()
    result = client.get(f'/warehouse/{wid}/schedule', headers=_h(token))
    assert result.status_code == 200
    data = result.get_json()
    assert data['timezone'] == 'Asia/Tokyo' and len(data['items']) == 7
    assert data['items'][0]['cyclecount'] == 1
    assert data['items'][6]['cyclecount'] == 1
    assert all('asn' not in row and 'pickup' not in row for row in data['items'])
    admin = client.get(f'/warehouse/{wid}/schedule', headers=_h(access_token)).get_json()
    assert admin['items'][0]['asn'] >= 1
    assert admin['items'][0]['pickup'] == 1 and admin['items'][6]['pickup'] == 1
    assert client.get(f'/warehouse/{wid}/schedule', headers=_h(outside_token)).status_code in (403, 404)
    assert client.get(f'/warehouse/{wid}/schedule', headers=_h(foreign['token'])).status_code in (403, 404)


@pytest.mark.parametrize('kind,parent', [('sorting','asn'), ('picking','dn'), ('packing','dn'), ('delivery','dn')])
def test_dashboard_work_rows_include_warehouse_for_local_dates(client, access_token, kind, parent):
    with client.application.app_context():
        wid = get_warehouse().id
    rows = client.get('/'+kind+'/?per_page=50', headers={**_h(access_token), 'X-Warehouse-ID':str(wid)}).get_json()['items']
    assert rows
    assert all(row['warehouse_id'] == wid for row in rows)


def test_dn_planned_date_range_is_inclusive_and_composes_with_status(client, access_token):
    with client.application.app_context():
        dn = get_dn()
        wid, did = dn.warehouse_id, dn.id
        dn.expected_shipping_date = clock.local_today(wid)
        today = dn.expected_shipping_date
        dn.status = 'pending'
        db.session.commit()
    headers = {**_h(access_token), 'X-Warehouse-ID':str(wid)}
    same = f'planned_start_date={today}&planned_end_date={today}'
    rows = client.get('/dn/?'+same+'&status=pending', headers=headers).get_json()['items']
    assert did in {row['id'] for row in rows}
    tomorrow = today+timedelta(days=1)
    future = client.get(f'/dn/?planned_start_date={tomorrow}', headers=headers).get_json()['items']
    assert did not in {row['id'] for row in future}
    past = client.get(f'/dn/?planned_end_date={today-timedelta(days=1)}', headers=headers).get_json()['items']
    assert did not in {row['id'] for row in past}
    assert client.get(f'/dn/?planned_start_date={tomorrow}&planned_end_date={today}', headers=headers).status_code == 400
    assert client.get('/dn/?planned_start_date=invalid', headers=headers).status_code == 400
