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

    # pending / sent / failed
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_retry_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.now)
    sent_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)

    # 关联
    api_key = db.relationship('APIKey', backref=db.backref('webhook_events', lazy='dynamic'))

    def __repr__(self):
        return f'<WebhookEvent {self.id} {self.event_type} {self.status}>'
