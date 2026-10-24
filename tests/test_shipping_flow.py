"""出库单流程汇总（GET /dn/<id>/shipping-flow，doc/shipping-flow.md §5）与列表的发货阶段（shipping_stage）

- 跨境件走 FedEx（运单 / 揽收 / 跟踪都是 api）：awaiting_waybill → awaiting_pickup → awaiting_handover → in_transit
  → delivered，每一步的 state / at / source / summary / actions
- 国内件（手填运单号）：documents skipped、揽收 none 时直接 awaiting_handover、manual 时 awaiting_pickup
- 结果不明的运单 → exception；DN 取消 → closed（没做的步骤 skipped）
- DN 列表的 shipping_stage / shipping 与 shipping_stage= 筛选（SQL）、发货任务列表的 shipping_stage 与流程同一口径
- 权限 / 归属：跨公司 403、不存在 404
"""
from .helpers import *
from .test_customs import PACKAGES, _create_dn, _force_packed, _h, _prepare, _ship
from .test_carrier_shipment import _create, _ready, fedex  # noqa: F401  fedex 是 fixture
from .test_carrier_pickup import _WAREHOUSE, _tomorrow, _wh
from .test_isolation_outbound import _make_company_b

from datetime import datetime, timedelta      # helpers 的 import * 会把 datetime 换成模块，放在它后面

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from warehouse.dn.models import DNCarrierShipment
from warehouse.shipping.flow import STEP_KEYS
from warehouse.shipping.stage import STAGES, stage_expression


