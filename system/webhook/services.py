"""Webhook 推送服务"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta

import requests

from extensions import db
from system.third_party.models import APIKey
from .models import WebhookEvent

logger = logging.getLogger(__name__)

# 重试配置：每 30 分钟自动重试，最多 10 次，与 Wholesale 保持一致
RETRY_INTERVAL_SECONDS = 1800  # 30 分钟
MAX_ATTEMPTS = 10

# 可订阅的广播事件（API Key 的 webhook_subscriptions 只允许这些）
SUBSCRIBABLE_EVENT_TYPES = ('goods.spec_updated',)


def emit(event_type, payload, api_key_id=None):
    """创建 Webhook 事件记录（定向推送）

    只为指定的 API Key 创建事件。如果未指定 api_key_id，则不推送
    （WMS 内部手动创建的单据不触发 Webhook）。

    Args:
        event_type: 事件类型，如 'dn.delivered', 'asn.completed'
        payload: 事件数据（dict）
        api_key_id: 目标 API Key ID（创建该单据的来源方）
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
        status='pending',
    )
    db.session.add(event)
    db.session.flush()


def _merge_pending_payload(old_payload, new_payload):
    """覆盖待发送事件时的 payload：以新 payload 为准；
    changed_fields 取并集（旧事件还没送达，它记录的变动不能丢）"""
    merged = dict(new_payload)
    old_fields = (old_payload or {}).get('changed_fields')
    new_fields = new_payload.get('changed_fields')
    if isinstance(old_fields, list) and isinstance(new_fields, list):
        merged['changed_fields'] = list(dict.fromkeys([*old_fields, *new_fields]))
    return merged


def emit_company_event(event_type, payload, company_id, dedupe_key=None):
    """按公司广播 Webhook 事件（在调用方的事务里落库，随业务一起提交 / 回滚）

    推给该公司所有「启用、配了 webhook 地址、且 webhook_subscriptions 含该事件」的 API Key；
    只看 Key 自己的 company_id（未绑定公司的平台 Key 不收公司事件）。

    dedupe_key 非空时：同一 Key 已有同类型、同 dedupe_key 且仍待发送的事件 → 用新 payload
    覆盖那一条（不新增）。正在推送中的那条（被推送进程锁住）跳过，另建新事件，避免新数据
    被标成「已发送」而实际没送出去。

    Returns:
        list[WebhookEvent]: 新建或被覆盖的事件
    """
    if not company_id:
        return []

    keys = APIKey.query.filter(
        APIKey.company_id == company_id,
        APIKey.is_active == True,
        APIKey.webhook_url.isnot(None),
        APIKey.webhook_url != '',
    ).order_by(APIKey.id).all()
    # 订阅列表是 JSON 数组，各数据库的包含查询写法不同；每家公司 Key 很少，直接在 Python 里筛
    targets = [k for k in keys if event_type in (k.webhook_subscriptions or [])]

    events = []
    for api_key in targets:
        existing = None
        if dedupe_key:
            existing = WebhookEvent.query.filter(
                WebhookEvent.api_key_id == api_key.id,
                WebhookEvent.event_type == event_type,
                WebhookEvent.dedupe_key == dedupe_key,
                WebhookEvent.status == 'pending',
            ).order_by(WebhookEvent.id.desc()).with_for_update(skip_locked=True).first()

        if existing is not None:
            # 整体赋新 dict，JSON 列才会被识别为已修改
            existing.payload = _merge_pending_payload(existing.payload, payload)
            events.append(existing)
            continue

        event = WebhookEvent(
            api_key_id=api_key.id,
            event_type=event_type,
            payload=dict(payload),
            dedupe_key=dedupe_key,
            status='pending',
        )
        db.session.add(event)
        events.append(event)

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


