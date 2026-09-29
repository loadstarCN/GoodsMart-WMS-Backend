"""商品原产国 + goods.spec_updated 广播事件

- 原产国：大写化、ISO 校验（10014）、空串清空、列表 origin_missing 筛选、CSV 导入
- goods.spec_updated：规格 / 原产国有变化才发；只发给同公司、启用、配了 URL 且订阅了的 Key；
  同一商品待发送的事件被新 payload 覆盖；来源 spec_source
- 精简商品模型带规格与原产国；asn.completed 明细带 goods_origin_country
- API Key 的 webhook_subscriptions 白名单
"""
from .helpers import *  # 先导入：helpers 里的 `import datetime` 会覆盖同名对象

import hashlib
import hmac
import io
import json
import uuid
from datetime import datetime
from unittest import mock

import pytest
from flask_restx import marshal

from system.webhook.models import WebhookEvent
from system.webhook.services import emit_company_event, push_pending_events
from warehouse.goods.schemas import goods_simple_model

EVENT = 'goods.spec_updated'


def _make_key(company_id, *, subscriptions=(EVENT,), url='http://127.0.0.1:9/webhook',
              active=True, secret=None, name=None):
    """直接落库一个 API Key（emit 只落库，不实际发送）"""
    key = APIKey(
        key=hash_api_key(str(uuid.uuid4())),
        system_name=name or f'spec-test-{uuid.uuid4().hex[:6]}',
        permissions=['all_access'],
    )
    key.company_id = company_id
    key.webhook_url = url
    key.webhook_secret = secret
    key.is_active = active
    key.webhook_subscriptions = list(subscriptions)
    db.session.add(key)
    db.session.commit()
    return key.id


def _events(api_key_id=None):
    query = WebhookEvent.query.filter_by(event_type=EVENT)
    if api_key_id is not None:
        query = query.filter_by(api_key_id=api_key_id)
    return query.order_by(WebhookEvent.id).all()


@pytest.fixture
def company_ids(client):
    with client.application.app_context():
        a = Company.query.filter_by(name='Company A').first()
        b = Company.query.filter_by(name='Company B').first()
        return a.id, b.id


@pytest.fixture
def goods_id(client):
    with client.application.app_context():
        return Goods.query.filter_by(code='G001').first().id


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


# ---------------------------------------------------------------------------
# 原产国：校验 / 规范化 / 筛选
# ---------------------------------------------------------------------------

def test_origin_country_normalized_and_cleared(client, access_token, goods_id):
    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': ' cn '})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['origin_country'] == 'CN'

    # 空串 = 清空（不回落任何默认值）
    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': ''})
    assert response.status_code == 200
    assert response.get_json()['origin_country'] is None

    # 不传 = 不动
    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': 'JP'})
    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'name': 'Renamed'})
    assert response.get_json()['origin_country'] == 'JP'


@pytest.mark.parametrize('bad', ['XX', 'CHN', 'Japan', 123, ['CN']])
def test_invalid_origin_country_rejected(client, access_token, goods_id, company_ids, bad):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token),
                          json={'origin_country': bad, 'weight': 9.9})
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 10014
    assert body.get('field') == 'origin_country'

    response = client.post('/goods/', headers=_auth(access_token), json={
        'company_id': company_ids[0], 'code': 'NEW-BAD', 'name': 'Bad origin', 'origin_country': bad,
    })
    assert response.status_code == 400
    assert response.get_json()['code'] == 10014

    with client.application.app_context():
        # 整个请求回滚：规格没改、没有事件、商品没建
        assert float(db.session.get(Goods, goods_id).weight) == pytest.approx(1.2)
        assert _events(key_id) == []
        assert Goods.query.filter_by(code='NEW-BAD').first() is None


def test_partial_update_keeps_other_fields(client, access_token, goods_id):
    """PDA 只传 origin_country、称重站只传规格 + spec_source：其余字段一律不动"""
    with client.application.app_context():
        before = marshal(db.session.get(Goods, goods_id), goods_simple_model)

    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': 'CN'})
    assert response.status_code == 200
    data = response.get_json()
    for field in ('code', 'name', 'weight', 'length', 'width', 'height', 'manufacturer'):
        assert data[field] == before[field], field
    assert data['is_active'] is True

    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={
        'weight': 0.25, 'length': 120, 'width': 80, 'height': 45, 'spec_source': 'station',
    })
    assert response.status_code == 200
    data = response.get_json()
    assert (data['weight'], data['length'], data['width'], data['height']) == (0.25, 120, 80, 45)
    assert data['origin_country'] == 'CN'
    assert data['name'] == before['name']
    assert 'spec_source' not in data


