"""手工记录的揽收（POST /dn/carrier-pickups/manual，doc/shipping-flow.md §4）与揽收方式（carriers.pickup_method）

- 揽收页列出揽收方式是 api / manual 的承运商；manual 的承运商可以列出待揽收运单（任一来源）、手工记一笔
- 手工记录直接 scheduled（source manual），不调承运商；占着所选运单；取消只改本地
- 插件预约 / 查可约时间只对 pickup_method = api（否则 409 16130 PICKUP_METHOD_NOT_API）；api 的承运商也可以手工记
"""
from .helpers import *
from .test_carrier_pickup import _WAREHOUSE, _book, _overview, _record, _shipment, _tomorrow, _wh, pickup_fedex  # noqa: F401
from .test_carrier_shipment import fedex  # noqa: F401  fedex 是 fixture
from .test_customs import PACKAGES, _create_dn, _force_packed, _h, _prepare

from warehouse.dn.models import DNCarrierShipment


@pytest.fixture
def manual_carrier(client):
    """揽收方式 manual 的承运商（没有在线对接）→ id；当前仓库 id 记到 _WAREHOUSE"""
    with client.application.app_context():
        base = get_carrier()
        carrier = Carrier(name='Local Express', code='local', company_id=base.company_id,
                          created_by=base.created_by, pickup_method='manual', pickup_contact='0120-111-222')
        db.session.add(carrier)
        db.session.commit()
        _WAREHOUSE['id'] = get_warehouse().id
        return carrier.id


def _manual_waybill(client, token, carrier_id, number, order_number):
    """国内 DN：已打包，发货任务的承运商 = carrier_id，手填运单号 → (dn_id, task_id, shipment_id)"""
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    assert client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token)).status_code == 200
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': number, 'carrier_id': carrier_id},
                          headers=_h(token))
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        shipment = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        return dn_id, task_id, shipment.id


def _record_pickup(client, token, carrier, shipments, overrides=None, **more):
    body = {'warehouse_id': _WAREHOUSE['id'], 'carrier_id': carrier, 'shipment_ids': shipments,
            'pickup_date': _tomorrow(), 'ready_time': '13:00', 'close_time': '17:00',
            'confirmation_code': 'TEL-0425', 'remarks': 'Booked by phone'}
    body.update(overrides or {})
    body.update(more)
    return client.post('/dn/carrier-pickups/manual', json=body, headers=_wh(token))


