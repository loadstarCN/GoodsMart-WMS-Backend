"""Batch client_batch_id (idempotent batch submission) and adjustments.details_modified_by

Revision ID: 9d3f7b2a4c61
Revises: 6b8f2d4c1e7a
Create Date: 2026-10-05

- sorting_batches / picking_batches / packing_batches 新增 client_batch_id（VARCHAR(64)，可空）：前端每次提交批次带一个
  标识，断网重试时原样带上，同一任务下重复提交不再重复记数（B-102）
- 唯一约束「同一任务内 client_batch_id 唯一」（显式命名 uq_<表>_task_client_batch_id）。NULL 不参与唯一判断
  （PostgreSQL / SQLite 默认行为），存量批次与不带 client_batch_id 的请求不受影响
- adjustments 新增 details_modified_by（可空，外键 users.id，RESTRICT，显式命名 fk_adjustments_details_modified_by）：
  最后增 / 改 / 删明细的人，审批人不能是他（409 16122，B-105）。存量调整单为 NULL（只按创建人判断，与原来一致）
- 新列都可空、无默认值：PostgreSQL 上 ADD COLUMN 不重写表；唯一约束建索引时短暂持表锁（批次表不大）
- downgrade：删约束 / 外键与列（client_batch_id 与修改人信息随之丢失，旧代码不读这些列）
"""
from alembic import op
import sqlalchemy as sa


revision = '9d3f7b2a4c61'
down_revision = '6b8f2d4c1e7a'
branch_labels = None
depends_on = None

BATCH_TABLES = (
    ('sorting_batches', 'sorting_task_id', 'uq_sorting_batches_task_client_batch_id'),
    ('picking_batches', 'picking_task_id', 'uq_picking_batches_task_client_batch_id'),
    ('packing_batches', 'packing_task_id', 'uq_packing_batches_task_client_batch_id'),
)
ADJUSTMENTS = 'adjustments'
MODIFIER_FK = 'fk_adjustments_details_modified_by'


def upgrade():
    for table, task_column, unique_name in BATCH_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('client_batch_id', sa.String(length=64), nullable=True))
            batch_op.create_unique_constraint(unique_name, [task_column, 'client_batch_id'])

    with op.batch_alter_table(ADJUSTMENTS, schema=None) as batch_op:
        batch_op.add_column(sa.Column('details_modified_by', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(MODIFIER_FK, 'users', ['details_modified_by'], ['id'], ondelete='RESTRICT')


def downgrade():
    with op.batch_alter_table(ADJUSTMENTS, schema=None) as batch_op:
        batch_op.drop_constraint(MODIFIER_FK, type_='foreignkey')
        batch_op.drop_column('details_modified_by')

    for table, _task_column, unique_name in reversed(BATCH_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(unique_name, type_='unique')
            batch_op.drop_column('client_batch_id')
