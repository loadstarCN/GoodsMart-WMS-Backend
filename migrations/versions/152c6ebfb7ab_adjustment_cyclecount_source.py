"""Adjustments: source cycle count task (cyclecount_id)

Revision ID: 152c6ebfb7ab
Revises: a5e6f7a8b9c0
Create Date: 2026-10-04

- adjustments 新增 cyclecount_id（可空，外键 cycle_count_tasks.id，RESTRICT）：由盘点单生成的调整单记录来源，
  同一盘点单已有启用中的调整单时再生成返回 409（16086，判断在 AdjustmentService 中、持盘点单行锁）
- 显式命名外键 fk_adjustments_cyclecount_id 与索引 idx_adjustment_cyclecount
- 存量调整单无法可靠回溯来源（adjustment_reason 可被编辑），保持 NULL；新列可空、无默认值，
  PostgreSQL 上 ADD COLUMN 不重写表，外键校验只扫一遍全 NULL 的列
- downgrade：删除索引、外键与列（来源信息随之丢失）
"""
from alembic import op
import sqlalchemy as sa


revision = '152c6ebfb7ab'
down_revision = 'a5e6f7a8b9c0'
branch_labels = None
depends_on = None

TABLE = 'adjustments'
FK_NAME = 'fk_adjustments_cyclecount_id'
INDEX_NAME = 'idx_adjustment_cyclecount'


def upgrade():
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column('cyclecount_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK_NAME, 'cycle_count_tasks', ['cyclecount_id'], ['id'], ondelete='RESTRICT')
        batch_op.create_index(INDEX_NAME, ['cyclecount_id'], unique=False)


def downgrade():
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(INDEX_NAME)
        batch_op.drop_constraint(FK_NAME, type_='foreignkey')
        batch_op.drop_column('cyclecount_id')
