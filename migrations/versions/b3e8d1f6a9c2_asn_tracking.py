"""Inbound flow (phase 3): carrier tracking result on ASNs

Revision ID: b3e8d1f6a9c2
Revises: a7c4e2f9b3d1
Create Date: 2026-10-25

设计见 doc/shipping-flow.md §7.1。asn 加入库单运单号的最近一次跟踪结果（承运商的跟踪方式是 api、插件 supports_tracking
且 tracks_any_number 时，在入库流程里手动刷新：POST /asn/<id>/track）：
- tracking_status（varchar 20）：TRACK_* 之一（label_created / in_transit / out_for_delivery / delivered / exception /
  unknown）
- tracking_detail（json）：{code, status_text, picked_up_at, delivered_at, estimated_delivery, events[], error}
- tracking_synced_at（timestamp）：最近一次查询的时刻
只加可空列（PostgreSQL 上 ADD COLUMN 不重写表），旧数据全部为空 = 没查过。
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3e8d1f6a9c2'
down_revision = 'a7c4e2f9b3d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('asn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tracking_status', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('tracking_detail', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('tracking_synced_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('asn', schema=None) as batch_op:
        batch_op.drop_column('tracking_synced_at')
        batch_op.drop_column('tracking_detail')
        batch_op.drop_column('tracking_status')
