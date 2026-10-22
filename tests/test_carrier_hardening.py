"""承运商自动运单（FedEx 插件）/ 出口单证加固（2026-10-04 审计 B-63 ~ B-66、B-68、B-87、B-89、W-31、W-35）：

- 取消分三段（标 cancelling 提交 → 不开事务调 FedEx → 落最终状态）；结果不明停在 cancelling，拦发货（16079），
  重试取消 / 确认作废时 FedEx 给出明确结果才落 cancelled；FedEx 明确拒绝回到 active
- 写库报错后先重读记录：提交其实成功了不取消；读不到不取消（unknown save_unconfirmed）；写库失败统一 502 16095
- 带运单号的 unknown 可以重试取消；确认作废带运单号的记录须 FedEx 确认已取消 / 查无此运单（否则 409 16094）
- 取消 / 确认作废可带目标标识，不符 → 409 16093 {expected, actual}
- 有未结束的运单时，单证数据除 AWB 外变了不能重签（16076 DOCUMENT_DATA_CHANGED）；16076 一律带 details
- 单价小数位超过币种小数位 → 400 16063；存量超精度单价按币种量化，单价 × 数量 = 金额

FedEx 一律 mock（沿用 test_carrier_shipment 的 fedex fixture），不联网。
"""
from .helpers import *
from .test_customs import _create_dn, _customs, _export_dn_ready, _h, _issue, _pdf_streams, _ship, PACKAGES
from .test_carrier_shipment import (  # noqa: F401  fedex 是 fixture
    fedex, _cancel, _create, _dismiss, _get, _ready, _records, _ship_body, _states,
)

from datetime import datetime as _datetime, timedelta as _timedelta
from urllib.parse import urlparse

import pytest
import requests
from sqlalchemy.exc import OperationalError

from warehouse.carrier.integrations.fedex import client as fedex_client
from warehouse.dn.carrier_services import CarrierShipmentService
from warehouse.dn.customs_services import CustomsService
from warehouse.dn.models import DNCarrierShipment, DNCustoms, DNDocument

TRACKING = '794600000001'
CANCEL_PATH = '/ship/v1/shipments/cancel'
CANCEL_OK = (200, {'transactionId': 'tx-cancel-ok', 'output': {'cancelledShipment': True}})
CANCEL_500 = (500, {'transactionId': 'tx-cancel-500', 'errors': [{'code': 'INTERNAL.SERVER.ERROR', 'message': 'x'}]})
CANCEL_NOT_ALLOWED = (400, {'transactionId': 'tx-cancel-400', 'errors': [
    {'code': 'SHIPMENT.CANCEL.NOTALLOWED', 'message': 'Shipment already picked up.'}]})
CANCEL_NOT_FOUND = (404, {'transactionId': 'tx-cancel-404', 'errors': [
    {'code': 'TRACKING.TRACKINGNUMBER.NOTFOUND', 'message': 'Tracking number not found.'}]})


def _cancel_with(client, token, dn_id, body):
    return client.post(f'/dn/{dn_id}/carrier-shipment/cancel', json=body, headers=_h(token))


def _complete(client, token, task_id, **body):
    return client.put(f'/delivery/{task_id}/complete/', json=body, headers=_h(token))


def _task(client, task_id):
    with client.application.app_context():
        return db.session.get(DeliveryTask, task_id)


def _record(client, dn_id):
    with client.application.app_context():
        return DNCarrierShipment.query.filter_by(dn_id=dn_id).order_by(DNCarrierShipment.id.desc()).first()


def _active(client, token):
    dn_id, task_id = _ready(client, token)
    response = _create(client, token, dn_id)
    assert response.status_code == 201, response.get_json()
    return dn_id, task_id, response.get_json()['shipment']


_ORIGINAL_STORE_LABEL = CarrierShipmentService.__dict__['_store_label']


