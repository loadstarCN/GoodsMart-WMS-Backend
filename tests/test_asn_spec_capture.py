"""收货通知的规格采集（warehouse/asn/spec_capture.py）

- 仓库默认规则（默认不采集 / missing / warn）、取值校验 17020；建单时没传 spec_capture 复制仓库默认值（快照），对象里没给的键
  取默认；修改（pending）只改给了的键
- 待采集项：missing 按商品资料缺的项、remeasure 没采集过就全部；跳过原因、已关闭、已完成且没到货的行不算；
  spec_pending_count 与列表 spec_pending= 筛选（SQL）同一口径
- PUT /asn/<id>/details/<detail_id>/spec：received 或完成后补采（completed）可以，其他状态 17023；值写回商品、推
  goods.spec_updated（source 默认
  receiving，可传 station）；长宽高一起、至少一个值或跳过原因、remeasure 要填齐（17022）；数值 16100、原产国 10014；
  记采集时刻与人；权限 goods_edit / sorting_edit / packing_edit
- 分拣完成：warn 照常完成、响应 spec_pending 列出分拣到但没采集的行；block → 409 17021（跳过并填了原因的、没分拣到的行不拦）；
  asn.completed 每行带 spec_pending；入库流程的分拣一步带 spec_capture
- 商品列表 image_missing / spec_missing 筛选
"""
from .helpers import *  # 先导入：helpers 里的 `import datetime` 会覆盖同名对象
from .test_goods_spec_webhook import _events, _make_key, _permission_key
from .test_process_guards import _staff_token

from datetime import timedelta

import pytest

from system.webhook.models import WebhookEvent
from warehouse.asn.spec_capture import line_spec_pending


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _set_warehouse(client, **settings):
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, 1)
        for field, value in settings.items():
            setattr(warehouse, field, value)
        db.session.commit()


def _set_goods(client, goods_id, **values):
    with client.application.app_context():
        goods = db.session.get(Goods, goods_id)
        for field, value in values.items():
            setattr(goods, field, value)
        db.session.commit()


