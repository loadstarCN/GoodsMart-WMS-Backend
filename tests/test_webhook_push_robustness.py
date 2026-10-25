"""Webhook 推送的健壮性（审计 B-59 / B-60，顺带 B-90 / B-91 / B-92）

- 单次推送有总时限（滴流的响应头 / 正文都会被中止），响应正文最多读 64KB、不解压
- 三段式：领取（sending + 租约）提交后才发 HTTP，发送期间不持锁、不开事务；租约过期的 sending 可重新领取
- 公平调度：每 Key 每批限额、单据事件优先、各 Key 轮流；按时间预算循环；连续连不上 / 超时熔断
- 推送失败过的事件不再被合并覆盖（同一 X-Webhook-Id 不会先后带不同 payload），另建新事件、旧事件作废
- Key 停用 / 取消订阅后已排队的事件作废，不再发送

HTTP 一律用 mock 或本机 127.0.0.1 上的临时接收端，不访问外网。
"""
from .helpers import *  # 先导入：helpers 里的 `import datetime` 会覆盖同名对象

import gzip
import io
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import requests
from sqlalchemy import text

from system.webhook import services
from system.webhook.models import WebhookEvent
from system.webhook.services import (
    emit, emit_company_event, emit_company_events, push_pending_events, retry_event_now,
)

EVENT = 'goods.spec_updated'
POST = 'system.webhook.services.requests.post'


@pytest.fixture(autouse=True)
def _clear_stuck_senders():
    # 内存库每个用例重建，Key 的 id 会复用：卡住线程的登记不能串到下一个用例
    services._stuck_senders.clear()
    yield
    services._stuck_senders.clear()


@pytest.fixture
def company_ids(client):
    with client.application.app_context():
        a = Company.query.filter_by(name='Company A').first()
        b = Company.query.filter_by(name='Company B').first()
        return a.id, b.id


def _make_key(company_id, *, url='http://127.0.0.1:9/webhook', subscriptions=(EVENT,), active=True):
    key = APIKey(
        key=hash_api_key(str(uuid.uuid4())),
        system_name=f'push-test-{uuid.uuid4().hex[:6]}',
        permissions=['all_access'],
    )
    key.company_id = company_id
    key.webhook_url = url
    key.webhook_secret = 'push-secret'
    key.is_active = active
    key.webhook_subscriptions = list(subscriptions)
    db.session.add(key)
    db.session.commit()
    return key.id


def _add_event(key_id, event_type='dn.delivered', payload=None, **fields):
    event = WebhookEvent(api_key_id=key_id, event_type=event_type, payload=payload or {'dn_id': 1},
                         status=fields.pop('status', 'pending'), attempts=fields.pop('attempts', 0), **fields)
    db.session.add(event)
    db.session.commit()
    return event.id


def _events(key_id, event_type=None):
    query = WebhookEvent.query.filter_by(api_key_id=key_id)
    if event_type:
        query = query.filter_by(event_type=event_type)
    return query.order_by(WebhookEvent.id).all()


def _response(status=200, body=b'ok'):
    resp = requests.models.Response()
    resp.status_code = status
    resp.raw = io.BytesIO(body)
    resp.url = 'http://receiver.test/webhook'
    return resp


def _recorder(calls, status=200):
    """requests.post 的替身：记下 (url, headers, payload)，回固定状态码"""
    def post(url, data=None, headers=None, **kwargs):
        calls.append((url, headers, json.loads(data)))
        return _response(status)
    return post


# ---------------------------------------------------------------------------
# 本机临时 HTTP 接收端
# ---------------------------------------------------------------------------

def _read_request(conn):
    data = b''
    while b'\r\n\r\n' not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return
        data += chunk
    head, _, body = data.partition(b'\r\n\r\n')
    length = 0
    for line in head.split(b'\r\n')[1:]:
        name, _, value = line.partition(b':')
        if name.strip().lower() == b'content-length':
            length = int(value)
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            return
        body += chunk


