"""Webhook 推送服务

推送分三段，HTTP 请求期间不持行锁、不开事务：
1. 领取：加锁（SKIP LOCKED）重读事件，做发送前检查，attempts +1、标 sending 并写租约，提交；
2. 发送：不持锁，子线程里 POST，单调时钟总时限 + 响应正文读取上限，超时就关闭连接记为失败；
3. 回写：加锁写结果（成功 / 失败及退避）。租约已被别人接手就不覆盖。
租约过期仍是 sending 的事件（推送进程中途被杀）视为可重新领取。
"""
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from flask import current_app
from sqlalchemy import case

from extensions import db
from system.third_party.models import APIKey
from .models import WebhookEvent

logger = logging.getLogger(__name__)

# 重试配置：每 30 分钟自动重试，最多 10 次，与 Wholesale 保持一致
RETRY_INTERVAL_SECONDS = 1800  # 30 分钟
MAX_ATTEMPTS = 10

# 单次推送的 HTTP 限制。requests 的 timeout 只限「两次收包之间」的间隔，接收方每隔几秒滴一个字节
# 就能让请求永不返回，所以另用单调时钟做总时限（含 DNS 解析、建连、收响应头和正文）
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
SEND_DEADLINE_SECONDS = 15
# 响应正文最多读 64KB（结果只看状态码），之后直接断开
MAX_RESPONSE_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 8 * 1024
# sending 租约 = 总时限 + 回写余量；到期仍是 sending 说明推送进程中途没了，可以重新领取
LEASE_SECONDS = SEND_DEADLINE_SECONDS + 45

# 每轮推送的公平调度
PER_KEY_BATCH = 20               # 每次取事件时每个 Key 最多取几条；取完一批按剩余时间接着取
PUSH_TIME_BUDGET_SECONDS = 40    # 默认每轮时间预算，到点不再开始新的推送（调度器按推送间隔另算）
BREAKER_THRESHOLD = 3            # 同一 Key 本轮连续「连不上 / 超时」达到该次数，本轮跳过它剩余的事件

# 可订阅的广播事件（API Key 的 webhook_subscriptions 只允许这些）；推送时排在单据事件之后
SUBSCRIBABLE_EVENT_TYPES = ('goods.spec_updated',)


class WebhookDeliveryTimeout(Exception):
    """单次推送超过总时限（连接已关闭 / 不再等待）"""


# 计入熔断的「连不上 / 超时」类错误；对方回了 HTTP 错误码说明它在线，不计
_UNREACHABLE_ERRORS = (
    requests.ConnectionError, requests.Timeout, ConnectionError, TimeoutError, WebhookDeliveryTimeout,
)

# 超过总时限仍没退出的发送线程：api_key_id → 线程。卡在 DNS / 建连 / 收响应头时没法从外面打断，
# 只能等它自己超时；它还活着就不再给该 Key 开新线程，一个 Key 最多占一个卡住的线程
_stuck_senders = {}


def emit(event_type, payload, api_key_id=None, dedupe_key=None):
    """创建 Webhook 事件记录（定向推送）

    只为指定的 API Key 创建事件。如果未指定 api_key_id，则不推送
    （WMS 内部手动创建的单据不触发 Webhook）。

    Args:
        event_type: 事件类型，如 'dn.delivered', 'asn.completed'
        payload: 事件数据（dict）
        api_key_id: 目标 API Key ID（创建该单据的来源方）
        dedupe_key: 覆盖键（payload 是完整的最新状态时用，如 dn.tracking_updated 的 'dn:<id>'）：
            发送前发现同 Key、同类型、同键已有更新的事件，本条作废不发（只送最新的）
    """
    if not api_key_id:
        return

    api_key = APIKey.query.filter(
        APIKey.id == api_key_id,
        APIKey.is_active == True,
        APIKey.webhook_url.isnot(None),
        APIKey.webhook_url != '',
    ).first()

    if not api_key:
        return

    event = WebhookEvent(
        api_key_id=api_key.id,
        event_type=event_type,
        payload=payload,
        dedupe_key=dedupe_key,
        status='pending',
    )
    db.session.add(event)
    db.session.flush()


