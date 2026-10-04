"""发货任务守卫（审计 B-57 / B-58 / B-67）

- 发货任务只由打包完成自动生成（手工新建接口 POST /delivery/ 已去掉 → 405）；一张 DN 同时只允许一条
  未完成（pending / in_progress）的有效发货任务：自动生成时已有（修复前手工建的存量任务）→ 复用那条
- 海外 DN 只能对当前发货任务（最新的有效任务）完成发货 / 存运单号 / 修改 → 409 16091
- 完成发货后任务上的运单号（请求值，没带则已存值）与 CI 上印的 AWB 不同 → 409 16080
- dn.delivered 的 tracking_number 取被完成的那条任务的运单号
- 有效自动运单期间运单号统一存运单上的标准写法；取消时按规范化后的号码清空任务上的运单号
- 本 DN 已取消（含补偿取消）的自动运单号不能再用 → 409 16078（details.status = cancelled）
- 有效自动运单期间保存运单号也不能换承运商 → 409 16078；海外件完成发货的承运商与 CI 上印的不同 → 409 16092

FedEx 一律 mock（沿用 test_carrier_shipment 的 fedex fixture），不联网。
"""
from .helpers import *
from .test_customs import (
    _create_dn, _export_dn_ready, _force_packed, _h, _issue, _pdf_streams, _prepare, _ship, _webhook_key,
)
from .test_carrier_shipment import _cancel, _create, _ready, _records, fedex  # noqa: F401  fedex 是 fixture
from .test_delivery_complete import _other_carrier

from system.webhook.models import WebhookEvent
from warehouse.dn.carrier_services import CarrierShipmentService
from warehouse.dn.customs_services import CustomsService


def _domestic_packed(client, token, order_number):
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    return dn_id, _force_packed(client, dn_id)


def _legacy_task(client, dn_id, tracking=None):
    """模拟修复前留下的存量数据：同一 DN 的第二条有效发货任务（直接写库，绕过 create_task 的校验）"""
    with client.application.app_context():
        first = DeliveryTask.query.filter_by(dn_id=dn_id).order_by(DeliveryTask.id).first()
        task = DeliveryTask(
            dn_id=dn_id, recipient_id=first.recipient_id, shipping_address=first.shipping_address,
            expected_shipping_date=first.expected_shipping_date, carrier_id=first.carrier_id,
            tracking_number=tracking, status='pending', created_by=first.created_by,
        )
        db.session.add(task)
        db.session.commit()
        return task.id


def _task(client, task_id):
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        return task.status, task.tracking_number, task.carrier_id


def _task_ids(client, dn_id):
    with client.application.app_context():
        return [t.id for t in DeliveryTask.query.filter_by(dn_id=dn_id).order_by(DeliveryTask.id)]


def _save_tracking(client, token, task_id, body):
    return client.put(f'/delivery/{task_id}/tracking', json=body, headers=_h(token))


def _complete(client, token, task_id, body):
    return client.put(f'/delivery/{task_id}/complete/', json=body, headers=_h(token))


# ---------------------------------------------------------------------------
# B-57：一张 DN 只允许一条未完成的有效发货任务
# ---------------------------------------------------------------------------

def test_manual_create_endpoint_removed(client, access_token):
    dn_id, task_id = _domestic_packed(client, access_token, 'ORD-GUARD-ONE')
    response = client.post('/delivery/', json={'dn_id': dn_id}, headers=_h(access_token))
    assert response.status_code == 405
    assert _task_ids(client, dn_id) == [task_id]


def test_packing_reuses_legacy_manual_task(client, access_token):
    """打包完成前已有（修复前手工建的）发货任务：自动生成时复用它（不报错，打包完成不被卡住），DN 仍只有一条任务"""
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-GUARD-MANUAL').get_json()['id']
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        manual = DeliveryTask(dn_id=dn_id, recipient_id=dn.recipient_id, shipping_address=dn.shipping_address,
                              expected_shipping_date=dn.expected_shipping_date, status='pending',
                              remark='manual', created_by=dn.created_by)
        db.session.add(manual)
        db.session.commit()
        manual_id = manual.id

    task_id = _force_packed(client, dn_id)     # 内部调 create_delivery_task_from_dn（打包完成的同一入口）
    assert task_id == manual_id
    assert _task_ids(client, dn_id) == [manual_id]
    with client.application.app_context():
        assert db.session.get(DeliveryTask, manual_id).remark == 'manual'


# ---------------------------------------------------------------------------
# B-57：海外 DN 只认当前发货任务
# ---------------------------------------------------------------------------

