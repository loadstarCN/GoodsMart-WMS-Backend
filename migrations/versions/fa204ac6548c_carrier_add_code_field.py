"""Carrier add code field

Revision ID: fa204ac6548c
Revises:
Create Date: 2026-03-18 15:21:06.424735

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'fa204ac6548c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('carriers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.String(length=30), nullable=True))


def downgrade():
    with op.batch_alter_table('carriers', schema=None) as batch_op:
        batch_op.drop_column('code')