def _merge_pending_payload(old_payload, new_payload):
    """覆盖 / 接替未送达事件时的 payload：以新 payload 为准；
    changed_fields 取并集（旧事件还没送达，它记录的变动不能丢）"""
    merged = dict(new_payload)
    old_fields = (old_payload or {}).get('changed_fields')
    new_fields = new_payload.get('changed_fields')
    if isinstance(old_fields, list) and isinstance(new_fields, list):
        merged['changed_fields'] = list(dict.fromkeys([*old_fields, *new_fields]))
    return merged


def _void(event, reason):
    """作废事件（superseded，不再发送），原因记在 last_error"""
    event.status = 'superseded'
    event.lease_expires_at = None
    event.last_error = reason


def company_event_targets(event_type, company_id) -> list:
    """该公司「启用、配了 webhook 地址、且 webhook_subscriptions 含该事件」的 API Key（按 id 排序）。
    只看 Key 自己的 company_id（未绑定公司的平台 Key 不收公司事件）。"""
    if not company_id:
        return []
    keys = APIKey.query.filter(
        APIKey.company_id == company_id,
        APIKey.is_active == True,
        APIKey.webhook_url.isnot(None),
        APIKey.webhook_url != '',
    ).order_by(APIKey.id).all()
    # 订阅列表是 JSON 数组，各数据库的包含查询写法不同；每家公司 Key 很少，直接在 Python 里筛
    return [k for k in keys if event_type in (k.webhook_subscriptions or [])]


# 批量查待发送事件时 IN 列表的分块大小
_DEDUPE_CHUNK = 500


def emit_company_event(event_type, payload, company_id, dedupe_key=None):
    """按公司广播 Webhook 事件（在调用方的事务里落库，随业务一起提交 / 回滚）

    推给该公司所有「启用、配了 webhook 地址、且 webhook_subscriptions 含该事件」的 API Key。

    dedupe_key 非空时，同一 Key 已有同类型、同 dedupe_key 且待发送（pending）的事件：
    - 还没推送过（attempts == 0）→ 用新 payload 覆盖那一条（不新增）；
    - 已推送失败过 → 不覆盖（接收方可能已按这个 X-Webhook-Id 收到过旧 payload），也不清零退避：
      另建新事件接替（沿用旧事件的下次重试时间，changed_fields 取并集），旧事件作废（superseded）。
    正在推送中（sending）或正被推送进程锁住的那条不动，另建新事件；它要是发送失败，
    下次领取时发现已有更新的同对象事件就会作废，不会晚到覆盖新数据。

    Returns:
        list[WebhookEvent]: 新建或被覆盖的事件
    """
    return emit_company_events(event_type, [(payload, dedupe_key)], company_id)


