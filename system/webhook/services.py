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

# 重试间隔（秒）：1分钟、5分钟、30分钟、2小时、6小时
RETRY_INTERVALS = [60, 300, 1800, 7200, 21600]
MAX_ATTEMPTS = 5


def emit(event_type, payload):
    """创建 Webhook 事件记录

    为所有配置了 webhook_url 的活跃 APIKey 创建事件。

    Args:
        event_type: 事件类型，如 'dn.delivered', 'asn.completed'
        payload: 事件数据（dict）
    """
    api_keys = APIKey.query.filter(
        APIKey.is_active == True,
        APIKey.webhook_url.isnot(None),
        APIKey.webhook_url != '',
    ).all()

    for api_key in api_keys:
        event = WebhookEvent(
            api_key_id=api_key.id,
            event_type=event_type,
            payload=payload,
            status='pending',
        )
        db.session.add(event)

    db.session.flush()


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

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Event': event.event_type,
    }

    # HMAC 签名
    if api_key.webhook_secret:
        signature = _sign_payload(payload_bytes, api_key.webhook_secret)
        headers['X-Webhook-Signature'] = f'sha256={signature}'

    try:
        resp = requests.post(
            api_key.webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()

        event.status = 'sent'
        event.sent_at = datetime.now()
        event.last_error = None
        return True

    except Exception as e:
        event.attempts += 1
        event.last_error = str(e)[:500]

        if event.attempts >= MAX_ATTEMPTS:
            event.status = 'failed'
        else:
            # 指数退避
            interval = RETRY_INTERVALS[min(event.attempts - 1, len(RETRY_INTERVALS) - 1)]
            event.next_retry_at = datetime.now() + timedelta(seconds=interval)

        return False


def push_pending_events():
    """推送所有待发送的事件

    查找所有 pending 状态且到达重试时间的事件，逐个推送。
    适用于定时任务调用。

    Returns:
        tuple: (sent_count, failed_count)
    """
    now = datetime.now()

    events = WebhookEvent.query.filter(
        WebhookEvent.status == 'pending',
        db.or_(
            WebhookEvent.next_retry_at.is_(None),
            WebhookEvent.next_retry_at <= now,
        ),
    ).order_by(WebhookEvent.created_at).limit(100).all()

    sent = 0
    failed = 0

    for event in events:
        if _send_event(event):
            sent += 1
        else:
            failed += 1

    db.session.commit()

    if sent or failed:
        logger.info(f'Webhook push: {sent} sent, {failed} failed')

    return sent, failed
