"""Identify count supplements without inferring historical rows."""
from alembic import op
import sqlalchemy as sa

revision = 'd8a2b6c3e045'
down_revision = 'c7f1a5b2d934'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cycle_count_details') as batch:
        batch.add_column(sa.Column('is_supplemental', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('cycle_count_details') as batch:
        batch.drop_column('is_supplemental')