def emit_company_events(event_type, items, company_id, targets=None):
    """emit_company_event 的批量版（批量导入用）：items = [(payload, dedupe_key), ...]。

    订阅 Key 只查一次（也可由调用方传 targets）；待发送的同 dedupe_key 事件按 Key 一次查出
    （分块 IN），同一批里重复的 dedupe_key 按顺序合并到同一条。语义与逐条调用相同。
    """
    if not company_id or not items:
        return []
    if targets is None:
        targets = company_event_targets(event_type, company_id)
    if not targets:
        return []

    dedupe_keys = sorted({key for _, key in items if key})
    events = []
    returned = set()   # 已放进 events 的对象（按 id() 去重，同一条被合并多次只返回一次）
    for api_key in targets:
        pending = {}   # dedupe_key → 待发送的同键事件（取 id 最大的那条）
        for start in range(0, len(dedupe_keys), _DEDUPE_CHUNK):
            chunk = dedupe_keys[start:start + _DEDUPE_CHUNK]
            rows = WebhookEvent.query.filter(
                WebhookEvent.api_key_id == api_key.id,
                WebhookEvent.event_type == event_type,
                WebhookEvent.dedupe_key.in_(chunk),
                WebhookEvent.status == 'pending',
            ).order_by(WebhookEvent.id).with_for_update(skip_locked=True).all()
            for row in rows:
                pending[row.dedupe_key] = row

        for payload, dedupe_key in items:
            existing = pending.get(dedupe_key) if dedupe_key else None
            if existing is not None and existing.attempts == 0:
                # 还没推送过：直接覆盖，同一个 X-Webhook-Id 不会先后带不同 payload
                # （整体赋新 dict，JSON 列才会被识别为已修改）
                existing.payload = _merge_pending_payload(existing.payload, payload)
                if id(existing) not in returned:
                    returned.add(id(existing))
                    events.append(existing)
                continue

            event = WebhookEvent(
                api_key_id=api_key.id,
                event_type=event_type,
                payload=dict(payload),
                dedupe_key=dedupe_key,
                status='pending',
                attempts=0,   # 显式给 0：同一批后面的同 key 要据此合并到这一条（flush 前列默认值还没生效）
            )
            if existing is not None:
                # 已推送失败过：新事件接替它，退避时间与未送达的变动字段带过来，旧事件作废
                event.payload = _merge_pending_payload(existing.payload, payload)
                event.next_retry_at = existing.next_retry_at
                _void(existing, 'Superseded by a newer event')
            db.session.add(event)
            events.append(event)
            returned.add(id(event))
            if dedupe_key:
                pending[dedupe_key] = event   # 同一批里后面的同 key 合并到这一条

    if events:
        db.session.flush()
    return events


def _sign_payload(payload_bytes, secret):
    """使用 HMAC-SHA256 签名"""
    return hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# 第一段：领取（加锁、发送前检查、标 sending + 租约）
# ---------------------------------------------------------------------------

def _due_filter(now):
    """可领取的事件：到期的 pending，或租约已过期的 sending（推送进程中途被杀 / 卡死）"""
    return db.or_(
        db.and_(
            WebhookEvent.status == 'pending',
            db.or_(WebhookEvent.next_retry_at.is_(None), WebhookEvent.next_retry_at <= now),
        ),
        db.and_(
            WebhookEvent.status == 'sending',
            db.or_(WebhookEvent.lease_expires_at.is_(None), WebhookEvent.lease_expires_at <= now),
        ),
    )


def _has_newer_event(event):
    """同一 Key、同类型、同 dedupe_key 是否已有更新的事件（有就说明本条的数据已过时）"""
    return db.session.query(WebhookEvent.id).filter(
        WebhookEvent.api_key_id == event.api_key_id,
        WebhookEvent.event_type == event.event_type,
        WebhookEvent.dedupe_key == event.dedupe_key,
        WebhookEvent.id > event.id,
    ).first() is not None


def _build_job(event, api_key):
    """组装本次推送的请求（在领取事务里算好，发送阶段不再碰数据库）"""
    payload_bytes = json.dumps(event.payload, ensure_ascii=False).encode('utf-8')
    timestamp = str(int(datetime.now().timestamp()))

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Event': event.event_type,
        # 事件 ID + 时间戳：接收方据此做幂等与防重放（同一事件重试时 ID 不变）
        'X-Webhook-Id': str(event.id),
        'X-Webhook-Timestamp': timestamp,
        'X-Webhook-Attempt': str(event.attempts),   # 领取时已 +1，即本次是第几次
    }

    # HMAC 签名：V1 只签正文（兼容已接入方）；V2 把事件 ID 与时间戳一起签，防重放
    if api_key.webhook_secret:
        signature = _sign_payload(payload_bytes, api_key.webhook_secret)
        headers['X-Webhook-Signature'] = f'sha256={signature}'
        signed_v2 = f'{event.id}.{timestamp}.'.encode('utf-8') + payload_bytes
        headers['X-Webhook-Signature-V2'] = f'sha256={_sign_payload(signed_v2, api_key.webhook_secret)}'
    else:
        logger.warning(f'Webhook event {event.id}: api_key {api_key.id} has no webhook_secret, payload sent unsigned')

    return {
        'event_id': event.id,
        'api_key_id': api_key.id,
        'url': api_key.webhook_url,
        'strict': bool(current_app.config.get('WEBHOOK_URL_STRICT', True)),
        'body': payload_bytes,
        'headers': headers,
        'lease': event.lease_expires_at,   # 回写时据此确认还是本次领取
    }


