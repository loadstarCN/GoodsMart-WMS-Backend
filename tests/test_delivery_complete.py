"""完成发货（PUT /delivery/<id>/complete/）的运单号 / 单证契约

- tracking_number 为空串 / null / 不传 → 不改已存值（国内件也一样）
- 海外件按写入前的已存状态检查单证：缺 → 409 16069 {missing_documents, outdated: false}；
  过期 → 409 16069 {missing_documents: [], outdated: true}
- 当前 CI 已印 AWB、请求带了不同的运单号 → 409 16080 {document_tracking_number, tracking_number}；
  CI 没印 AWB 时照常写入
- 有有效自动运单时承运商不一致 → 409 16078（details 带 carrier_id）
- 检查前先锁 DN 行
"""
from .helpers import *
from .test_customs import (
    PACKAGES, _create_dn, _export_dn_ready, _force_packed, _h, _issue, _prepare, _ship,
)
from .test_carrier_shipment import _create, _ready, fedex  # noqa: F401  fedex 是 fixture

import pytest

from warehouse.dn.customs_services import CustomsService
from warehouse.dn.models import DNDocument


def _complete(client, token, task_id, body):
    return client.put(f'/delivery/{task_id}/complete/', json=body, headers=_h(token))


def _save_tracking(client, token, task_id, tracking):
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': tracking}, headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response


def _task(client, task_id):
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        return task.status, task.tracking_number, task.carrier_id


# ---------------------------------------------------------------------------
# 空运单号不覆盖已存值
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('body', [{'tracking_number': ''}, {'tracking_number': '   '},
                                  {'tracking_number': None}, {}])
def test_domestic_empty_tracking_keeps_saved(client, access_token, body):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DOM-KEEP').get_json()['id']
    task_id = _force_packed(client, dn_id)
    _save_tracking(client, access_token, task_id, 'DOM-SAVED-1')

    response = _ship(client, access_token, task_id, **body)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'DOM-SAVED-1'
    assert response.get_json()['status'] == 'completed'


