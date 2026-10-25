"""入库单流程汇总（GET /asn/<id>/flow，doc/shipping-flow.md §7.1）、跟踪刷新（POST /asn/<id>/track）与列表的入库阶段

- 没有运单号：expected（在途 skipped）→ 收货 sorting → 分拣中 → 分拣完成 putaway（待上架按 sorted_stock 估算）→ 上架后
  completed；每一步的 state / at（带偏移）/ source / summary / actions
- 只有查询页的承运商：in_transit，只给 open_tracking；刷新 → 409 16140
- FedEx（跟踪方式 api、tracks_any_number）：刷新存跟踪结果，在途 → 已送达（receiving，不自动收货）→ 异常（exception）；
  查询失败只记 error、保留上次结果；改运单号清空跟踪结果
- 待上架的归属：同一商品的 sorted_stock 先算给晚完成的入库单
- 关闭 → closed（没做的步骤 skipped）；ASN 列表的 inbound_stage 与 inbound_stage= 筛选（SQL）；权限 / 归属

FedEx 一律 mock（沿用 test_carrier_shipment 的 fedex fixture），不联网。
"""
from .helpers import *
from .test_customs import _h
from .test_carrier_shipment import fedex  # noqa: F401  fedex 是 fixture
from .test_carrier_tracking import AWB, DL, IT, OC, PU, TRACK, _track_body
from .test_isolation_outbound import _make_company_b
from .test_process_guards import _staff_token

from datetime import date, datetime, timedelta     # helpers 的 import * 会把 datetime 换成模块，放在它后面

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from warehouse.asn.flow import STEP_KEYS
from warehouse.asn.stage import STAGES, stage_expression
from warehouse.carrier.models import Carrier
from warehouse.sorting.models import SortingTask

DE = {'date': '2026-10-24T11:00:00+09:00', 'derivedStatusCode': 'DE', 'eventType': 'DE',
      'eventDescription': 'Delivery exception', 'scanLocation': {'city': 'OSAKA', 'countryCode': 'JP'}}