def _send_event(event):
    """推送单个事件

    Returns:
        True if sent successfully, False otherwise
    """
    api_key = event.api_key
    if not api_key or not api_key.webhook_url:
        event.status = 'failed'
        event.last_error = 'No webhook URL configured'
        return False

    payload_bytes = json.dumps(event.payload, ensure_ascii=False).encode('utf-8')
    timestamp = str(int(datetime.now().timestamp()))

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Event': event.event_type,
        # 事件 ID + 时间戳：接收方据此做幂等与防重放（同一事件重试时 ID 不变）
        'X-Webhook-Id': str(event.id),
        'X-Webhook-Timestamp': timestamp,
        'X-Webhook-Attempt': str(event.attempts + 1),
    }

    # HMAC 签名：V1 只签正文（兼容已接入方）；V2 把事件 ID 与时间戳一起签，防重放
    if api_key.webhook_secret:
        signature = _sign_payload(payload_bytes, api_key.webhook_secret)
        headers['X-Webhook-Signature'] = f'sha256={signature}'
        signed_v2 = f'{event.id}.{timestamp}.'.encode('utf-8') + payload_bytes
        headers['X-Webhook-Signature-V2'] = f'sha256={_sign_payload(signed_v2, api_key.webhook_secret)}'
    else:
        logger.warning(f'Webhook event {event.id}: api_key {api_key.id} has no webhook_secret, payload sent unsigned')

    try:
        # 发送前再校验一次 URL（防止配置后 DNS 改指内网），且不跟随重定向
        from .utils import validate_webhook_url
        validate_webhook_url(api_key.webhook_url)

        resp = requests.post(
            api_key.webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=10,
            allow_redirects=False,
        )
        if 300 <= resp.status_code < 400:
            raise requests.HTTPError(f'Redirect ({resp.status_code}) is not allowed', response=resp)
        resp.raise_for_status()

        event.status = 'sent'
        event.sent_at = datetime.now()
        event.last_error = None
        return True

    except Exception as e:
        event.attempts += 1
        # 错误信息不带目标 URL / 响应体，避免被用作内网探测的回显
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        event.last_error = (f'{type(e).__name__}' + (f' (HTTP {status})' if status else ''))[:500]

        if event.attempts >= MAX_ATTEMPTS:
            event.status = 'failed'
        else:
            # 固定 30 分钟间隔重试
            event.next_retry_at = datetime.now() + timedelta(seconds=RETRY_INTERVAL_SECONDS)

        return False


def push_pending_events():
    """推送所有待发送的事件

    查找所有 pending 状态且到达重试时间的事件，逐个推送。
    适用于定时任务调用。

    Returns:
        tuple: (sent_count, failed_count)
    """
    now = datetime.now()

    event_ids = [row.id for row in db.session.query(WebhookEvent.id).filter(
        WebhookEvent.status == 'pending',
        db.or_(
            WebhookEvent.next_retry_at.is_(None),
            WebhookEvent.next_retry_at <= now,
        ),
    ).order_by(WebhookEvent.created_at, WebhookEvent.id).limit(100).all()]

    sent = 0
    failed = 0

    # 逐条提交：一条的状态更新失败不会让整批已推送的事件回滚后被重复推送
    for event_id in event_ids:
        # 逐条加行锁后再读再推：推送期间业务事务不能改写这条的 payload
        # （emit_company_event 覆盖时 SKIP LOCKED 绕开它另建新事件），
        # 并发的另一个推送进程也会在锁释放后看到状态已变而跳过，不会重复推送
        event = WebhookEvent.query.filter(
            WebhookEvent.id == event_id,
            WebhookEvent.status == 'pending',
        ).with_for_update().populate_existing().first()
        if event is None:
            continue  # 已被别的推送进程处理 / 已删除
        ok = _send_event(event)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f'Webhook event {event.id}: failed to persist status: {e}')
            failed += 1
            continue
        if ok:
            sent += 1
        else:
            failed += 1

    if sent or failed:
        logger.info(f'Webhook push: {sent} sent, {failed} failed')

    return sent, failed
