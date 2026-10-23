"""承运商跟踪（插件 supports_tracking，FedEx Track API）→ 自动完成发货 / 签收、推 dn.tracking_updated

- 只建了单（OC）：只存跟踪结果，不发货；结果没变不重复推事件
- 已揽收（PU / 在途）：packed 的 DN 以建运单的人的名义完成发货（推 dn.delivered），再推 dn.tracking_updated
- 已送达（DL）：签收发货任务（签收时间 = 送达时刻的服务器本地时间），DN 完成；packed 的同一轮先发货再签收
- 做不成（计划发货日在后 → 16061）记 tracking_problem，改好后下一轮做成并清空
- 查询失败（HTTP 错误 / 单号级错误）只记 error，保留上次结果
- 手动刷新 POST /dn/<id>/carrier-shipment/track；没有可跟踪的运单 → 409 16134

FedEx 一律 mock（沿用 test_carrier_shipment 的 fedex fixture），不联网。
"""
from .helpers import *
from .test_customs import _h, _webhook_key
from .test_carrier_shipment import _create, _ready, fedex  # noqa: F401  fedex 是 fixture

from system.webhook.models import WebhookEvent
from warehouse.carrier.integrations.base import (
    TRACK_DELIVERED, TRACK_EXCEPTION, TRACK_IN_TRANSIT, TRACK_LABEL_CREATED, TRACK_UNKNOWN,
)
from warehouse.carrier.integrations.fedex.tracking import normalize_track_result, status_of
from warehouse.dn.models import DNCarrierShipment
from warehouse.dn.tracking_services import CarrierTrackingService

from datetime import date, datetime, timedelta, timezone     # helpers 的 import * 会把 datetime 换成模块，放在它后面

AWB = '794600000001'
TRACK = '/track/v1/trackingnumbers'
OC = {'date': '2026-10-21T16:50:36+09:00', 'derivedStatusCode': 'OC', 'eventType': 'OC',
      'eventDescription': 'Shipment information sent to FedEx', 'scanLocation': {}}
PU = {'date': '2026-10-22T15:10:00+09:00', 'derivedStatusCode': 'PU', 'eventType': 'PU',
      'eventDescription': 'Picked up', 'scanLocation': {'city': 'NARITA', 'countryCode': 'JP'}}
IT = {'date': '2026-10-23T09:00:00+09:00', 'derivedStatusCode': 'IT', 'eventType': 'IT',
      'eventDescription': 'In transit', 'scanLocation': {'city': 'MEMPHIS', 'stateOrProvinceCode': 'TN',
                                                         'countryCode': 'US'}}
DL = {'date': '2026-10-25T14:00:00+02:00', 'derivedStatusCode': 'DL', 'eventType': 'DL',
      'eventDescription': 'Delivered', 'scanLocation': {'city': 'BERLIN', 'countryCode': 'DE'}}
CREATED_AT = datetime(2026, 10, 21, 15, 0, 0)        # 运单记录的建单时刻（服务器本地时间）
TEXT = {'OC': 'Label created', 'PU': 'Picked up', 'IT': 'In transit', 'DL': 'Delivered', 'DE': 'Delivery exception'}


def _track_body(code, events, delivered=None, number=AWB):
    result = {
        'trackingNumberInfo': {'trackingNumber': number},
        'latestStatusDetail': {'derivedCode': code, 'code': code, 'statusByLocale': TEXT.get(code, code)},
        'dateAndTimes': ([{'type': 'ACTUAL_DELIVERY', 'dateTime': delivered}] if delivered else [])
        + [{'type': 'ESTIMATED_DELIVERY', 'dateTime': '2026-10-26T18:00:00+01:00'}],
        'scanEvents': list(reversed(events)),
    }
    return {'transactionId': 'tx-track', 'output': {'completeTrackResults': [
        {'trackingNumber': number, 'trackResults': [result]}]}}