def _create_asn(client, token, **extra):
    body = {'warehouse_id': 1, 'supplier_id': 1, 'order_number': 'PO-SPEC-1',
            'expected_arrival_date': (warehouse_today() + timedelta(days=1)).isoformat(),
            'details': [{'goods_id': 1, 'quantity': 5}, {'goods_id': 2, 'quantity': 3}]}
    body.update(extra)
    response = client.post('/asn/', headers=_h(token), json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _asn(client, token, asn_id):
    response = client.get(f'/asn/{asn_id}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _lines(asn_body):
    """goods_id → 明细"""
    return {d['goods_id']: d for d in asn_body['details']}


def _receive(client, token, asn_id):
    response = client.put(f'/asn/{asn_id}/receive/', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        return SortingTask.query.filter_by(asn_id=asn_id).one().id


def _sort(client, token, task_id, details):
    assert client.put(f'/sorting/{task_id}/process/', headers=_h(token)).status_code == 200
    response = client.post(f'/sorting/{task_id}/batches/', headers=_h(token), json={'details': details})
    assert response.status_code == 201, response.get_json()


def _capture(client, headers, asn_id, detail_id, body):
    return client.put(f'/asn/{asn_id}/details/{detail_id}/spec', headers=headers, json=body)


def _listed(client, token, **params):
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    response = client.get(f'/asn/?page=1&per_page=100&{query}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return {item['id']: item for item in response.get_json()['items']}


ALL = ['weight', 'dimensions', 'origin_country']


# ---------------------------------------------------------------------------
# 仓库默认规则 / 单据上的快照
# ---------------------------------------------------------------------------

def test_warehouse_defaults_and_validation(client, access_token):
    body = client.get('/warehouse/1', headers=_h(access_token)).get_json()
    assert (body['default_spec_capture_fields'], body['default_spec_capture_mode'],
            body['spec_capture_enforcement']) == ([], 'missing', 'warn')

    for bad in ({'default_spec_capture_fields': ['weight', 'colour']}, {'default_spec_capture_fields': 'weight'},
                {'default_spec_capture_mode': 'all'}, {'spec_capture_enforcement': 'strict'}):
        response = client.put('/warehouse/1', headers=_h(access_token), json=bad)
        assert response.status_code == 400, bad
        assert (response.get_json()['code'], response.get_json()['field']) == (17020, next(iter(bad))), bad

    response = client.put('/warehouse/1', headers=_h(access_token), json={
        'default_spec_capture_fields': ['origin_country', ' Weight ', 'weight', 'dimensions'],
        'default_spec_capture_mode': 'REMEASURE', 'spec_capture_enforcement': 'block'})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body['default_spec_capture_fields'], body['default_spec_capture_mode'],
            body['spec_capture_enforcement']) == (ALL, 'remeasure', 'block')

    # 只改带了的；null 采集项 = 不采集，null 模式 = 不变
    response = client.put('/warehouse/1', headers=_h(access_token), json={
        'default_spec_capture_fields': None, 'default_spec_capture_mode': None, 'name': 'Warehouse A'})
    body = response.get_json()
    assert (body['default_spec_capture_fields'], body['default_spec_capture_mode'],
            body['spec_capture_enforcement']) == ([], 'remeasure', 'block')

    # 新建仓库也可以带
    response = client.post('/warehouse/', headers=_h(access_token), json={
        'name': 'Spec WH', 'company_id': 1, 'default_spec_capture_fields': ['weight']})
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert (body['default_spec_capture_fields'], body['default_spec_capture_mode'],
            body['spec_capture_enforcement']) == (['weight'], 'missing', 'warn')


def test_asn_snapshots_warehouse_defaults(client, access_token):
    # 默认不采集：现有客户端零影响
    body = _create_asn(client, access_token)
    assert body['spec_capture'] == {'fields': [], 'mode': 'missing'}
    assert body['spec_pending_count'] == 0
    assert all(d['spec_pending'] == [] for d in body['details'])

    _set_warehouse(client, default_spec_capture_fields=ALL, default_spec_capture_mode='missing')
    snapshot = _create_asn(client, access_token)
    assert snapshot['spec_capture'] == {'fields': ALL, 'mode': 'missing'}
    # 对象里没给的键取仓库默认；给了的照用
    partial = _create_asn(client, access_token, spec_capture={'mode': 'remeasure'})
    assert partial['spec_capture'] == {'fields': ALL, 'mode': 'remeasure'}
    explicit = _create_asn(client, access_token, spec_capture={'fields': ['weight'], 'mode': None})
    assert explicit['spec_capture'] == {'fields': ['weight'], 'mode': 'missing'}
    none = _create_asn(client, access_token, spec_capture={'fields': []})
    assert none['spec_capture'] == {'fields': [], 'mode': 'missing'}

    # 之后改仓库设置不影响已建的单
    _set_warehouse(client, default_spec_capture_fields=['weight'], default_spec_capture_mode='remeasure')
    assert _asn(client, access_token, snapshot['id'])['spec_capture'] == {'fields': ALL, 'mode': 'missing'}

    # 不合法 → 400 17020，单据不建
    with client.application.app_context():
        before = ASN.query.count()
    for bad, field in (({'fields': ['size']}, 'spec_capture.fields'), ({'mode': 'every'}, 'spec_capture.mode'),
                       ('weight', 'spec_capture'), ({'fields': 'weight'}, 'spec_capture.fields')):
        response = client.post('/asn/', headers=_h(access_token), json={
            'warehouse_id': 1, 'supplier_id': 1, 'details': [{'goods_id': 1, 'quantity': 1}], 'spec_capture': bad})
        assert response.status_code == 400, bad
        assert (response.get_json()['code'], response.get_json()['field']) == (17020, field)
    with client.application.app_context():
        assert ASN.query.count() == before

    # 修改（pending）：只改给了的键；不传 / null 不变
    asn_id = snapshot['id']
    response = client.put(f'/asn/{asn_id}', headers=_h(access_token), json={'spec_capture': {'mode': 'remeasure'}})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['spec_capture'] == {'fields': ALL, 'mode': 'remeasure'}
    response = client.put(f'/asn/{asn_id}', headers=_h(access_token), json={'remark': 'x', 'spec_capture': None})
    assert response.get_json()['spec_capture'] == {'fields': ALL, 'mode': 'remeasure'}
    response = client.put(f'/asn/{asn_id}', headers=_h(access_token),
                          json={'spec_capture': {'fields': ['dimensions', 'weight']}})
    assert response.get_json()['spec_capture'] == {'fields': ['weight', 'dimensions'], 'mode': 'remeasure'}
    response = client.put(f'/asn/{asn_id}', headers=_h(access_token), json={'spec_capture': {'fields': ['x']}})
    assert response.status_code == 400 and response.get_json()['code'] == 17020


# ---------------------------------------------------------------------------
# 待采集项 / 列表筛选
# ---------------------------------------------------------------------------

def test_pending_missing_mode_and_list_filter(client, access_token):
    # 商品 1：重量 / 尺寸齐全、没有原产国；商品 2：没有重量、尺寸缺高、有原产国
    _set_goods(client, 2, weight=None, height=None, origin_country='CN')
    plain = _create_asn(client, access_token)['id']
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id = body['id']
    lines = _lines(body)
    assert lines[1]['spec_pending'] == ['origin_country']
    assert lines[2]['spec_pending'] == ['weight', 'dimensions']
    assert body['spec_pending_count'] == 2
    weight_only = _create_asn(client, access_token, spec_capture={'fields': ['weight']})['id']

    pending = _listed(client, access_token, spec_pending='true')
    assert asn_id in pending and weight_only in pending and plain not in pending
    assert pending[asn_id]['spec_pending_count'] == 2 and pending[weight_only]['spec_pending_count'] == 1
    assert pending[asn_id]['spec_capture'] == {'fields': ALL, 'mode': 'missing'}
    not_pending = _listed(client, access_token, spec_pending='false')
    assert plain in not_pending and asn_id not in not_pending

    # 商品资料从别处补上（商品编辑）也就不再待采集；补齐商品 2 的重量 → 只剩尺寸
    _set_goods(client, 2, weight=0.5)
    assert _lines(_asn(client, access_token, asn_id))[2]['spec_pending'] == ['dimensions']
    assert _asn(client, access_token, weight_only)['spec_pending_count'] == 0
    assert weight_only not in _listed(client, access_token, spec_pending='true')
    assert weight_only in _listed(client, access_token, spec_pending='false')

    # 关闭的单据没有待采集
    assert client.put(f'/asn/{asn_id}/close/', headers=_h(access_token)).status_code == 200
    closed = _asn(client, access_token, asn_id)
    assert closed['spec_pending_count'] == 0 and all(d['spec_pending'] == [] for d in closed['details'])
    assert asn_id not in _listed(client, access_token, spec_pending='true')


def test_python_and_sql_pending_agree(client, access_token):
    """line_spec_pending（输出）与 pending_condition（列表筛选）同一口径：逐个状态比对"""
    from warehouse.asn.services import ASNService
    _set_goods(client, 2, origin_country='JP')
    ids = [
        _create_asn(client, access_token, spec_capture={'fields': ALL})['id'],                  # 商品 1 缺原产国
        _create_asn(client, access_token, spec_capture={'fields': ['weight', 'dimensions']})['id'],  # 都不缺
        _create_asn(client, access_token, spec_capture={'fields': ['weight'], 'mode': 'remeasure'})['id'],
        _create_asn(client, access_token, spec_capture={'fields': []})['id'],
    ]
    with client.application.app_context():
        remeasure = db.session.get(ASN, ids[2])
        for detail in remeasure.details:
            detail.spec_captured_at = clock_now()
        skipped = db.session.get(ASN, ids[0])
        skipped.details[0].spec_capture_note = 'no label'
        db.session.commit()

    with client.application.app_context():
        expected = {}
        for asn in ASN.query.all():
            expected[asn.id] = any(line_spec_pending(d, asn) for d in asn.details)
        listed = {asn.id for asn in ASNService.list_asns({'spec_pending': True, 'is_active': True})}
        unlisted = {asn.id for asn in ASNService.list_asns({'spec_pending': False, 'is_active': True})}
    assert listed == {asn_id for asn_id, pending in expected.items() if pending}
    assert unlisted == {asn_id for asn_id, pending in expected.items() if not pending}
    assert expected[ids[0]] is False      # 商品 1 跳过了，商品 2 不缺
    assert expected[ids[1]] is False and expected[ids[2]] is False and expected[ids[3]] is False


def clock_now():
    from warehouse.common import clock
    return clock.now()


# ---------------------------------------------------------------------------
# 采集接口
# ---------------------------------------------------------------------------

def test_capture_writes_goods_and_emits_spec_updated(client, access_token):
    with client.application.app_context():
        key_id = _make_key(1)
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id, line = body['id'], _lines(body)[1]

    # 还没收货 → 409 17023
    response = _capture(client, _h(access_token), asn_id, line['id'], {'origin_country': 'CN'})
    assert response.status_code == 409 and response.get_json()['code'] == 17023
    assert response.get_json()['details'] == {'asn_id': asn_id, 'status': 'pending'}

    _receive(client, access_token, asn_id)
    response = _capture(client, _h(access_token), asn_id, line['id'], {
        'weight': '0.2354', 'length': 120, 'width': 80.4, 'height': 45, 'origin_country': ' cn '})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['spec_pending'] == []
    assert data['spec_captured_at'] and data['spec_captured_by'] == 1
    assert data['spec_captured_by_user']['user_name'] == 'admin' and data['spec_capture_note'] is None
    assert (data['goods']['weight'], data['goods']['length'], data['goods']['width'], data['goods']['height'],
            data['goods']['origin_country']) == (0.235, 120, 80, 45, 'CN')
    # 只剩商品 2（没有原产国）
    after = _asn(client, access_token, asn_id)
    assert after['spec_pending_count'] == 1 and _lines(after)[2]['spec_pending'] == ['origin_country']

    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 1
        payload = events[0].payload
        assert payload['source'] == 'receiving'
        assert set(payload['changed_fields']) == {'goods_weight_kg', 'goods_length_mm', 'goods_width_mm',
                                                  'goods_height_mm', 'goods_origin_country'}

    # 称重站显式传 station；没变化不发事件，变了覆盖同一条待发送事件
    response = _capture(client, _h(access_token), asn_id, line['id'], {'weight': 0.235, 'spec_source': 'station'})
    assert response.status_code == 200
    with client.application.app_context():
        assert len(_events(key_id)) == 1 and _events(key_id)[0].payload['source'] == 'receiving'
    _capture(client, _h(access_token), asn_id, line['id'], {'weight': 0.3, 'spec_source': 'station'})
    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 1 and events[0].payload['source'] == 'station'
        assert events[0].payload['goods_weight_kg'] == pytest.approx(0.3)


def test_capture_validation(client, access_token):
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id, line = body['id'], _lines(body)[1]
    _receive(client, access_token, asn_id)
    with client.application.app_context():
        before = db.session.get(Goods, 1)
        before = (float(before.weight), before.length, before.width, before.height, before.origin_country)

    cases = [
        ({}, 400, 17022, None),
        ({'weight': '', 'origin_country': '', 'note': '  '}, 400, 17022, None),
        ({'length': 10, 'width': 10}, 400, 17022, 'dimensions'),
        ({'weight': 0}, 400, 16100, 'weight'),
        ({'weight': 'heavy'}, 400, 16100, 'weight'),
        ({'length': -1, 'width': 1, 'height': 1}, 400, 16100, 'length'),
        ({'origin_country': 'XX', 'weight': 2}, 400, 10014, 'origin_country'),
        ({'note': 123}, 400, 17022, 'note'),
        ({'note': 'x' * 256}, 400, 17022, 'note'),
    ]
    for payload, status, code, field in cases:
        response = _capture(client, _h(access_token), asn_id, line['id'], payload)
        assert (response.status_code, response.get_json()['code']) == (status, code), payload
        assert response.get_json().get('field') == field, payload
    response = client.put(f'/asn/{asn_id}/details/{line["id"]}/spec', headers=_h(access_token), data='[]',
                          content_type='application/json')
    assert response.status_code == 400 and response.get_json()['code'] == 17022

    # 别的单据的明细 → 404
    other = _create_asn(client, access_token)
    other_line = other['details'][0]['id']
    assert _capture(client, _h(access_token), asn_id, other_line, {'weight': 1}).status_code == 404

    with client.application.app_context():
        goods = db.session.get(Goods, 1)
        assert (float(goods.weight), goods.length, goods.width, goods.height, goods.origin_country) == before
        detail = db.session.get(ASNDetail, line['id'])
        assert detail.spec_captured_at is None and detail.spec_capture_note is None


def test_skip_with_reason_then_capture_clears_it(client, access_token):
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id, line = body['id'], _lines(body)[1]
    _receive(client, access_token, asn_id)

    response = _capture(client, _h(access_token), asn_id, line['id'], {'note': '  包装无原产国标识 '})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['spec_pending'] == [] and data['spec_capture_note'] == '包装无原产国标识'
    assert data['spec_captured_at'] is not None
    with client.application.app_context():
        assert db.session.get(Goods, 1).origin_country is None
    assert _asn(client, access_token, asn_id)['spec_pending_count'] == 1      # 只剩商品 2

    # 重新采集（不带原因）= 不再是跳过；商品还缺原产国就仍待采集
    response = _capture(client, _h(access_token), asn_id, line['id'], {'weight': 1.5})
    data = response.get_json()
    assert data['spec_capture_note'] is None and data['spec_pending'] == ['origin_country']
    response = _capture(client, _h(access_token), asn_id, line['id'], {'origin_country': 'jp'})
    assert response.get_json()['spec_pending'] == []


def test_remeasure_mode(client, access_token):
    _set_goods(client, 1, origin_country='CN')
    body = _create_asn(client, access_token, spec_capture={'fields': ALL, 'mode': 'remeasure'})
    asn_id, line = body['id'], _lines(body)[1]
    # 商品资料齐全也要重新测
    assert line['spec_pending'] == ALL
    _receive(client, access_token, asn_id)

    response = _capture(client, _h(access_token), asn_id, line['id'], {'weight': 1.3})
    assert response.status_code == 400 and response.get_json()['code'] == 17022
    assert response.get_json()['details'] == {'missing': ['dimensions', 'origin_country']}
    response = _capture(client, _h(access_token), asn_id, line['id'], {
        'weight': 1.3, 'length': 11, 'width': 6, 'height': 3, 'origin_country': 'CN'})
    assert response.status_code == 200 and response.get_json()['spec_pending'] == []
    # 填了跳过原因可以只测一部分
    line2 = _lines(body)[2]
    response = _capture(client, _h(access_token), asn_id, line2['id'], {'weight': 2, 'note': '无法拆封测量'})
    assert response.status_code == 200 and response.get_json()['spec_pending'] == []
    assert _asn(client, access_token, asn_id)['spec_pending_count'] == 0


@pytest.mark.parametrize('permission,status', [('sorting_edit', 200), ('packing_edit', 200), ('goods_edit', 200),
                                               ('asn_edit', 403), ('asn_receive', 403)])
def test_capture_permissions(client, access_token, permission, status):
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id, line = body['id'], _lines(body)[1]
    _receive(client, access_token, asn_id)
    with client.application.app_context():
        plain = _permission_key(1, [permission])
    response = _capture(client, {'X-API-KEY': plain}, asn_id, line['id'], {'origin_country': 'CN'})
    assert response.status_code == status, response.get_json()
    if status == 200:
        # 没绑用户的 API Key：采集人为空
        assert response.get_json()['spec_captured_by'] is None
        assert response.get_json()['spec_captured_at'] is not None


def test_capture_scoped_to_warehouse(client, access_token):
    body = _create_asn(client, access_token, spec_capture={'fields': ALL})
    asn_id, line = body['id'], _lines(body)[1]
    _receive(client, access_token, asn_id)
    with client.application.app_context():
        warehouse_b = Warehouse(name='Spec B', company_id=1, created_by=1)
        db.session.add(warehouse_b)
        db.session.commit()
        warehouse_b_id = warehouse_b.id
    headers = _staff_token(client, 'spec-sorter', ['sorting_edit', 'asn_read'])
    response = _capture(client, {**headers, 'X-WAREHOUSE-ID': str(warehouse_b_id)}, asn_id, line['id'],
                        {'origin_country': 'CN'})
    assert response.status_code == 403
    response = _capture(client, headers, asn_id, line['id'], {'origin_country': 'CN'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['spec_captured_by_user']['user_name'] == 'spec-sorter'


# ---------------------------------------------------------------------------
# 分拣完成：提示 / 拦截；asn.completed；入库流程
# ---------------------------------------------------------------------------

def test_complete_warn_lists_pending_and_webhook(client, access_token):
    _set_warehouse(client, default_spec_capture_fields=ALL)
    _set_goods(client, 2, origin_country='CN')
    body = _create_asn(client, access_token)
    asn_id = body['id']
    with client.application.app_context():
        api_key = APIKey(key=hash_api_key('spec-asn-webhook'), system_name='spec_asn', permissions=['all_access'])
        api_key.webhook_url = 'http://127.0.0.1:9/webhook'
        db.session.add(api_key)
        db.session.commit()
        db.session.get(ASN, asn_id).api_key_id = api_key.id
        db.session.commit()
        key_id = api_key.id

    task_id = _receive(client, access_token, asn_id)
    _sort(client, access_token, task_id, [{'goods_id': 1, 'sorted_quantity': 5, 'damage_quantity': 0},
                                          {'goods_id': 2, 'sorted_quantity': 3, 'damage_quantity': 0}])
    task = client.get(f'/sorting/{task_id}', headers=_h(access_token)).get_json()
    assert [line['goods_id'] for line in task['spec_pending']] == [1]
    assert task['spec_pending'][0]['spec_pending'] == ['origin_country']
    assert task['asn']['spec_pending_count'] == 1

    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['status'] == 'completed' and data['asn']['status'] == 'completed'
    line_1 = next(line for line in data['asn']['details'] if line['goods_id'] == 1)
    assert data['spec_pending'] == [{'asn_detail_id': line_1['id'], 'goods_id': 1, 'goods_code': 'G001',
                                     'goods_name': 'Sample Goods', 'spec_pending': ['origin_country']}]

    with client.application.app_context():
        event = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='asn.completed').one()
        pending = {d['goods_code']: d['spec_pending'] for d in event.payload['details']}
    assert pending == {'G001': ['origin_country'], 'G002': []}


def test_capture_after_completion(client, access_token):
    """完成后补采：同样写回商品、推 goods.spec_updated、记采集时刻与人；不重推 asn.completed。
    pending / closed 仍 409 17023"""
    _set_warehouse(client, default_spec_capture_fields=ALL)
    _set_goods(client, 2, origin_country='CN')
    with client.application.app_context():
        spec_key = _make_key(1)
        api_key = APIKey(key=hash_api_key('spec-asn-late'), system_name='spec_asn_late', permissions=['all_access'])
        api_key.webhook_url = 'http://127.0.0.1:9/webhook'
        db.session.add(api_key)
        db.session.commit()
        asn_key = api_key.id
    body = _create_asn(client, access_token)
    asn_id = body['id']
    with client.application.app_context():
        db.session.get(ASN, asn_id).api_key_id = asn_key
        db.session.commit()
    task_id = _receive(client, access_token, asn_id)
    _sort(client, access_token, task_id, [{'goods_id': 1, 'sorted_quantity': 5, 'damage_quantity': 0},
                                          {'goods_id': 2, 'sorted_quantity': 3, 'damage_quantity': 0}])
    assert client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token)).status_code == 200
    completed = _asn(client, access_token, asn_id)
    assert completed['status'] == 'completed' and completed['spec_pending_count'] == 1
    assert asn_id in _listed(client, access_token, spec_pending='true')
    line_1 = _lines(completed)[1]

    # 补采：员工账号（sorting_edit）
    headers = _staff_token(client, 'late-sorter', ['sorting_edit', 'asn_read'])
    response = _capture(client, headers, asn_id, line_1['id'], {'origin_country': 'vn'})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['spec_pending'] == [] and data['goods']['origin_country'] == 'VN'
    assert data['spec_captured_at'] is not None
    assert data['spec_captured_by_user']['user_name'] == 'late-sorter'
    after = _asn(client, access_token, asn_id)
    assert after['status'] == 'completed' and after['spec_pending_count'] == 0
    assert asn_id not in _listed(client, access_token, spec_pending='true')

    with client.application.app_context():
        events = _events(spec_key)
        assert len(events) == 1
        assert events[0].payload['source'] == 'receiving'
        assert events[0].payload['changed_fields'] == ['goods_origin_country']
        assert events[0].payload['goods_origin_country'] == 'VN'
        # asn.completed 只在完成时推过一次
        assert WebhookEvent.query.filter_by(api_key_id=asn_key, event_type='asn.completed').count() == 1

    # 完成后跳过同样可以
    line_2 = _lines(after)[2]
    response = _capture(client, _h(access_token), asn_id, line_2['id'], {'note': '商品破损'})
    assert response.status_code == 200 and response.get_json()['spec_capture_note'] == '商品破损'

    # pending / closed 仍不能采集
    pending = _create_asn(client, access_token)
    response = _capture(client, _h(access_token), pending['id'], pending['details'][0]['id'], {'origin_country': 'CN'})
    assert (response.status_code, response.get_json()['code']) == (409, 17023)
    assert response.get_json()['details'] == {'asn_id': pending['id'], 'status': 'pending'}
    assert client.put(f"/asn/{pending['id']}/close/", headers=_h(access_token)).status_code == 200
    response = _capture(client, _h(access_token), pending['id'], pending['details'][0]['id'], {'origin_country': 'CN'})
    assert (response.status_code, response.get_json()['code']) == (409, 17023)
    assert response.get_json()['details'] == {'asn_id': pending['id'], 'status': 'closed'}


def test_complete_block_until_captured_or_skipped(client, access_token):
    _set_warehouse(client, default_spec_capture_fields=ALL, spec_capture_enforcement='block')
    _set_goods(client, 2, weight=None)
    body = _create_asn(client, access_token, details=[{'goods_id': 1, 'quantity': 5}, {'goods_id': 2, 'quantity': 3}])
    asn_id, lines = body['id'], _lines(body)
    task_id = _receive(client, access_token, asn_id)
    # 商品 2 一件都没到：不拦它
    _sort(client, access_token, task_id, [{'goods_id': 1, 'sorted_quantity': 4, 'damage_quantity': 1}])

    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 409, response.get_json()
    error = response.get_json()
    assert error['code'] == 17021 and error['message'].isascii()
    assert error['details'] == {'asn_id': asn_id, 'enforcement': 'block', 'lines': [
        {'asn_detail_id': lines[1]['id'], 'goods_id': 1, 'goods_code': 'G001', 'goods_name': 'Sample Goods',
         'spec_pending': ['origin_country']}]}
    with client.application.app_context():
        assert db.session.get(SortingTask, task_id).status == 'in_progress'
        assert db.session.get(ASN, asn_id).status == 'received'

    # 流程页：分拣一步列出会拦的行
    flow = client.get(f'/asn/{asn_id}/flow', headers=_h(access_token)).get_json()
    spec = next(step for step in flow['steps'] if step['key'] == 'sorting')['summary']['spec_capture']
    assert spec == {'fields': ALL, 'mode': 'missing', 'enforcement': 'block', 'pending_count': 2,
                    'sorted_pending': error['details']['lines']}

    # 跳过并填原因 → 放行
    response = _capture(client, _h(access_token), asn_id, lines[1]['id'], {'note': '包装无原产国标识'})
    assert response.status_code == 200
    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['spec_pending'] == []
    # 没到货的商品 2 完成后不再算待采集
    detail_2 = next(line for line in data['asn']['details'] if line['goods_id'] == 2)
    assert detail_2['actual_quantity'] == 0 and detail_2['spec_pending'] == []
    assert data['asn']['spec_pending_count'] == 0


def test_complete_block_passes_after_capture(client, access_token):
    _set_warehouse(client, default_spec_capture_fields=['origin_country'], spec_capture_enforcement='block')
    body = _create_asn(client, access_token, details=[{'goods_id': 1, 'quantity': 2}])
    asn_id, line = body['id'], body['details'][0]
    task_id = _receive(client, access_token, asn_id)
    _sort(client, access_token, task_id, [{'goods_id': 1, 'sorted_quantity': 2, 'damage_quantity': 0}])
    assert client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token)).status_code == 409
    assert _capture(client, _h(access_token), asn_id, line['id'], {'origin_country': 'VN'}).status_code == 200
    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()