class _StubServer:
    """127.0.0.1 上的一次性接收端：每个连接读完请求后交给 respond(server, conn) 回应"""

    def __init__(self, respond):
        self.respond = respond
        self.stop = threading.Event()
        self.sent_bytes = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(8)
        self._sock.settimeout(0.1)
        self.url = f'http://127.0.0.1:{self._sock.getsockname()[1]}/webhook'
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(5)
                try:
                    _read_request(conn)
                    self.respond(self, conn)
                except OSError:
                    pass   # 客户端已断开

    def close(self):
        self.stop.set()
        self._thread.join(5)
        self._sock.close()


def _drip_body(server, conn):
    """回 200 头后每 0.2 秒滴 1 字节正文（每次间隔都小于读超时）"""
    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n')
    while not server.stop.wait(0.2):
        conn.sendall(b'1\r\nx\r\n')


def _drip_headers(server, conn):
    """响应头一个字节一个字节地滴（requests 在收齐响应头之前不会返回）"""
    conn.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ')
    while not server.stop.wait(0.2):
        conn.sendall(b'a')


HUGE_BODY_BYTES = 100 * 1024 * 1024


def _huge_body(server, conn):
    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n'
                 b'Content-Length: %d\r\n\r\n' % HUGE_BODY_BYTES)
    block = b'x' * 65536
    while server.sent_bytes < HUGE_BODY_BYTES and not server.stop.is_set():
        conn.sendall(block)
        server.sent_bytes += len(block)


@pytest.fixture
def short_deadline(monkeypatch):
    """总时限缩到 1.5 秒、读超时 1 秒，让用例跑得快"""
    monkeypatch.setattr(services, 'SEND_DEADLINE_SECONDS', 1.5)
    monkeypatch.setattr(services, 'READ_TIMEOUT_SECONDS', 1)
    monkeypatch.setattr(services, 'CONNECT_TIMEOUT_SECONDS', 1)


@pytest.fixture
def read_sizes(monkeypatch):
    """记录每次推送实际读了多少字节响应正文"""
    sizes = []
    real = services._read_limited

    def spy(resp, deadline):
        size = real(resp, deadline)
        sizes.append(size)
        return size
    monkeypatch.setattr(services, '_read_limited', spy)
    return sizes


# ---------------------------------------------------------------------------
# B-59：总时限 / 正文上限 / 不持锁 / 租约
# ---------------------------------------------------------------------------

def test_slow_drip_body_is_aborted_within_deadline(client, company_ids, short_deadline):
    server = _StubServer(_drip_body)
    try:
        with client.application.app_context():
            key_id = _make_key(company_ids[0], url=server.url)
            event_id = _add_event(key_id)

            started = time.monotonic()
            assert push_pending_events() == (0, 1)
            assert time.monotonic() - started < 1.5 + 1.0

            event = db.session.get(WebhookEvent, event_id)
            assert (event.status, event.attempts) == ('pending', 1)
            assert event.last_error == 'WebhookDeliveryTimeout'
            assert event.next_retry_at is not None and event.lease_expires_at is None
    finally:
        server.close()


def test_slow_drip_headers_are_aborted_and_receiver_not_hit_again(client, company_ids, short_deadline):
    """卡在收响应头的请求打断不了：本线程照样按时返回；该线程退出前同一 Key 不再开新请求"""
    server = _StubServer(_drip_headers)
    try:
        with client.application.app_context():
            key_id = _make_key(company_ids[0], url=server.url)
            first = _add_event(key_id)
            others = [_add_event(key_id) for _ in range(2)]

            started = time.monotonic()
            assert push_pending_events() == (0, 1)
            assert time.monotonic() - started < 1.5 + 1.0

            assert db.session.get(WebhookEvent, first).last_error == 'WebhookDeliveryTimeout'
            for event_id in others:   # 没被领取，不耗次数
                event = db.session.get(WebhookEvent, event_id)
                assert (event.status, event.attempts) == ('pending', 0)
            assert services._sender_stuck(key_id)
    finally:
        server.close()
    services._stuck_senders[key_id].join(5)   # 接收端断开后发送线程自行退出
    assert not services._sender_stuck(key_id)