def _claim_locked(event):
    """调用方已对事件加行锁（之后由调用方提交）：发送前检查，能发就领取。

    - Key 已停用、已取消订阅该广播事件、或同对象已有更新的事件 → 作废（superseded）
    - 没有回调地址，或租约过期回收时次数已用完 → failed
    - 否则 attempts +1、标 sending 并写租约，组装好请求

    Returns:
        dict | None: 发送任务；None 表示不发送（状态已就地更新）
    """
    api_key = db.session.get(APIKey, event.api_key_id)
    if api_key is None or not api_key.is_active:
        _void(event, 'API key is inactive')
        return None
    if event.event_type in SUBSCRIBABLE_EVENT_TYPES \
            and event.event_type not in (api_key.webhook_subscriptions or []):
        _void(event, 'API key is no longer subscribed to this event')
        return None
    if not api_key.webhook_url:
        event.status = 'failed'
        event.lease_expires_at = None
        event.last_error = 'No webhook URL configured'
        return None
    if event.dedupe_key and _has_newer_event(event):
        _void(event, 'Superseded by a newer event')
        return None
    if event.attempts >= MAX_ATTEMPTS:
        # 租约过期回收的事件：上一次发送结果不明，但次数已经用完
        event.status = 'failed'
        event.lease_expires_at = None
        event.last_error = 'Send interrupted (lease expired)'
        return None

    event.attempts += 1
    event.status = 'sending'
    event.lease_expires_at = datetime.now() + timedelta(seconds=LEASE_SECONDS)
    return _build_job(event, api_key)


def _claim_event(event_id):
    """领取一条事件：加锁重读（SKIP LOCKED，别的进程锁着就跳过），并重新核对到期条件
    （别的推送进程刚发过、或刚失败进入退避的不会被立即重发），检查后提交、释放锁。

    Returns:
        tuple[bool, dict | None]: (是否处理了这条, 发送任务)；任务为 None 表示已就地作废 / 标失败
    """
    event = WebhookEvent.query.filter(
        WebhookEvent.id == event_id,
        _due_filter(datetime.now()),
    ).with_for_update(skip_locked=True).populate_existing().first()
    if event is None:
        return False, None   # 已被别的推送进程处理 / 正锁着 / 已不到期
    job = _claim_locked(event)
    db.session.commit()
    return True, job


# ---------------------------------------------------------------------------
# 第二段：发送（不持锁、不开事务；总时限 + 正文上限）
# ---------------------------------------------------------------------------

def _sender_stuck(api_key_id):
    worker = _stuck_senders.get(api_key_id)
    return worker is not None and worker.is_alive()


def _read_limited(resp, deadline):
    """读响应正文：最多 MAX_RESPONSE_BYTES 字节（够了就不再读，由调用方断开），返回读到的字节数；
    到总时限还没读完（滴流）抛 WebhookDeliveryTimeout。
    read1 收到一点就返回，滴流响应也能按时停下；requests 建的 raw 是 decode_content=False，
    读的是原始字节、不解压（gzip 炸弹展不开）"""
    total = 0
    while total < MAX_RESPONSE_BYTES:
        if time.monotonic() >= deadline:
            raise WebhookDeliveryTimeout('response body not finished within deadline')
        chunk = resp.raw.read1(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES - total))
        if not chunk:
            break
        total += len(chunk)
    return total


def _post_in_thread(job, deadline, box):
    """发送线程：复核 URL → POST → 读有限的正文 → 关闭连接。
    不碰数据库和 Flask 上下文；拿到的响应 / 异常放进 box"""
    from .utils import strict_webhook_session, validate_webhook_url
    resp = None
    session = None
    try:
        # 发送前再校验一次 URL（防止配置后 DNS 改指内网），且不跟随重定向。
        # 严格模式下建连时还会再解析一次、校验全部地址并只连校验过的 IP（防 DNS rebinding），
        # 证书仍按原域名校验
        validate_webhook_url(job['url'], strict=job['strict'])
        post = requests.post
        if job['strict']:
            session = strict_webhook_session()
            post = session.post
        resp = post(
            job['url'],
            data=job['body'],
            headers=job['headers'],
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            stream=True,
        )
        box['response'] = resp
        try:
            _read_limited(resp, deadline)
        except WebhookDeliveryTimeout:
            raise
        except Exception:
            pass   # 结果只看状态码；正文读出错（对方提前断开等）不影响，超时除外
    except Exception as e:
        box['error'] = e
    finally:
        if resp is not None:
            resp.close()
        if session is not None:
            session.close()