def _flow(client, token, asn_id):
    response = client.get(f'/asn/{asn_id}/flow', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _steps(flow):
    return {s['key']: s for s in flow['steps']}


def _states(flow):
    return {s['key']: s['state'] for s in flow['steps']}


def _actions(step):
    return {a['key']: a for a in step['actions']}


def _list_stage(client, token, asn_id):
    items = client.get('/asn/?page=1&per_page=100', headers=_h(token)).get_json()['items']
    return next(i for i in items if i['id'] == asn_id)['inbound_stage']


def _filtered(client, token, stage):
    response = client.get(f'/asn/?page=1&per_page=100&inbound_stage={stage}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return [i['id'] for i in response.get_json()['items']]


def _assert_stage(client, token, asn_id, stage):
    flow = _flow(client, token, asn_id)
    assert flow['stage'] == stage, _states(flow)
    assert _list_stage(client, token, asn_id) == stage
    assert asn_id in _filtered(client, token, stage)
    for other in STAGES:
        if other != stage:
            assert asn_id not in _filtered(client, token, other), other
    return flow


def _offset(iso):
    """带时区偏移的 ISO 串"""
    return iso is not None and datetime.fromisoformat(iso).tzinfo is not None


def _create_asn(client, token, **extra):
    body = {'warehouse_id': 1, 'supplier_id': 1, 'expected_arrival_date': (warehouse_today() + timedelta(days=3)).isoformat(),
            'order_number': 'PO-FLOW-1', 'details': [{'goods_id': 1, 'quantity': 5}, {'goods_id': 2, 'quantity': 3}]}
    body.update(extra)
    response = client.post('/asn/', headers=_h(token), json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()['id']


def _sorted_stock(client, goods_id, value=None):
    with client.application.app_context():
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, 1)
        if value is not None:
            inventory.sorted_stock = value
            db.session.commit()
        return inventory.sorted_stock


# ---------------------------------------------------------------------------
# 没有运单号：预计到货 → 收货 → 分拣 → 上架 → 完成
# ---------------------------------------------------------------------------

def test_flow_without_tracking(client, access_token):
    _sorted_stock(client, 1, 0)
    _sorted_stock(client, 2, 0)
    asn_id = _create_asn(client, access_token)
    flow = _assert_stage(client, access_token, asn_id, 'expected')
    assert [s['key'] for s in flow['steps']] == list(STEP_KEYS)
    assert flow['supplier']['id'] == 1 and flow['tracking_number'] is None and flow['carrier'] is None
    steps = _steps(flow)
    assert _states(flow) == {'created': 'done', 'in_transit': 'skipped', 'received': 'current', 'sorting': 'todo',
                             'putaway': 'todo', 'completed': 'todo'}
    assert steps['created']['source'] == 'warehouse' and _offset(steps['created']['at'])
    assert steps['created']['summary']['line_count'] == 2 and steps['created']['summary']['quantity'] == 8
    assert steps['created']['summary']['creator']['user_name']
    assert _actions(steps['received'])['receive']['enabled'] is True
    assert steps['received']['summary']['overdue'] is False and flow['problems'] == []

    # 收货 → 分拣
    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    flow = _assert_stage(client, access_token, asn_id, 'sorting')
    steps = _steps(flow)
    with client.application.app_context():
        task_id = SortingTask.query.filter_by(asn_id=asn_id).one().id
    assert flow['tasks'] == {'sorting': [task_id]}
    assert steps['received']['state'] == 'done' and steps['received']['source'] == 'warehouse'
    assert _offset(steps['received']['at']) and steps['received']['actions'] == []
    assert steps['sorting']['state'] == 'current' and steps['sorting']['at'] is None
    assert steps['sorting']['summary']['task_ids'] == [task_id]
    assert steps['sorting']['summary']['tasks'][0]['status'] == 'pending'

    # 分拣中：进度来自分拣明细
    assert client.put(f'/sorting/{task_id}/process/', headers=_h(access_token)).status_code == 200
    response = client.post(f'/sorting/{task_id}/batches/', headers=_h(access_token), json={
        'remark': 'flow', 'details': [{'goods_id': 1, 'sorted_quantity': 4, 'damage_quantity': 1},
                                      {'goods_id': 2, 'sorted_quantity': 3, 'damage_quantity': 0}]})
    assert response.status_code == 201, response.get_json()
    steps = _steps(_assert_stage(client, access_token, asn_id, 'sorting'))
    assert steps['sorting']['state'] == 'current' and steps['sorting']['source'] == 'warehouse'
    assert _offset(steps['sorting']['at'])
    assert (steps['sorting']['summary']['sorted_quantity'], steps['sorting']['summary']['damage_quantity']) == (7, 1)

    # 分拣完成 → 入库单自动完成，货在待上架库存里
    assert client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token)).status_code == 200
    flow = _assert_stage(client, access_token, asn_id, 'putaway')
    steps = _steps(flow)
    assert _states(flow) == {'created': 'done', 'in_transit': 'skipped', 'received': 'done', 'sorting': 'done',
                             'putaway': 'current', 'completed': 'done'}
    assert steps['completed']['source'] == 'system' and _offset(steps['completed']['at'])
    assert steps['completed']['summary'] == {'expected_quantity': 8, 'actual_quantity': 8, 'sorted_quantity': 7,
                                             'damage_quantity': 1, 'difference': 0}
    assert steps['received']['summary']['actual_quantity'] == 8
    putaway = steps['putaway']['summary']
    assert (putaway['quantity'], putaway['pending_quantity'], putaway['put_away_quantity']) == (8, 8, 0)
    assert {g['goods_id']: g['pending'] for g in putaway['goods']} == {1: 5, 2: 3}
    assert putaway['estimated'] is True and putaway['goods'][0]['code']

    # 上架一部分：还是 putaway
    with client.application.app_context():
        location_id = get_location().id
    response = client.post('/putaway/', headers=_h(access_token), json={'goods_id': 1, 'location_id': location_id,
                                                                         'quantity': 5})
    assert response.status_code == 201, response.get_json()
    putaway = _steps(_assert_stage(client, access_token, asn_id, 'putaway'))['putaway']['summary']
    assert (putaway['pending_quantity'], putaway['put_away_quantity']) == (3, 5)

    # 全部上架 → completed，上架一步的时刻 = 最后一次上架
    response = client.post('/putaway/', headers=_h(access_token), json={'goods_id': 2, 'location_id': location_id,
                                                                         'quantity': 3})
    assert response.status_code == 201, response.get_json()
    steps = _steps(_assert_stage(client, access_token, asn_id, 'completed'))
    assert steps['putaway']['state'] == 'done' and steps['putaway']['source'] == 'warehouse'
    assert _offset(steps['putaway']['at']) and steps['putaway']['summary']['pending_quantity'] == 0


