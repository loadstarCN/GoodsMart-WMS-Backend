"""Shipping flow phase 2: carrier file mode, structured addresses, shipping options

Revision ID: a7c4e2f9b3d1
Revises: 55e9fa27fb9e
Create Date: 2026-10-25

设计见 doc/shipping-flow.md §6。
- carriers：文件方式 file_preset（varchar 60）/ file_template（json）/ file_account（json）。
  waybill_method 的 CHECK 约束第一期已经允许 file，不用改。
- recipients：结构化地址 admin_area / locality / dependent_locality / address_line1 / address_line2 / organization /
  latin（json）；zip_code 即 postal_code（不加列）。
- warehouses：admin_area / locality / address_line1 / address_line2 / latin（json）。
- dn：ship_to（json，收件地址快照）、shipping_options（json，运单服务选项）。
- carrier_file_batches：导出 / 导入批次（carrier_id → carriers CASCADE、warehouse_id → warehouses CASCADE、
  created_by → users SET NULL）。
- dn_carrier_shipments：file_batch_id（→ carrier_file_batches SET NULL，带索引）；状态加 exported（文件方式已导出、
  待导回运单号，属于未结束状态）：CHECK chk_dn_carrier_shipment_status 先删后建，部分唯一索引
  uq_dn_carrier_shipment_open（同一 DN 至多一条未结束的记录）的条件加 exported（先删后建）。
- 全部是可空的新列 / 新表，PostgreSQL 上 ADD COLUMN 不重写表；已有数据都在新的取值范围内。
  SQLite 走 batch（重建表），部分唯一索引先删后建（batch 重建时不一定带得上 WHERE）。
- downgrade：旧代码不认识 exported → 先把 exported 记录改成 cancelled（reason discarded，只是本地的导出记录），
  再恢复约束 / 索引、去掉新列与新表。
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c4e2f9b3d1'
down_revision = '55e9fa27fb9e'
branch_labels = None
depends_on = None

SHIPMENTS = 'dn_carrier_shipments'
CHECK_NAME = 'chk_dn_carrier_shipment_status'
OPEN_INDEX = 'uq_dn_carrier_shipment_open'
OLD_STATUSES = "status IN ('pending','active','cancelled','failed','unknown','dismissed','cancelling')"
NEW_STATUSES = "status IN ('pending','active','cancelled','failed','unknown','dismissed','cancelling','exported')"
OLD_OPEN_WHERE = "status IN ('pending','active','unknown','cancelling')"
NEW_OPEN_WHERE = "status IN ('pending','active','unknown','cancelling','exported')"
BATCH_FK = 'fk_dn_carrier_shipments_file_batch_id'
BATCH_INDEX = 'ix_dn_carrier_shipments_file_batch_id'


def _drop_open_index():
    op.drop_index(OPEN_INDEX, table_name=SHIPMENTS)


def _create_open_index(where: str):
    op.create_index(
        OPEN_INDEX, SHIPMENTS, ['dn_id'], unique=True,
        postgresql_where=sa.text(where),
        sqlite_where=sa.text(where),
    )


def upgrade():
    with op.batch_alter_table('carriers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('file_preset', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('file_template', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('file_account', sa.JSON(), nullable=True))

    with op.batch_alter_table('recipients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('admin_area', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('locality', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('dependent_locality', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('address_line1', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('address_line2', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('organization', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('latin', sa.JSON(), nullable=True))

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('admin_area', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('locality', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('address_line1', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('address_line2', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('latin', sa.JSON(), nullable=True))

    with op.batch_alter_table('dn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ship_to', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('shipping_options', sa.JSON(), nullable=True))

    op.create_table(
        'carrier_file_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('carrier_id', sa.Integer(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['carrier_id'], ['carriers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('export','import')", name='chk_carrier_file_batch_kind'),
    )
    op.create_index('idx_carrier_file_batch_carrier', 'carrier_file_batches', ['carrier_id', 'created_at'],
                    unique=False)
    op.create_index('idx_carrier_file_batch_warehouse', 'carrier_file_batches', ['warehouse_id', 'created_at'],
                    unique=False)

    # 部分唯一索引先删后建（SQLite 的 batch 重建表时不一定带得上 WHERE）
    _drop_open_index()
    with op.batch_alter_table(SHIPMENTS, schema=None) as batch_op:
        batch_op.drop_constraint(CHECK_NAME, type_='check')
        batch_op.create_check_constraint(CHECK_NAME, NEW_STATUSES)
        batch_op.add_column(sa.Column('file_batch_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(BATCH_FK, 'carrier_file_batches', ['file_batch_id'], ['id'],
                                    ondelete='SET NULL')
    _create_open_index(NEW_OPEN_WHERE)
    op.create_index(BATCH_INDEX, SHIPMENTS, ['file_batch_id'], unique=False)


def downgrade():
    # 旧代码不认识 exported：只是本地的导出记录，按作废导出处理
    op.execute(f"UPDATE {SHIPMENTS} SET status = 'cancelled', reason = 'discarded' WHERE status = 'exported'")
    op.drop_index(BATCH_INDEX, table_name=SHIPMENTS)
    _drop_open_index()
    with op.batch_alter_table(SHIPMENTS, schema=None) as batch_op:
        # 删列时外键一并删掉（PostgreSQL 的 DROP COLUMN 连带删除涉及该列的约束；SQLite 重建表）
        batch_op.drop_column('file_batch_id')
        batch_op.drop_constraint(CHECK_NAME, type_='check')
        batch_op.create_check_constraint(CHECK_NAME, OLD_STATUSES)
    _create_open_index(OLD_OPEN_WHERE)

    op.drop_index('idx_carrier_file_batch_warehouse', table_name='carrier_file_batches')
    op.drop_index('idx_carrier_file_batch_carrier', table_name='carrier_file_batches')
    op.drop_table('carrier_file_batches')

    with op.batch_alter_table('dn', schema=None) as batch_op:
        batch_op.drop_column('shipping_options')
        batch_op.drop_column('ship_to')

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        for column in ('latin', 'address_line2', 'address_line1', 'locality', 'admin_area'):
            batch_op.drop_column(column)

    with op.batch_alter_table('recipients', schema=None) as batch_op:
        for column in ('latin', 'organization', 'address_line2', 'address_line1', 'dependent_locality', 'locality',
                       'admin_area'):
            batch_op.drop_column(column)

    with op.batch_alter_table('carriers', schema=None) as batch_op:
        batch_op.drop_column('file_account')
        batch_op.drop_column('file_template')
        batch_op.drop_column('file_preset')
