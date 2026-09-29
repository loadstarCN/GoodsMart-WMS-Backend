"""Master data: supplier.email unique per company; goods.created_by nullable

Revision ID: d9e2f3a4b5c6
Revises: c8d1e2f3a4b5
Create Date: 2026-09-29

- suppliers.email 原来是全局唯一（跨公司会冲突、可被用来枚举别家供应商邮箱），
  改为 (company_id, email) 联合唯一 uq_company_supplier_email。
- goods.created_by 声明了 ondelete='SET NULL' 却是 NOT NULL，删创建人必然报错，改为可空。

旧的全局唯一约束名字随方言不同（PostgreSQL: suppliers_email_key、MySQL: email、
SQLite: 匿名），这里按反射结果动态处理；SQLite 用 batch 模式重建表。
"""
from alembic import op
import sqlalchemy as sa


revision = 'd9e2f3a4b5c6'
down_revision = 'c8d1e2f3a4b5'
branch_labels = None
depends_on = None

NEW_UNIQUE = 'uq_company_supplier_email'
# SQLite 上的匿名 UNIQUE 约束只能借 naming_convention 反射出一个名字后再 drop
SQLITE_NAMING = {'uq': 'uq_%(table_name)s_%(column_0_name)s'}


def _email_unique_constraints(bind):
    """返回 suppliers 表上只覆盖 email 一列的唯一约束名（None 表示匿名）"""
    inspector = sa.inspect(bind)
    return [
        uc.get('name')
        for uc in inspector.get_unique_constraints('suppliers')
        if list(uc.get('column_names') or []) == ['email']
    ]


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'
    old_names = _email_unique_constraints(bind)

    kwargs = {'naming_convention': SQLITE_NAMING} if is_sqlite else {}
    with op.batch_alter_table('suppliers', schema=None, **kwargs) as batch_op:
        for name in old_names:
            batch_op.drop_constraint(name or 'uq_suppliers_email', type_='unique')
        batch_op.create_unique_constraint(NEW_UNIQUE, ['company_id', 'email'])

    with op.batch_alter_table('goods', schema=None) as batch_op:
        batch_op.alter_column('created_by', existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table('goods', schema=None) as batch_op:
        batch_op.alter_column('created_by', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_constraint(NEW_UNIQUE, type_='unique')
        batch_op.create_unique_constraint('uq_suppliers_email', ['email'])
