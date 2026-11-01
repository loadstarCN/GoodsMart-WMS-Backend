"""Explicit per-carton packed goods and lots, without historical inference."""
from alembic import op
import sqlalchemy as sa

revision = 'f4c8d2e9b601'
down_revision = 'e3b7c1d8a590'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('dn_packages', sa.Column('contents', sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM dn_packages WHERE contents IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Cannot discard recorded carton contents')
    op.drop_column('dn_packages', 'contents')
