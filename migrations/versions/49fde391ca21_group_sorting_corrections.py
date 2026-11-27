"""Group the rows of a sorting quantity correction request."""
from alembic import op
import sqlalchemy as sa

revision = '49fde391ca21'
down_revision = 'f8ad6ee13b71'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sorting_quantity_corrections', sa.Column('request_group', sa.String(36), nullable=True))


def downgrade():
    with op.batch_alter_table('sorting_quantity_corrections') as batch:
        batch.drop_column('request_group')