def _break_store_label(monkeypatch):
    monkeypatch.setattr(CarrierShipmentService, '_store_label',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full'))))


def _unknown_with_tracking(client, token, fedex, monkeypatch):
    """写库失败 + 补偿取消失败 → unknown（compensation_failed，带运单号）"""
    dn_id, task_id = _ready(client, token)
    _break_store_label(monkeypatch)
    fedex.set(CANCEL_PATH, CANCEL_500)
    response = _create(client, token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16095
    monkeypatch.setattr(CarrierShipmentService, '_store_label', _ORIGINAL_STORE_LABEL)
    assert _states(client, dn_id) == [('unknown', 'compensation_failed', TRACKING)]
    return dn_id, task_id


def _set_record(client, dn_id, **fields):
    with client.application.app_context():
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id).order_by(DNCarrierShipment.id.desc()).first()
        for key, value in fields.items():
            setattr(record, key, value)
        db.session.commit()
        return record.id


# ---------------------------------------------------------------------------
# B-63 取消三段式 / cancelling
# ---------------------------------------------------------------------------

def test_cancel_marks_cancelling_and_calls_fedex_outside_transaction(client, access_token, fedex, monkeypatch):
    dn_id, task_id, created = _active(client, access_token)
    seen = {}

    def spy(method, url, **kwargs):
        if urlparse(url).path == CANCEL_PATH:
            seen['in_transaction'] = db.session().in_transaction()
            seen['rows'] = [tuple(r) for r in db.session.execute(
                db.text("SELECT status, reason, cancelled_by FROM dn_carrier_shipments WHERE dn_id = :d"),
                {'d': dn_id})]
            db.session.rollback()
        return fedex(method, url, **kwargs)
    monkeypatch.setattr(fedex_client, '_send', spy)

    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    # 调 FedEx 时没有开着的事务（DN 行锁已随第 1 段提交释放），记录已提交为 cancelling
    assert seen['in_transaction'] is False
    assert len(seen['rows']) == 1 and seen['rows'][0][:2] == ('cancelling', 'cancel_in_progress')
    assert seen['rows'][0][2] is not None
    data = response.get_json()
    assert (data['shipment']['status'], data['shipment']['reason']) == ('cancelled', None)
    assert data['unresolved'] is None and data['can_cancel'] is False and data['can_create'] is True
    assert _task(client, task_id).tracking_number is None
    with client.application.app_context():
        assert db.session.get(DNDocument, created['label_document_id']).status == 'void'


@pytest.mark.parametrize('arrange, status_code, code, reason', [
    (lambda f: f.errors.__setitem__(CANCEL_PATH, requests.ReadTimeout('read timed out')), 504, 16074, 'timeout'),
    (lambda f: f.set(CANCEL_PATH, CANCEL_500), 502, 16073, 'server_error'),
    # B-89：200 但没有 cancelledShipment —— 不能当成功
    (lambda f: f.set(CANCEL_PATH, (200, {'transactionId': 'tx-c-empty', 'output': {}})), 502, 16073, 'bad_response'),
    (lambda f: f.set(CANCEL_PATH, (200, {'transactionId': 'tx-c-str', 'output': {'cancelledShipment': 'yes'}})),
     502, 16073, 'bad_response'),
])
def test_cancel_unclear_result_keeps_cancelling(client, access_token, fedex, arrange, status_code, code, reason):
    dn_id, task_id, created = _active(client, access_token)
    arrange(fedex)
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == status_code and response.get_json()['code'] == code
    details = response.get_json()['details']
    assert details['maybe_processed'] is True
    assert (details['unresolved']['status'], details['unresolved']['reason']) == ('cancelling', reason)
    assert 'retry the cancellation' in response.get_json()['message']
    assert _states(client, dn_id) == [('cancelling', reason, TRACKING)]
    # 面单与运单号都还在（取消没落定）
    assert _task(client, task_id).tracking_number == TRACKING
    with client.application.app_context():
        assert db.session.get(DNDocument, created['label_document_id']).status == 'issued'

    view = _get(client, access_token, dn_id)
    assert view['shipment']['id'] == view['unresolved']['id'] == created['id']
    assert view['shipment']['status'] == 'cancelling' and view['unresolved']['status'] == 'cancelling'
    assert view['can_cancel'] is True and view['can_dismiss'] is True and view['can_create'] is False
    assert any(b['code'] == 'SHIPMENT_EXISTS' for b in view['blockers'])


def test_cancelling_blocks_shipping_tracking_and_edits(client, access_token, fedex):
    dn_id, task_id, _created = _active(client, access_token)
    fedex.errors[CANCEL_PATH] = requests.ReadTimeout('read timed out')
    assert _cancel(client, access_token, dn_id).status_code == 504

    # 删发货任务 16078（任务还是 pending 时）
    response = client.delete(f'/delivery/{task_id}', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details']['status'] == 'cancelling'

    # 完成发货：不带运单号 / 带同一个号码都 16079（面单可能已作废）
    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    assert response.get_json()['details']['unresolved']['status'] == 'cancelling'
    response = _complete(client, access_token, task_id, tracking_number=TRACKING)
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    assert _task(client, task_id).status == 'in_progress'

    # 存运单号：同一号码 / 清空都 16079；修改任务传空值视为不改
    for value in (TRACKING, None):
        response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': value},
                              headers=_h(access_token))
        assert response.status_code == 409 and response.get_json()['code'] == 16079, value
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': None, 'remark': 'x'},
                          headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['tracking_number'] == TRACKING

    # 建单 16079；箱子 / 报关 16076（带 details）
    assert _create(client, access_token, dn_id).get_json()['code'] == 16079
    changed = [dict(PACKAGES[0], gross_weight_kg=4.5), PACKAGES[1]]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076
    assert response.get_json()['details']['carrier_shipment']['status'] == 'cancelling'
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076

    # 重试取消：FedEx 回「已取消」→ cancelled，之后可以照常发货（不带运单号）
    del fedex.errors[CANCEL_PATH]
    fedex.set(CANCEL_PATH, CANCEL_NOT_FOUND)
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    assert (response.get_json()['shipment']['status'], response.get_json()['shipment']['reason']) == (
        'cancelled', 'already_cancelled')
    assert _records(client, dn_id)[0]['cancel_transaction_id'] == 'tx-cancel-404'
    response = _complete(client, access_token, task_id)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] is None


