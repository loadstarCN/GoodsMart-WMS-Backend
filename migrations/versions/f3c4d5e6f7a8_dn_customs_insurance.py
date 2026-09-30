"""DN customs: insurance charge and declared value for carriage

Revision ID: f3c4d5e6f7a8
Revises: f2b3c4d5e6f7
Create Date: 2026-09-30

- dn_customs.insurance_charge：运送保险费（发票上 Freight 下单列并计入总额；没投保为空）
- dn_customs.declared_value_carriage：运送申告价额（仓库在承运商系统登记出货时填写；没投保为空）
"""
from alembic import op
import sqlalchemy as sa


revision = 'f3c4d5e6f7a8'
down_revision = 'f2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dn_customs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('insurance_charge', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('declared_value_carriage', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('dn_customs', schema=None) as batch_op:
        batch_op.drop_column('declared_value_carriage')
        batch_op.drop_column('insurance_charge')
