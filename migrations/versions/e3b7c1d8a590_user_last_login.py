"""Record successful password logins; historical timestamps remain unknown."""
from alembic import op
import sqlalchemy as sa

revision = 'e3b7c1d8a590'
down_revision = 'd2a6b0c7f489'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('users', 'last_login_at')
