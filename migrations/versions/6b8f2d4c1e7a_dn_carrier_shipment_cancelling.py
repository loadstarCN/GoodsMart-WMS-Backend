"""DN carrier shipments: cancelling state (cancellation in progress / result unclear)

Revision ID: 6b8f2d4c1e7a
Revises: 3a9d6c1e8b42
Create Date: 2026-10-05

- dn_carrier_shipments.status 增加 cancelling（有效运单正在取消，或取消结果不明）：取消改为三段（加锁标 cancelling
  提交 → 不持锁调 FedEx → 加锁落最终状态），中途失败 / 结果不明时记录停在 cancelling，拦发货，重试取消后落 cancelled
- CHECK 约束 chk_dn_carrier_shipment_status 加入 cancelling（显式命名，先删后建）
- 部分唯一索引 uq_dn_carrier_shipment_open（同一 DN 至多一条未结束的记录）加入 cancelling
- 只改约束与索引，不动列；已有数据都在新取值范围内，PostgreSQL 上可直接执行
- downgrade：旧结构表达不了 cancelling → 改回 active（reason cancel_unconfirmed）：旧代码里 active 可以再点取消，
  FedEx 回「已取消」时按已取消落库并清掉运单号 / 作废面单；同一 DN 至多一条未结束记录的约束不受影响
"""
from alembic import op
import sqlalchemy as sa


revision = '6b8f2d4c1e7a'
down_revision = '3a9d6c1e8b42'
branch_labels = None
depends_on = None

TABLE = 'dn_carrier_shipments'
CHECK_NAME = 'chk_dn_carrier_shipment_status'
INDEX_NAME = 'uq_dn_carrier_shipment_open'
OLD_STATUSES = "status IN ('pending','active','cancelled','failed','unknown','dismissed')"
NEW_STATUSES = "status IN ('pending','active','cancelled','failed','unknown','dismissed','cancelling')"
OLD_UNIQUE_WHERE = "status IN ('pending','active','unknown')"
NEW_UNIQUE_WHERE = "status IN ('pending','active','unknown','cancelling')"


def _swap(check_sql: str, unique_where: str):
    op.drop_index(INDEX_NAME, table_name=TABLE)
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_constraint(CHECK_NAME, type_='check')
        batch_op.create_check_constraint(CHECK_NAME, check_sql)
    op.create_index(
        INDEX_NAME, TABLE, ['dn_id'], unique=True,
        postgresql_where=sa.text(unique_where),
        sqlite_where=sa.text(unique_where),
    )


def upgrade():
    _swap(NEW_STATUSES, NEW_UNIQUE_WHERE)


def downgrade():
    op.execute(
        f"UPDATE {TABLE} SET status = 'active', reason = 'cancel_unconfirmed' WHERE status = 'cancelling'"
    )
    _swap(OLD_STATUSES, OLD_UNIQUE_WHERE)
