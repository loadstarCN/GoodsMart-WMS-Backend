"""Spec capture on receiving: warehouse defaults, request on the ASN, result on ASN lines

Revision ID: b8d3f1a7c2e5
Revises: e7b4c2a9d1f3
Create Date: 2026-10-25

收货规格采集（warehouse/asn/spec_capture.py，README「Spec capture on receiving」）：
- warehouses：default_spec_capture_fields（json，默认 []：不采集）、default_spec_capture_mode（varchar 16，非空，默认
  missing）、spec_capture_enforcement（varchar 8，非空，默认 warn）；CHECK 兜底取值
- asn：spec_capture_fields（json，可空）、spec_capture_mode（varchar 16，可空）—— 建单时从仓库默认值复制的快照；
  存量单据为空 = 不采集
- asn_details：spec_captured_at（timestamptz）、spec_captured_by（→ users，SET NULL）、spec_capture_note（varchar 255，
  跳过原因）。待采集项不落库
存量仓库全部为「不采集 / missing / warn」，现有流程不变。PostgreSQL 上 ADD COLUMN（可空，或非空 + 常量默认值）不重写表；
SQLite 走 batch（重建表）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d3f1a7c2e5'
down_revision = 'e7b4c2a9d1f3'
branch_labels = None
depends_on = None

WAREHOUSE_MODE_CHECK = 'chk_warehouse_spec_capture_mode'
WAREHOUSE_ENFORCEMENT_CHECK = 'chk_warehouse_spec_capture_enforcement'
ASN_MODE_CHECK = 'chk_asn_spec_capture_mode'
CAPTURED_BY_FK = 'fk_asn_details_spec_captured_by'


def upgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('default_spec_capture_fields', sa.JSON(), nullable=True,
                                      server_default=sa.text("'[]'")))
        batch_op.add_column(sa.Column('default_spec_capture_mode', sa.String(length=16), nullable=False,
                                      server_default='missing'))
        batch_op.add_column(sa.Column('spec_capture_enforcement', sa.String(length=8), nullable=False,
                                      server_default='warn'))
        batch_op.create_check_constraint(WAREHOUSE_MODE_CHECK, "default_spec_capture_mode IN ('missing','remeasure')")
        batch_op.create_check_constraint(WAREHOUSE_ENFORCEMENT_CHECK, "spec_capture_enforcement IN ('warn','block')")

    with op.batch_alter_table('asn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('spec_capture_fields', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('spec_capture_mode', sa.String(length=16), nullable=True))
        batch_op.create_check_constraint(
            ASN_MODE_CHECK, "spec_capture_mode IS NULL OR spec_capture_mode IN ('missing','remeasure')")

    with op.batch_alter_table('asn_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('spec_captured_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('spec_captured_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('spec_capture_note', sa.String(length=255), nullable=True))
        batch_op.create_foreign_key(CAPTURED_BY_FK, 'users', ['spec_captured_by'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('asn_details', schema=None) as batch_op:
        batch_op.drop_constraint(CAPTURED_BY_FK, type_='foreignkey')
        batch_op.drop_column('spec_capture_note')
        batch_op.drop_column('spec_captured_by')
        batch_op.drop_column('spec_captured_at')

    with op.batch_alter_table('asn', schema=None) as batch_op:
        batch_op.drop_constraint(ASN_MODE_CHECK, type_='check')
        batch_op.drop_column('spec_capture_mode')
        batch_op.drop_column('spec_capture_fields')

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_constraint(WAREHOUSE_ENFORCEMENT_CHECK, type_='check')
        batch_op.drop_constraint(WAREHOUSE_MODE_CHECK, type_='check')
        batch_op.drop_column('spec_capture_enforcement')
        batch_op.drop_column('default_spec_capture_mode')
        batch_op.drop_column('default_spec_capture_fields')
