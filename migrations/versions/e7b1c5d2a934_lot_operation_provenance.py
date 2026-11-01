"""Preserve product lots through warehouse operations and outbound orders.

Revision ID: e7b1c5d2a934
Revises: d6a0b4f1c823
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7b1c5d2a934'
down_revision = 'd6a0b4f1c823'
branch_labels = None
depends_on = None

TABLES = ('sorting_task_details', 'picking_task_details', 'putaway_records', 'removal_records', 'transfer_records', 'cycle_count_details', 'adjustment_details')


def upgrade():
    op.create_table('company_lot_rules',
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('defaults', sa.JSON(), nullable=False), sa.Column('categories', sa.JSON(), nullable=False))
    op.create_table('goods_lot_rules',
        sa.Column('goods_id', sa.Integer(), sa.ForeignKey('goods.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('overrides', sa.JSON(), nullable=False))
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('lot_id', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_' + table + '_lot', 'goods_lots', ['lot_id'], ['id'], ondelete='RESTRICT')
    op.create_table('dn_stock_allocations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dn_id', sa.Integer(), sa.ForeignKey('dn.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('goods_id', sa.Integer(), sa.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('lot_id', sa.Integer(), sa.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('picked_quantity', sa.Integer(), nullable=False),
        sa.Column('packed_quantity', sa.Integer(), nullable=False),
        sa.CheckConstraint('picked_quantity >= 0 AND packed_quantity >= 0 AND packed_quantity <= picked_quantity', name='chk_dn_lot_quantities'))
    op.create_index('uix_dn_stock_allocation', 'dn_stock_allocations', ['dn_id', 'goods_id', sa.text('coalesce(lot_id, 0)')], unique=True)


def downgrade():
    bind = op.get_bind()
    for table in ('company_lot_rules', 'goods_lot_rules'):
        if bind.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first():
            raise RuntimeError('Cannot discard configured lot rules')
    if bind.execute(sa.text('SELECT 1 FROM dn_stock_allocations LIMIT 1')).first():
        raise RuntimeError('Cannot discard outbound lot provenance')
    for table in TABLES:
        if bind.execute(sa.text(f'SELECT 1 FROM {table} WHERE lot_id IS NOT NULL LIMIT 1')).first():
            raise RuntimeError('Cannot discard operation lot provenance')
    op.drop_table('dn_stock_allocations')
    op.drop_table('goods_lot_rules')
    op.drop_table('company_lot_rules')
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint('fk_' + table + '_lot', type_='foreignkey')
            batch.drop_column('lot_id')
