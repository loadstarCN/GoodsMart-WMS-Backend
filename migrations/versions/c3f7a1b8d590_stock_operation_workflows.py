"""Supplier-return inventory pools, audited lot holds, warehouse transfers and customer returns."""
from alembic import op
import sqlalchemy as sa

revision = 'c3f7a1b8d590'
down_revision = 'b2e6f0a7c489'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('inventory', sa.Column('damaged_dn_stock', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('inventory', sa.Column('return_dn_stock', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('goods_lots', sa.Column('status_revision', sa.Integer(), nullable=False, server_default='0'))
    with op.batch_alter_table('dn') as b:
        b.add_column(sa.Column('stock_source', sa.String(10), nullable=False, server_default='standard'))
        b.add_column(sa.Column('supplier_id', sa.Integer()))
        b.add_column(sa.Column('purpose_reason', sa.String(255)))
        b.create_foreign_key('fk_dn_supplier_return', 'suppliers', ['supplier_id'], ['id'], ondelete='RESTRICT')
    # Existing orders keep their original standard-stock reservation semantics.

    op.create_table('goods_lot_status_changes',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('lot_id', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('previous_status', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('operator_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operator_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lot_id', 'revision', name='uq_lot_status_revision')
    )

    op.create_index(op.f('ix_goods_lot_status_changes_lot_id'), 'goods_lot_status_changes', ['lot_id'], unique=False)

    op.create_table('warehouse_transfer_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_warehouse_id', sa.Integer(), nullable=False),
    sa.Column('destination_warehouse_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('first_shipped_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['destination_warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_warehouse_transfer_orders_destination_warehouse_id'), 'warehouse_transfer_orders', ['destination_warehouse_id'], unique=False)

    op.create_index(op.f('ix_warehouse_transfer_orders_source_warehouse_id'), 'warehouse_transfer_orders', ['source_warehouse_id'], unique=False)

    op.create_table('warehouse_transfer_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('goods_id', sa.Integer(), nullable=False),
    sa.Column('lot_id', sa.Integer(), nullable=True),
    sa.Column('source_location_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('cancelled', sa.Integer(), server_default='0', nullable=False),
    sa.Column('shipped', sa.Integer(), nullable=False),
    sa.Column('received', sa.Integer(), nullable=False),
    sa.Column('lost', sa.Integer(), nullable=False),
    sa.CheckConstraint('quantity >= shipped + cancelled AND cancelled >= 0 AND shipped >= received + lost AND received >= 0 AND lost >= 0', name='chk_transfer_order_quantities'),
    sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['warehouse_transfer_orders.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_warehouse_transfer_lines_order_id'), 'warehouse_transfer_lines', ['order_id'], unique=False)

    op.create_table('stock_operation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('result_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('operator_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['operator_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'document_id', 'request_id', name='uq_stock_operation_request')
    )

    op.create_index(op.f('ix_stock_operation_events_document_id'), 'stock_operation_events', ['document_id'], unique=False)

    op.create_table('customer_returns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('warehouse_id', sa.Integer(), nullable=False),
    sa.Column('original_dn_id', sa.Integer(), nullable=True),
    sa.Column('recipient_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('exception_reason', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['original_dn_id'], ['dn.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipient_id'], ['recipients.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_customer_returns_original_dn_id'), 'customer_returns', ['original_dn_id'], unique=False)

    op.create_index(op.f('ix_customer_returns_warehouse_id'), 'customer_returns', ['warehouse_id'], unique=False)

    op.create_table('customer_return_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('return_id', sa.Integer(), nullable=False),
    sa.Column('goods_id', sa.Integer(), nullable=False),
    sa.Column('lot_id', sa.Integer(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('received', sa.Integer(), nullable=False),
    sa.Column('disposed', sa.Integer(), nullable=False),
    sa.CheckConstraint('quantity >= received AND received >= disposed AND disposed >= 0', name='chk_customer_return_quantities'),
    sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['return_id'], ['customer_returns.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_customer_return_lines_return_id'), 'customer_return_lines', ['return_id'], unique=False)

    op.create_table('customer_return_holdings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('line_id', sa.Integer(), nullable=False),
    sa.Column('location_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.CheckConstraint('quantity >= 0', name='chk_return_holding_quantity'),
    sa.ForeignKeyConstraint(['line_id'], ['customer_return_lines.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('line_id', 'location_id', name='uq_return_holding_location')
    )

    op.create_index(op.f('ix_customer_return_holdings_line_id'), 'customer_return_holdings', ['line_id'], unique=False)

    op.create_table('customer_return_dispositions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('return_id', sa.Integer(), nullable=False),
    sa.Column('line_id', sa.Integer(), nullable=False),
    sa.Column('location_id', sa.Integer(), nullable=False),
    sa.Column('target_location_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('restored_quantity', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_reason', sa.String(length=255), nullable=True),
    sa.Column('linked_dn_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['line_id'], ['customer_return_lines.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['linked_dn_id'], ['dn.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['return_id'], ['customer_returns.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['target_location_id'], ['locations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_index(op.f('ix_customer_return_dispositions_return_id'), 'customer_return_dispositions', ['return_id'], unique=False)


def downgrade():
    op.drop_table('customer_return_dispositions')
    op.drop_table('customer_return_holdings')
    op.drop_table('customer_return_lines')
    op.drop_table('customer_returns')
    op.drop_table('stock_operation_events')
    op.drop_table('warehouse_transfer_lines')
    op.drop_table('warehouse_transfer_orders')
    op.drop_table('goods_lot_status_changes')
    with op.batch_alter_table('dn') as b:
        b.drop_constraint('fk_dn_supplier_return', type_='foreignkey')
        for column in ('purpose_reason', 'supplier_id', 'stock_source'):
            b.drop_column(column)
    with op.batch_alter_table('goods_lots') as b:
        b.drop_column('status_revision')
    with op.batch_alter_table('inventory') as b:
        b.drop_column('return_dn_stock')
        b.drop_column('damaged_dn_stock')