def test_create_goods_without_origin_has_no_default(client, access_company_admin_token):
    response = client.post('/goods/', headers=_auth(access_company_admin_token), json={
        'code': 'NO-ORIGIN', 'name': 'No origin',
    })
    assert response.status_code == 201
    assert response.get_json()['origin_country'] is None


def test_origin_missing_filter(client, access_token, goods_id):
    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': 'CN'})

    response = client.get('/goods/?page=1&per_page=50&origin_missing=true', headers=_auth(access_token))
    assert response.status_code == 200
    codes = {item['code'] for item in response.get_json()['items']}
    assert 'G001' not in codes
    assert 'G002' in codes

    response = client.get('/goods/?page=1&per_page=50&origin_missing=false', headers=_auth(access_token))
    items = response.get_json()['items']
    assert [item['code'] for item in items] == ['G001']
    assert items[0]['origin_country'] == 'CN'


# ---------------------------------------------------------------------------
# goods.spec_updated
# ---------------------------------------------------------------------------

def test_spec_change_emits_only_to_subscribed_keys_of_same_company(client, access_token, goods_id, company_ids):
    company_a, company_b = company_ids
    with client.application.app_context():
        subscribed = _make_key(company_a, name='subscribed')
        not_subscribed = _make_key(company_a, subscriptions=())
        no_url = _make_key(company_a, url=None)
        empty_url = _make_key(company_a, url='')
        inactive = _make_key(company_a, active=False)
        other_company = _make_key(company_b)
        platform_key = _make_key(None)  # 未绑定公司的平台 Key 不收公司事件

    response = client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={
        'weight': 0.235, 'origin_country': 'cn',
    })
    assert response.status_code == 200, response.get_json()

    with client.application.app_context():
        events = _events()
        assert [e.api_key_id for e in events] == [subscribed]
        for key_id in (not_subscribed, no_url, empty_url, inactive, other_company, platform_key):
            assert _events(key_id) == []

        event = events[0]
        assert event.status == 'pending'
        assert event.dedupe_key == f'goods:{goods_id}'
        payload = event.payload
        assert payload['goods_code'] == 'G001'
        assert payload['goods_id'] == goods_id
        assert payload['goods_weight_kg'] == 0.235
        assert isinstance(payload['goods_weight_kg'], float)
        assert payload['goods_length_mm'] == 10
        assert payload['goods_width_mm'] == 5
        assert payload['goods_height_mm'] == 2
        assert payload['goods_origin_country'] == 'CN'
        assert payload['changed_fields'] == ['goods_weight_kg', 'goods_origin_country']
        assert payload['source'] == 'manual'  # 登录用户
        changed_at = datetime.fromisoformat(payload['changed_at'])
        assert changed_at.tzinfo is not None  # 带时区偏移
        json.dumps(payload)  # 能序列化（没有 Decimal）


def test_no_spec_change_no_event(client, access_token, goods_id, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    # 只改非规格字段
    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'name': 'Only name', 'price': 100})
    # 规格值与现有相同（1.2 kg / 10×5×2 mm），类型不同也算没变
    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={
        'weight': '1.200', 'length': 10.0, 'width': 5, 'height': 2,
    })
    # 原产国本来就空，再清一次也算没变
    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': ''})

    with client.application.app_context():
        assert _events(key_id) == []