def test_huge_response_reads_only_first_64kb(client, company_ids, read_sizes):
    server = _StubServer(_huge_body)
    try:
        with client.application.app_context():
            key_id = _make_key(company_ids[0], url=server.url)
            event_id = _add_event(key_id)

            started = time.monotonic()
            assert push_pending_events() == (1, 0)
            assert time.monotonic() - started < 5
            assert read_sizes == [services.MAX_RESPONSE_BYTES]
            assert db.session.get(WebhookEvent, event_id).status == 'sent'
    finally:
        server.close()
    assert server.sent_bytes < HUGE_BODY_BYTES   # 读够 64KB 就断开，没等对方发完


def test_compressed_response_is_not_decompressed(client, company_ids, read_sizes):
    """gzip 正文按原始字节读（不解压），炸弹展不开"""
    compressed = gzip.compress(b'\0' * (20 * 1024 * 1024))

    def respond(server, conn):
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n'
                     b'Content-Length: %d\r\n\r\n' % len(compressed) + compressed)

    server = _StubServer(respond)
    try:
        with client.application.app_context():
            key_id = _make_key(company_ids[0], url=server.url)
            _add_event(key_id)
            assert push_pending_events() == (1, 0)
            assert read_sizes == [len(compressed)]
    finally:
        server.close()