def test_manual_carrier_overview_and_record(client, access_token, manual_carrier):
    dn_id, _task_id, shipment_id = _manual_waybill(client, access_token, manual_carrier, 'LX-0001', 'ORD-MP-1')
    data = _overview(client, access_token, manual_carrier)
    assert data['method'] == 'manual' and data['enabled'] is False and data['can_create'] is False
    assert data['can_record'] is True and data['blockers'] == []
    assert data['carrier']['pickup_method'] == 'manual' and data['carrier']['pickup_contact'] == '0120-111-222'
    row = next(r for r in data['shipments'] if r['shipment_id'] == shipment_id)
    assert row['source'] == 'manual' and row['tracking_number'] == 'LX-0001' and row['can_schedule'] is True
    assert row['package_count'] == 2 and row['weight_kg'] == 6.5

    response = _record_pickup(client, access_token, manual_carrier, [shipment_id])
    assert response.status_code == 201, response.get_json()
    pickup = response.get_json()['pickup']
    assert (pickup['source'], pickup['status'], pickup['carrier']) == ('manual', 'scheduled', None)
    assert pickup['confirmation_code'] == 'TEL-0425' and pickup['remarks'] == 'Booked by phone'
    assert (pickup['ready_time'], pickup['close_time'], pickup['package_count']) == ('13:00', '17:00', 2)
    assert pickup['can_cancel'] is True and pickup['holds_shipments'] is True
    assert pickup['shipments'][0]['source'] == 'manual'
    assert _record(client, pickup['id'])['held'] == [shipment_id]

    # 占着运单：不能再记 / 约
    response = _record_pickup(client, access_token, manual_carrier, [shipment_id])
    assert response.status_code == 409 and response.get_json()['code'] == 16132
    assert response.get_json()['details']['shipments'][0]['reason'] == 'ALREADY_SCHEDULED'

    # 取消只改本地，放开运单
    response = client.post(f"/dn/carrier-pickups/{pickup['id']}/cancel", headers=_wh(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['pickup']['status'] == 'cancelled'
    assert _record(client, pickup['id'])['held'] == []
    assert _overview(client, access_token, manual_carrier)['pickups'][0]['status'] == 'cancelled'


def test_manual_carrier_cannot_book_through_api(client, access_token, manual_carrier):
    _dn_id, _task_id, shipment_id = _manual_waybill(client, access_token, manual_carrier, 'LX-0002', 'ORD-MP-2')
    response = _book(client, access_token, [shipment_id], carrier_id=manual_carrier)
    assert response.status_code == 409 and response.get_json()['code'] == 16130
    assert [b['code'] for b in response.get_json()['details']['blockers']] == ['PICKUP_METHOD_NOT_API']
    response = client.get(f'/dn/carrier-pickups/availability?carrier_id={manual_carrier}', headers=_wh(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16130


def test_api_carrier_can_record_by_hand(client, access_token, pickup_fedex):
    """FedEx 系统故障时：电话约好，手工记一笔（不调 FedEx），带插件建的运单"""
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    with client.application.app_context():
        carrier_id = get_carrier().id
    calls = len(pickup_fedex.calls)
    response = _record_pickup(client, access_token, carrier_id, [shipment_id], confirmation_code=None)
    assert response.status_code == 201, response.get_json()
    pickup = response.get_json()['pickup']
    assert (pickup['source'], pickup['carrier'], pickup['confirmation_code']) == ('manual', 'fedex', None)
    assert pickup['can_cancel'] is True
    response = client.post(f"/dn/carrier-pickups/{pickup['id']}/cancel", headers=_wh(access_token))
    assert response.status_code == 200 and response.get_json()['pickup']['status'] == 'cancelled'
    assert len(pickup_fedex.calls) == calls                 # 没调 FedEx


@pytest.mark.parametrize('overrides, field', [
    ({'carrier_id': None}, 'carrier_id'),
    ({'shipment_ids': []}, 'shipment_ids'),
    ({'pickup_date': '2020-01-01'}, 'pickup_date'),
    ({'ready_time': '25:00'}, 'ready_time'),
    ({'close_time': '12:00'}, 'close_time'),
    ({'confirmation_code': 'X' * 41}, 'confirmation_code'),
    ({'confirmation_code': ['A']}, 'confirmation_code'),
    ({'remarks': 'x' * 101}, 'remarks'),
    ({'warehouse_id': 999999}, 'warehouse_id'),
])
def test_invalid_manual_record(client, access_token, manual_carrier, overrides, field):
    _dn_id, _task_id, shipment_id = _manual_waybill(client, access_token, manual_carrier, 'LX-0003', 'ORD-MP-3')
    response = _record_pickup(client, access_token, manual_carrier, [shipment_id], overrides)
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16131 and response.get_json()['field'] == field


def test_carriers_without_booked_pickups_are_not_listed(client, access_token, manual_carrier):
    with client.application.app_context():
        base = get_carrier()
        db.session.add(Carrier(name='Drop Co', code='drop', company_id=base.company_id, created_by=base.created_by,
                               pickup_method='dropoff'))
        db.session.commit()
    data = _overview(client, access_token)
    assert [c['name'] for c in data['carriers']] == ['Local Express']
    assert data['carrier']['id'] == manual_carrier
    response = client.get('/dn/carrier-pickups?carrier_id=1', headers=_wh(access_token))   # Test Carrier: none
    assert response.status_code == 400 and response.get_json()['field'] == 'carrier_id'