def test_no_capture_requested_completes_as_before(client, access_token):
    """仓库默认不采集、block 也不影响（没有采集项就没有待采集）"""
    _set_warehouse(client, spec_capture_enforcement='block')
    body = _create_asn(client, access_token, details=[{'goods_id': 1, 'quantity': 1}])
    task_id = _receive(client, access_token, body['id'])
    _sort(client, access_token, task_id, [{'goods_id': 1, 'sorted_quantity': 1, 'damage_quantity': 0}])
    response = client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['spec_pending'] == []


# ---------------------------------------------------------------------------
# 商品列表：image_missing / spec_missing
# ---------------------------------------------------------------------------

def test_goods_list_image_and_spec_missing_filters(client, access_token):
    _set_goods(client, 1, image_url='https://example.com/g1.png', origin_country='CN')
    _set_goods(client, 2, height=None)

    def codes(query):
        response = client.get(f'/goods/?page=1&per_page=100&{query}', headers=_h(access_token))
        assert response.status_code == 200, response.get_json()
        return {item['code'] for item in response.get_json()['items']}

    assert codes('image_missing=true') == {'G002'}
    assert codes('image_missing=false') == {'G001'}
    assert codes('spec_missing=dimensions') == {'G002'}
    assert codes('spec_missing=origin_country') == {'G002'}
    assert codes('spec_missing=weight') == set()
    assert codes('spec_missing=weight,origin_country') == {'G002'}
    _set_goods(client, 1, weight=None)
    assert codes('spec_missing=weight') == {'G001'}
    assert codes('spec_missing=weight,dimensions') == {'G001', 'G002'}
    assert codes('spec_missing=weight&image_missing=true') == set()

    response = client.get('/goods/?spec_missing=weight,colour', headers=_h(access_token))
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 一次性设置脚本 scripts/set_warehouse_spec_capture.py（只测函数，不跑 main：main 用的是生产配置）
# ---------------------------------------------------------------------------