def test_pending_event_is_overwritten_then_new_after_sent(client, access_token, goods_id, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'weight': 0.5})
    client.put(f'/goods/{goods_id}', headers=_auth(access_token),
               json={'origin_country': 'VN', 'spec_source': 'station'})

    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 1  # 覆盖，不新增
        payload = events[0].payload
        assert payload['goods_weight_kg'] == 0.5
        assert payload['goods_origin_country'] == 'VN'
        # 旧事件还没送达：变动字段取并集；其余以新 payload 为准
        assert payload['changed_fields'] == ['goods_weight_kg', 'goods_origin_country']
        assert payload['source'] == 'station'

        events[0].status = 'sent'
        db.session.commit()

    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'height': 30})

    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 2
        assert events[1].status == 'pending'
        assert events[1].payload['changed_fields'] == ['goods_height_mm']
        assert events[1].payload['goods_height_mm'] == 30


def test_failed_event_is_not_overwritten(client, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        emit_company_event(EVENT, {'n': 1}, company_ids[0], dedupe_key='goods:1')
        db.session.commit()
        first = _events(key_id)[0]
        first.status = 'failed'
        db.session.commit()

        emit_company_event(EVENT, {'n': 2}, company_ids[0], dedupe_key='goods:1')
        # 不同 dedupe_key 各自独立
        emit_company_event(EVENT, {'n': 3}, company_ids[0], dedupe_key='goods:2')
        db.session.commit()

        events = _events(key_id)
        assert [(e.status, e.payload['n'], e.dedupe_key) for e in events] == [
            ('failed', 1, 'goods:1'), ('pending', 2, 'goods:1'), ('pending', 3, 'goods:2'),
        ]


def test_spec_source_defaults_to_api_for_api_key(client, goods_id, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    response = client.put(f'/goods/{goods_id}', headers={'X-API-KEY': TEST_API_KEY_PLAIN},
                          json={'weight': 0.8})
    assert response.status_code == 200, response.get_json()
    # 不认识的 spec_source 退回默认推断
    response = client.put(f'/goods/{goods_id}', headers={'X-API-KEY': TEST_API_KEY_PLAIN},
                          json={'width': 7, 'spec_source': 'robot'})
    assert response.status_code == 200

    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 1
        assert events[0].payload['source'] == 'api'
        assert events[0].payload['changed_fields'] == ['goods_weight_kg', 'goods_width_mm']


def test_create_goods_with_spec_emits_event(client, access_company_admin_token, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    # 不带规格 → 不发
    response = client.post('/goods/', headers=_auth(access_company_admin_token), json={
        'code': 'NEW-PLAIN', 'name': 'Plain',
    })
    assert response.status_code == 201

    response = client.post('/goods/', headers=_auth(access_company_admin_token), json={
        'code': 'NEW-SPEC', 'name': 'With spec', 'weight': 0.12, 'origin_country': 'th',
        'spec_source': 'station',
    })
    assert response.status_code == 201
    new_id = response.get_json()['id']
    assert response.get_json()['origin_country'] == 'TH'

    with client.application.app_context():
        events = _events(key_id)
        assert len(events) == 1
        payload = events[0].payload
        assert payload['goods_id'] == new_id
        assert payload['goods_code'] == 'NEW-SPEC'
        assert payload['changed_fields'] == ['goods_weight_kg', 'goods_origin_country']
        assert payload['goods_length_mm'] is None
        assert payload['source'] == 'station'
        assert events[0].dedupe_key == f'goods:{new_id}'


def test_company_admin_update_only_reaches_own_company(client, access_company_admin_token, goods_id, company_ids):
    with client.application.app_context():
        mine = _make_key(company_ids[0])
        theirs = _make_key(company_ids[1])

    response = client.put(f'/goods/{goods_id}', headers=_auth(access_company_admin_token),
                          json={'length': 99})
    assert response.status_code == 200

    with client.application.app_context():
        assert len(_events(mine)) == 1
        assert _events(theirs) == []


def test_spec_updated_event_is_pushed_and_signed(client, access_token, goods_id, company_ids):
    """事件表没有单据关联也能照常推送：请求头、V1/V2 签名、状态流转与单据类事件相同"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0], secret='spec-secret')

    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'origin_country': 'KR'})

    with client.application.app_context():
        event = _events(key_id)[0]
        event_id = event.id
        with mock.patch('system.webhook.services.requests.post') as post:
            post.return_value = mock.Mock(status_code=200, raise_for_status=mock.Mock())
            sent, failed = push_pending_events()
        assert (sent, failed) == (1, 0)

        kwargs = post.call_args.kwargs
        headers = kwargs['headers']
        body = kwargs['data']
        assert headers['X-Webhook-Event'] == EVENT
        assert headers['X-Webhook-Id'] == str(event_id)
        expected_v1 = hmac.new(b'spec-secret', body, hashlib.sha256).hexdigest()
        assert headers['X-Webhook-Signature'] == f'sha256={expected_v1}'
        signed_v2 = f"{event_id}.{headers['X-Webhook-Timestamp']}.".encode() + body
        expected_v2 = hmac.new(b'spec-secret', signed_v2, hashlib.sha256).hexdigest()
        assert headers['X-Webhook-Signature-V2'] == f'sha256={expected_v2}'
        assert json.loads(body)['goods_origin_country'] == 'KR'

        event = db.session.get(WebhookEvent, event_id)
        assert event.status == 'sent'


def test_spec_updated_push_failure_retries(client, access_token, goods_id, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    client.put(f'/goods/{goods_id}', headers=_auth(access_token), json={'weight': 2})

    with client.application.app_context():
        with mock.patch('system.webhook.services.requests.post', side_effect=ConnectionError('down')):
            sent, failed = push_pending_events()
        assert (sent, failed) == (0, 1)
        event = _events(key_id)[0]
        assert event.status == 'pending'
        assert event.attempts == 1
        assert event.next_retry_at is not None


# ---------------------------------------------------------------------------
# CSV 导入
# ---------------------------------------------------------------------------

def _upload(client, token, csv_text, overwrite='skip', company_id=1):
    return client.post('/goods/bulk_upload', headers=_auth(token), data={
        'file': (io.BytesIO(csv_text.encode('utf-8')), 'goods.csv'),
        'company_id': str(company_id),
        'overwrite': overwrite,
    }, content_type='multipart/form-data')


def test_csv_import_origin_country(client, access_token, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    # 新商品：原产国大写化，发 import 来源的事件
    response = _upload(client, access_token, "code,name,origin_country\nC001,Csv One,cn\nC002,Csv Two,\n")
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        assert Goods.query.filter_by(code='C001').first().origin_country == 'CN'
        assert Goods.query.filter_by(code='C002').first().origin_country is None
        events = _events(key_id)
        assert len(events) == 1
        assert events[0].payload['goods_code'] == 'C001'
        assert events[0].payload['source'] == 'import'

    # 不合法 → 400 10014，整批不落库
    response = _upload(client, access_token, "code,name,origin_country\nC003,Csv Three,ZZ\n")
    assert response.status_code == 400
    assert response.get_json()['code'] == 10014
    with client.application.app_context():
        assert Goods.query.filter_by(code='C003').first() is None


def test_csv_import_origin_country_existing_goods(client, access_token, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])

    # append：只补空白
    response = _upload(client, access_token, "code,name,origin_country\nG001,Sample Goods,cn\n", overwrite='append')
    assert response.status_code == 200, response.get_json()
    response = _upload(client, access_token, "code,name,origin_country\nG001,Sample Goods,vn\n", overwrite='append')
    assert response.status_code == 200
    with client.application.app_context():
        assert Goods.query.filter_by(code='G001').first().origin_country == 'CN'

    # override：非空覆盖；空值不清空
    response = _upload(client, access_token, "code,name,origin_country\nG001,Sample Goods,vn\n", overwrite='override')
    assert response.status_code == 200
    response = _upload(client, access_token, "code,name,origin_country\nG001,Sample Goods,\n", overwrite='override')
    assert response.status_code == 200
    response = _upload(client, access_token, "code,name\nG001,Sample Goods\n", overwrite='override')
    assert response.status_code == 200

    with client.application.app_context():
        assert Goods.query.filter_by(code='G001').first().origin_country == 'VN'
        events = _events(key_id)
        # CN（append）与 VN（override）两次变更合并在同一条待发送事件里
        assert len(events) == 1
        assert events[0].payload['goods_origin_country'] == 'VN'
        assert events[0].payload['changed_fields'] == ['goods_origin_country']
        assert events[0].payload['source'] == 'import'


# ---------------------------------------------------------------------------
# 精简商品模型 / asn.completed
# ---------------------------------------------------------------------------

def test_goods_simple_model_includes_spec_and_origin(client, goods_id):
    with client.application.app_context():
        goods = db.session.get(Goods, goods_id)
        goods.origin_country = 'CN'
        db.session.commit()
        data = marshal(goods, goods_simple_model)
        assert data['weight'] == pytest.approx(1.2)
        assert data['length'] == 10
        assert data['width'] == 5
        assert data['height'] == 2
        assert data['origin_country'] == 'CN'


def test_sorting_task_nested_goods_includes_spec(client, access_token):
    """称重站从分拣任务的 asn.details[].goods 读已有规格与原产国"""
    with client.application.app_context():
        task = get_sorting_task()
        task_id = task.id
        goods = task.asn.details[0].goods
        goods.origin_country = 'CN'
        db.session.commit()
        goods_id = goods.id

    response = client.get(f'/sorting/{task_id}', headers=_auth(access_token))
    assert response.status_code == 200, response.get_json()
    nested = next(d['goods'] for d in response.get_json()['asn']['details'] if d['goods_id'] == goods_id)
    assert nested['origin_country'] == 'CN'
    assert nested['weight'] == pytest.approx(1.2)
    assert nested['length'] == 10
    assert nested['width'] == 5
    assert nested['height'] == 2


def test_asn_completed_includes_goods_origin_country(client):
    from warehouse.asn.services import ASNService

    with client.application.app_context():
        api_key = APIKey(key=hash_api_key('wh-origin-asn'), system_name='asn_origin_test',
                         permissions=['all_access'])
        api_key.webhook_url = 'http://127.0.0.1:9/webhook'
        db.session.add(api_key)
        db.session.commit()

        asn = get_asn()
        goods = asn.details[0].goods
        goods.origin_country = 'CN'
        db.session.commit()
        goods_code = goods.code
        ASNService.receive_asn(asn.id)
        asn.api_key_id = api_key.id
        db.session.commit()

        ASNService.complete_asn(asn.id)

        event = WebhookEvent.query.filter_by(api_key_id=api_key.id, event_type='asn.completed').first()
        detail = next(d for d in event.payload['details'] if d['goods_code'] == goods_code)
        assert detail['goods_origin_country'] == 'CN'
        others = [d for d in event.payload['details'] if d['goods_code'] != goods_code]
        for d in others:
            assert 'goods_origin_country' in d


# ---------------------------------------------------------------------------
# API Key 订阅
# ---------------------------------------------------------------------------

def test_api_key_webhook_subscriptions(client, access_token):
    headers = _auth(access_token)
    response = client.post('/api-keys/api-keys', headers=headers, json={
        'system_name': 'Subscriber', 'company_id': 1,
        'webhook_subscriptions': ['goods.spec_updated', 'goods.spec_updated'],
    })
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['webhook_subscriptions'] == ['goods.spec_updated']
    key_id = data['id']

    # 不传 = 默认空列表
    response = client.post('/api-keys/api-keys', headers=headers, json={'system_name': 'Plain'})
    assert response.status_code == 201
    assert response.get_json()['webhook_subscriptions'] == []

    # 只允许白名单事件
    for bad in (['dn.delivered'], ['goods.*'], 'goods.spec_updated', [1]):
        response = client.put(f'/api-keys/api-keys/{key_id}', headers=headers,
                              json={'webhook_subscriptions': bad})
        assert response.status_code == 400, bad
        assert response.get_json()['code'] == 14018
    response = client.post('/api-keys/api-keys', headers=headers, json={
        'system_name': 'Bad', 'webhook_subscriptions': ['asn.completed'],
    })
    assert response.status_code == 400

    # 修改时不传 = 保持不变
    response = client.put(f'/api-keys/api-keys/{key_id}', headers=headers, json={'system_name': 'Renamed'})
    assert response.status_code == 200
    assert response.get_json()['webhook_subscriptions'] == ['goods.spec_updated']

    # 取消订阅
    response = client.put(f'/api-keys/api-keys/{key_id}', headers=headers, json={'webhook_subscriptions': []})
    assert response.get_json()['webhook_subscriptions'] == []
    response = client.get(f'/api-keys/api-keys/{key_id}', headers=headers)
    assert response.get_json()['webhook_subscriptions'] == []
