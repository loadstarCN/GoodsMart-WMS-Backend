"""外部订单系统需要每条 ASN 关闭路径的通知，不能只通知已签收后的取消。"""
from .helpers import *
from .test_dn_progress_webhook import _hook_key
from system.webhook.models import WebhookEvent
from warehouse.asn.services import ASNService
from datetime import datetime


@pytest.mark.parametrize(('received', 'action'), [(False, 'close'), (False, 'cancel'), (True, 'cancel')])
def test_every_asn_close_path_notifies_source_once(client, access_token, received, action):
    key_id = _hook_key(client)
    with client.application.app_context():
        asn = get_asn()
        asn_id = asn.id
        asn.api_key_id = key_id
        asn.order_number = 'WHOLESALE-CANCEL-1'
        db.session.commit()
        if received:
            ASNService.receive_asn(asn_id)
    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.put(f'/asn/{asn_id}/{action}/', headers=headers)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'closed'
    assert client.put(f'/asn/{asn_id}/{action}/', headers=headers).status_code in (400, 409)
    with client.application.app_context():
        event = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='asn.cancelled').one()
        assert event.payload['asn_id'] == asn_id
        assert event.payload['order_number'] == 'WHOLESALE-CANCEL-1'
        assert event.payload['status'] == 'closed'
        assert event.payload['details'][0]['goods_code']
        moment = datetime.fromisoformat(event.payload['closed_at'])
        assert moment.tzinfo is not None
        assert abs(int(moment.timestamp() * 1000) - event.payload['closed_at_ms']) < 1000


def test_internal_asn_close_has_no_external_notification(client):
    with client.application.app_context():
        asn = get_asn()
        asn.api_key_id = None
        ASNService.close_asn(asn.id)
        assert WebhookEvent.query.filter_by(event_type='asn.cancelled').count() == 0
