from datetime import date, timedelta
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.common import clock
from warehouse.dn.models import DN


def test_daily_uses_local_business_dates_and_due_cohort(client, access_token, monkeypatch, foreign):
    day = date(2026, 11, 2)
    monkeypatch.setattr(clock, 'local_today', lambda where=None: day)
    with client.application.app_context():
        warehouse = get_warehouse(); warehouse.timezone = 'Asia/Tokyo'
        wid = warehouse.id
        dn = get_dn(); dn.warehouse_id = wid; dn.is_active = True
        dn.expected_shipping_date = day; dn.status = 'packed'; dn.delivered_at = None; dn.completed_at = None
        for other in DN.query.filter(DN.warehouse_id == wid, DN.id != dn.id):
            other.expected_shipping_date = day - timedelta(days=4)
            other.delivered_at = None; other.completed_at = None
        ident = dn.id
        start = clock.local_midnight(day, warehouse)
        db.session.commit()
    url = f'/warehouse/{wid}/daily'
    body = client.get(url, headers=_h(access_token)).get_json()
    assert body['dispatch']['due'] == 1 and body['dispatch']['sent'] == 0
    assert body['sent_today'] == 0 and body['signed_today'] == 0
    with client.application.app_context():
        dn = db.session.get(DN, ident); dn.status = 'delivered'; dn.delivered_at = start
        db.session.commit()
    body = client.get(url, headers=_h(access_token)).get_json()
    assert body['dispatch']['sent'] == 1 and body['sent_today'] == 1
    assert sum(row['sent'] for row in body['dispatch']['carriers']) == 1
    with client.application.app_context():
        dn = db.session.get(DN, ident); dn.status = 'completed'
        dn.delivered_at = start - timedelta(microseconds=1); dn.completed_at = start
        db.session.commit()
    body = client.get(url, headers=_h(access_token)).get_json()
    assert body['dispatch']['sent'] == 1 and body['sent_today'] == 0 and body['signed_today'] == 1
    with client.application.app_context():
        dn = db.session.get(DN, ident); dn.completed_at = start + timedelta(days=1)
        db.session.commit()
    assert client.get(url, headers=_h(access_token)).get_json()['signed_today'] == 0
    assert client.get(url, headers=_h(foreign['token'])).status_code in (403, 404)


def test_daily_hides_metrics_without_permission(client):
    with client.application.app_context():
        warehouse = get_warehouse(); wid = warehouse.id
        reader = _staff('daily_inventory', _role('daily_inventory', ['inventory_read']), [warehouse])
        outsider = _staff('daily_outside', _role('daily_outside', ['dn_read']), [])
        token = create_access_token(identity=reader)
        outside = create_access_token(identity=outsider)
    response = client.get(f'/warehouse/{wid}/daily', headers=_h(token))
    assert response.status_code == 200
    assert set(response.get_json()) == {'date', 'timezone', 'putaway_today'}
    assert client.get(f'/warehouse/{wid}/daily', headers=_h(outside)).status_code == 403


def test_daily_putaway_counts_destination_once_at_business_time(client, access_token):
    from warehouse.stock.service import StockOp, Leg
    with client.application.app_context():
        warehouse = get_warehouse(); wid = warehouse.id
        location = Location.query.filter_by(warehouse_id=wid, location_type='standard').first()
        goods = Goods.query.filter_by(company_id=warehouse.company_id).first()
        start = clock.local_midnight(clock.local_today(warehouse), warehouse)
        before = client.get(f'/warehouse/{wid}/daily', headers=_h(access_token)).get_json()['putaway_today']
        opening = StockOp(wid, 'opening'); opening.move(goods.id, 9, dst=Leg.pending()); opening.commit()
        for amount, occurred in [(3, start), (2, start-timedelta(microseconds=1)), (4, start+timedelta(days=1))]:
            op = StockOp(wid, 'putaway', occurred_at=occurred)
            op.move(goods.id, amount, src=Leg.pending(), dst=Leg.at(location.id)); op.commit()
        db.session.commit()
    body = client.get(f'/warehouse/{wid}/daily', headers=_h(access_token)).get_json()
    assert body['putaway_today'] == before + 3
