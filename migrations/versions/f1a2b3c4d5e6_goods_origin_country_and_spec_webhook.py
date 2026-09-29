"""Goods origin country; goods.spec_updated webhook subscriptions and dedupe key

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-09-29

- goods.origin_country：原产国（ISO 3166-1 alpha-2，可空 + 索引）。存量一律为空，不回填默认值。
- api_keys.webhook_subscriptions：订阅的广播事件（JSON 数组，默认 []）。
  存量 Key 全部为 []，即不会收到 goods.spec_updated，需要的 Key 显式订阅。
- webhook_events.dedupe_key：待发送事件的覆盖键（可空 + 索引）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'e0f1a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('goods', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin_country', sa.String(length=2), nullable=True))
        batch_op.create_index('ix_goods_origin_country', ['origin_country'], unique=False)

    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'webhook_subscriptions', sa.JSON(), nullable=True, server_default=sa.text("'[]'"),
        ))

    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dedupe_key', sa.String(length=100), nullable=True))
        batch_op.create_index('ix_webhook_events_dedupe_key', ['dedupe_key'], unique=False)


def downgrade():
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_index('ix_webhook_events_dedupe_key')
        batch_op.drop_column('dedupe_key')

    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_column('webhook_subscriptions')

    with op.batch_alter_table('goods', schema=None) as batch_op:
        batch_op.drop_index('ix_goods_origin_country')
        batch_op.drop_column('origin_country')