def _flow(client, token, dn_id):
    response = client.get(f'/dn/{dn_id}/shipping-flow', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _steps(flow):
    return {s['key']: s for s in flow['steps']}


def _states(flow):
    return {s['key']: s['state'] for s in flow['steps']}


def _actions(step):
    return {a['key']: a for a in step['actions']}


def _list_stage(client, token, dn_id):
    items = client.get('/dn/?page=1&per_page=50', headers=_h(token)).get_json()['items']
    row = next(i for i in items if i['id'] == dn_id)
    return row['shipping_stage'], row['shipping']


def _filtered(client, token, stage):
    response = client.get(f'/dn/?page=1&per_page=50&shipping_stage={stage}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return [i['id'] for i in response.get_json()['items']]


def _assert_stage(client, token, dn_id, stage):
    flow = _flow(client, token, dn_id)
    assert flow['stage'] == stage, _states(flow)
    assert _list_stage(client, token, dn_id)[0] == stage
    assert dn_id in _filtered(client, token, stage)
    return flow


def test_export_flow_through_fedex(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    with client.application.app_context():
        carrier_id = get_carrier().id
        _WAREHOUSE['id'] = get_warehouse().id

    flow = _assert_stage(client, access_token, dn_id, 'awaiting_waybill')
    assert [s['key'] for s in flow['steps']] == list(STEP_KEYS)
    assert flow['dn_status'] == 'packed' and flow['order_number'] == 'ORD-EXPORT-1'
    assert flow['warehouse_id'] == _WAREHOUSE['id']
    assert flow['route'] == {'origin_country': 'JP', 'destination_country': 'DE', 'international': True,
                             'requires_customs': True}
    assert flow['carrier'] == {'id': carrier_id, 'name': 'Test Carrier', 'code': None, 'integration': 'fedex',
                               'methods': {'waybill': 'api', 'pickup': 'api', 'tracking': 'api'},
                               'auto_sign_days': None, 'tracking_url': None}
    assert flow['tasks']['delivery'] == task_id and len(flow['tasks']['picking']) == 1
    steps = _steps(flow)
    assert _states(flow) == {'ordered': 'done', 'picking': 'done', 'packing': 'done', 'documents': 'current',
                             'waybill': 'current', 'pickup': 'todo', 'handover': 'todo', 'transit': 'todo',
                             'delivered': 'todo'}
    assert steps['ordered']['source'] == 'warehouse' and steps['ordered']['at'][-6] in '+-'
    assert steps['packing']['summary']['package_count'] == 2
    assert steps['packing']['summary']['gross_weight_kg'] == 6.5 and len(steps['packing']['summary']['packages']) == 2
    assert steps['documents']['summary']['missing'] == ['commercial_invoice', 'packing_list']
    assert _actions(steps['documents'])['issue_documents']['enabled'] is True
    waybill_actions = _actions(steps['waybill'])
    assert waybill_actions['create_waybill']['enabled'] is True and waybill_actions['enter_tracking']['enabled']
    assert _actions(steps['handover'])['complete_shipping']['enabled'] is True
    # 不能做的操作带 reason_code（界面按它翻译）
    assert _actions(steps['pickup'])['record_pickup']['reason_code'] == 'NO_WAYBILL'
    assert _actions(steps['delivered'])['sign']['reason_code'] == 'NOT_HANDED_OVER'
    assert 'reason_code' not in waybill_actions['create_waybill']

    # 建运单（同时签发 CI / PL）→ 待揽收
    assert _create(client, access_token, dn_id).status_code == 201
    flow = _assert_stage(client, access_token, dn_id, 'awaiting_pickup')
    steps = _steps(flow)
    assert steps['documents']['state'] == 'done' and steps['documents']['summary']['version'] == 1
    assert steps['waybill']['state'] == 'done' and steps['waybill']['source'] == 'api'
    summary = steps['waybill']['summary']
    assert summary['tracking_number'] == '794600000001' and summary['source'] == 'api'
    assert summary['shipment']['status'] == 'active' and summary['label_document_id']
    assert summary['shipment']['tracking_url'] == 'https://www.fedex.com/fedextrack/?trknbr=794600000001'
    waybill_actions = _actions(steps['waybill'])
    assert waybill_actions['print_label']['enabled'] is True and waybill_actions['cancel_waybill']['enabled'] is True
    assert waybill_actions['create_waybill']['enabled'] is False and waybill_actions['enter_tracking']['enabled'] is False
    assert waybill_actions['enter_tracking']['reason_code'] == 'API_WAYBILL_OPEN'
    assert steps['pickup']['state'] == 'current'
    assert set(_actions(steps['pickup'])) == {'book_pickup', 'record_pickup'}
    assert flow['carrier']['tracking_url'] == 'https://www.fedex.com/fedextrack/?trknbr=794600000001'
    assert _list_stage(client, access_token, dn_id)[1] == {
        'carrier_id': carrier_id, 'carrier_name': 'Test Carrier', 'tracking_number': '794600000001'}

    # 手工记一笔揽收 → 等交承运商
    shipment_id = summary['shipment']['id']
    response = client.post('/dn/carrier-pickups/manual', json={
        'carrier_id': carrier_id, 'shipment_ids': [shipment_id], 'pickup_date': _tomorrow(),
        'ready_time': '13:00', 'close_time': '17:00'}, headers=_wh(access_token))
    assert response.status_code == 201, response.get_json()
    flow = _assert_stage(client, access_token, dn_id, 'awaiting_handover')
    steps = _steps(flow)
    assert steps['pickup']['state'] == 'done' and steps['pickup']['source'] == 'warehouse'
    assert steps['pickup']['summary']['pickup']['source'] == 'manual'
    assert _actions(steps['pickup'])['cancel_pickup']['enabled'] is True
    assert _actions(steps['pickup'])['record_pickup']['reason_code'] == 'PICKUP_EXISTS'
    assert steps['handover']['state'] == 'current'

    # 完成发货 → 运输中
    shipped = (datetime.now() - timedelta(hours=1)).replace(microsecond=0)
    assert _ship(client, access_token, task_id, shipped_at=shipped.isoformat()).status_code == 200
    flow = _assert_stage(client, access_token, dn_id, 'in_transit')
    steps = _steps(flow)
    assert steps['handover']['state'] == 'done' and steps['handover']['source'] == 'warehouse'
    assert datetime.fromisoformat(steps['handover']['at']) == shipped.astimezone()
    assert steps['transit']['state'] == 'current'
    assert steps['transit']['summary']['tracked_by_api'] is True
    assert set(_actions(steps['transit'])) == {'refresh_tracking', 'open_tracking'}
    assert steps['delivered']['state'] == 'todo' and _actions(steps['delivered'])['sign']['enabled'] is True
    # 发货后单证锁定：报关问题不再列（没法处理了）
    assert not [p for p in flow['problems'] if p['step'] == 'documents']

    # 签收 → 已签收
    response = client.put(f'/delivery/{task_id}/sign/', json={}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    flow = _assert_stage(client, access_token, dn_id, 'delivered')
    steps = _steps(flow)
    assert steps['delivered']['state'] == 'done' and steps['delivered']['source'] == 'warehouse'
    assert steps['transit']['state'] == 'done'


def test_documents_problems_dropped_after_shipping(client, access_token):
    """报关问题（如缺收件人税号）发货前列在单证一步；发货后单证锁定、没法再处理，不再列"""
    from .test_customs import _customs, _export_dn_ready
    dn_id, task_id = _export_dn_ready(client, access_token, customs=_customs(recipient_tax_id=None,
                                                                             recipient_tax_id_type=None))
    flow = _flow(client, access_token, dn_id)
    assert [p['code'] for p in flow['problems'] if p['step'] == 'documents'] == ['RECIPIENT_TAX_ID_MISSING']
    assert _ship(client, access_token, task_id).status_code == 200
    flow = _flow(client, access_token, dn_id)
    assert not [p for p in flow['problems'] if p['step'] == 'documents']
    issue = _actions(_steps(flow)['documents'])['issue_documents']
    assert issue['enabled'] is False and issue['reason_code'] == 'DN_SHIPPED'


def _domestic(client, token, order_number, pickup_method='none'):
    _prepare(client)
    with client.application.app_context():
        get_recipient().country = 'JP'
        carrier = get_carrier()
        carrier.pickup_method = pickup_method
        db.session.commit()
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    assert client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token)).status_code == 200
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).carrier_id = get_carrier().id
        db.session.commit()
    return dn_id, task_id


def test_domestic_manual_flow(client, access_token):
    dn_id, task_id = _domestic(client, access_token, 'ORD-FLOW-D1')
    flow = _assert_stage(client, access_token, dn_id, 'awaiting_waybill')
    states = _states(flow)
    assert states['documents'] == 'skipped' and states['pickup'] == 'skipped' and states['transit'] == 'skipped'
    assert flow['route']['requires_customs'] is False
    assert flow['carrier']['methods'] == {'waybill': 'manual', 'pickup': 'none', 'tracking': 'link'}
    waybill_actions = _actions(_steps(flow)['waybill'])
    assert 'create_waybill' not in waybill_actions and waybill_actions['enter_tracking']['enabled'] is True

    # 手填运单号：揽收 none → 直接等交承运商
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'DOM-42'},
                          headers=_h(access_token))
    assert response.status_code == 200
    flow = _assert_stage(client, access_token, dn_id, 'awaiting_handover')
    waybill = _steps(flow)['waybill']
    assert waybill['state'] == 'done' and waybill['source'] == 'warehouse'
    assert waybill['summary']['source'] == 'manual' and waybill['summary']['tracking_number'] == 'DOM-42'

    # 揽收要逐票约（manual）→ 待揽收
    with client.application.app_context():
        get_carrier().pickup_method = 'manual'
        get_carrier().tracking_url_template = 'https://track.example.com/{tracking_number}'
        get_carrier().auto_sign_days = 5
        db.session.commit()
    flow = _assert_stage(client, access_token, dn_id, 'awaiting_pickup')
    steps = _steps(flow)
    assert steps['pickup']['state'] == 'current' and set(_actions(steps['pickup'])) == {'record_pickup'}
    assert flow['carrier']['tracking_url'] == 'https://track.example.com/DOM-42'

    # 完成发货：没有 API 跟踪 → 运输中只有查询页链接；签收步骤给出规则签收时刻
    assert _ship(client, access_token, task_id).status_code == 200
    flow = _assert_stage(client, access_token, dn_id, 'in_transit')
    steps = _steps(flow)
    assert steps['transit']['state'] == 'current' and set(_actions(steps['transit'])) == {'open_tracking'}
    assert steps['pickup']['state'] == 'skipped'
    with client.application.app_context():
        completed_at = db.session.get(DeliveryTask, task_id).completed_at
    assert datetime.fromisoformat(steps['delivered']['summary']['auto_sign_at']) == \
        (completed_at + timedelta(days=5)).replace(microsecond=0).astimezone()

    # 发货任务列表也带 DN 的阶段
    items = client.get('/delivery/?page=1&per_page=50', headers=_h(access_token)).get_json()['items']
    assert next(i for i in items if i['id'] == task_id)['shipping_stage'] == 'in_transit'