def _shipped_ready(client, token, fedex):
    """海外 DN：已打包、建好 FedEx 运单（AWB，建单时自动签发 CI / PL），DN 挂到带 webhook 的 API Key
    → (dn_id, task_id, key_id)"""
    dn_id, task_id = _ready(client, token)
    assert _create(client, token, dn_id).status_code == 201
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        # 建单时刻早于下面造的扫描（晚于建单的扫描才算这张运单的，见 from_older_shipment）
        DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one().created_at = CREATED_AT
        db.session.commit()
    return dn_id, task_id, key_id


def _sync(client, **kwargs):
    with client.application.app_context():
        return CarrierTrackingService.sync(**kwargs)


def _state(client, dn_id, task_id):
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        task = db.session.get(DeliveryTask, task_id)
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        return {'dn': dn.status, 'task': task.status, 'signed_at': task.signed_at,
                'tracking': record.tracking_payload(), 'created_by': record.created_by}


def _events(client, key_id):
    with client.application.app_context():
        return [(e.event_type, e.payload, e.dedupe_key)
                for e in WebhookEvent.query.filter_by(api_key_id=key_id).order_by(WebhookEvent.id)]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_status_mapping():
    assert status_of('OC', picked_up=False) == TRACK_LABEL_CREATED
    assert status_of('RP', picked_up=True) == TRACK_IN_TRANSIT       # 揽收后的通知类码按在途
    assert status_of('PU', picked_up=True) == TRACK_IN_TRANSIT
    assert status_of('DL', picked_up=True) == TRACK_DELIVERED
    assert status_of('RS', picked_up=True) == TRACK_EXCEPTION
    assert status_of('ZZ', picked_up=True) == TRACK_UNKNOWN


def test_normalize_picks_first_handled_scan():
    body = _track_body('IT', [OC, PU, IT])
    result = normalize_track_result(body['output']['completeTrackResults'][0]['trackResults'][0])
    assert result['status'] == TRACK_IN_TRANSIT
    assert result['picked_up_at'] == '2026-10-22T06:10:00+00:00'
    assert [e['code'] for e in result['events']] == ['IT', 'PU', 'OC']
    assert result['events'][0]['location'] == 'MEMPHIS, TN, US'
    assert result['estimated_delivery'] == '2026-10-26'
    oc_only = normalize_track_result(_track_body('OC', [OC])['output']['completeTrackResults'][0]['trackResults'][0])
    assert (oc_only['status'], oc_only['picked_up_at']) == (TRACK_LABEL_CREATED, None)


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------

def test_label_created_only_records_tracking(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('OC', [OC])))

    stats = _sync(client)
    assert (stats['tracked'], stats['updated'], stats['shipped']) == (1, 1, 0)
    state = _state(client, dn_id, task_id)
    assert (state['dn'], state['task']) == ('packed', 'pending')
    assert state['tracking']['status'] == TRACK_LABEL_CREATED and state['tracking']['code'] == 'OC'
    assert fedex.of(TRACK)[-1]['json']['trackingInfo'] == [{'trackingNumberInfo': {'trackingNumber': AWB}}]
    events = _events(client, key_id)
    assert [e[0] for e in events] == ['dn.tracking_updated']
    assert events[0][1]['tracking']['status'] == TRACK_LABEL_CREATED
    assert events[0][2] == f'dn:{dn_id}'

    # 结果没变：不重复推
    assert _sync(client)['updated'] == 0
    assert len(_events(client, key_id)) == 1


def test_picked_up_ships_automatically(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('IT', [OC, PU, IT])))

    stats = _sync(client)
    assert (stats['shipped'], stats['signed'], stats['problems']) == (1, 0, 0)
    state = _state(client, dn_id, task_id)
    assert (state['dn'], state['task']) == ('delivered', 'completed')
    assert state['tracking']['status'] == TRACK_IN_TRANSIT
    assert state['tracking']['picked_up_at'] == '2026-10-22T06:10:00+00:00'
    assert state['tracking']['problem'] is None
    with client.application.app_context():
        from warehouse.delivery.models import DeliveryTaskStatusLog
        logs = DeliveryTaskStatusLog.query.filter_by(task_id=task_id).order_by(DeliveryTaskStatusLog.id).all()
        assert [(log.old_status, log.new_status) for log in logs][-2:] == [
            ('pending', 'in_progress'), ('in_progress', 'completed')]
        assert {log.operator_id for log in logs[-2:]} == {state['created_by']}

    events = _events(client, key_id)
    assert [e[0] for e in events] == ['dn.delivered', 'dn.tracking_updated']     # 先发货、后跟踪
    assert events[0][1]['tracking_number'] == AWB
    assert events[1][1]['tracking']['status'] == TRACK_IN_TRANSIT


