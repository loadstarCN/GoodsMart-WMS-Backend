"""Store optional recipient tax defaults for customs forms."""
from alembic import op
import sqlalchemy as sa
revision = 'b4e8a2d9f601'
down_revision = 'a3f9c2e7d415'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('recipients', sa.Column('tax_id', sa.String(100), nullable=True))
    op.add_column('recipients', sa.Column('tax_id_type', sa.String(30), nullable=True))

def downgrade():
    op.drop_column('recipients', 'tax_id_type')
    op.drop_column('recipients', 'tax_id')