@pytest.mark.parametrize('refusal', [
    CANCEL_NOT_ALLOWED,
    (200, {'transactionId': 'tx-c-no', 'output': {'cancelledShipment': False,
                                                  'alerts': [{'code': 'X', 'message': 'Not cancellable'}]}}),
])
def test_cancel_refused_returns_to_active(client, access_token, fedex, refusal):
    dn_id, task_id, _created = _active(client, access_token)
    fedex.set(CANCEL_PATH, refusal)
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    assert response.get_json()['details']['maybe_processed'] is False
    assert response.get_json()['details']['unresolved'] is None
    assert _states(client, dn_id) == [('active', None, TRACKING)]
    record = _records(client, dn_id)[0]
    assert record['error_message'].startswith('Cancel refused')
    with client.application.app_context():
        assert DNCarrierShipment.query.filter_by(dn_id=dn_id).one().cancelled_by is None
    view = _get(client, access_token, dn_id)
    assert view['can_cancel'] is True and view['unresolved'] is None
    # 运单仍有效：照常发货
    response = _ship(client, access_token, task_id)
    assert response.status_code == 200 and response.get_json()['tracking_number'] == TRACKING


def test_cancel_in_progress_and_stale(client, access_token, fedex):
    dn_id, task_id, _created = _active(client, access_token)
    _set_record(client, dn_id, status='cancelling', reason='cancel_in_progress', updated_at=_datetime.now())

    view = _get(client, access_token, dn_id)
    assert (view['unresolved']['status'], view['unresolved']['reason']) == ('cancelling', 'cancel_in_progress')
    assert view['can_cancel'] is False and view['can_dismiss'] is False
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    assert 'being cancelled' in response.get_json()['message']
    response = _dismiss(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16075
    assert 'cancellation is still in progress' in response.get_json()['message']
    assert fedex.of(CANCEL_PATH) == []
    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16079

    # 超过 stale_after_seconds（默认 10 分钟）没有落定：按结果不明（stale），可以重试
    _set_record(client, dn_id, updated_at=_datetime.now() - _timedelta(minutes=11))
    view = _get(client, access_token, dn_id)
    assert (view['unresolved']['status'], view['unresolved']['reason']) == ('cancelling', 'stale')
    assert view['shipment']['reason'] == 'stale'
    assert view['can_cancel'] is True and view['can_dismiss'] is True
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    assert _states(client, dn_id) == [('cancelled', None, TRACKING)]


def test_cancel_interrupted_can_be_retried(client, access_token, fedex):
    dn_id, task_id, _created = _active(client, access_token)
    fedex.errors[CANCEL_PATH] = SystemExit(1)
    with pytest.raises(SystemExit):
        _cancel(client, access_token, dn_id)
    assert _states(client, dn_id) == [('cancelling', 'interrupted', TRACKING)]
    assert _get(client, access_token, dn_id)['can_cancel'] is True

    del fedex.errors[CANCEL_PATH]
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    assert _states(client, dn_id) == [('cancelled', None, TRACKING)]
    assert _task(client, task_id).tracking_number is None


def test_dismiss_cancelling_record_asks_fedex(client, access_token, fedex):
    dn_id, task_id, created = _active(client, access_token)
    fedex.errors[CANCEL_PATH] = requests.ReadTimeout('read timed out')
    assert _cancel(client, access_token, dn_id).status_code == 504
    del fedex.errors[CANCEL_PATH]
    record_id = created['id']

    # FedEx 仍说不清 → 409 16094，仍是 cancelling
    fedex.set(CANCEL_PATH, (200, {'transactionId': 'tx-c-empty', 'output': {}}))
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': record_id})
    assert response.status_code == 409 and response.get_json()['code'] == 16094
    details = response.get_json()['details']
    assert details['result'] == 'unclear' and details['maybe_processed'] is True
    assert details['unresolved']['status'] == 'cancelling' and details['tracking_number'] == TRACKING
    assert _states(client, dn_id) == [('cancelling', 'bad_response', TRACKING)]

    # FedEx 确认取消 → 有效运单落 cancelled（面单作废、运单号清掉），不是 dismissed
    fedex.set(CANCEL_PATH, CANCEL_OK)
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': record_id})
    assert response.status_code == 200, response.get_json()
    assert _states(client, dn_id) == [('cancelled', None, TRACKING)]
    assert _task(client, task_id).tracking_number is None
    with client.application.app_context():
        assert db.session.get(DNDocument, created['label_document_id']).status == 'void'


def test_dismiss_cancelling_refused_returns_to_active(client, access_token, fedex):
    dn_id, task_id, _created = _active(client, access_token)
    fedex.set(CANCEL_PATH, CANCEL_500)
    assert _cancel(client, access_token, dn_id).status_code == 502
    fedex.set(CANCEL_PATH, CANCEL_NOT_ALLOWED)
    response = _dismiss(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16094
    assert response.get_json()['details']['result'] == 'refused'
    assert response.get_json()['details']['http_status'] == 400
    assert _states(client, dn_id) == [('active', None, TRACKING)]


# ---------------------------------------------------------------------------
# B-64 / B-87 建单写库失败
# ---------------------------------------------------------------------------

def test_commit_succeeded_but_error_raised_keeps_shipment(client, access_token, fedex, monkeypatch):
    dn_id, task_id = _ready(client, access_token)
    original = CarrierShipmentService._save

    def save_then_fail(*args, **kwargs):
        original(*args, **kwargs)          # 已提交
        raise OperationalError('COMMIT', {}, Exception('server closed the connection unexpectedly'))
    monkeypatch.setattr(CarrierShipmentService, '_save', staticmethod(save_then_fail))

    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['shipment']['status'] == 'active'
    assert fedex.of(CANCEL_PATH) == []                       # 没有把有效运单取消掉
    assert _states(client, dn_id) == [('active', None, TRACKING)]
    assert _task(client, task_id).tracking_number == TRACKING


def test_unverifiable_save_is_not_compensated(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)
    _break_store_label(monkeypatch)
    monkeypatch.setattr(CarrierShipmentService, '_saved_despite_error', staticmethod(lambda *a: None))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16095
    details = response.get_json()['details']
    assert details['compensation']['attempted'] is False and details['compensation']['cancelled'] is False
    assert (details['unresolved']['status'], details['unresolved']['reason']) == ('unknown', 'save_unconfirmed')
    assert details['unresolved']['tracking_number'] == TRACKING and details['maybe_processed'] is True
    assert fedex.of(CANCEL_PATH) == []
    assert _states(client, dn_id) == [('unknown', 'save_unconfirmed', TRACKING)]


def test_business_error_while_saving_is_wrapped(client, access_token, fedex, monkeypatch):
    """B-87：建单期间 DN 被发货（16065），补偿取消又失败 → 502 16095，带 unresolved（运单号）与原业务码"""
    dn_id, _task_id = _ready(client, access_token)
    fedex.set(CANCEL_PATH, CANCEL_500)

    def spy(method, url, **kwargs):
        if urlparse(url).path == '/ship/v1/shipments':
            db.session.execute(db.text("UPDATE dn SET status='delivered' WHERE id=:i"), {'i': dn_id})
            db.session.commit()
        return fedex(method, url, **kwargs)
    monkeypatch.setattr(fedex_client, '_send', spy)
    response = _create(client, access_token, dn_id)
    body = response.get_json()
    assert response.status_code == 502 and body['code'] == 16095, body
    assert body['details']['error']['code'] == 16065
    assert body['details']['unresolved']['tracking_number'] == TRACKING
    assert body['details']['compensation']['attempted'] is True
    assert body['details']['compensation']['cancelled'] is False
    assert body['details']['compensation']['cancel_transaction_id'] == 'tx-cancel-500'
    assert TRACKING in body['message']
    assert _states(client, dn_id) == [('unknown', 'compensation_failed', TRACKING)]


def test_document_change_during_create_is_compensated(client, access_token, fedex, monkeypatch):
    """建单期间商品原产国变了：写库时重签 CI 与已提交的不一致（16076）→ 补偿取消 → 502 16095"""
    dn_id, _task_id = _ready(client, access_token)

    def spy(method, url, **kwargs):
        if urlparse(url).path == '/ship/v1/shipments':
            goods = Goods.query.filter_by(code='G001').first()
            goods.origin_country = 'JP'
            db.session.commit()
        return fedex(method, url, **kwargs)
    monkeypatch.setattr(fedex_client, '_send', spy)
    response = _create(client, access_token, dn_id)
    body = response.get_json()
    assert response.status_code == 502 and body['code'] == 16095, body
    assert body['details']['error']['code'] == 16076
    assert body['details']['unresolved'] is None and body['details']['compensation']['cancelled'] is True
    assert _states(client, dn_id) == [('cancelled', 'compensated', TRACKING)]


# ---------------------------------------------------------------------------
# B-65 带运单号的 unknown：重试取消 / 确认作废先问 FedEx
# ---------------------------------------------------------------------------

def test_unknown_with_tracking_can_be_cancelled(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _unknown_with_tracking(client, access_token, fedex, monkeypatch)
    view = _get(client, access_token, dn_id)
    assert view['can_cancel'] is True and view['can_dismiss'] is True and view['shipment'] is None

    calls = len(fedex.of(CANCEL_PATH))
    fedex.set(CANCEL_PATH, CANCEL_OK)
    response = _cancel_with(client, access_token, dn_id, {'tracking_number': TRACKING})
    assert response.status_code == 200, response.get_json()
    cancels = fedex.of(CANCEL_PATH)[calls:]
    assert len(cancels) == 1 and cancels[0]['json']['trackingNumber'] == TRACKING
    assert cancels[0]['json']['senderCountryCode'] == 'JP'
    data = response.get_json()
    assert data['unresolved'] is None and data['can_create'] is True
    assert (data['shipment']['status'], data['shipment']['reason']) == ('cancelled', None)
    assert data['shipment']['cancelled_by'] is not None
    record = _records(client, dn_id)[0]
    assert record['cancel_transaction_id'] == 'tx-cancel-ok'

    # 已取消的号码不能再存；可以重新建单
    fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000777')))
    assert _create(client, access_token, dn_id).status_code == 201


def test_unknown_cancel_failure_keeps_unknown(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _unknown_with_tracking(client, access_token, fedex, monkeypatch)
    fedex.set(CANCEL_PATH, CANCEL_NOT_ALLOWED)
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    assert response.get_json()['details']['unresolved']['status'] == 'unknown'
    assert _states(client, dn_id) == [('unknown', 'compensation_failed', TRACKING)]
    assert _records(client, dn_id)[0]['error_message'].startswith('Cancel refused')


@pytest.mark.parametrize('arrange, result', [
    (lambda f: f.set(CANCEL_PATH, CANCEL_NOT_ALLOWED), 'refused'),
    (lambda f: f.set(CANCEL_PATH, CANCEL_500), 'unclear'),
    (lambda f: f.errors.__setitem__(CANCEL_PATH, requests.ReadTimeout('read timed out')), 'unclear'),
])
def test_dismiss_with_tracking_requires_fedex_confirmation(client, access_token, fedex, monkeypatch, arrange,
                                                           result):
    dn_id, _task_id = _unknown_with_tracking(client, access_token, fedex, monkeypatch)
    arrange(fedex)
    response = _dismiss(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16094
    details = response.get_json()['details']
    assert details['result'] == result and details['tracking_number'] == TRACKING
    assert details['unresolved']['status'] == 'unknown'
    assert _states(client, dn_id) == [('unknown', 'compensation_failed', TRACKING)]
    # 不能再建第二张
    assert _create(client, access_token, dn_id).get_json()['code'] == 16079


@pytest.mark.parametrize('confirmation', [CANCEL_OK, CANCEL_NOT_FOUND])
def test_dismiss_with_tracking_after_fedex_confirms(client, access_token, fedex, monkeypatch, confirmation):
    dn_id, task_id = _unknown_with_tracking(client, access_token, fedex, monkeypatch)
    fedex.set(CANCEL_PATH, confirmation)
    record_id = _records(client, dn_id)[0]['id']
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': record_id})
    assert response.status_code == 200, response.get_json()
    record = _records(client, dn_id)[0]
    assert (record['status'], record['reason']) == ('dismissed', 'compensation_failed')
    assert record['dismissed_by'] is not None and record['cancel_transaction_id'] == confirmation[1]['transactionId']
    # 已确认作废的号码不能再用（16078）；可以重新建单
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': TRACKING},
                          headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000777')))
    assert _create(client, access_token, dn_id).status_code == 201


def test_dismiss_without_tracking_does_not_call_fedex(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    unresolved = _create(client, access_token, dn_id).get_json()['details']['unresolved']
    assert unresolved['tracking_number'] is None
    view = _get(client, access_token, dn_id)
    assert view['can_cancel'] is False and view['can_dismiss'] is True
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16075
    assert response.get_json()['details']['unresolved']['id'] == unresolved['id']
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': unresolved['id']})
    assert response.status_code == 200, response.get_json()
    assert fedex.of(CANCEL_PATH) == []
    assert _states(client, dn_id) == [('dismissed', 'timeout', None)]


def test_complete_blocked_while_create_result_unclear(client, access_token, fedex):
    """结果不明（unknown）时不带运单号也不能完成发货（16079）；确认作废后照常"""
    dn_id, task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    assert _create(client, access_token, dn_id).status_code == 504
    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    assert response.get_json()['details']['unresolved']['status'] == 'unknown'
    assert _dismiss(client, access_token, dn_id).status_code == 200
    response = _complete(client, access_token, task_id)
    assert response.status_code == 200, response.get_json()


# ---------------------------------------------------------------------------
# W-31 目标标识
# ---------------------------------------------------------------------------

def test_cancel_target_tracking_number(client, access_token, fedex):
    dn_id, _task_id, created = _active(client, access_token)
    view = _get(client, access_token, dn_id)
    assert view['shipment']['id'] == created['id'] and view['can_cancel'] is True

    response = _cancel_with(client, access_token, dn_id, {'tracking_number': '794600000009'})
    assert response.status_code == 409 and response.get_json()['code'] == 16093
    assert response.get_json()['details'] == {'expected': '794600000009', 'actual': TRACKING}
    for bad in (12.5, True, ['x'], {'a': 1}):
        response = _cancel_with(client, access_token, dn_id, {'tracking_number': bad})
        assert response.status_code == 400 and response.get_json()['field'] == 'tracking_number', bad
    assert fedex.of(CANCEL_PATH) == []
    assert _states(client, dn_id) == [('active', None, TRACKING)]

    # 分组写法也算同一个；不带 / null / 空串不校验
    response = _cancel_with(client, access_token, dn_id, {'tracking_number': '7946 0000-0001'})
    assert response.status_code == 200, response.get_json()
    # 已取消后再带号码：没有可取消的 → 16075
    response = _cancel_with(client, access_token, dn_id, {'tracking_number': TRACKING})
    assert response.status_code == 409 and response.get_json()['code'] == 16075


@pytest.mark.parametrize('body', [{}, {'tracking_number': None}, {'tracking_number': ''}, {'other': 1}])
def test_cancel_without_target_compatible(client, access_token, fedex, body):
    dn_id, _task_id, _created = _active(client, access_token)
    response = _cancel_with(client, access_token, dn_id, body)
    assert response.status_code == 200, response.get_json()


def test_dismiss_target_id(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    unresolved = _create(client, access_token, dn_id).get_json()['details']['unresolved']
    assert _get(client, access_token, dn_id)['unresolved']['id'] == unresolved['id']

    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': unresolved['id'] + 1})
    assert response.status_code == 409 and response.get_json()['code'] == 16093
    assert response.get_json()['details'] == {'expected': unresolved['id'] + 1, 'actual': unresolved['id']}
    for bad in ('abc', 0, -1, 1.5, True):
        response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': bad})
        assert response.status_code == 400 and response.get_json()['field'] == 'unresolved_id', bad
    assert _states(client, dn_id) == [('unknown', 'timeout', None)]
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': str(unresolved['id'])})
    assert response.status_code == 200, response.get_json()

    # 已没有结果不明的记录：带 id 也只是 16075
    response = _dismiss(client, access_token, dn_id, {'confirm': True, 'unresolved_id': unresolved['id']})
    assert response.status_code == 409 and response.get_json()['code'] == 16075


# ---------------------------------------------------------------------------
# B-66 / W-35 单证与运单一致、16076 details
# ---------------------------------------------------------------------------

def _assert_16076(response, reason, status, record_id, tracking):
    assert response.status_code == 409 and response.get_json()['code'] == 16076, response.get_json()
    details = response.get_json()['details']
    assert details['reason'] == reason and details['status'] == status and details['carrier'] == 'fedex'
    assert details['carrier_shipment'] == {'id': record_id, 'status': status, 'tracking_number': tracking}


def test_reissue_with_changed_master_data_blocked_while_shipment_active(client, access_token, fedex):
    dn_id, _task_id, created = _active(client, access_token)
    # 数据没变：照常返回现有版本
    assert _issue(client, access_token, dn_id).status_code == 200

    with client.application.app_context():
        Goods.query.filter_by(code='G001').first().origin_country = 'JP'
        db.session.commit()
    _assert_16076(_issue(client, access_token, dn_id), 'DOCUMENT_DATA_CHANGED', 'active', created['id'], TRACKING)
    with client.application.app_context():
        company = get_company()
        Goods.query.filter_by(code='G001').first().origin_country = 'CN'
        company.export_signatory_name = 'Someone Else'
        db.session.commit()
    _assert_16076(_issue(client, access_token, dn_id), 'DOCUMENT_DATA_CHANGED', 'active', created['id'], TRACKING)

    # 取消运单后放开：单证还是建单时那份（数据已变 → 过期），重新生成即可
    assert _cancel(client, access_token, dn_id).status_code == 200
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['documents_outdated'] is True
    assert _issue(client, access_token, dn_id).status_code == 201
    assert client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()['documents_outdated'] is False


def test_reissue_blocked_while_result_unclear(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    unresolved = _create(client, access_token, dn_id).get_json()['details']['unresolved']
    with client.application.app_context():
        Goods.query.filter_by(code='G002').first().origin_country = 'TH'
        db.session.commit()
    _assert_16076(_issue(client, access_token, dn_id), 'DOCUMENT_DATA_CHANGED', 'unknown', unresolved['id'], None)


def test_16076_details_for_packages_and_customs(client, access_token, fedex):
    dn_id, _task_id, created = _active(client, access_token)
    changed = [dict(PACKAGES[0], gross_weight_kg=4.5), PACKAGES[1]]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    _assert_16076(response, 'CARRIER_SHIPMENT_OPEN', 'active', created['id'], TRACKING)
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    _assert_16076(response, 'CARRIER_SHIPMENT_OPEN', 'active', created['id'], TRACKING)


# ---------------------------------------------------------------------------
# B-68 单价精度
# ---------------------------------------------------------------------------

def _customs_with(currency, *unit_values):
    body = _customs(currency=currency)
    for line, value in zip(body['lines'], unit_values):
        line['unit_value'] = value
    return body


@pytest.mark.parametrize('currency, value, places', [
    ('JPY', 99.5, 0), ('JPY', '1200.4', 0), ('jpy', 0.4, 0), ('USD', 12.345, 2), ('EUR', '0.001', 2),
    (None, 1.005, 2),
])
def test_unit_value_precision_rejected(client, access_token, currency, value, places):
    _export_dn_ready(client, access_token)        # 准备公司 / 商品 / 库存
    response = _create_dn(client, access_token, customs=_customs_with(currency, value),
                          order_number='ORD-PRECISION-1')
    assert response.status_code == 400 and response.get_json()['code'] == 16063, response.get_json()
    details = response.get_json()['details']
    assert details['field'] == 'customs.lines[0].unit_value' and details['max_decimal_places'] == places
    assert details['currency'] == (currency.upper() if currency else None)


@pytest.mark.parametrize('currency, value', [
    ('JPY', 1200), ('JPY', 1200.0), ('JPY', '1,200.00'), ('USD', 12.34), ('USD', '12.30'), ('EUR', 0.5),
])
def test_unit_value_precision_accepted(client, access_token, currency, value):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    response = client.put(f'/dn/{dn_id}/customs', json=_customs_with(currency, value), headers=_h(access_token))
    assert response.status_code == 200, response.get_json()


def test_put_customs_rejects_over_precision(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    response = client.put(f'/dn/{dn_id}/customs', json=_customs_with('JPY', 1200, 21000.5), headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 16063
    assert response.get_json()['field'] == 'customs.lines[1].unit_value'


def _legacy_unit_values(client, dn_id, values: dict):
    """存量快照：直接写库（绕过解析校验）"""
    with client.application.app_context():
        customs = DNCustoms.query.filter_by(dn_id=dn_id).one()
        lines = [dict(line) for line in customs.lines]
        for line in lines:
            if line['goods_code'] in values:
                line['unit_value'] = values[line['goods_code']]
        customs.lines = lines
        db.session.commit()


def test_legacy_over_precision_quantized(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)          # JPY；G001 × 3、G002 × 2 已打包
    _legacy_unit_values(client, dn_id, {'G001': 99.5, 'G002': 0.4})
    response = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token))
    assert response.status_code == 200
    view = response.get_json()
    lines = {line['goods_code']: line for line in view['lines']}
    assert (lines['G001']['unit_value'], lines['G001']['amount']) == (100, 300)       # 单价 × 数量 = 金额
    assert (lines['G002']['unit_value'], lines['G002']['amount']) == (0, 0)
    problems = {(p['code'], p.get('goods_code')) for p in view['problems']}
    assert ('UNIT_VALUE_MISSING', 'G002') in problems                                # 量化后 ≤ 0
    assert view['totals']['goods_value'] == 300
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16068

    _legacy_unit_values(client, dn_id, {'G002': 20999.6})
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        data = CustomsService._document_data(dn, CustomsService.build_view(dn))
    items = {item['goods_code']: item for item in data['items']}
    assert (items['G001']['unit_value'], items['G001']['amount']) == ('100', '300')
    assert (items['G002']['unit_value'], items['G002']['amount']) == ('21,000', '42,000')
    ci_id = [d['id'] for d in response.get_json()['documents'] if d['doc_type'] == 'commercial_invoice'][0]
    pdf = client.get(f'/dn/{dn_id}/customs-documents/{ci_id}/file', headers=_h(access_token)).data
    assert b'99.5' not in _pdf_streams(pdf)

    # FedEx 的 unitPrice / customsValue 用量化后的值
    assert _create(client, access_token, dn_id).status_code == 201
    commodities = fedex.ship_request()['requestedShipment']['customsClearanceDetail']['commodities']
    prices = sorted((c['unitPrice']['amount'], c['customsValue']['amount']) for c in commodities)
    assert prices == [(100, 300), (21000, 42000)]


def test_legacy_garbage_unit_value_does_not_500(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    _legacy_unit_values(client, dn_id, {'G001': 'abc', 'G002': 1e30})
    response = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    codes = {(p['code'], p.get('goods_code')) for p in response.get_json()['problems']}
    assert {('UNIT_VALUE_MISSING', 'G001'), ('UNIT_VALUE_MISSING', 'G002')} <= codes
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16068
