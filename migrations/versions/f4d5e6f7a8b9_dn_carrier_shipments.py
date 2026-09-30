"""DN carrier shipments (FedEx Ship API) and shipping label documents

Revision ID: f4d5e6f7a8b9
Revises: f3c4d5e6f7a8
Create Date: 2026-09-30

- dn_carrier_shipments：DN 在承运商系统自动建的运单（主运单号、服务、运费、面单打印方式 / 格式 / 纸张、
  面单文档及其构成、ETD 文档 ID、承运商 transactionId、建单 / 取消人与时间）；同一 DN 至多一条 active（部分唯一索引）
- dn_documents.doc_type 允许 shipping_label（承运商面单 PDF）
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4d5e6f7a8b9'
down_revision = 'f3c4d5e6f7a8'
branch_labels = None
depends_on = None


OLD_DOC_TYPES = "doc_type IN ('commercial_invoice','packing_list')"
NEW_DOC_TYPES = "doc_type IN ('commercial_invoice','packing_list','shipping_label')"


def _replace_doc_type_check(condition):
    with op.batch_alter_table('dn_documents', schema=None) as batch_op:
        batch_op.drop_constraint('chk_dn_document_type', type_='check')
        batch_op.create_check_constraint('chk_dn_document_type', condition)


def upgrade():
    _replace_doc_type_check(NEW_DOC_TYPES)

    op.create_table(
        'dn_carrier_shipments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dn_id', sa.Integer(), nullable=False),
        sa.Column('carrier', sa.String(length=30), nullable=False),
        sa.Column('tracking_number', sa.String(length=100), nullable=False),
        sa.Column('package_tracking_numbers', sa.JSON(), nullable=True),
        sa.Column('service_type', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='active'),
        sa.Column('ship_date', sa.Date(), nullable=True),
        sa.Column('package_count', sa.Integer(), nullable=True),
        sa.Column('net_charge', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=True),
        sa.Column('declared_value', sa.Integer(), nullable=True),
        sa.Column('label_format', sa.String(length=10), nullable=True),
        sa.Column('image_type', sa.String(length=10), nullable=True),
        sa.Column('label_stock_type', sa.String(length=40), nullable=True),
        sa.Column('label_parts', sa.JSON(), nullable=True),
        sa.Column('label_document_id', sa.Integer(), nullable=True),
        sa.Column('etd_document_id', sa.String(length=100), nullable=True),
        sa.Column('transaction_id', sa.String(length=100), nullable=True),
        sa.Column('cancel_transaction_id', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['dn_id'], ['dn.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['label_document_id'], ['dn_documents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('active','cancelled')", name='chk_dn_carrier_shipment_status'),
    )
    op.create_index('idx_dn_carrier_shipment_dn', 'dn_carrier_shipments', ['dn_id', 'status'], unique=False)
    op.create_index('ix_dn_carrier_shipments_tracking_number', 'dn_carrier_shipments', ['tracking_number'],
                    unique=False)
    op.create_index(
        'uq_dn_carrier_shipment_active', 'dn_carrier_shipments', ['dn_id'], unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade():
    op.drop_index('uq_dn_carrier_shipment_active', table_name='dn_carrier_shipments')
    op.drop_index('ix_dn_carrier_shipments_tracking_number', table_name='dn_carrier_shipments')
    op.drop_index('idx_dn_carrier_shipment_dn', table_name='dn_carrier_shipments')
    op.drop_table('dn_carrier_shipments')
    # 面单文档随 doc_type 约束收回一起删掉（否则旧约束加不回去）
    op.execute("DELETE FROM dn_documents WHERE doc_type = 'shipping_label'")
    _replace_doc_type_check(OLD_DOC_TYPES)