def test_overdue_and_no_details(client, access_token):
    asn_id = _create_asn(client, access_token, expected_arrival_date=(warehouse_today() - timedelta(days=2)).isoformat())
    flow = _flow(client, access_token, asn_id)
    assert [p['code'] for p in flow['problems']] == ['ARRIVAL_OVERDUE']
    assert flow['problems'][0]['step'] == 'received' and _steps(flow)['received']['summary']['overdue'] is True

    empty = _create_asn(client, access_token, details=[])
    flow = _flow(client, access_token, empty)
    receive = _actions(_steps(flow)['received'])['receive']
    assert receive['enabled'] is False and receive['reason_code'] == 'NO_DETAILS'
    assert 'NO_DETAILS' in [p['code'] for p in flow['problems']]


def test_closed_flow(client, access_token):
    asn_id = _create_asn(client, access_token, tracking_number='LINK-0001', carrier_id=1)
    assert client.put(f'/asn/{asn_id}/close/', headers=_h(access_token)).status_code == 200
    flow = _assert_stage(client, access_token, asn_id, 'closed')
    assert _states(flow) == {'created': 'done', 'in_transit': 'skipped', 'received': 'skipped', 'sorting': 'skipped',
                             'putaway': 'skipped', 'completed': 'skipped'}
    assert all(s['actions'] == [] for s in flow['steps'] if s['key'] != 'created')
    assert _offset(flow['closed_at']) and flow['problems'] == []


# ---------------------------------------------------------------------------
# 运单号：只有查询页 / 经 API 跟踪
# ---------------------------------------------------------------------------