def test_no_lock_or_transaction_held_while_sending(client, company_ids):
    """发 HTTP 时事件已是 sending（带租约、attempts 已 +1）且已提交，推送进程的会话不在事务里"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        event_id = _add_event(key_id)
        session = db.session()
        engine = db.engine
        seen = {}

        def post(url, data=None, headers=None, **kwargs):
            seen['in_transaction'] = session.in_transaction()
            with engine.connect() as conn:
                seen['row'] = tuple(conn.execute(
                    text('SELECT status, attempts, lease_expires_at FROM webhook_events WHERE id = :id'),
                    {'id': event_id},
                ).one())
            seen['attempt_header'] = headers['X-Webhook-Attempt']
            assert kwargs['stream'] is True and kwargs['allow_redirects'] is False
            return _response()

        with mock.patch(POST, side_effect=post):
            assert push_pending_events() == (1, 0)

        assert seen['in_transaction'] is False
        status, attempts, lease = seen['row']
        assert (status, attempts) == ('sending', 1) and lease is not None
        assert seen['attempt_header'] == '1'
        event = db.session.get(WebhookEvent, event_id)
        assert (event.status, event.attempts, event.lease_expires_at) == ('sent', 1, None)


def test_expired_lease_sending_event_is_reclaimed(client, company_ids):
    now = datetime.now(timezone.utc)
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        expired = _add_event(key_id, status='sending', attempts=1, lease_expires_at=now - timedelta(seconds=1))
        active = _add_event(key_id, status='sending', attempts=1, lease_expires_at=now + timedelta(minutes=1))
        exhausted = _add_event(key_id, status='sending', attempts=services.MAX_ATTEMPTS,
                               lease_expires_at=now - timedelta(seconds=1))

        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            assert push_pending_events() == (1, 0)

        assert [(c[1]['X-Webhook-Id'], c[1]['X-Webhook-Attempt']) for c in calls] == [(str(expired), '2')]
        assert db.session.get(WebhookEvent, expired).status == 'sent'
        assert db.session.get(WebhookEvent, active).status == 'sending'      # 租约未到期：别的进程还在发
        event = db.session.get(WebhookEvent, exhausted)
        assert event.status == 'failed' and event.lease_expires_at is None   # 次数已用完：不再发


def test_claim_rechecks_backoff_and_status(client, company_ids):
    """B-91：两个推送进程并发时，另一进程刚失败进入退避 / 正在发送的事件，重读时不会被领取"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        in_backoff = _add_event(key_id, attempts=1, next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=30))
        sending = _add_event(key_id, status='sending', attempts=1,
                             lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        assert services._claim_event(in_backoff) == (False, None)
        assert services._claim_event(sending) == (False, None)


def test_result_not_recorded_after_claim_was_taken_over(client, company_ids):
    """租约过期后被别的进程重新领取：原进程迟到的结果不覆盖"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        event_id = _add_event(key_id)
        claimed, job = services._claim_event(event_id)
        assert claimed and job is not None

        event = db.session.get(WebhookEvent, event_id)
        event.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)   # 另一个进程的新租约
        db.session.commit()

        services._record_result(job, requests.ConnectionError('late failure'))
        event = db.session.get(WebhookEvent, event_id)
        assert (event.status, event.last_error) == ('sending', None)


# ---------------------------------------------------------------------------
# B-60：公平调度 / 时间预算 / 熔断
# ---------------------------------------------------------------------------

def _bulk_broadcast(company_id, count, prefix='goods'):
    emit_company_events(EVENT, [({'goods_id': i, 'changed_fields': ['w']}, f'{prefix}:{i}')
                                for i in range(count)], company_id)
    db.session.commit()


def test_other_company_dn_event_not_starved_by_broadcast_backlog(client, company_ids):
    with client.application.app_context():
        key_a = _make_key(company_ids[0], url='http://a.test/webhook')
        key_b = _make_key(company_ids[1], url='http://b.test/webhook', subscriptions=())
        _bulk_broadcast(company_ids[0], 150)
        emit('dn.delivered', {'dn_id': 99, 'status': 'delivered'}, api_key_id=key_b)
        db.session.commit()

        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            assert push_pending_events() == (151, 0)

        # 单据事件优先：B 的 dn.delivered 第一个发出；A 的积压在同一轮按批次全部发完
        assert calls[0][0] == 'http://b.test/webhook'
        assert calls[0][1]['X-Webhook-Event'] == 'dn.delivered'
        assert {e.status for e in _events(key_a)} == {'sent'}


def test_keys_take_turns_within_broadcast_backlog(client, company_ids):
    with client.application.app_context():
        _make_key(company_ids[0], url='http://a.test/webhook')
        _make_key(company_ids[1], url='http://b.test/webhook')
        _bulk_broadcast(company_ids[0], 150)
        _bulk_broadcast(company_ids[1], 5)

        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            push_pending_events()

        first_ten = [url for url, _, _ in calls[:10]]
        assert first_ten.count('http://b.test/webhook') == 5   # 各 Key 轮流，B 不用等 A 的 150 条


def test_time_budget_stops_starting_new_sends(client, company_ids):
    with client.application.app_context():
        key_a = _make_key(company_ids[0], url='http://a.test/webhook')
        key_b = _make_key(company_ids[1], url='http://b.test/webhook', subscriptions=())
        _bulk_broadcast(company_ids[0], 150)
        _add_event(key_b, 'dn.delivered')

        calls = []
        record = _recorder(calls)

        def slow_post(url, **kwargs):
            time.sleep(0.1)
            return record(url, **kwargs)

        with mock.patch(POST, side_effect=slow_post):
            sent, failed = push_pending_events(time_budget=0.35)

        assert 1 <= sent <= 6 and failed == 0
        assert calls[0][0] == 'http://b.test/webhook'
        statuses = [e.status for e in _events(key_a)]
        assert 'sending' not in statuses   # 没轮到的不会被领取后卡在 sending
        assert statuses.count('pending') == 150 - (sent - 1)


def test_breaker_skips_unreachable_key_for_the_rest_of_the_run(client, company_ids):
    with client.application.app_context():
        key_dead = _make_key(company_ids[0], url='http://dead.test/webhook')
        key_b = _make_key(company_ids[1], url='http://b.test/webhook', subscriptions=())
        _bulk_broadcast(company_ids[0], 150)
        _add_event(key_b, 'dn.delivered')

        calls = []
        record = _recorder(calls)

        def post(url, **kwargs):
            if 'dead' in url:
                calls.append((url, kwargs['headers'], None))
                raise requests.ConnectTimeout('connect timeout')
            return record(url, **kwargs)

        with mock.patch(POST, side_effect=post):
            assert push_pending_events() == (1, services.BREAKER_THRESHOLD)

        assert sum('dead' in url for url, _, _ in calls) == services.BREAKER_THRESHOLD
        events = _events(key_dead)
        assert sum(e.attempts for e in events) == services.BREAKER_THRESHOLD   # 跳过的不耗次数
        assert _events(key_b)[0].status == 'sent'


def test_http_error_status_does_not_trip_breaker(client, company_ids):
    """对方回了 HTTP 错误码说明在线，不算「连不上」"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        for _ in range(5):
            _add_event(key_id)

        calls = []
        with mock.patch(POST, side_effect=_recorder(calls, status=500)):
            assert push_pending_events() == (0, 5)
        assert len(calls) == 5
        assert {e.last_error for e in _events(key_id)} == {'HTTPError (HTTP 500)'}


# ---------------------------------------------------------------------------
# B-60 / B-90：失败过的事件不被合并覆盖
# ---------------------------------------------------------------------------

def test_attempted_event_is_not_overwritten_and_old_one_is_voided(client, access_token, company_ids):
    """推送超时过（接收方可能已收到）的事件，商品再改规格时：另建新事件，旧事件作废；
    每个 X-Webhook-Id 始终只对应一份 payload"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        goods_id = Goods.query.filter_by(code='G001').first().id
    headers = {'Authorization': f'Bearer {access_token}'}
    deliveries = []

    def timeout(url, data=None, headers=None, **kwargs):
        deliveries.append((headers['X-Webhook-Id'], data))
        raise requests.ReadTimeout('read timeout (receiver may have processed it)')

    assert client.put(f'/goods/{goods_id}', headers=headers, json={'weight': 2.0}).status_code == 200
    with client.application.app_context(), mock.patch(POST, side_effect=timeout):
        assert push_pending_events() == (0, 1)

    assert client.put(f'/goods/{goods_id}', headers=headers, json={'weight': 3.0}).status_code == 200
    with client.application.app_context():
        old, new = _events(key_id, EVENT)
        assert (old.status, old.attempts, old.payload['goods_weight_kg']) == ('superseded', 1, 2.0)
        assert (new.status, new.attempts, new.payload['goods_weight_kg']) == ('pending', 0, 3.0)
        assert new.next_retry_at == old.next_retry_at   # 退避不清零

        # 退避到期后只发新事件
        new.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        old.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            assert push_pending_events() == (1, 0)
        deliveries += [(h['X-Webhook-Id'], json.dumps(p)) for _, h, p in calls]
        assert calls[0][1]['X-Webhook-Id'] == str(new.id)

    payloads_by_id = {}
    for event_id, body in deliveries:
        payloads_by_id.setdefault(event_id, set()).add(json.loads(body)['goods_weight_kg'])
    assert all(len(values) == 1 for values in payloads_by_id.values())


def test_in_flight_event_is_voided_when_newer_exists(client, company_ids):
    """正在发送的旧事件不动、另建新事件；旧的发送失败后再领取时发现有更新的同对象事件 → 作废，不晚到"""
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        emit_company_event(EVENT, {'n': 1, 'changed_fields': ['a']}, company_ids[0], dedupe_key='goods:1')
        db.session.commit()
        old = _events(key_id)[0]
        old.status, old.attempts = 'sending', 1
        old.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        db.session.commit()

        emit_company_event(EVENT, {'n': 2, 'changed_fields': ['b']}, company_ids[0], dedupe_key='goods:1')
        db.session.commit()
        old, new = _events(key_id)
        assert (old.status, old.payload['n']) == ('sending', 1)
        assert (new.status, new.payload['n']) == ('pending', 2)

        old.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)   # 旧的那次没回写（进程被杀）
        db.session.commit()
        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            assert push_pending_events() == (1, 0)
        assert [c[1]['X-Webhook-Id'] for c in calls] == [str(new.id)]
        old = db.session.get(WebhookEvent, old.id)
        assert (old.status, old.last_error) == ('superseded', 'Superseded by a newer event')


# ---------------------------------------------------------------------------
# B-92：Key 停用 / 取消订阅
# ---------------------------------------------------------------------------

def test_events_of_inactive_key_are_voided_not_sent(client, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        dn_event = _add_event(key_id, 'dn.delivered')
        spec_event = _add_event(key_id, EVENT, dedupe_key='goods:1')
        db.session.get(APIKey, key_id).is_active = False
        db.session.commit()

        with mock.patch(POST) as post:
            assert push_pending_events() == (0, 0)
        post.assert_not_called()
        for event_id in (dn_event, spec_event):
            event = db.session.get(WebhookEvent, event_id)
            assert (event.status, event.last_error) == ('superseded', 'API key is inactive')


def test_unsubscribed_broadcast_event_is_voided(client, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        dn_event = _add_event(key_id, 'dn.delivered')
        spec_event = _add_event(key_id, EVENT, dedupe_key='goods:1')
        db.session.get(APIKey, key_id).webhook_subscriptions = []
        db.session.commit()

        calls = []
        with mock.patch(POST, side_effect=_recorder(calls)):
            assert push_pending_events() == (1, 0)
        assert [c[1]['X-Webhook-Event'] for c in calls] == ['dn.delivered']   # 单据事件不看订阅
        assert db.session.get(WebhookEvent, dn_event).status == 'sent'
        assert db.session.get(WebhookEvent, spec_event).status == 'superseded'


# ---------------------------------------------------------------------------
# 手动重试同样走三段式
# ---------------------------------------------------------------------------

def test_manual_retry_sends_without_holding_lock(client, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        event_id = _add_event(key_id, status='failed', attempts=services.MAX_ATTEMPTS, last_error='old')
        session = db.session()
        seen = {}

        def post(url, data=None, headers=None, **kwargs):
            seen['in_transaction'] = session.in_transaction()
            seen['attempt'] = headers['X-Webhook-Attempt']
            return _response()

        event = WebhookEvent.query.filter_by(id=event_id).with_for_update().first()
        with mock.patch(POST, side_effect=post):
            event = retry_event_now(event)
        assert seen == {'in_transaction': False, 'attempt': '1'}
        assert (event.status, event.attempts, event.last_error) == ('sent', 1, None)


@pytest.fixture
def webhook_client(client):
    """测试 app 没挂 /webhook 命名空间：挂到一个蓝图上（/wh 前缀，不生成文档页，避免与主 Api 冲突）"""
    from flask import Blueprint
    from flask_restx import Api
    from system.webhook.schemas import api_ns as webhook_ns

    blueprint = Blueprint(f'webhook_test_{uuid.uuid4().hex[:6]}', __name__)
    Api(blueprint, doc=False).add_namespace(webhook_ns, path='/webhook')
    client.application.register_blueprint(blueprint, url_prefix='/wh')
    return client


def test_retry_endpoint_rejects_in_flight_and_resends_failed(webhook_client, access_token, company_ids):
    client = webhook_client
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        key_id = _make_key(company_ids[0])
        in_flight = _add_event(key_id, status='sending', attempts=1,
                               lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        failed = _add_event(key_id, status='failed', attempts=services.MAX_ATTEMPTS)

    assert client.post(f'/wh/webhook/{in_flight}/retry', headers=headers).status_code == 409

    calls = []
    with mock.patch(POST, side_effect=_recorder(calls)):
        response = client.post(f'/wh/webhook/{failed}/retry', headers=headers)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body['status'], body['attempts'], body['lease_expires_at']) == ('sent', 1, None)
    assert [c[1]['X-Webhook-Id'] for c in calls] == [str(failed)]


def test_manual_retry_of_inactive_key_event_does_not_send(client, company_ids):
    with client.application.app_context():
        key_id = _make_key(company_ids[0], active=False)
        event_id = _add_event(key_id, status='failed', attempts=3)
        event = WebhookEvent.query.filter_by(id=event_id).with_for_update().first()
        with mock.patch(POST) as post:
            event = retry_event_now(event)
        post.assert_not_called()
        assert event.status == 'superseded'


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

def test_scheduler_time_budget_fits_interval():
    from scheduler import _webhook_time_budget
    # 间隔 1 分钟：预算 + 最后一次推送的总时限不超过间隔，下一轮不会因 max_instances=1 被跳过
    assert _webhook_time_budget(1) + services.SEND_DEADLINE_SECONDS < 60
    assert _webhook_time_budget(30) == 30 * 60 - services.SEND_DEADLINE_SECONDS - 15
