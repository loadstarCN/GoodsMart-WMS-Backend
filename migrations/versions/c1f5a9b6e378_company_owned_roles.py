"""Company-owned roles with stable assignment codes and revision checks."""
from alembic import op
import sqlalchemy as sa
revision = 'c1f5a9b6e378'
down_revision = 'b0e4f8a5d267'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roles') as batch:
        batch.add_column(sa.Column('company_id',sa.Integer,nullable=True))
        batch.add_column(sa.Column('display_name',sa.String(80),nullable=True))
        batch.add_column(sa.Column('revision',sa.Integer,nullable=False,server_default='1'))
        batch.create_foreign_key('fk_role_company','companies',['company_id'],['id'],ondelete='RESTRICT')
        batch.create_index('ix_roles_company_id',['company_id'])
    table=sa.table('permissions',sa.column('name',sa.String),sa.column('description',sa.String))
    for name,description in [('company_roles_read','Read company roles'),('company_roles_edit','Manage company roles')]:
        if not op.get_bind().execute(sa.select(table.c.name).where(table.c.name==name)).first():
            op.bulk_insert(table,[dict(name=name,description=description)])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM roles WHERE company_id IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Cannot discard company role ownership')
    with op.batch_alter_table('roles') as batch:
        batch.drop_index('ix_roles_company_id')
        batch.drop_constraint('fk_role_company',type_='foreignkey')
        for name in ('company_id','display_name','revision'):
            batch.drop_column(name)