def test_export_only_current_task_can_ship(client, access_token):
    dn_id, t1 = _export_dn_ready(client, access_token)
    t2 = _legacy_task(client, dn_id)            # 存量：较新的 T2 是当前任务（单证 / 自动运单都看它）
    assert client.put(f'/delivery/{t1}/process/', headers=_h(access_token)).status_code == 200
    expected = {'delivery_task_id': t2, 'current_status': 'pending'}

    for response in (
        _complete(client, access_token, t1, {}),
        _complete(client, access_token, t1, {'tracking_number': 'AAA-999'}),
        _save_tracking(client, access_token, t1, {'tracking_number': 'AAA-999'}),
        client.put(f'/delivery/{t1}', json={'remark': 'x'}, headers=_h(access_token)),
    ):
        assert response.status_code == 409 and response.get_json()['code'] == 16091, response.get_json()
        assert response.get_json()['details'] == expected
    assert _task(client, t1)[:2] == ('in_progress', None)

    # 当前任务照常：存运单号 → 出单证 → 完成发货
    assert _save_tracking(client, access_token, t2, {'tracking_number': 'BBB-111'}).status_code == 200
    assert _issue(client, access_token, dn_id).status_code == 201
    response = _ship(client, access_token, t2)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'BBB-111'
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'delivered'