def test_set_warehouse_spec_capture_script_helpers(client):
    from extensions.error import BadRequestException
    from scripts.set_warehouse_spec_capture import apply_settings, find_warehouses
    with client.application.app_context():
        assert find_warehouses('Kyoto#1') == []
        assert [w.id for w in find_warehouses('Warehouse A', company_id=1)] == [1]
        assert find_warehouses('Warehouse A', company_id=2) == []
        warehouse = find_warehouses(warehouse_id=1)[0]

        # dry-run：只算出要改成什么，不写库
        before, after = apply_settings(warehouse, ['origin_country', 'weight', 'dimensions'], 'missing', 'warn')
        assert before == {'default_spec_capture_fields': [], 'default_spec_capture_mode': 'missing',
                          'spec_capture_enforcement': 'warn'}
        assert after == {'default_spec_capture_fields': ALL, 'default_spec_capture_mode': 'missing',
                         'spec_capture_enforcement': 'warn'}
        assert db.session.get(Warehouse, 1).default_spec_capture_fields == []

        apply_settings(find_warehouses(warehouse_id=1)[0], ALL, 'missing', 'warn', apply=True)
        stored = db.session.get(Warehouse, 1)
        assert (stored.default_spec_capture_fields, stored.default_spec_capture_mode,
                stored.spec_capture_enforcement) == (ALL, 'missing', 'warn')
        with pytest.raises(BadRequestException):
            apply_settings(stored, ['size'], 'missing', 'warn', apply=True)
