from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.common.clock import local_today
from warehouse.dn.models import DN
from warehouse.asn.models import ASN
from warehouse.inventory.models import Inventory


def test_overview_total_includes_non_shelf_stock_but_not_transit(client, access_token):
    from warehouse.stock.service import StockOp, Leg
    with client.application.app_context():
        wid = get_warehouse().id
        row = Inventory.query.filter_by(warehouse_id=wid).first()
        gid = row.goods_id
        before = client.get(f'/warehouse/{wid}/overview', headers=_h(access_token)).get_json()
        op = StockOp(wid, 'opening')
        op.move(gid, 7, src=None, dst=Leg.pending())
        op.move(gid, 7, src=None, dst=Leg.picked())
        op.move(gid, 3, src=None, dst=Leg.packed())
        op.move(gid, 100, src=None, dst=Leg.in_transit())
        op.commit()
        db.session.commit()
        result = client.get(f'/warehouse/{wid}/overview', headers=_h(access_token))
        assert result.status_code == 200
        body = result.get_json()
        assert body['total_stock'] == before['total_stock'] + 17
        assert body['onhand'] == before['onhand']
        op = StockOp(wid, 'pack')
        op.move(gid, 3, src=Leg.picked(), dst=Leg.packed())
        op.commit()
        db.session.commit()
        assert client.get(f'/warehouse/{wid}/overview', headers=_h(access_token)).get_json()['total_stock'] == body['total_stock']
        op = StockOp(wid, 'ship')
        op.move(gid, 3, src=Leg.packed(), dst=Leg.in_transit())
        op.commit()
        db.session.commit()
        assert client.get(f'/warehouse/{wid}/overview', headers=_h(access_token)).get_json()['total_stock'] == body['total_stock'] - 3


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
    for resource, key in [('dn', 'dispatch_today'), ('asn', 'receive_today')]:
        drilldown = client.get(f'/{resource}/?warehouse_todo_date={today.isoformat()}',
                               headers={**_h(access_token), 'X-WAREHOUSE-ID': str(wid)})
        assert drilldown.status_code == 200, drilldown.get_json()
        assert drilldown.get_json()['total'] == result.get_json()[key]
        assert client.get(f'/{resource}/?warehouse_todo_date=invalid', headers=_h(access_token)).status_code == 400
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


def test_overview_secondary_counts_use_current_tasks_and_pending_receipts(client, access_token):
    from warehouse.delivery.models import DeliveryTask
    from warehouse.asn.models import ASNDetail
    from sqlalchemy import func
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        today = local_today(warehouse)
        task = get_delivery_task()
        dn = db.session.get(DN, task.dn_id)
        dn.warehouse_id = wid
        dn.expected_shipping_date = today
        dn.status = 'pending'
        dn.is_active = True
        for current in DeliveryTask.query.filter_by(dn_id=dn.id):
            current.tracking_number = None
        task.is_active = True
        asn = get_asn()
        asn.expected_arrival_date = today
        asn.status = 'pending'
        asn.is_active = True
        db.session.commit()
        expected_quantity = db.session.query(func.coalesce(func.sum(ASNDetail.quantity), 0)).join(ASN).filter(
            ASN.warehouse_id == wid, ASN.is_active.is_(True), ASN.status == 'pending',
            ASN.expected_arrival_date == today).scalar()
        endpoint = f'/warehouse/{wid}/overview'
        before = client.get(endpoint, headers=_h(access_token)).get_json()
        assert before['receive_today_quantity'] == expected_quantity
        zones = client.get(f'/warehouse/{wid}/zones', headers=_h(access_token)).get_json()
        assert before['zones'] == zones['total']
        assert before['empty_locations'] == sum(row['empty'] for row in zones['items'])
        task.tracking_number = 'QA-TRACKING'
        db.session.commit()
        assert client.get(endpoint, headers=_h(access_token)).get_json()['dispatch_without_tracking'] == before['dispatch_without_tracking'] - 1
        task.is_active = False
        db.session.commit()
        assert client.get(endpoint, headers=_h(access_token)).get_json()['dispatch_without_tracking'] == before['dispatch_without_tracking']


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