def test_document_awb_compared_with_saved_tracking(client, access_token, monkeypatch):
    """完成后任务上的运单号（没带就是已存值）与 CI 上印的 AWB 不同 → 16080；带了与 CI 一致的号码则按 CI 的写法写入"""
    dn_id, task_id = _export_dn_ready(client, access_token)
    assert _save_tracking(client, access_token, task_id, {'tracking_number': 'AWB-SAVED'}).status_code == 200
    assert _issue(client, access_token, dn_id).status_code == 201

    # 模拟 CI 上印的是别的号码（修复前：单证按同一 DN 的另一条任务出的）
    original = CustomsService.assert_ready_to_ship
    monkeypatch.setattr(CustomsService, 'assert_ready_to_ship',
                        staticmethod(lambda dn: {**original(dn), 'awb': 'AWB-ON-CI'}))

    response = _ship(client, access_token, task_id)                       # 不带运单号：按已存值比
    assert response.status_code == 409 and response.get_json()['code'] == 16080
    assert response.get_json()['details'] == {'document_tracking_number': 'AWB-ON-CI',
                                              'tracking_number': 'AWB-SAVED'}
    response = _complete(client, access_token, task_id, {'tracking_number': ''})   # 空串同样按已存值
    assert response.status_code == 409 and response.get_json()['code'] == 16080
    assert _task(client, task_id)[:2] == ('in_progress', 'AWB-SAVED')

    # 带了与 CI 一致的号码（空格 / 连字符不同也算）→ 按 CI 上的写法写入
    response = _complete(client, access_token, task_id, {'tracking_number': 'AWB ON CI'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'AWB-ON-CI'


def test_delivered_webhook_uses_completed_task_tracking(client, access_token):
    dn_id, t1 = _domestic_packed(client, access_token, 'ORD-GUARD-WEBHOOK')
    assert _save_tracking(client, access_token, t1, {'tracking_number': 'OLD-T1'}).status_code == 200
    t2 = _legacy_task(client, dn_id)            # 存量第二条（国内件不限当前任务）
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        db.session.commit()

    response = _ship(client, access_token, t2, tracking_number='T2-AWB')
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        payload = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.delivered').one().payload
    assert payload['tracking_number'] == 'T2-AWB'


# ---------------------------------------------------------------------------
# B-58：自动运单号的标准写法、取消后清空、已取消的号码不能再用
# ---------------------------------------------------------------------------

def test_spaced_tracking_cleared_on_cancel(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201

    # 有效运单期间存分组写法 → 入库为运单上的标准写法
    response = _save_tracking(client, access_token, task_id, {'tracking_number': '7946 0000 0001'})
    assert response.status_code == 200 and response.get_json()['tracking_number'] == '794600000001'

    # 存量数据：修复前原样入库的分组写法；取消时按规范化后的号码比较，照样清空
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).tracking_number = '7946 0000 0001'
        db.session.commit()
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    assert _task(client, task_id)[1] is None

    # 重签的 CI 不再印已取消的 AWB
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['documents_outdated'] is False
    ci = client.get(f"/dn/{dn_id}/customs-documents/{view['current_documents'][0]['id']}/file",
                    headers=_h(access_token))
    assert b'794600000001' not in _pdf_streams(ci.data) and b'7946 0000 0001' not in _pdf_streams(ci.data)


def test_cancelled_tracking_rejected(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    assert _cancel(client, access_token, dn_id).status_code == 200
    assert client.put(f'/delivery/{task_id}/process/', headers=_h(access_token)).status_code == 200
    expected = {'tracking_number': None, 'cancelled_tracking_number': '794600000001', 'carrier': 'fedex',
                'status': 'cancelled', 'record_status': 'cancelled'}

    # 完成发货（PDA 表单还停在旧预填值）/ 保存运单号 / 修改任务：已取消的号码（各种写法）→ 16078
    for value in ('794600000001', '7946 0000 0001', '7946-0000-0001'):
        for response in (
            _complete(client, access_token, task_id, {'tracking_number': value}),
            _save_tracking(client, access_token, task_id, {'tracking_number': value}),
            client.put(f'/delivery/{task_id}', json={'tracking_number': value}, headers=_h(access_token)),
        ):
            assert response.status_code == 409 and response.get_json()['code'] == 16078, (value, response.get_json())
            assert response.get_json()['details'] == expected
    assert _task(client, task_id)[:2] == ('in_progress', None)

    # 别的号码照常
    response = _complete(client, access_token, task_id, {'tracking_number': 'MANUAL-NEW-1'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == 'MANUAL-NEW-1'


def test_compensated_cancelled_tracking_rejected(client, access_token, fedex, monkeypatch):
    """写库失败后补偿取消（cancelled / compensated）的运单号同样不能再用"""
    dn_id, task_id = _ready(client, access_token)
    monkeypatch.setattr(CarrierShipmentService, '_store_label',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full'))))
    assert _create(client, access_token, dn_id).status_code == 500
    assert [(r['status'], r['reason']) for r in _records(client, dn_id)] == [('cancelled', 'compensated')]

    response = _save_tracking(client, access_token, task_id, {'tracking_number': '794600000001'})
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details']['status'] == 'cancelled'
    response = _ship(client, access_token, task_id, tracking_number='7946 0000 0001')
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details']['cancelled_tracking_number'] == '794600000001'


# ---------------------------------------------------------------------------
# B-67：承运商不能绕过自动运单 / CI
# ---------------------------------------------------------------------------

def test_save_tracking_cannot_change_carrier_while_shipment_active(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    other_id = _other_carrier(client)
    fedex_id = _task(client, task_id)[2]

    for body in ({'carrier_id': other_id}, {'tracking_number': '794600000001', 'carrier_id': other_id}):
        response = _save_tracking(client, access_token, task_id, body)
        assert response.status_code == 409 and response.get_json()['code'] == 16078, body
        assert response.get_json()['details'] == {
            'tracking_number': '794600000001', 'carrier': 'fedex', 'carrier_id': other_id}
    assert _task(client, task_id) == ('pending', '794600000001', fedex_id)

    # 同一承运商照常；取消运单后可以换
    assert _save_tracking(client, access_token, task_id, {'carrier_id': fedex_id}).status_code == 200
    assert _cancel(client, access_token, dn_id).status_code == 200
    response = _save_tracking(client, access_token, task_id, {'carrier_id': other_id})
    assert response.status_code == 200 and response.get_json()['carrier_id'] == other_id


def test_export_complete_carrier_must_match_ci(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    yamato = _other_carrier(client, 'yamato')
    sagawa = _other_carrier(client, 'sagawa')
    response = _save_tracking(client, access_token, task_id, {'tracking_number': 'AWB-1', 'carrier_id': yamato})
    assert response.status_code == 200, response.get_json()
    assert _issue(client, access_token, dn_id).status_code == 201       # CI 印 Other yamato

    response = _ship(client, access_token, task_id, carrier_id=sagawa)
    assert response.status_code == 409 and response.get_json()['code'] == 16092
    assert response.get_json()['details'] == {'document_carrier': 'Other yamato', 'document_carrier_id': yamato,
                                              'carrier_id': sagawa}
    # null = 清空任务上的承运商，CI 取数回落到 DN 的承运商（与 CI 上的不同）→ 同样 16092
    response = _complete(client, access_token, task_id, {'carrier_id': None})
    assert response.status_code == 409 and response.get_json()['code'] == 16092
    assert _task(client, task_id) == ('in_progress', 'AWB-1', yamato)

    # 同一承运商照常
    response = _complete(client, access_token, task_id, {'carrier_id': yamato})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['carrier_id'] == yamato
