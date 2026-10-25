"""Inventory: damaged part of the stock waiting to be put away (sorted_damage_stock)

Revision ID: e7b4c2a9d1f3
Revises: d4f2a8c6e1b7
Create Date: 2026-10-25

设计见 doc/库存数值系统说明.md。inventory 加 sorted_damage_stock（int，非空，默认 0）：待上架（sorted_stock）里的破损品数量，
是 sorted_stock 的一部分（不变式 0 ≤ sorted_damage_stock ≤ sorted_stock），只能上到 damaged 库位：
- 分拣完成（ASN completed）时破损数量（damage_quantity）同时记进 sorted_stock 与 sorted_damage_stock
- 上架到 damaged 库位先扣破损部分；上到 standard / return 库位只能用良品部分（sorted_stock − sorted_damage_stock），
  超出 → 400 15016
CHECK 约束 chk_inventory_sorted_damage_stock 兜底非负。
旧数据全部为 0 = 现有待上架都按良品（与升级前的行为一致；存量里分不出哪些是破损的）。
PostgreSQL 上 ADD COLUMN ... NOT NULL DEFAULT 常量不重写表（11+）；SQLite 走 batch（重建表）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'e7b4c2a9d1f3'
down_revision = 'd4f2a8c6e1b7'
branch_labels = None
depends_on = None

CHECK_NAME = 'chk_inventory_sorted_damage_stock'


def upgrade():
    with op.batch_alter_table('inventory', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sorted_damage_stock', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_check_constraint(CHECK_NAME, 'sorted_damage_stock >= 0')


def downgrade():
    with op.batch_alter_table('inventory', schema=None) as batch_op:
        batch_op.drop_constraint(CHECK_NAME, type_='check')
        batch_op.drop_column('sorted_damage_stock')
