"""Warehouse layout draft and immutable published versions."""
from alembic import op
import sqlalchemy as sa

revision = 'c5f9a3e0b712'
down_revision = 'b4e8a2d9f601'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('warehouse_layouts',
        sa.Column('warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('published_version', sa.Integer(), nullable=False),
        sa.Column('draft', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('warehouse_layout_versions',
        sa.Column('warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('layout', sa.JSON(), nullable=False),
        sa.Column('published_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('warehouse_layout_versions')
    op.drop_table('warehouse_layouts')
