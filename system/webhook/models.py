"""Webhook 事件模型"""
from datetime import datetime

from extensions import db
from sqlalchemy.dialects.postgresql import JSON


class WebhookEvent(db.Model):
    """Webhook 事件记录

    记录每个需要推送的 Webhook 事件，支持重试和状态追踪。
    """
    __tablename__ = 'webhook_events'

    id = db.Column(db.Integer, primary_key=True)
    api_key_id = db.Column(
        db.Integer,
        db.ForeignKey('api_keys.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(50), nullable=False, index=True)
    payload = db.Column(JSON, nullable=False)
    # 覆盖键：同一 Key、同类型、同 dedupe_key 的待发送事件只保留一条（新 payload 覆盖旧的），
    # 如 goods.spec_updated 用 goods:<id>。空值 = 不合并（单据类事件）
    dedupe_key = db.Column(db.String(100), nullable=True, index=True)

    # pending：待发送 / sending：推送进程已领取、正在发送（带租约）/ sent：已送达 /
    # failed：重试次数用完或没有回调地址 / superseded：作废，不再发送
    # （同一对象有了更新的事件，或 Key 已停用 / 已取消订阅；原因见 last_error）
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    # 已尝试次数：领取发送时就 +1 并提交，进程在发送中途被杀也会计数，不会无限重来
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_retry_at = db.Column(db.DateTime)
    # sending 租约到期时间：到期仍是 sending（进程被杀 / 卡死）就视为可重新领取；其他状态为空
    lease_expires_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.now)
    sent_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)

    # 关联
    api_key = db.relationship('APIKey', backref=db.backref('webhook_events', lazy='dynamic'))

    def __repr__(self):
        return f'<WebhookEvent {self.id} {self.event_type} {self.status}>'
