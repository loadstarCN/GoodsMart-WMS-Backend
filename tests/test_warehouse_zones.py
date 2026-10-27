from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign


def test_zones_aggregate_all_locations_and_volume(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        locations = Location.query.filter_by(warehouse_id=wid).order_by(Location.id).all()
        for index, location in enumerate(locations):
            location.code = f'A-{index+1:02d}-1'
            location.capacity = 1
        goods = Goods.query.filter_by(company_id=warehouse.company_id).all()
        for item in goods:
            item.length = item.width = item.height = 100
        db.session.commit()
        expected = sum(row.quantity for row in GoodsLocation.query.join(Location).filter(Location.warehouse_id == wid).all())
    response = client.get(f'/warehouse/{wid}/zones', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['total'] == 1
    zone = data['items'][0]
    assert zone['code'] == 'A'
    assert zone['locations'] == data['locations']
    assert zone['quantity'] == expected
    assert zone['capacity_m3'] == data['locations']
    assert zone['volume_utilization'] == round(expected * .001 / data['locations'], 4)


def test_zones_scope_and_inventory_permissions(client, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        user = _staff('zones_reader', _role('zones_reader', ['location_read']), [warehouse])
        token = create_access_token(identity=user)
    response = client.get(f'/warehouse/{wid}/zones', headers=_h(token))
    assert response.status_code == 200
    data = response.get_json()
    assert data['inventory_visible'] is False
    assert all(row['quantity'] is None and row['empty'] is None and row['volume_utilization'] is None for row in data['items'])
    assert client.get(f'/warehouse/{wid}/zones', headers=_h(foreign['token'])).status_code in (403, 404)
