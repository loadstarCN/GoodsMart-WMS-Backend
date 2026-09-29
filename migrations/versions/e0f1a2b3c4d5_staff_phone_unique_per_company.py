"""Staff: phone unique per company instead of globally

Revision ID: e0f1a2b3c4d5
Revises: d9e2f3a4b5c6
Create Date: 2026-09-29

staff.phone 原来是全局唯一：不同公司的员工不能用同一个手机号，且可以借此探测某手机号
是否在别家公司注册过。改为 (company_id, phone) 联合唯一 uq_staff_company_phone。
旧约束名随方言不同（PostgreSQL: staff_phone_key、SQLite: 匿名），按反射结果动态处理。
"""
from alembic import op
import sqlalchemy as sa


revision = 'e0f1a2b3c4d5'
down_revision = 'd9e2f3a4b5c6'
branch_labels = None
depends_on = None

NEW_UNIQUE = 'uq_staff_company_phone'
SQLITE_NAMING = {'uq': 'uq_%(table_name)s_%(column_0_name)s'}


def _phone_unique_constraints(bind):
    inspector = sa.inspect(bind)
    return [
        uc.get('name')
        for uc in inspector.get_unique_constraints('staff')
        if list(uc.get('column_names') or []) == ['phone']
    ]


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'
    old_names = _phone_unique_constraints(bind)

    kwargs = {'naming_convention': SQLITE_NAMING} if is_sqlite else {}
    with op.batch_alter_table('staff', schema=None, **kwargs) as batch_op:
        for name in old_names:
            batch_op.drop_constraint(name or 'uq_staff_phone', type_='unique')
        batch_op.create_unique_constraint(NEW_UNIQUE, ['company_id', 'phone'])


def downgrade():
    with op.batch_alter_table('staff', schema=None) as batch_op:
        batch_op.drop_constraint(NEW_UNIQUE, type_='unique')
        batch_op.create_unique_constraint('uq_staff_phone', ['phone'])