def test_domestic_non_empty_tracking_still_overwrites(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DOM-NEW').get_json()['id']
    task_id = _force_packed(client, dn_id)
    _save_tracking(client, access_token, task_id, 'DOM-SAVED-1')
    response = _ship(client, access_token, task_id, tracking_number=' DOM-NEW-2 ')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'DOM-NEW-2'


def test_tracking_number_type_and_length_checked(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DOM-BAD').get_json()['id']
    task_id = _force_packed(client, dn_id)
    _ship_ready = client.put(f'/delivery/{task_id}/process/', headers=_h(access_token))
    assert _ship_ready.status_code == 200
    for bad in ({'x': 1}, ['a'], 'X' * 101):
        response = _complete(client, access_token, task_id, {'tracking_number': bad})
        assert response.status_code == 400 and response.get_json()['field'] == 'tracking_number', bad
    response = _complete(client, access_token, task_id, {'tracking_number': 794600000001})   # 数字照收
    assert response.status_code == 200 and response.get_json()['tracking_number'] == '794600000001'


def test_export_empty_tracking_keeps_awb_printed_on_ci(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    _save_tracking(client, access_token, task_id, 'AWB-SAVED-1')
    assert _issue(client, access_token, dn_id).status_code == 201
    response = _ship(client, access_token, task_id, tracking_number='')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'AWB-SAVED-1'


# ---------------------------------------------------------------------------
# 16069：单证缺失 / 过期（按写入前的状态）
# ---------------------------------------------------------------------------

def test_missing_documents_details(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16069
    assert response.get_json()['details'] == {
        'missing_documents': ['commercial_invoice', 'packing_list'], 'outdated': False}


def test_outdated_documents_block_shipping(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _issue(client, access_token, dn_id).status_code == 201
    # 出单证后才存运单号：单证上没有 AWB → 过期
    _save_tracking(client, access_token, task_id, '7946 0000 0009')
    assert client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()['documents_outdated'] is True

    response = _ship(client, access_token, task_id)
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 16069
    assert response.get_json()['details'] == {'missing_documents': [], 'outdated': True}
    assert _task(client, task_id)[0] == 'in_progress'
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'packed'

    # 重出单证（v2 印 AWB）后照常发货
    assert _issue(client, access_token, dn_id).get_json()['version'] == 2
    response = _complete(client, access_token, task_id, {})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == '7946 0000 0009'


def test_exporter_profile_change_makes_documents_outdated(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _issue(client, access_token, dn_id).status_code == 201
    with client.application.app_context():
        get_company().export_signatory_name = 'Jiro Example'
        db.session.commit()
    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['details']['outdated'] is True


def test_outdated_check_uses_state_before_writing_request_tracking(client, access_token):
    """CI 没印 AWB 时带运单号完成发货：写入会让单证过期，但检查按写入前的状态 → 放行"""
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _issue(client, access_token, dn_id).status_code == 201
    response = _ship(client, access_token, task_id, tracking_number='NEW-AWB-1')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'NEW-AWB-1'
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'delivered'


# ---------------------------------------------------------------------------
# 16080：请求的运单号与 CI 上印的 AWB 不一致
# ---------------------------------------------------------------------------

def test_tracking_different_from_ci_awb_rejected(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    _save_tracking(client, access_token, task_id, '7946 0000 0001')
    assert _issue(client, access_token, dn_id).status_code == 201

    response = _ship(client, access_token, task_id, tracking_number='7946 0000 0002')
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['code'] == 16080
    assert response.get_json()['details'] == {
        'document_tracking_number': '7946 0000 0001', 'tracking_number': '7946 0000 0002'}
    assert _task(client, task_id)[:2] == ('in_progress', '7946 0000 0001')

    # 同一号码（空白不同）照常，已存值保持与 CI 一字不差
    response = _complete(client, access_token, task_id, {'tracking_number': '794600000001'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == '7946 0000 0001'


def test_changing_tracking_requires_saving_and_reissuing(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    _save_tracking(client, access_token, task_id, 'AWB-OLD')
    assert _issue(client, access_token, dn_id).status_code == 201
    assert _ship(client, access_token, task_id, tracking_number='AWB-NEW').get_json()['code'] == 16080

    # 按提示：先保存新运单号 → 单证过期 16069 → 重出单证 → 发货
    _save_tracking(client, access_token, task_id, 'AWB-NEW')
    assert _complete(client, access_token, task_id, {'tracking_number': 'AWB-NEW'}).get_json()['code'] == 16069
    assert _issue(client, access_token, dn_id).get_json()['version'] == 2
    response = _complete(client, access_token, task_id, {'tracking_number': 'AWB-NEW'})
    assert response.status_code == 200 and response.get_json()['tracking_number'] == 'AWB-NEW'


# ---------------------------------------------------------------------------
# 先锁 DN 行
# ---------------------------------------------------------------------------

def test_complete_locks_dn_before_checking(client, access_token, monkeypatch):
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _issue(client, access_token, dn_id).status_code == 201
    calls = []
    original = CustomsService._lock

    def spy(dn):
        calls.append(('lock', dn.id))
        return original(dn)

    original_check = CustomsService.assert_ready_to_ship

    def check_spy(dn):
        calls.append(('check', dn.id))
        return original_check(dn)

    monkeypatch.setattr(CustomsService, '_lock', staticmethod(spy))
    monkeypatch.setattr(CustomsService, 'assert_ready_to_ship', staticmethod(check_spy))
    response = _ship(client, access_token, task_id)
    assert response.status_code == 200, response.get_json()
    assert calls[:2] == [('lock', dn_id), ('check', dn_id)]


def test_documents_voided_concurrently_are_seen_after_lock(client, access_token, monkeypatch):
    """锁 DN 后重新读取：拿锁前单证被并发作废（别的事务改了箱子）→ 仍然 16069"""
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _issue(client, access_token, dn_id).status_code == 201
    assert client.put(f'/delivery/{task_id}/process/', headers=_h(access_token)).status_code == 200
    original = CustomsService._lock

    def lock_after_concurrent_void(dn):
        # 模拟等锁期间另一事务已提交：单证被作废
        DNDocument.query.filter_by(dn_id=dn.id).update({'status': 'void', 'void_reason': 'packages_changed'})
        return original(dn)

    monkeypatch.setattr(CustomsService, '_lock', staticmethod(lock_after_concurrent_void))
    response = _complete(client, access_token, task_id, {})
    assert response.status_code == 409 and response.get_json()['code'] == 16069
    assert response.get_json()['details']['missing_documents'] == ['commercial_invoice', 'packing_list']


# ---------------------------------------------------------------------------
# 16078：有有效自动运单时承运商不一致
# ---------------------------------------------------------------------------

def _other_carrier(client, code='yamato'):
    with client.application.app_context():
        carrier = Carrier(name=f'Other {code}', code=code, company_id=get_company().id,
                          created_by=get_admin_user().id)
        db.session.add(carrier)
        db.session.commit()
        return carrier.id


def test_carrier_must_match_active_shipment(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    other_id = _other_carrier(client)
    fedex_id = _task(client, task_id)[2]

    # 修改发货任务 / 完成发货：换成别的承运商 → 409 16078，details 带 carrier_id
    response = client.put(f'/delivery/{task_id}', json={'carrier_id': other_id}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details'] == {
        'tracking_number': '794600000001', 'carrier': 'fedex', 'carrier_id': other_id}
    response = _ship(client, access_token, task_id, carrier_id=other_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details']['carrier_id'] == other_id
    assert _task(client, task_id) == ('in_progress', '794600000001', fedex_id)

    # null 视为不改；同一承运商照常
    response = _complete(client, access_token, task_id, {'carrier_id': None})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['carrier_id'] == fedex_id


def test_same_carrier_code_accepted(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    other_fedex = _other_carrier(client, code='FedEx')      # 另一条 code 也是 fedex 的承运商记录
    response = _ship(client, access_token, task_id, carrier_id=other_fedex)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['carrier_id'] == other_fedex


def test_carrier_change_allowed_without_shipment(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DOM-CARRIER').get_json()['id']
    task_id = _force_packed(client, dn_id)
    other_id = _other_carrier(client)
    response = _ship(client, access_token, task_id, carrier_id=other_id)
    assert response.status_code == 200 and response.get_json()['carrier_id'] == other_id
