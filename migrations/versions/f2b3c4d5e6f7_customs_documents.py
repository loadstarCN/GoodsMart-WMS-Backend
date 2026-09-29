"""Export documents: company / warehouse export profile, DN customs snapshot, packages, documents

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-29

- companies：出口资料（英文名称 / 英文地址 / 国家 / 税号及标签 / 出口联系人 / 签字人姓名与职务）
- warehouses：英文地址 / 国家 / 英文联系人（仓库地址 ≠ 公司地址时印 Ship From）
- dn_customs：DN 报关快照（一张 DN 至多一条；存在即海外件）
- dn_packages：DN 装箱记录（箱号 / 毛重 / 外箱尺寸）
- dn_documents：商业发票 / 装箱单 PDF（生成即定稿，按版本作废 / 升版）
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


COMPANY_COLUMNS = (
    ('legal_name_en', 255),
    ('address_en', 500),
    ('tax_id_label', 40),
    ('tax_id', 40),
    ('export_contact_name', 100),
    ('export_signatory_name', 100),
    ('export_signatory_title', 100),
)
WAREHOUSE_COLUMNS = (
    ('address_en', 500),
    ('contact_name_en', 100),
)


def upgrade():
    with op.batch_alter_table('companies', schema=None) as batch_op:
        for name, length in COMPANY_COLUMNS:
            batch_op.add_column(sa.Column(name, sa.String(length=length), nullable=True))
        batch_op.add_column(sa.Column('country_code', sa.String(length=2), nullable=True, server_default='JP'))

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        for name, length in WAREHOUSE_COLUMNS:
            batch_op.add_column(sa.Column(name, sa.String(length=length), nullable=True))
        batch_op.add_column(sa.Column('country_code', sa.String(length=2), nullable=True, server_default='JP'))

    op.create_table(
        'dn_customs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dn_id', sa.Integer(), nullable=False),
        sa.Column('invoice_number', sa.String(length=50), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=True),
        sa.Column('incoterm', sa.String(length=20), nullable=True),
        sa.Column('export_reason', sa.String(length=50), nullable=True),
        sa.Column('recipient_country', sa.String(length=10), nullable=True),
        sa.Column('recipient_tax_id', sa.String(length=100), nullable=True),
        sa.Column('recipient_tax_id_type', sa.String(length=30), nullable=True),
        sa.Column('freight_charge', sa.Integer(), nullable=True),
        sa.Column('consignee', sa.JSON(), nullable=True),
        sa.Column('lines', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['dn_id'], ['dn.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dn_id', name='uq_dn_customs_dn_id'),
    )

    op.create_table(
        'dn_packages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dn_id', sa.Integer(), nullable=False),
        sa.Column('package_no', sa.Integer(), nullable=False),
        sa.Column('gross_weight_kg', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('length_mm', sa.Integer(), nullable=False),
        sa.Column('width_mm', sa.Integer(), nullable=False),
        sa.Column('height_mm', sa.Integer(), nullable=False),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['dn_id'], ['dn.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dn_id', 'package_no', name='uq_dn_package_no'),
        sa.CheckConstraint('package_no >= 1', name='chk_dn_package_no'),
    )
    op.create_index('ix_dn_packages_dn_id', 'dn_packages', ['dn_id'], unique=False)

    op.create_table(
        'dn_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dn_id', sa.Integer(), nullable=False),
        sa.Column('doc_type', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('document_number', sa.String(length=80), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='issued'),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('data_sha256', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(length=150), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=True),
        sa.Column('issued_by', sa.Integer(), nullable=True),
        sa.Column('voided_at', sa.DateTime(), nullable=True),
        sa.Column('void_reason', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['dn_id'], ['dn.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['issued_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dn_id', 'doc_type', 'version', name='uq_dn_document_version'),
        sa.CheckConstraint("doc_type IN ('commercial_invoice','packing_list')", name='chk_dn_document_type'),
        sa.CheckConstraint("status IN ('issued','void')", name='chk_dn_document_status'),
    )
    op.create_index('idx_dn_document_status', 'dn_documents', ['dn_id', 'status'], unique=False)


def downgrade():
    op.drop_index('idx_dn_document_status', table_name='dn_documents')
    op.drop_table('dn_documents')
    op.drop_index('ix_dn_packages_dn_id', table_name='dn_packages')
    op.drop_table('dn_packages')
    op.drop_table('dn_customs')

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_column('country_code')
        for name, _length in reversed(WAREHOUSE_COLUMNS):
            batch_op.drop_column(name)

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_column('country_code')
        for name, _length in reversed(COMPANY_COLUMNS):
            batch_op.drop_column(name)
