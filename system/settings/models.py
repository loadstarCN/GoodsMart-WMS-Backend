"""系统设置模型 — 通用 key-value 存储"""
from datetime import datetime

from extensions import db


class SystemSetting(db.Model):
    __tablename__ = 'system_settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False, default='')
    is_secret = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<SystemSetting {self.key}>'
