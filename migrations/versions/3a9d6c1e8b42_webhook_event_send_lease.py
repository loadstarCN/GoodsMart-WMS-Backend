"""Webhook events: send lease (sending state) and superseded state

Revision ID: 3a9d6c1e8b42
Revises: 152c6ebfb7ab
Create Date: 2026-10-04

- webhook_events.lease_expires_at：推送租约到期时间（可空，存量全部为空）。
  推送改为「加锁领取 → 不持锁发送 → 加锁回写」，领取时标 sending 并写租约；
  租约过期仍是 sending 的事件（推送进程中途被杀）视为可重新领取。
- status 新增取值 sending（推送中）/ superseded（作废：已有更新的同对象事件，或 Key 已停用 / 取消订阅）。
  status 是 String(20)、没有 CHECK 约束，无需改列。
- downgrade：旧代码只认 pending / sent / failed —— sending 退回 pending（照常重发），
  superseded 改为 failed（不再自动发送，last_error 保留作废原因）。
"""
from alembic import op
import sqlalchemy as sa


revision = '3a9d6c1e8b42'
down_revision = '152c6ebfb7ab'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lease_expires_at', sa.DateTime(), nullable=True))


def downgrade():
    op.execute("UPDATE webhook_events SET status = 'pending' WHERE status = 'sending'")
    op.execute("UPDATE webhook_events SET status = 'failed' WHERE status = 'superseded'")
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_column('lease_expires_at')