def test_delivered_on_packed_dn_ships_then_signs(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, IT, DL], delivered='2026-10-25T14:00:00+02:00')))

    stats = _sync(client)
    assert (stats['shipped'], stats['signed']) == (1, 1)
    state = _state(client, dn_id, task_id)
    assert (state['dn'], state['task']) == ('completed', 'signed')
    # 12:00 UTC → 服务器本地时间（与 datetime.now() 同一口径）
    assert state['signed_at'] == datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert state['tracking']['delivered_at'] == '2026-10-25T12:00:00+00:00'
    assert [e[0] for e in _events(client, key_id)] == ['dn.delivered', 'dn.completed', 'dn.tracking_updated']

    # DN 完成后定时任务不再跟
    assert _sync(client)['tracked'] == 0


def test_signs_after_manual_shipping(client, access_token, fedex):
    """人先手动完成了发货：送达时只签收"""
    from .test_customs import _ship
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    assert _ship(client, access_token, task_id).status_code == 200
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, DL], delivered='2026-10-25T14:00:00+02:00')))
    stats = _sync(client)
    assert (stats['shipped'], stats['signed']) == (0, 1)
    assert _state(client, dn_id, task_id)['dn'] == 'completed'


def test_blocked_shipping_is_recorded_then_retried(client, access_token, fedex):
    """计划发货日在后：自动发货做不成（16061）→ tracking_problem；改好后下一轮做成并清空"""
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).expected_shipping_date = date.today() + timedelta(days=3)
        db.session.commit()
    fedex.set(TRACK, (200, _track_body('PU', [OC, PU])))

    stats = _sync(client)
    assert (stats['shipped'], stats['problems']) == (0, 1)
    state = _state(client, dn_id, task_id)
    assert state['dn'] == 'packed' and state['task'] == 'pending'     # 整体回滚：发货任务也没被开始
    assert '16061' in state['tracking']['problem']
    assert state['tracking']['status'] == TRACK_IN_TRANSIT
    assert [e[0] for e in _events(client, key_id)] == ['dn.tracking_updated']

    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).expected_shipping_date = date.today()
        db.session.commit()
    stats = _sync(client)
    assert (stats['shipped'], stats['problems']) == (1, 0)
    state = _state(client, dn_id, task_id)
    assert state['dn'] == 'delivered' and state['tracking']['problem'] is None


def test_lookup_errors_keep_last_result(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('OC', [OC])))
    _sync(client)

    # 整批请求失败（5xx）
    fedex.set(TRACK, (500, {'errors': [{'code': 'SYSTEM.UNAVAILABLE.EXCEPTION', 'message': 'down'}]}))
    stats = _sync(client)
    assert stats['errors'] == 1
    tracking = _state(client, dn_id, task_id)['tracking']
    assert tracking['status'] == TRACK_LABEL_CREATED and len(tracking['events']) == 1
    assert 'SYSTEM.UNAVAILABLE' in tracking['error']

    # 单号级错误
    fedex.set(TRACK, (200, {'output': {'completeTrackResults': [{'trackingNumber': AWB, 'trackResults': [
        {'error': {'code': 'TRACKING.TRACKINGNUMBER.NOTFOUND', 'message': 'not found'}}]}]}}))
    _sync(client)
    tracking = _state(client, dn_id, task_id)['tracking']
    assert tracking['status'] == TRACK_LABEL_CREATED
    assert tracking['error'].startswith('TRACKING.TRACKINGNUMBER.NOTFOUND')
    assert [e[0] for e in _events(client, key_id)] == ['dn.tracking_updated']

    # 恢复后 error 清空
    fedex.set(TRACK, (200, _track_body('OC', [OC])))
    _sync(client)
    assert _state(client, dn_id, task_id)['tracking']['error'] is None


