"""Company product label defaults."""
from alembic import op
import sqlalchemy as sa

revision = 'd6a0b4f1c823'
down_revision = 'c5f9a3e0b712'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('company_product_labels',
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('template', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('company_product_labels')
