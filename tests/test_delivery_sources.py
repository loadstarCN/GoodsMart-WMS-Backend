"""完成发货 / 签收的实际时刻与来源（doc/shipping-flow.md §4）

- 手工完成发货可带 shipped_at（实际交承运商的时刻，ISO 8601，默认现在）：完成时间、实际发货日、DN 的 delivered_at、
  dn.delivered 的 shipped_at 都是它；格式不对 400 16050，晚于现在 400 16136；completed_source = warehouse
- 手工签收 signed_source = warehouse；dn.completed 带 signed_source
- 承运商跟踪自动完成发货 / 签收：completed_source / signed_source = carrier
- 跟踪手填的运单号：插件 tracks_any_number（FedEx）且承运商 tracking_method = api 才跟；旧包裹判定的基准时刻是 DN 创建时刻；
  tracking_method 不是 api 时插件建的运单也不跟
"""
from .helpers import *
from .test_customs import _create_dn, _force_packed, _h, _prepare, _ship, _webhook_key
from .test_carrier_shipment import fedex  # noqa: F401  fedex 是 fixture
from .test_carrier_tracking import AWB, DL, IT, OC, PU, TRACK, _shipped_ready, _track_body

from datetime import datetime, timedelta, timezone      # helpers 的 import * 会把 datetime 换成模块，放在它后面

from system.webhook.models import WebhookEvent
from warehouse.dn.models import DNCarrierShipment
from warehouse.dn.tracking_services import CarrierTrackingService


def _domestic(client, token, order_number='ORD-SRC-1'):
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        task = db.session.get(DeliveryTask, task_id)
        task.carrier_id = get_carrier().id
        task.expected_shipping_date = (datetime.now(timezone.utc) - timedelta(days=3)).date()
        db.session.commit()
    return dn_id, task_id, key_id


def _payload(client, key_id, event_type):
    with client.application.app_context():
        return WebhookEvent.query.filter_by(api_key_id=key_id, event_type=event_type).one().payload


def test_complete_with_shipped_at_and_sources(client, access_token):
    dn_id, task_id, key_id = _domestic(client, access_token)
    shipped = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
    response = _ship(client, access_token, task_id, shipped_at=shipped.isoformat())
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['completed_source'] == 'warehouse'
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        assert task.completed_at == shipped and task.actual_shipping_date == shipped.date()
        assert task.completed_source == 'warehouse'
        assert db.session.get(DN, dn_id).delivered_at == shipped
    delivered = _payload(client, key_id, 'dn.delivered')
    assert datetime.fromisoformat(delivered['shipped_at']) == shipped.astimezone()

    signed = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
    response = client.put(f'/delivery/{task_id}/sign/', json={'signed_at': signed.isoformat()},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['signed_source'] == 'warehouse'
    completed = _payload(client, key_id, 'dn.completed')
    assert completed['signed_source'] == 'warehouse'
    assert datetime.fromisoformat(completed['signed_at']) == signed.astimezone()


def test_shipped_at_with_offset_is_stored_as_utc(client, access_token):
    _dn_id, task_id, _key = _domestic(client, access_token, 'ORD-SRC-2')
    moment = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).expected_shipping_date = datetime(2026, 9, 1).date()
        db.session.commit()
    response = _ship(client, access_token, task_id, shipped_at='2026-10-01T03:00:00Z')
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).completed_at == moment      # 带时区的 UTC


@pytest.mark.parametrize('value, code', [
    ('not a time', 16050),
    (12345, 16050),
    ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 16136),
])
def test_invalid_shipped_at(client, access_token, value, code):
    _dn_id, task_id, _key = _domestic(client, access_token, 'ORD-SRC-3')
    response = _ship(client, access_token, task_id, shipped_at=value)
    assert response.status_code == 400 and response.get_json()['code'] == code, response.get_json()
    assert response.get_json()['field'] == 'shipped_at'
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).status == 'in_progress'


def test_carrier_tracking_records_carrier_sources(client, access_token, fedex):
    dn_id, task_id, key_id = _shipped_ready(client, access_token, fedex)
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, IT, DL], delivered='2026-10-25T14:00:00+02:00')))
    with client.application.app_context():
        stats = CarrierTrackingService.sync()
        assert (stats['shipped'], stats['signed']) == (1, 1)
        task = db.session.get(DeliveryTask, task_id)
        assert (task.completed_source, task.signed_source) == ('carrier', 'carrier')
    assert _payload(client, key_id, 'dn.completed')['signed_source'] == 'carrier'


def test_manual_number_tracked_when_plugin_tracks_any_number(client, access_token, fedex):
    """FedEx 能查任何运单号：在 FedEx Ship Manager 手工建、手填进 WMS 的号码也照常跟踪、自动发货"""
    dn_id, task_id, key_id = _domestic(client, access_token, 'ORD-SRC-4')
    with client.application.app_context():
        db.session.get(DN, dn_id).created_at = datetime(2026, 10, 21, 12, 0)     # 基准时刻 = DN 创建时刻
        db.session.get(DeliveryTask, task_id).expected_shipping_date = datetime(2026, 10, 21).date()
        db.session.commit()
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': AWB}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        assert (record.source, record.carrier) == ('manual', 'fedex')
        record.created_at = datetime(2026, 10, 24, 9, 0)    # 记录晚于扫描（号码比记录早录入承运商系统）
        db.session.commit()
    fedex.set(TRACK, (200, _track_body('IT', [OC, PU, IT])))
    with client.application.app_context():
        stats = CarrierTrackingService.sync()
        assert (stats['tracked'], stats['shipped']) == (1, 1), stats
        task = db.session.get(DeliveryTask, task_id)
        assert (task.status, task.completed_source) == ('completed', 'carrier')
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        assert record.tracking_status == 'in_transit'

    # 手动刷新也能用于手填运单
    response = client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()


def test_tracking_method_not_api_is_not_tracked(client, access_token, fedex):
    dn_id, task_id, _key = _shipped_ready(client, access_token, fedex)
    with client.application.app_context():
        get_carrier().tracking_method = 'link'
        db.session.commit()
    fedex.set(TRACK, (200, _track_body('IT', [OC, PU, IT])))
    with client.application.app_context():
        assert CarrierTrackingService.sync()['tracked'] == 0
    response = client.post(f'/dn/{dn_id}/carrier-shipment/track', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16134
    assert fedex.of(TRACK) == []


def test_manual_number_not_tracked_without_tracks_any_number(client, access_token, fedex, monkeypatch):
    from warehouse.carrier.integrations.fedex.adapter import FedexAdapter
    monkeypatch.setattr(FedexAdapter, 'tracks_any_number', False)
    dn_id, task_id, _key = _domestic(client, access_token, 'ORD-SRC-5')
    assert client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': AWB},
                      headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        assert CarrierTrackingService.sync()['tracked'] == 0