# ---------------------------------------------------------------------------
# 手动刷新 / GET
# ---------------------------------------------------------------------------

def test_manual_refresh_endpoint(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('PU', [OC, PU])))
    response = client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['tracking_sync']['shipped'] == 1
    assert body['shipment']['tracking']['status'] == TRACK_IN_TRANSIT
    assert body['integration']['tracks_shipments'] is True

    got = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert got['shipment']['tracking']['code'] == 'PU'


def test_manual_refresh_without_shipment(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    response = client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=_h(access_token))
    assert response.status_code == 409
    assert response.get_json()['code'] == 16134


# ---------------------------------------------------------------------------
# 审核修正（2026-10-23）
# ---------------------------------------------------------------------------

def _record_id(client, dn_id):
    with client.application.app_context():
        return DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one().id


def test_scans_older_than_the_shipment_are_ignored(client, access_token, fedex):
    """承运商回收再用的运单号查到旧包裹（揽收 / 送达早于建单）：不自动发货 / 签收，记 error"""
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    old_pu = {**PU, 'date': '2025-01-03T10:00:00+09:00'}
    old_dl = {**DL, 'date': '2025-01-05T10:00:00+01:00'}
    fedex.set(TRACK, (200, _track_body('DL', [old_pu, old_dl], delivered='2025-01-05T10:00:00+01:00')))
    stats = _sync(client)
    assert (stats['shipped'], stats['signed']) == (0, 0)
    state = _state(client, dn_id, task_id)
    assert (state['dn'], state['task']) == ('packed', 'pending')
    assert state['tracking']['error'].startswith('TRACKING.OLD_SHIPMENT')
    assert _events(client, key_id) == []


def test_newest_of_duplicate_results_is_used():
    """同一单号回来新旧两条结果：取最新扫描最晚的那条"""
    from warehouse.carrier.integrations.fedex.tracking import parse_track_response
    old = _track_body('DL', [{**PU, 'date': '2025-01-03T10:00:00+09:00'}])['output']['completeTrackResults'][0]
    new = _track_body('OC', [OC])['output']['completeTrackResults'][0]
    body = {'output': {'completeTrackResults': [
        {'trackingNumber': AWB, 'trackResults': old['trackResults'] + new['trackResults']}]}}
    assert parse_track_response(body)[AWB]['code'] == 'OC'


def test_failed_batch_does_not_discard_other_batches(client, access_token, fedex, monkeypatch):
    """一批请求失败只影响这一批：另一批照常处理（插件 track_batch_size = 1 → 两张运单两批）"""
    from warehouse.carrier.integrations.fedex.adapter import FedexAdapter
    from .test_carrier_shipment import _ship_body
    from .test_customs import _create_dn, _force_packed, PACKAGES
    from .test_carrier_shipment import _use_fedex
    first_dn, first_task, key_id = _shipped_ready(client, access_token, fedex)
    second_dn = _create_dn(client, access_token, order_number='ORD-EXPORT-2').get_json()['id']
    second_task = _force_packed(client, second_dn)
    assert client.put(f'/dn/{second_dn}/packages', json={'packages': PACKAGES},
                      headers=_h(access_token)).status_code == 200
    _use_fedex(client, second_task)
    fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000002')))
    assert _create(client, access_token, second_dn).status_code == 201
    with client.application.app_context():
        DNCarrierShipment.query.filter_by(dn_id=second_dn, status='active').one().created_at = CREATED_AT
        db.session.commit()

    monkeypatch.setattr(FedexAdapter, 'track_batch_size', 1)
    fedex.set(TRACK, (500, {'errors': [{'code': 'SYSTEM.UNAVAILABLE.EXCEPTION', 'message': 'down'}]}),
              (200, _track_body('PU', [OC, PU], number='794600000002')))
    stats = _sync(client)
    assert (stats['errors'], stats['shipped']) == (1, 1)
    assert _state(client, first_dn, first_task)['tracking']['error'].startswith('FedEx track')
    assert _state(client, second_dn, second_task)['dn'] == 'delivered'


