"""Record confirmation and resolution of receiving quantity differences."""
from alembic import op
import sqlalchemy as sa

revision = 'f8ad6ee13b71'
down_revision = 'e4b7c912a6d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sorting_tasks') as batch:
        batch.add_column(sa.Column('variance_reason', sa.String(500), nullable=True))
        batch.add_column(sa.Column('variance_resolution', sa.String(500), nullable=True))
        batch.add_column(sa.Column('variance_resolved_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('variance_resolved_by', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_sorting_variance_resolver', 'users', ['variance_resolved_by'], ['id'], ondelete='RESTRICT')


def downgrade():
    with op.batch_alter_table('sorting_tasks') as batch:
        batch.drop_constraint('fk_sorting_variance_resolver', type_='foreignkey')
        for column in ('variance_resolved_by', 'variance_resolved_at', 'variance_resolution', 'variance_reason'):
            batch.drop_column(column)