def _post_with_deadline(job):
    """在总时限内完成一次 POST，返回（已关闭的）响应；超时抛 WebhookDeliveryTimeout。

    请求在子线程里做，本线程最多等 SEND_DEADLINE_SECONDS：卡在收正文的，关掉连接让它尽快退出；
    卡在 DNS / 建连 / 收响应头的从外面打断不了，记进 _stuck_senders，它退出前该 Key 不再开新线程。
    """
    if _sender_stuck(job['api_key_id']):
        raise WebhookDeliveryTimeout('previous request to this receiver is still hanging')

    deadline = time.monotonic() + SEND_DEADLINE_SECONDS
    box = {}
    worker = threading.Thread(
        target=_post_in_thread, args=(job, deadline, box),
        name=f"webhook-send-{job['event_id']}", daemon=True,
    )
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if worker.is_alive():
        resp = box.get('response')
        if resp is not None:
            resp.close()
        _stuck_senders[job['api_key_id']] = worker
        raise WebhookDeliveryTimeout(f'no complete response within {SEND_DEADLINE_SECONDS}s')
    if 'error' in box:
        raise box['error']
    return box['response']


def _deliver(job):
    """发送一次。成功返回 None，失败返回异常"""
    try:
        resp = _post_with_deadline(job)
        if 300 <= resp.status_code < 400:
            raise requests.HTTPError(f'Redirect ({resp.status_code}) is not allowed', response=resp)
        resp.raise_for_status()
        return None
    except Exception as e:
        return e


# ---------------------------------------------------------------------------
# 第三段：回写结果
# ---------------------------------------------------------------------------

def _record_result(job, error):
    """加锁回写发送结果。事件已不是本次领取时的样子（租约过期后被别的进程接手等）就不覆盖"""
    event = WebhookEvent.query.filter(WebhookEvent.id == job['event_id']) \
        .with_for_update().populate_existing().first()
    if event is None or event.status != 'sending' or event.lease_expires_at != job['lease']:
        db.session.rollback()
        logger.warning(f"Webhook event {job['event_id']}: claim was taken over, result not recorded")
        return

    event.lease_expires_at = None
    if error is None:
        event.status = 'sent'
        event.sent_at = datetime.now()
        event.last_error = None
    else:
        # 错误信息不带目标 URL / 响应体，避免被用作内网探测的回显
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        event.last_error = (f'{type(error).__name__}' + (f' (HTTP {status})' if status else ''))[:500]
        if event.attempts >= MAX_ATTEMPTS:
            event.status = 'failed'
        else:
            # 固定 30 分钟间隔重试
            event.status = 'pending'
            event.next_retry_at = datetime.now() + timedelta(seconds=RETRY_INTERVAL_SECONDS)
    db.session.commit()


def retry_event_now(event):
    """手动重试：调用方已对事件加行锁、校验过权限，并拦下了已送达 / 正在推送中的事件。

    不受次数限制：计数清零后立即按三段式推送一次（同样不在 HTTP 期间持锁）。
    发送前检查照常生效：Key 已停用、已有更新的同对象事件等会直接作废而不发送。

    Returns:
        WebhookEvent: 推送后的事件
    """
    event_id = event.id
    event.status = 'pending'
    event.attempts = 0
    event.next_retry_at = None
    event.lease_expires_at = None
    job = _claim_locked(event)
    db.session.commit()
    if job is not None:
        _record_result(job, _deliver(job))
    return db.session.get(WebhookEvent, event_id)


# ---------------------------------------------------------------------------
# 定时推送
# ---------------------------------------------------------------------------

