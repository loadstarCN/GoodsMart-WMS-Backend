"""Preserve supplier-announced lot data independently from received inventory."""
from alembic import op
import sqlalchemy as sa

revision = 'f0c4d8e5a267'
down_revision = 'e9b3c7d4f156'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('asn_details') as batch:
        batch.add_column(sa.Column('announced_lots', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('asn_details') as batch:
        batch.drop_column('announced_lots')