def test_preparing_exception_and_closed(client, access_token):
    _prepare(client)
    pending = _create_dn(client, access_token, customs=None, order_number='ORD-FLOW-P').get_json()['id']
    flow = _assert_stage(client, access_token, pending, 'preparing')
    assert _states(flow)['picking'] == 'current' and flow['tasks'] == {'picking': [], 'packing': [], 'delivery': None}

    dn_id, task_id = _domestic(client, access_token, 'ORD-FLOW-X')
    with client.application.app_context():
        db.session.add(DNCarrierShipment(dn_id=dn_id, source='api', carrier='fedex', carrier_id=get_carrier().id,
                                         status='unknown', reason='timeout', created_at=datetime.now(),
                                         updated_at=datetime.now()))
        db.session.commit()
    flow = _assert_stage(client, access_token, dn_id, 'exception')
    assert _steps(flow)['waybill']['state'] == 'warning'
    assert 'WAYBILL_UNRESOLVED' in [p['code'] for p in flow['problems']]
    complete = _actions(_steps(flow)['handover'])['complete_shipping']
    assert complete['enabled'] is False and complete['reason_code'] == 'WAYBILL_UNRESOLVED'
    # 没有运单号的结果不明记录不能向承运商取消，只能确认作废
    waybill_actions = _actions(_steps(flow)['waybill'])
    assert 'cancel_waybill' not in waybill_actions and waybill_actions['dismiss_waybill']['enabled'] is True

    with client.application.app_context():
        DNCarrierShipment.query.filter_by(dn_id=dn_id).one().status = 'dismissed'
        dn = db.session.get(DN, dn_id)
        dn.picked_at = dn.packed_at = datetime.now()        # _force_packed 不记这两个时刻
        db.session.commit()
    assert client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token)).status_code == 200
    flow = _assert_stage(client, access_token, dn_id, 'closed')
    states = _states(flow)
    assert states['packing'] == 'done' and states['handover'] == 'skipped' and states['delivered'] == 'skipped'


def test_stage_filter_and_validation(client, access_token):
    dn_id, _task_id = _domestic(client, access_token, 'ORD-FLOW-F')
    assert dn_id in _filtered(client, access_token, 'awaiting_waybill')
    assert dn_id not in _filtered(client, access_token, 'in_transit')
    response = client.get('/dn/?shipping_stage=nowhere', headers=_h(access_token))
    assert response.status_code == 400


def test_stage_expression_compiles_for_postgresql(client):
    with client.application.app_context():
        sql = str(select(stage_expression()).compile(dialect=postgresql.dialect()))
    assert 'CASE' in sql and 'EXISTS' in sql
    assert set(STAGES) >= {'preparing', 'awaiting_waybill', 'awaiting_pickup', 'awaiting_handover', 'in_transit',
                           'exception', 'delivered', 'closed'}


def test_flow_permissions(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-FLOW-ACL').get_json()['id']
    headers_b, _warehouse_b = _make_company_b(client, company_admin=True)
    assert client.get(f'/dn/{dn_id}/shipping-flow', headers=headers_b).status_code == 403
    assert client.get('/dn/999999/shipping-flow', headers=_h(access_token)).status_code == 404
