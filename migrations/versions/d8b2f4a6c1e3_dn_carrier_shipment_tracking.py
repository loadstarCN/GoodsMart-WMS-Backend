"""Carrier tracking on DN carrier shipments

Revision ID: d8b2f4a6c1e3
Revises: c7e1a9d3f5b2
Create Date: 2026-10-23

dn_carrier_shipments 加跟踪结果（承运商对接插件 supports_tracking，定时任务查）：tracking_status / tracking_code /
tracking_detail / tracking_synced_at，以及按跟踪自动完成发货 / 签收没做成的原因 tracking_problem。只加可空列。
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8b2f4a6c1e3'
down_revision = 'c7e1a9d3f5b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tracking_status', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('tracking_code', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('tracking_detail', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('tracking_synced_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('tracking_problem', sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.drop_column('tracking_problem')
        batch_op.drop_column('tracking_synced_at')
        batch_op.drop_column('tracking_detail')
        batch_op.drop_column('tracking_code')
        batch_op.drop_column('tracking_status')
