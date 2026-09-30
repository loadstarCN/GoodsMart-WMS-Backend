"""DN carrier shipments: request states (pending / failed / unknown / dismissed), reason, ship-from country

Revision ID: a5e6f7a8b9c0
Revises: f4d5e6f7a8b9
Create Date: 2026-09-30

- dn_carrier_shipments.status 增加 pending（正在请求承运商）/ failed（承运商明确拒绝）/ unknown（结果不明）/
  dismissed（操作员确认承运商上没有这张运单）；部分唯一索引改为同一 DN 至多一条 pending / active / unknown
  （uq_dn_carrier_shipment_active → uq_dn_carrier_shipment_open）
- tracking_number 改为可空（pending / failed / 没拿到号码的 unknown 没有运单号）
- 新列：reason（状态原因）、error_message（错误摘要）、sender_country（建单时的发件国，取消时沿用）、
  updated_at（状态最后变化时间，存量用 cancelled_at / created_at 回填）、dismissed_at / dismissed_by
- downgrade：旧结构表达不了新状态，pending / failed / unknown / dismissed 的记录（及没有运单号的记录）直接删除
"""
from alembic import op
import sqlalchemy as sa


revision = 'a5e6f7a8b9c0'
down_revision = 'f4d5e6f7a8b9'
branch_labels = None
depends_on = None

TABLE = 'dn_carrier_shipments'
OLD_STATUSES = "status IN ('active','cancelled')"
NEW_STATUSES = "status IN ('pending','active','cancelled','failed','unknown','dismissed')"
OLD_UNIQUE_WHERE = "status = 'active'"
NEW_UNIQUE_WHERE = "status IN ('pending','active','unknown')"
FK_DISMISSED_BY = 'fk_dn_carrier_shipments_dismissed_by'


def upgrade():
    op.drop_index('uq_dn_carrier_shipment_active', table_name=TABLE)
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_constraint('chk_dn_carrier_shipment_status', type_='check')
        batch_op.create_check_constraint('chk_dn_carrier_shipment_status', NEW_STATUSES)
        batch_op.alter_column('tracking_number', existing_type=sa.String(length=100), nullable=True)
        batch_op.alter_column('status', existing_type=sa.String(length=10), existing_nullable=False,
                              server_default='pending')
        batch_op.add_column(sa.Column('reason', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('error_message', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('sender_country', sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('dismissed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('dismissed_by', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK_DISMISSED_BY, 'users', ['dismissed_by'], ['id'], ondelete='SET NULL')
    op.execute(f"UPDATE {TABLE} SET updated_at = COALESCE(cancelled_at, created_at) WHERE updated_at IS NULL")
    op.create_index(
        'uq_dn_carrier_shipment_open', TABLE, ['dn_id'], unique=True,
        postgresql_where=sa.text(NEW_UNIQUE_WHERE),
        sqlite_where=sa.text(NEW_UNIQUE_WHERE),
    )


def downgrade():
    op.drop_index('uq_dn_carrier_shipment_open', table_name=TABLE)
    op.execute(f"DELETE FROM {TABLE} WHERE NOT ({OLD_STATUSES}) OR tracking_number IS NULL")
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(FK_DISMISSED_BY, type_='foreignkey')
        batch_op.drop_column('dismissed_by')
        batch_op.drop_column('dismissed_at')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('sender_country')
        batch_op.drop_column('error_message')
        batch_op.drop_column('reason')
        batch_op.alter_column('status', existing_type=sa.String(length=10), existing_nullable=False,
                              server_default='active')
        batch_op.alter_column('tracking_number', existing_type=sa.String(length=100), nullable=False)
        batch_op.drop_constraint('chk_dn_carrier_shipment_status', type_='check')
        batch_op.create_check_constraint('chk_dn_carrier_shipment_status', OLD_STATUSES)
    op.create_index(
        'uq_dn_carrier_shipment_active', TABLE, ['dn_id'], unique=True,
        postgresql_where=sa.text(OLD_UNIQUE_WHERE),
        sqlite_where=sa.text(OLD_UNIQUE_WHERE),
    )
