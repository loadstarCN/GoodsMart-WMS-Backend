"""Time zone generalisation (phase 3): warehouse time zone

Revision ID: d4f2a8c6e1b7
Revises: b3e8d1f6a9c2
Create Date: 2026-10-25

设计见 doc/timezone-utc.md §2。warehouses 加 timezone（varchar 64，可空）：仓库所在地的 IANA 时区名（如 Asia/Tokyo、
America/Los_Angeles）。空 = 用默认时区（WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC），旧数据全部为空、行为不变。
只加可空列（PostgreSQL 上 ADD COLUMN 不重写表）。

时刻列从 timestamp 改成 timestamptz 不在迁移里做（要停机、备份，按旧口径时区换算）：生产走手动工作流
Migrate WMS Time to UTC（scripts/manual/migrate_utc.py），自建实例用 flask wms convert-utc，见 doc/timezone-utc.md §5。
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4f2a8c6e1b7'
down_revision = 'b3e8d1f6a9c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timezone', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_column('timezone')
