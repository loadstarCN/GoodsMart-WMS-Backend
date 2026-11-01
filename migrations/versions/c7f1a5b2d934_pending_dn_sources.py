"""Prospective pending DN return provenance; no historical attribution."""
from alembic import op
import sqlalchemy as sa

revision = 'c7f1a5b2d934'
down_revision = 'b6e0f4a1c823'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('receipt_pending_stock') as batch:
        batch.alter_column('asn_id', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('dn_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('source_type', sa.String(32), nullable=False, server_default='asn'))
        batch.add_column(sa.Column('entered_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('fk_pending_source_dn', 'dn', ['dn_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_receipt_pending_stock_dn_id', ['dn_id'])
        batch.create_check_constraint('chk_pending_source_document',
            "(source_type = 'asn' AND asn_id IS NOT NULL AND dn_id IS NULL) OR "
            "(source_type IN ('pack_short_return','dn_cancel_return') AND asn_id IS NULL AND dn_id IS NOT NULL AND entered_at IS NOT NULL)")


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM receipt_pending_stock WHERE asn_id IS NULL')).scalar():
        raise RuntimeError('Cannot discard recorded non-ASN pending sources')
    with op.batch_alter_table('receipt_pending_stock') as batch:
        batch.drop_constraint('chk_pending_source_document', type_='check')
        batch.drop_index('ix_receipt_pending_stock_dn_id')
        batch.drop_constraint('fk_pending_source_dn', type_='foreignkey')
        batch.drop_column('entered_at')
        batch.drop_column('source_type')
        batch.drop_column('dn_id')
        batch.alter_column('asn_id', existing_type=sa.Integer(), nullable=False)