def test_link_only_tracking(client, access_token):
    with client.application.app_context():
        carrier = db.session.get(Carrier, 1)
        carrier.tracking_url_template = 'https://track.example.com/?n={tracking_number}'
        db.session.commit()
    asn_id = _create_asn(client, access_token, tracking_number=' 4711 ', carrier_id=1)
    flow = _assert_stage(client, access_token, asn_id, 'in_transit')
    steps = _steps(flow)
    assert flow['tracking_number'] == '4711'
    assert flow['carrier']['tracking_url'] == 'https://track.example.com/?n=4711'
    assert flow['carrier']['tracking_method'] == 'link'
    assert steps['in_transit']['state'] == 'current' and steps['in_transit']['summary']['tracked_by_api'] is False
    assert _actions(steps['in_transit']) == {'open_tracking': {'key': 'open_tracking', 'enabled': True,
                                                               'url': 'https://track.example.com/?n=4711'}}
    # 承运商说还在路上：收货还没轮到（仓库照样可以收）
    assert steps['received']['state'] == 'todo' and _actions(steps['received'])['receive']['enabled'] is True
    response = client.post(f'/asn/{asn_id}/track', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16140

    # 收货后在途一步算完成（没有承运商时刻）
    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    step = _steps(_flow(client, access_token, asn_id))['in_transit']
    assert (step['state'], step['at'], step['source']) == ('done', None, None)


def _fedex_asn(client, token):
    asn_id = _create_asn(client, token, tracking_number=AWB, carrier_id=1)
    with client.application.app_context():
        # 入库单创建早于下面造的扫描（比创建早很多的扫描算旧包裹，见 warehouse/asn/tracking.py）
        db.session.get(ASN, asn_id).created_at = datetime(2026, 10, 20, 12, 0, 0)
        db.session.commit()
    return asn_id


def _track(client, token, asn_id):
    response = client.post(f'/asn/{asn_id}/track', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_api_tracking_refresh(client, access_token, fedex):
    asn_id = _fedex_asn(client, access_token)
    flow = _assert_stage(client, access_token, asn_id, 'in_transit')
    step = _steps(flow)['in_transit']
    assert flow['carrier']['tracking_method'] == 'api' and flow['carrier']['integration'] == 'fedex'
    assert step['summary']['tracked_by_api'] is True and step['summary']['tracking'] is None
    assert _actions(step)['refresh_tracking']['enabled'] is True
    assert AWB in _actions(step)['open_tracking']['url']

    # 在途
    fedex.set(TRACK, (200, _track_body('IT', [OC, PU, IT])))
    body = _track(client, access_token, asn_id)
    assert body['tracking_sync'] == {'tracked': True, 'updated': True, 'error': None}
    assert fedex.of(TRACK)[-1]['json']['trackingInfo'] == [{'trackingNumberInfo': {'trackingNumber': AWB}}]
    assert body['stage'] == 'in_transit'
    step = _steps(body)['in_transit']
    tracking = step['summary']['tracking']
    assert (tracking['status'], tracking['code']) == ('in_transit', 'IT') and len(tracking['events']) == 3
    assert _offset(tracking['synced_at'])
    assert step['state'] == 'current' and step['source'] == 'carrier'
    assert datetime.fromisoformat(step['at']) == datetime.fromisoformat('2026-10-23T09:00:00+09:00')
    assert _steps(body)['received']['state'] == 'todo'
    # 结果没变
    assert _track(client, access_token, asn_id)['tracking_sync']['updated'] is False

    # 已送达：不自动收货，阶段 receiving
    fedex.set(TRACK, (200, _track_body('DL', [OC, PU, IT, DL], delivered='2026-10-25T14:00:00+02:00')))
    body = _track(client, access_token, asn_id)
    flow = _assert_stage(client, access_token, asn_id, 'receiving')
    steps = _steps(flow)
    assert flow['asn_status'] == 'pending'
    assert steps['in_transit']['state'] == 'done' and steps['in_transit']['source'] == 'carrier'
    assert datetime.fromisoformat(steps['in_transit']['at']) == datetime.fromisoformat('2026-10-25T14:00:00+02:00')
    assert steps['received']['state'] == 'current'

    # 查询失败：保留上次结果，只记 error
    fedex.set(TRACK, (500, {'errors': [{'code': 'SYSTEM.UNAVAILABLE.EXCEPTION', 'message': 'down'}]}))
    body = _track(client, access_token, asn_id)
    assert body['tracking_sync']['tracked'] is False and 'SYSTEM.UNAVAILABLE' in body['tracking_sync']['error']
    tracking = _steps(body)['in_transit']['summary']['tracking']
    assert tracking['status'] == 'delivered' and 'SYSTEM.UNAVAILABLE' in tracking['error']
    assert body['stage'] == 'receiving'

    # 异常
    fedex.set(TRACK, (200, _track_body('DE', [OC, PU, DE])))
    _track(client, access_token, asn_id)
    flow = _assert_stage(client, access_token, asn_id, 'exception')
    assert _steps(flow)['in_transit']['state'] == 'warning'
    assert [p['code'] for p in flow['problems']] == ['TRACKING_EXCEPTION']

    # 改运单号：之前的跟踪结果清空
    response = client.put(f'/asn/{asn_id}', headers=_h(access_token), json={'tracking_number': '794600000099'})
    assert response.status_code == 200, response.get_json()
    flow = _assert_stage(client, access_token, asn_id, 'in_transit')
    assert _steps(flow)['in_transit']['summary']['tracking'] is None
    with client.application.app_context():
        asn = db.session.get(ASN, asn_id)
        assert (asn.tracking_status, asn.tracking_detail, asn.tracking_synced_at) == (None, None, None)

    # 收货后不再需要刷新
    assert client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)).status_code == 200
    refresh = _actions(_steps(_flow(client, access_token, asn_id))['in_transit'])['refresh_tracking']
    assert refresh['enabled'] is False and refresh['reason_code'] == 'ASN_NOT_PENDING'