def test_signed_time_not_before_shipping(client, access_token, fedex):
    """手动发货晚于承运商的送达时刻：签收时间按完成发货时刻（不早于发货）"""
    from .test_customs import _ship
    dn_id, task_id, _key = _shipped_ready(client, access_token, fedex)
    assert _ship(client, access_token, task_id).status_code == 200
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, {**DL, 'date': '2026-10-22T08:00:00+00:00'}],
                                       delivered='2026-10-22T08:00:00+00:00')))
    assert _sync(client)['signed'] == 1
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        assert task.signed_at == task.completed_at


def test_cancelled_or_fresher_record_is_not_overwritten(client, access_token, fedex):
    """运单已不是有效的这张、或这一轮开始后别人已存了更新的结果：不写、不推"""
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    record_id = _record_id(client, dn_id)
    result = {'status': TRACK_IN_TRANSIT, 'code': 'IT', 'events': []}
    with client.application.app_context():
        record = db.session.get(DNCarrierShipment, record_id)
        record.tracking_synced_at = datetime.now()
        db.session.commit()
        assert CarrierTrackingService._save(record_id, AWB, result, None, datetime.now() - timedelta(minutes=1)) is False
        record = db.session.get(DNCarrierShipment, record_id)
        record.status = 'cancelled'
        db.session.commit()
        assert CarrierTrackingService._save(record_id, AWB, result, None, datetime.now()) is False
        assert db.session.get(DNCarrierShipment, record_id).tracking_status is None
    assert _events(client, key_id) == []


def test_unexpected_error_is_recorded_as_problem(client, access_token, fedex, monkeypatch):
    from warehouse.delivery.services import DeliveryTaskService

    def boom(*args, **kwargs):
        raise RuntimeError('db constraint')

    dn_id, task_id, _key = _shipped_ready(client, access_token, fedex)
    monkeypatch.setattr(DeliveryTaskService, 'complete_task', staticmethod(boom))
    fedex.set(TRACK, (200, _track_body('PU', [OC, PU])))
    stats = _sync(client)
    assert (stats['shipped'], stats['problems'], stats['errors']) == (0, 1, 0)
    tracking = _state(client, dn_id, task_id)['tracking']
    assert 'Unexpected error: RuntimeError' in tracking['problem']
    assert tracking['status'] == TRACK_IN_TRANSIT


def test_completed_dn_keeps_tracking_until_delivered(client, access_token, fedex):
    """仓库手工签收（DN 完成）后，跟踪没到送达前继续更新显示；送达后不再查"""
    from .test_customs import _ship
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    assert _ship(client, access_token, task_id).status_code == 200
    assert client.put(f'/delivery/{task_id}/sign/', json={}, headers=_h(access_token)).status_code == 200
    fedex.set(TRACK, (200, _track_body('IT', [OC, PU, IT])))
    assert _sync(client)['tracked'] == 1
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, IT, DL], delivered='2026-10-25T14:00:00+02:00')))
    stats = _sync(client)
    assert (stats['tracked'], stats['signed']) == (1, 0)
    assert _state(client, dn_id, task_id)['tracking']['status'] == TRACK_DELIVERED
    assert _sync(client)['tracked'] == 0
    completed = [e for e in _events(client, key_id) if e[0] == 'dn.completed']
    assert completed[0][1]['tracking_number'] == AWB


def test_other_plugin_is_not_used_for_tracking(client, access_token, fedex):
    """运单记录的插件与承运商现在的对接插件不同：不拿别家插件去查"""
    dn_id, _task, _key = _shipped_ready(client, access_token, fedex)
    with client.application.app_context():
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        record.carrier = 'otherplugin'
        db.session.commit()
    response = client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16134


def test_refresh_needs_delivery_edit(client, access_token, fedex):
    from .test_process_guards import _staff_token
    dn_id, _task, _key = _shipped_ready(client, access_token, fedex)
    packer = _staff_token(client, 'packer-only', ['packing_edit', 'packing_read'])
    assert client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=packer).status_code == 403
