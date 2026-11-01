"""Warehouse operations and manual location ordering."""
from alembic import op
import sqlalchemy as sa

revision = 'a1d5e9f6b378'
down_revision = 'f0c4d8e5a267'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('warehouses') as batch:
        batch.add_column(sa.Column('operations', sa.JSON(), nullable=True))
    with op.batch_alter_table('locations') as batch:
        batch.add_column(sa.Column('map_rack_id', sa.String(100), nullable=True))
        for name in ('picking_order', 'rack_column', 'rack_level'):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('locations') as batch:
        batch.drop_column('map_rack_id')
        for name in ('picking_order', 'rack_column', 'rack_level'):
            batch.drop_column(name)
    with op.batch_alter_table('warehouses') as batch:
        batch.drop_column('operations')
