"""上门揽收预约（FedEx 对接插件的 Pickup Request API）：承运商选择、待揽收运单、前置条件、查可约时间、预约请求体、
预约结果（scheduled / failed / unknown）、占用与释放运单、取消、确认作废、运单状态里的揽收概要。

FedEx 一律 mock：替换 warehouse.carrier.integrations.fedex.client._send，不联网（夹具见 test_carrier_shipment）。
"""
from .helpers import *
from .test_carrier_shipment import (  # noqa: F401
    ACCOUNT, _create, _other_carrier, _ready, _set_fedex, _ship_body, _use_fedex, fedex,
)
from .test_customs import PACKAGES, _create_dn, _force_packed, _h, _prepare, _ship

from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone

import requests

from warehouse.dn.models import CarrierPickup, DNCarrierShipment
from zoneinfo import ZoneInfo

PICKUP = '/pickup/v1/pickups'
PICKUP_CANCEL = '/pickup/v1/pickups/cancel'
AVAILABILITY = '/pickup/v1/pickups/availabilities'

AVAILABILITY_BODY = {   # 正式环境 2026-10-21 晚上查京都仓的返回（节选）
    'transactionId': 'tx-avail-1',
    'output': {'requestTimestamp': '2026-10-21T20:31:34', 'options': [
        {'carrier': 'FDXE', 'available': True, 'pickupDate': '2026-10-23', 'cutOffTime': '17:00:00',
         'accessTime': {'hours': 2, 'minutes': 0}, 'residentialAvailable': False,
         'readyTimeOptions': ['08:00:00', '12:00:00', '17:00:00'], 'defaultReadyTime': '12:00:00',
         'latestTimeOptions': ['14:00:00', '18:00:00', '23:59:00'], 'defaultLatestTimeOptions': '18:00:00',
         'countryRelationship': 'INTERNATIONAL', 'scheduleDay': 'FUTURE_DAY'},
        {'carrier': 'FDXE', 'available': True, 'pickupDate': '2026-10-22', 'cutOffTime': '17:00:00',
         'accessTime': {'hours': 2, 'minutes': 30}, 'readyTimeOptions': ['08:00:00'],
         'latestTimeOptions': ['18:00:00'], 'defaultReadyTime': '12:00:00',
         'defaultLatestTimeOptions': '18:00:00', 'scheduleDay': 'FUTURE_DAY'},
    ]},
}


def _created_body(code='3229', location='NQAA'):
    return {'transactionId': 'tx-pickup-1', 'output': {'pickupConfirmationCode': code, 'location': location,
                                                       'message': 'Courier on the way'}}


@pytest.fixture
def pickup_fedex(client, fedex):
    fedex.set(PICKUP, (200, _created_body()))
    fedex.set(PICKUP_CANCEL, (200, {'transactionId': 'tx-pcancel-1', 'output': {
        'pickupConfirmationCode': '3229', 'cancelConfirmationMessage': 'Requested pickup has been cancelled'}}))
    fedex.set(AVAILABILITY, (200, AVAILABILITY_BODY))
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.phone = '075-123-4567'
        warehouse.contact_name_en = 'Warehouse Staff'
        warehouse.timezone = 'Asia/Tokyo'       # 京都仓：揽收的日期 / 时间按仓库时区（doc/timezone-utc.md）
        db.session.commit()
        _WAREHOUSE['id'] = warehouse.id
    return fedex


_WAREHOUSE = {}


def _wh(token):
    """带当前仓库请求头（揽收接口按仓库）"""
    return _h(token, **{'X-WAREHOUSE-ID': str(_WAREHOUSE['id'])})


def _shipment(client, token, order_number=None, carrier_id=None):
    """建好一张 FedEx 自动运单（DN 已打包、2 箱各 3.25 kg；承运商默认 get_carrier()）。返回 (dn_id, task_id, shipment_id)。"""
    if order_number:
        dn_id = _create_dn(client, token, order_number=order_number).get_json()['id']
        task_id = _force_packed(client, dn_id)
        assert client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token)).status_code == 200
        _use_fedex(client, task_id, carrier_id)
    else:
        dn_id, task_id = _ready(client, token)
    response = _create(client, token, dn_id)
    assert response.status_code == 201, response.get_json()
    return dn_id, task_id, response.get_json()['shipment']['id']


