"""Carrier pickups (FedEx Pickup Request API) and dn_carrier_shipments.pickup_id

Revision ID: c7a1e5d3b902
Revises: 9d3f7b2a4c61
Create Date: 2026-10-21

- carrier_pickups：向承运商预约的上门揽收（仓库、揽收日、准备好 / 最晚揽收时间、件数、总重量、所带运单快照、
  揽收地点、确认号、FedEx 站点代码、transactionId、预约 / 取消 / 作废人与时间）；状态 pending / scheduled /
  cancelled / failed / unknown / dismissed（检查约束）
- dn_carrier_shipments 新增 pickup_id（可空，外键 carrier_pickups.id，SET NULL，带索引）：最近一次包含这张运单的
  揽收预约。新列可空、无默认值：PostgreSQL 上 ADD COLUMN 不重写表
- downgrade：删外键 / 列与表（揽收预约记录随之丢失，旧代码不读这些数据）
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7a1e5d3b902'
down_revision = '9d3f7b2a4c61'
branch_labels = None
depends_on = None

PICKUP_FK = 'fk_dn_carrier_shipments_pickup_id'
PICKUP_INDEX = 'ix_dn_carrier_shipments_pickup_id'


def upgrade():
    op.create_table(
        'carrier_pickups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('carrier', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('reason', sa.String(length=40), nullable=True),
        sa.Column('error_message', sa.String(length=500), nullable=True),
        sa.Column('pickup_date', sa.Date(), nullable=False),
        sa.Column('ready_time', sa.String(length=5), nullable=False),
        sa.Column('close_time', sa.String(length=5), nullable=False),
        sa.Column('package_count', sa.Integer(), nullable=False),
        sa.Column('total_weight_kg', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('shipments', sa.JSON(), nullable=True),
        sa.Column('pickup_location', sa.JSON(), nullable=True),
        sa.Column('remarks', sa.String(length=100), nullable=True),
        sa.Column('notification_email', sa.String(length=120), nullable=True),
        sa.Column('confirmation_code', sa.String(length=40), nullable=True),
        sa.Column('location_code', sa.String(length=20), nullable=True),
        sa.Column('transaction_id', sa.String(length=100), nullable=True),
        sa.Column('cancel_transaction_id', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by', sa.Integer(), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(), nullable=True),
        sa.Column('dismissed_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['dismissed_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('pending','scheduled','cancelled','failed','unknown','dismissed')",
            name='chk_carrier_pickup_status',
        ),
    )
    op.create_index('idx_carrier_pickup_warehouse_date', 'carrier_pickups', ['warehouse_id', 'pickup_date'],
                    unique=False)

    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pickup_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(PICKUP_FK, 'carrier_pickups', ['pickup_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(PICKUP_INDEX, ['pickup_id'], unique=False)


def downgrade():
    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.drop_index(PICKUP_INDEX)
        batch_op.drop_constraint(PICKUP_FK, type_='foreignkey')
        batch_op.drop_column('pickup_id')

    op.drop_index('idx_carrier_pickup_warehouse_date', table_name='carrier_pickups')
    op.drop_table('carrier_pickups')