def test_api_tracking_ignores_old_package(client, access_token, fedex):
    asn_id = _fedex_asn(client, access_token)
    old_pu = {**PU, 'date': '2025-01-02T10:00:00+09:00'}
    old_dl = {**DL, 'date': '2025-01-05T10:00:00+01:00'}
    fedex.set(TRACK, (200, _track_body('DL', [old_pu, old_dl], delivered='2025-01-05T10:00:00+01:00')))
    body = _track(client, access_token, asn_id)
    assert body['tracking_sync']['error'].startswith('TRACKING.OLD_SHIPMENT')
    assert body['stage'] == 'in_transit'


def test_track_needs_asn_edit(client, access_token, fedex):
    asn_id = _fedex_asn(client, access_token)
    reader = _staff_token(client, 'asn-reader', ['asn_read'])
    assert client.get(f'/asn/{asn_id}/flow', headers=reader).status_code == 200
    assert client.post(f'/asn/{asn_id}/track', headers=reader).status_code == 403
    assert fedex.of(TRACK) == []


# ---------------------------------------------------------------------------
# 待上架的归属 / 列表 / 权限
# ---------------------------------------------------------------------------

def _completed_asn(client, actual, completed_at):
    """直接造一张已完成的入库单（商品 1，实收 actual）→ id"""
    with client.application.app_context():
        admin = get_admin_user()
        asn = ASN(warehouse_id=1, supplier_id=1, asn_type='inbound', status='completed', created_by=admin.id,
                  received_at=completed_at, completed_at=completed_at)
        db.session.add(asn)
        db.session.flush()
        db.session.add(ASNDetail(asn_id=asn.id, goods_id=1, quantity=actual, actual_quantity=actual,
                                 sorted_quantity=actual, damage_quantity=0, created_by=admin.id))
        db.session.commit()
        return asn.id


def test_putaway_stock_goes_to_newer_asn_first(client, access_token):
    older = _completed_asn(client, 4, datetime(2026, 10, 1, 9, 0))
    newer = _completed_asn(client, 6, datetime(2026, 10, 2, 9, 0))

    _sorted_stock(client, 1, 6)       # 只够新的那张
    _assert_stage(client, access_token, older, 'completed')
    flow = _assert_stage(client, access_token, newer, 'putaway')
    assert _steps(flow)['putaway']['summary']['pending_quantity'] == 6

    _sorted_stock(client, 1, 8)       # 多出来的 2 件算到旧的那张
    flow = _assert_stage(client, access_token, older, 'putaway')
    assert _steps(flow)['putaway']['summary']['goods'] == [
        {'goods_id': 1, 'code': 'G001', 'name': _steps(flow)['putaway']['summary']['goods'][0]['name'],
         'quantity': 4, 'pending': 2}]
    _assert_stage(client, access_token, newer, 'putaway')

    _sorted_stock(client, 1, 0)
    _assert_stage(client, access_token, older, 'completed')
    _assert_stage(client, access_token, newer, 'completed')


def test_stage_filter_validation_and_sql(client, access_token):
    response = client.get('/asn/?inbound_stage=nowhere', headers=_h(access_token))
    assert response.status_code == 400
    with client.application.app_context():
        sql = str(select(stage_expression()).compile(dialect=postgresql.dialect()))
    assert 'CASE' in sql and 'EXISTS' in sql
    assert set(STAGES) == {'expected', 'in_transit', 'receiving', 'sorting', 'putaway', 'completed', 'closed',
                           'exception'}


def test_flow_permissions(client, access_token):
    asn_id = _create_asn(client, access_token)
    headers_b, _warehouse_b = _make_company_b(client, company_admin=True)
    assert client.get(f'/asn/{asn_id}/flow', headers=headers_b).status_code == 403
    assert client.post(f'/asn/{asn_id}/track', headers=headers_b).status_code == 403
    assert client.get('/asn/999999/flow', headers=_h(access_token)).status_code == 404
    # 带仓库头 / warehouse_id 参数的照样能读
    assert client.get(f'/asn/{asn_id}/flow?warehouse_id=1', headers=_h(access_token)).status_code == 200
    assert client.get(f'/asn/{asn_id}/flow', headers=_h(access_token, **{'X-WAREHOUSE-ID': '1'})).status_code == 200
