"""Prospective receipt provenance for putaway; historical timestamps stay unknown."""
from alembic import op
import sqlalchemy as sa
revision = 'a9d3e7f4c156'
down_revision = 'f8c2d6e3b045'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('asn', sa.Column('putaway_tracked', sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_table('receipt_pending_stock',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('asn_id', sa.Integer, sa.ForeignKey('asn.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('warehouse_id', sa.Integer, sa.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('goods_id', sa.Integer, sa.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('lot_id', sa.Integer, sa.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('good_received', sa.Integer, nullable=False),
        sa.Column('damaged_received', sa.Integer, nullable=False),
        sa.Column('good_remaining', sa.Integer, nullable=False),
        sa.Column('damaged_remaining', sa.Integer, nullable=False),
        sa.CheckConstraint('good_remaining >= 0 AND damaged_remaining >= 0', name='chk_receipt_pending_nonnegative'),
        sa.CheckConstraint('good_remaining <= good_received AND damaged_remaining <= damaged_received', name='chk_receipt_pending_received'))
    op.create_index('ix_receipt_pending_stock_asn_id', 'receipt_pending_stock', ['asn_id'])
    op.create_index('idx_receipt_pending_scope', 'receipt_pending_stock', ['warehouse_id','goods_id','lot_id'])
    with op.batch_alter_table('putaway_records') as batch:
        batch.add_column(sa.Column('receipt_source_id', sa.Integer, nullable=True))
        batch.create_foreign_key('fk_putaway_receipt_source', 'receipt_pending_stock', ['receipt_source_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_putaway_records_receipt_source_id', ['receipt_source_id'])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM receipt_pending_stock LIMIT 1')).first() or bind.execute(sa.text('SELECT 1 FROM asn WHERE putaway_tracked = true LIMIT 1')).first():
        raise RuntimeError('Cannot discard recorded putaway provenance')
    with op.batch_alter_table('putaway_records') as batch:
        batch.drop_index('ix_putaway_records_receipt_source_id')
        batch.drop_constraint('fk_putaway_receipt_source', type_='foreignkey')
        batch.drop_column('receipt_source_id')
    op.drop_table('receipt_pending_stock')
    with op.batch_alter_table('asn') as batch:
        batch.drop_column('putaway_tracked')