def local_today():
    """仓库当地今天（pickup_fedex 把测试仓库的时区设成 Asia/Tokyo）"""
    return _datetime.now(ZoneInfo('Asia/Tokyo')).date()


def _tomorrow():
    return (local_today() + _timedelta(days=1)).isoformat()


def _book(client, token, shipment_ids, **overrides):
    body = {'shipment_ids': shipment_ids, 'pickup_date': _tomorrow(), 'ready_time': '12:00', 'close_time': '18:00'}
    body.update(overrides)
    return client.post('/dn/carrier-pickups', json=body, headers=_wh(token))


def _overview(client, token, carrier_id=None):
    query = f'?carrier_id={carrier_id}' if carrier_id else ''
    response = client.get(f'/dn/carrier-pickups{query}', headers=_wh(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _row(overview, shipment_id):
    return next(r for r in overview['shipments'] if r['shipment_id'] == shipment_id)


def _record(client, pickup_id):
    with client.application.app_context():
        record = db.session.get(CarrierPickup, pickup_id)
        held = [s.id for s in DNCarrierShipment.query.filter_by(pickup_id=pickup_id).all()]
        return {'status': record.status, 'reason': record.reason, 'held': held,
                'confirmation_code': record.confirmation_code, 'location_code': record.location_code,
                'error_message': record.error_message}


# ---------------------------------------------------------------------------
# 待揽收运单与前置条件
# ---------------------------------------------------------------------------

def test_overview_lists_open_shipments_and_location(client, access_token, pickup_fedex):
    dn_id, task_id, shipment_id = _shipment(client, access_token)
    data = _overview(client, access_token)
    assert data['enabled'] is True and data['blockers'] == [] and data['can_create'] is True
    with client.application.app_context():
        carrier_id = get_carrier().id
    assert data['carrier']['id'] == carrier_id and data['carrier']['adapter'] == 'fedex'
    assert [c['id'] for c in data['carriers']] == [carrier_id]
    assert data['integration']['code'] == 'fedex' and data['integration']['name'] == 'FedEx'
    assert data['max_remarks'] == 60
    assert data['today'] == local_today().isoformat() and data['timezone'] == 'Asia/Tokyo'
    row = _row(data, shipment_id)
    assert row['dn_id'] == dn_id and row['delivery_task_id'] == task_id
    assert row['tracking_number'] == '794600000001' and row['package_count'] == 2 and row['weight_kg'] == 6.5
    assert row['service_name'] == 'International Economy'
    assert row['pickup'] is None and row['can_schedule'] is True
    # 仓库没有英文地址 → 用公司的；联系人 / 电话用仓库的
    assert data['pickup_location'] == {
        'contact': {'phoneNumber': '0751234567', 'personName': 'Warehouse Staff',
                    'companyName': 'Example Trading Co., Ltd.'},
        'address': {'streetLines': ['1-2-3 Example', 'Minato-ku'], 'city': 'Tokyo', 'postalCode': '1050000',
                    'countryCode': 'JP'},
    }
    assert data['pickups'] == []


def test_shipped_dn_is_not_listed(client, access_token, pickup_fedex):
    _dn_id, task_id, shipment_id = _shipment(client, access_token)
    assert _ship(client, access_token, task_id, tracking_number='794600000001').status_code == 200
    data = _overview(client, access_token)
    assert data['shipments'] == [] and data['can_create'] is False
    response = _book(client, access_token, [shipment_id])
    assert response.status_code == 409 and response.get_json()['code'] == 16132
    assert response.get_json()['details']['shipments'][0]['reason'] == 'DN_SHIPPED'


def test_blockers(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.phone = '123'
        warehouse.company.phone = None
        db.session.commit()
    data = _overview(client, access_token)
    assert [b['code'] for b in data['blockers']] == ['PICKUP_PHONE_INVALID'] and data['can_create'] is False

    _set_fedex(client, enabled=False)          # 对接停用
    response = _book(client, access_token, [shipment_id])
    assert response.status_code == 409 and response.get_json()['code'] == 16130
    assert [b['code'] for b in response.get_json()['details']['blockers']] == [
        'CARRIER_INTEGRATION_DISABLED', 'PICKUP_PHONE_INVALID']
    assert _overview(client, access_token)['enabled'] is False
    assert client.get('/dn/carrier-pickups/availability', headers=_wh(access_token)).get_json()['code'] == 16130
    assert pickup_fedex.of(PICKUP) == [] and pickup_fedex.of(AVAILABILITY) == []


def test_warehouse_header_required(client, access_token, pickup_fedex):
    response = client.get('/dn/carrier-pickups', headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 14003


# ---------------------------------------------------------------------------
# 查可约时间
# ---------------------------------------------------------------------------

def test_availability(client, access_token, pickup_fedex):
    _prepare(client)
    response = client.get('/dn/carrier-pickups/availability', headers=_wh(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert [o['pickup_date'] for o in data['options']] == ['2026-10-22', '2026-10-23']     # 按日期排
    option = data['options'][1]
    assert option == {
        'pickup_date': '2026-10-23', 'schedule_day': 'FUTURE_DAY', 'available': True, 'cutoff_time': '17:00',
        'access_minutes': 120, 'ready_times': ['08:00', '12:00', '17:00'], 'latest_times': ['14:00', '18:00', '23:59'],
        'default_ready_time': '12:00', 'default_latest_time': '18:00', 'residential_available': False,
    }
    assert data['options'][0]['access_minutes'] == 150 and data['transaction_id'] == 'tx-avail-1'
    request = pickup_fedex.of(AVAILABILITY)[0]['json']
    assert request == {
        'pickupAddress': {'streetLines': ['1-2-3 Example', 'Minato-ku'], 'city': 'Tokyo', 'postalCode': '1050000',
                          'countryCode': 'JP'},
        'pickupRequestType': ['SAME_DAY', 'FUTURE_DAY'], 'carriers': ['FDXE'], 'countryRelationship': 'INTERNATIONAL',
    }


def test_availability_permission_denied(client, access_token, pickup_fedex):
    _prepare(client)
    pickup_fedex.set(AVAILABILITY, (403, {'transactionId': 'tx-403', 'errors': [
        {'code': 'FORBIDDEN.ERROR', 'message': 'We could not authorize your credentials.'}]}))
    response = client.get('/dn/carrier-pickups/availability', headers=_wh(access_token))
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    details = response.get_json()['details']
    assert details['permission_denied'] is True and details['action'] == 'pickup_availability'


# ---------------------------------------------------------------------------
# 预约
# ---------------------------------------------------------------------------

def test_schedule_pickup_request_and_result(client, access_token, pickup_fedex):
    dn_id, _task_id, first = _shipment(client, access_token)
    pickup_fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000009')))
    _dn2, _task2, second = _shipment(client, access_token, order_number='ORD-EXP-2')

    response = _book(client, access_token, [second, first, first], remarks='  Please   ring the bell ',
                     notification_email='loop@example.com')
    assert response.status_code == 201, response.get_json()
    pickup = response.get_json()['pickup']
    assert pickup['status'] == 'scheduled' and pickup['confirmation_code'] == '3229'
    assert pickup['location_code'] == 'NQAA' and pickup['transaction_id'] == 'tx-pickup-1'
    assert pickup['pickup_date'] == _tomorrow() and (pickup['ready_time'], pickup['close_time']) == ('12:00', '18:00')
    assert pickup['package_count'] == 4 and pickup['total_weight_kg'] == 13.0
    assert [s['shipment_id'] for s in pickup['shipments']] == [second, first]
    assert pickup['can_cancel'] is True and pickup['holds_shipments'] is True and pickup['can_dismiss'] is False

    request = pickup_fedex.of(PICKUP)[0]['json']
    assert request == {
        'associatedAccountNumber': {'value': ACCOUNT},
        'originDetail': {
            'pickupLocation': {
                'contact': {'phoneNumber': '0751234567', 'personName': 'Warehouse Staff',
                            'companyName': 'Example Trading Co., Ltd.'},
                'address': {'streetLines': ['1-2-3 Example', 'Minato-ku'], 'city': 'Tokyo',
                            'postalCode': '1050000', 'countryCode': 'JP'},
            },
            'readyDateTimestamp': f'{_tomorrow()}T12:00:00+09:00',      # 当地时间带时区偏移
            'customerCloseTime': '18:00:00',
            'pickupDateType': 'FUTURE_DAY',
        },
        'carrierCode': 'FDXE',
        'countryRelationships': 'INTERNATIONAL',
        'packageCount': 4,
        'totalWeight': {'units': 'KG', 'value': 13.0},
        'remarks': 'Please ring the bell',
        'pickupNotificationDetail': {'emailDetails': [{'address': 'loop@example.com', 'locale': 'en_US'}],
                                     'format': 'HTML'},
    }

    # 两张运单都被占着；运单状态里能看到揽收概要
    data = _overview(client, access_token)
    assert data['can_create'] is False
    assert {_row(data, s)['can_schedule'] for s in (first, second)} == {False}
    assert _row(data, first)['pickup']['confirmation_code'] == '3229'
    assert data['pickups'][0]['id'] == pickup['id']
    status = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert status['shipment']['pickup']['id'] == pickup['id'] and status['shipment']['pickup']['status'] == 'scheduled'

    # 再约同一张 → 409 16132，不调 FedEx
    response = _book(client, access_token, [first])
    assert response.status_code == 409 and response.get_json()['code'] == 16132
    problem = response.get_json()['details']['shipments'][0]
    assert problem['reason'] == 'ALREADY_SCHEDULED' and problem['pickup']['id'] == pickup['id']
    assert len(pickup_fedex.of(PICKUP)) == 1


def test_same_day_pickup_type(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    response = _book(client, access_token, [shipment_id], pickup_date=local_today().isoformat(),
                     ready_time='9:30', close_time='17:00:00')
    assert response.status_code == 201, response.get_json()
    origin = pickup_fedex.of(PICKUP)[0]['json']['originDetail']
    assert origin['pickupDateType'] == 'SAME_DAY' and origin['readyDateTimestamp'].endswith('T09:30:00+09:00')
    assert origin['customerCloseTime'] == '17:00:00'
    assert 'remarks' not in pickup_fedex.of(PICKUP)[0]['json']


@pytest.mark.parametrize('overrides, field', [
    ({'shipment_ids': []}, 'shipment_ids'),
    ({'shipment_ids': [0]}, 'shipment_ids'),
    ({'shipment_ids': 'x'}, 'shipment_ids'),
    ({'pickup_date': '2026/10/22'}, 'pickup_date'),
    ({'pickup_date': -1}, 'pickup_date'),          # 昨天（用例里换成日期）
    ({'pickup_date': 31}, 'pickup_date'),          # 31 天后
    ({'ready_time': '25:00'}, 'ready_time'),
    ({'close_time': '12:00'}, 'close_time'),
    ({'remarks': 'x' * 61}, 'remarks'),
    ({'notification_email': 'not-an-email'}, 'notification_email'),
])
def test_invalid_request(client, access_token, pickup_fedex, overrides, field):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    if isinstance(overrides.get('pickup_date'), int):
        with client.application.app_context():
            overrides = {'pickup_date': (local_today() + _timedelta(days=overrides['pickup_date'])).isoformat()}
    body = {'shipment_ids': [shipment_id], **overrides}
    response = _book(client, access_token, **body)
    assert response.status_code == 400 and response.get_json()['code'] == 16131
    assert response.get_json()['field'] == field
    assert pickup_fedex.of(PICKUP) == []


def test_unknown_shipment_id(client, access_token, pickup_fedex):
    _prepare(client)
    response = _book(client, access_token, [99999])
    assert response.status_code == 409 and response.get_json()['code'] == 16132
    assert response.get_json()['details']['shipments'] == [
        {'shipment_id': 99999, 'tracking_number': None, 'reason': 'NOT_FOUND'}]


def test_fedex_rejects_pickup_releases_shipments(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    pickup_fedex.set(PICKUP, (400, {'transactionId': 'tx-p-400', 'errors': [
        {'code': 'PICKUP.READYTIME.INVALID', 'message': 'Ready time is after the cutoff time.'}]}))
    response = _book(client, access_token, [shipment_id])
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    details = response.get_json()['details']
    assert details['action'] == 'pickup' and details['maybe_processed'] is False
    assert details['errors'][0]['code'] == 'PICKUP.READYTIME.INVALID'
    assert details['pickup']['status'] == 'failed' and details['pickup']['reason'] == 'rejected'
    record = _record(client, details['pickup']['id'])
    assert record['held'] == [] and 'PICKUP.READYTIME.INVALID' in record['error_message']
    assert _row(_overview(client, access_token), shipment_id)['can_schedule'] is True

    pickup_fedex.set(PICKUP, (200, _created_body(code='77')))
    assert _book(client, access_token, [shipment_id]).status_code == 201


def test_timeout_keeps_shipments_until_dismissed(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    pickup_fedex.errors[PICKUP] = requests.ReadTimeout('read timed out')
    response = _book(client, access_token, [shipment_id])
    assert response.status_code == 504 and response.get_json()['code'] == 16074
    pickup = response.get_json()['details']['pickup']
    assert pickup['status'] == 'unknown' and pickup['reason'] == 'timeout' and pickup['can_dismiss'] is True
    assert 'dismiss' in response.get_json()['message']
    del pickup_fedex.errors[PICKUP]

    # 结果不明：运单仍被占着，不能重复预约
    assert _row(_overview(client, access_token), shipment_id)['can_schedule'] is False
    assert _book(client, access_token, [shipment_id]).get_json()['details']['shipments'][0]['reason'] == \
        'ALREADY_SCHEDULED'
    # 结果不明的不能取消；确认作废要 confirm: true
    url = f"/dn/carrier-pickups/{pickup['id']}"
    assert client.post(f'{url}/cancel', headers=_wh(access_token)).get_json()['code'] == 16133
    response = client.post(f'{url}/dismiss', json={}, headers=_wh(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'confirm'
    response = client.post(f'{url}/dismiss', json={'confirm': True}, headers=_wh(access_token))
    assert response.status_code == 200 and response.get_json()['pickup']['status'] == 'dismissed'
    assert _record(client, pickup['id'])['held'] == []
    assert _row(_overview(client, access_token), shipment_id)['can_schedule'] is True
    # 再作废 → 409
    assert client.post(f'{url}/dismiss', json={'confirm': True}, headers=_wh(access_token)).get_json()['code'] == 16133


def test_response_without_confirmation_is_unknown(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    pickup_fedex.set(PICKUP, (200, {'transactionId': 'tx-odd', 'output': {}}))
    response = _book(client, access_token, [shipment_id])
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    assert response.get_json()['details']['maybe_processed'] is True
    assert response.get_json()['details']['pickup']['status'] == 'unknown'
    assert response.get_json()['details']['pickup']['reason'] == 'bad_response'


def test_stale_pending_is_unknown(client, access_token, pickup_fedex):
    _dn_id, _task_id, shipment_id = _shipment(client, access_token)
    with client.application.app_context():
        record = CarrierPickup(warehouse_id=get_warehouse().id, carrier='fedex', carrier_id=get_carrier().id,
                               status='pending',
                               pickup_date=local_today(), ready_time='12:00', close_time='18:00', package_count=2,
                               total_weight_kg=6.5, created_at=_datetime.now(_timezone.utc) - _timedelta(hours=1))
        db.session.add(record)
        db.session.flush()
        db.session.get(DNCarrierShipment, shipment_id).pickup_id = record.id
        db.session.commit()
        pickup_id = record.id
    data = _overview(client, access_token)
    assert data['pickups'][0]['status'] == 'unknown' and data['pickups'][0]['reason'] == 'stale'
    assert data['pickups'][0]['can_dismiss'] is True and _row(data, shipment_id)['can_schedule'] is False
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/dismiss', json={'confirm': True},
                           headers=_wh(access_token))
    assert response.status_code == 200 and _record(client, pickup_id)['held'] == []


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

def _scheduled(client, token, fake):
    _dn_id, _task_id, shipment_id = _shipment(client, token)
    response = _book(client, token, [shipment_id])
    assert response.status_code == 201, response.get_json()
    return shipment_id, response.get_json()['pickup']['id']


def test_cancel_pickup(client, access_token, pickup_fedex):
    shipment_id, pickup_id = _scheduled(client, access_token, pickup_fedex)
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/cancel', headers=_wh(access_token))
    assert response.status_code == 200, response.get_json()
    pickup = response.get_json()['pickup']
    assert pickup['status'] == 'cancelled' and pickup['cancelled_by_user']['id'] and pickup['can_cancel'] is False
    assert pickup_fedex.of(PICKUP_CANCEL)[0]['json'] == {
        'associatedAccountNumber': {'value': ACCOUNT}, 'pickupConfirmationCode': '3229', 'carrierCode': 'FDXE',
        'scheduledDate': _tomorrow(), 'location': 'NQAA'}
    assert _record(client, pickup_id)['held'] == []
    assert _row(_overview(client, access_token), shipment_id)['can_schedule'] is True
    # 已取消的再取消 → 409 16133，不调 FedEx
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/cancel', headers=_wh(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16133
    assert len(pickup_fedex.of(PICKUP_CANCEL)) == 1


def test_cancel_refused_and_unclear_then_already_cancelled(client, access_token, pickup_fedex):
    shipment_id, pickup_id = _scheduled(client, access_token, pickup_fedex)
    url = f'/dn/carrier-pickups/{pickup_id}/cancel'

    pickup_fedex.set(PICKUP_CANCEL, (400, {'errors': [{'code': 'PICKUP.CANCEL.NOTALLOWED', 'message': 'no'}]}))
    response = client.post(url, headers=_wh(access_token))
    assert response.status_code == 502 and response.get_json()['details']['maybe_processed'] is False
    assert _record(client, pickup_id)['status'] == 'scheduled'
    assert _record(client, pickup_id)['reason'] == 'cancel_refused'

    pickup_fedex.set(PICKUP_CANCEL, (500, {'errors': [{'code': 'INTERNAL.SERVER.ERROR', 'message': 'oops'}]}))
    response = client.post(url, headers=_wh(access_token))
    assert response.status_code == 502 and response.get_json()['details']['maybe_processed'] is True
    assert 'retry' in response.get_json()['message']
    record = _record(client, pickup_id)
    assert record['status'] == 'scheduled' and record['reason'] == 'cancel_unclear'
    assert record['held'] == [shipment_id]

    pickup_fedex.set(PICKUP_CANCEL, (400, {'errors': [{'code': 'PICKUP.ALREADY.CANCELLED', 'message': 'gone'}]}))
    response = client.post(url, headers=_wh(access_token))
    assert response.status_code == 200 and response.get_json()['pickup']['status'] == 'cancelled'
    assert response.get_json()['pickup']['reason'] == 'already_cancelled'
    assert _record(client, pickup_id)['held'] == []


def test_past_pickup_cannot_be_cancelled_and_releases_shipments(client, access_token, pickup_fedex):
    shipment_id, pickup_id = _scheduled(client, access_token, pickup_fedex)
    with client.application.app_context():
        db.session.get(CarrierPickup, pickup_id).pickup_date = local_today() - _timedelta(days=1)
        db.session.commit()
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/cancel', headers=_wh(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16133
    data = _overview(client, access_token)
    row = _row(data, shipment_id)
    # 揽收日过了、DN 还没发货：可以重新预约，仍带着上次预约的信息
    assert row['can_schedule'] is True and row['pickup']['past'] is True and row['pickup']['id'] == pickup_id
    assert data['pickups'][0]['past'] is True and data['pickups'][0]['can_cancel'] is False
    assert _book(client, access_token, [shipment_id]).status_code == 201


def test_unknown_pickup_is_404(client, access_token, pickup_fedex):
    for action in ('cancel', 'dismiss'):
        response = client.post(f'/dn/carrier-pickups/99999/{action}', json={'confirm': True},
                               headers=_wh(access_token))
        assert response.status_code == 404 and response.get_json()['code'] == 13001


# ---------------------------------------------------------------------------
# 承运商
# ---------------------------------------------------------------------------

def test_no_carrier_can_book_pickups(client, access_token):
    with client.application.app_context():
        _WAREHOUSE['id'] = get_warehouse().id
    data = _overview(client, access_token)
    assert data['enabled'] is False and data['carrier'] is None and data['carriers'] == []
    assert [b['code'] for b in data['blockers']] == ['PICKUP_CARRIER_MISSING']
    assert data['shipments'] == [] and data['pickups'] == [] and data['can_create'] is False
    response = client.get('/dn/carrier-pickups/availability', headers=_wh(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16130


def test_pickups_are_per_carrier(client, access_token, pickup_fedex):
    """同公司两个承运商各有 FedEx 对接（各自的账号）：运单、预约按承运商分开，预约用该承运商的账号"""
    _dn_id, _task_id, first = _shipment(client, access_token)
    other = _other_carrier(client, name='FedEx (second account)', code='fedex2')
    _set_fedex(client, carrier_id=other, account_number='999999999')
    pickup_fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000009')))
    _dn2, _task2, second = _shipment(client, access_token, order_number='ORD-EXP-2', carrier_id=other)

    with client.application.app_context():
        base = get_carrier().id
    default = _overview(client, access_token, carrier_id=base)
    assert len(default['carriers']) == 2
    assert [r['shipment_id'] for r in default['shipments']] == [first]
    data = _overview(client, access_token, carrier_id=other)
    assert data['carrier']['id'] == other and [r['shipment_id'] for r in data['shipments']] == [second]

    # 别的承运商的运单不能一起约
    response = _book(client, access_token, [first, second], carrier_id=base)
    assert response.status_code == 409 and response.get_json()['code'] == 16132
    assert response.get_json()['details']['shipments'] == [
        {'shipment_id': second, 'tracking_number': '794600000009', 'reason': 'OTHER_CARRIER', 'carrier': 'fedex'}]

    response = _book(client, access_token, [second], carrier_id=other)
    assert response.status_code == 201, response.get_json()
    pickup = response.get_json()['pickup']
    assert pickup['carrier_id'] == other and pickup['carrier'] == 'fedex'
    assert pickup_fedex.of(PICKUP)[0]['json']['associatedAccountNumber'] == {'value': '999999999'}
    assert [p['id'] for p in _overview(client, access_token, carrier_id=other)['pickups']] == [pickup['id']]
    assert _overview(client, access_token, carrier_id=base)['pickups'] == []


@pytest.mark.parametrize('carrier_id', ['abc', 0, 99999])
def test_invalid_carrier_id(client, access_token, pickup_fedex, carrier_id):
    response = client.get(f'/dn/carrier-pickups?carrier_id={carrier_id}', headers=_wh(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 16131
    assert response.get_json()['field'] == 'carrier_id'


def test_carrier_without_integration_is_not_offered(client, access_token, pickup_fedex):
    other = _other_carrier(client)
    data = _overview(client, access_token)
    assert [c['id'] for c in data['carriers']] != [] and other not in [c['id'] for c in data['carriers']]
    response = client.get(f'/dn/carrier-pickups?carrier_id={other}', headers=_wh(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'carrier_id'


def test_cancel_works_after_integration_turned_off(client, access_token, pickup_fedex):
    _shipment_id, pickup_id = _scheduled(client, access_token, pickup_fedex)
    _set_fedex(client, enabled=False)
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/cancel', headers=_wh(access_token))
    assert response.status_code == 200 and response.get_json()['pickup']['status'] == 'cancelled'


def test_cancel_blocked_when_integration_removed(client, access_token, pickup_fedex):
    _shipment_id, pickup_id = _scheduled(client, access_token, pickup_fedex)
    with client.application.app_context():
        db.session.delete(get_carrier().integration)
        db.session.commit()
    response = client.post(f'/dn/carrier-pickups/{pickup_id}/cancel', headers=_wh(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16130
    assert response.get_json()['details']['blockers'][0]['code'] == 'CARRIER_NOT_INTEGRATED'
    assert pickup_fedex.of(PICKUP_CANCEL) == []
    assert _record(client, pickup_id)['status'] == 'scheduled'