def _plan_batch(skip_key_ids):
    """取一批候选事件：每个 Key 最多 PER_KEY_BATCH 条（单据事件在前，再按创建先后），
    排成「先所有 Key 的单据事件、后广播事件，同类里各 Key 轮流」的发送顺序。

    Returns:
        list[tuple[int, int]]: [(api_key_id, event_id), ...]
    """
    due = _due_filter(datetime.now())
    is_broadcast = case((WebhookEvent.event_type.in_(SUBSCRIBABLE_EVENT_TYPES), 1), else_=0)
    key_ids = [row[0] for row in db.session.query(WebhookEvent.api_key_id).filter(due).distinct()]

    candidates = []
    for key_id in key_ids:
        if key_id in skip_key_ids:
            continue
        rows = db.session.query(WebhookEvent.id, is_broadcast).filter(
            due,
            WebhookEvent.api_key_id == key_id,
        ).order_by(is_broadcast, WebhookEvent.created_at, WebhookEvent.id).limit(PER_KEY_BATCH).all()
        for rank, (event_id, broadcast) in enumerate(rows):
            candidates.append((broadcast, rank, key_id, event_id))
    candidates.sort()
    return [(key_id, event_id) for _, _, key_id, event_id in candidates]


def push_pending_events(time_budget=None):
    """推送到期的事件（定时任务 / CLI 调用）

    按时间预算循环取批：每批每个 Key 至多 PER_KEY_BATCH 条，单据事件优先、各 Key 轮流发，
    一个 Key 的积压或坏接收端挡不住别的 Key；同一 Key 本轮连续连不上 / 超时 BREAKER_THRESHOLD 次
    就跳过它剩余的事件（熔断）。到了时间预算不再开始新的推送，没轮到的留到下一轮。

    Args:
        time_budget: 本轮时间预算（秒），默认 PUSH_TIME_BUDGET_SECONDS

    Returns:
        tuple: (sent_count, failed_count)
    """
    stop_at = time.monotonic() + (PUSH_TIME_BUDGET_SECONDS if time_budget is None else time_budget)
    sent = 0
    failed = 0
    unreachable_streak = defaultdict(int)   # api_key_id → 本轮连续连不上 / 超时的次数
    skipped_keys = set()                    # 本轮熔断（或上次的请求还卡着）的 Key

    while time.monotonic() < stop_at:
        handled = 0
        for key_id, event_id in _plan_batch(skipped_keys):
            if time.monotonic() >= stop_at:
                break
            if key_id in skipped_keys:
                continue
            if _sender_stuck(key_id):
                skipped_keys.add(key_id)
                continue

            # 逐条领取 / 回写并提交：一条出错不影响其他条，也不会让已推送的事件回滚后被重复推送
            try:
                claimed, job = _claim_event(event_id)
            except Exception as e:
                db.session.rollback()
                logger.error(f'Webhook event {event_id}: failed to claim: {e}')
                continue
            if not claimed:
                continue
            handled += 1
            if job is None:
                continue   # 已作废 / 标失败，不发送

            error = _deliver(job)
            try:
                _record_result(job, error)
            except Exception as e:
                db.session.rollback()
                # 事件留在 sending，租约到期后会被重新领取
                logger.error(f'Webhook event {event_id}: failed to persist status: {e}')

            if error is None:
                sent += 1
                unreachable_streak[key_id] = 0
                continue
            failed += 1
            if not isinstance(error, _UNREACHABLE_ERRORS):
                unreachable_streak[key_id] = 0
                continue
            unreachable_streak[key_id] += 1
            if unreachable_streak[key_id] >= BREAKER_THRESHOLD:
                skipped_keys.add(key_id)
                logger.warning(f'Webhook push: api_key {key_id} unreachable {BREAKER_THRESHOLD} times '
                               f'in a row, skipping its remaining events this run')

        if not handled:
            break   # 这一批一条都没处理（没有到期事件 / 都被别的进程处理着 / Key 都被跳过）

    if sent or failed:
        logger.info(f'Webhook push: {sent} sent, {failed} failed')

    return sent, failed
