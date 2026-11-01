"""Carrier invoice files, reconciliation and settings."""
from alembic import op
import sqlalchemy as sa
revision = 'f8c2d6e3b045'
down_revision = 'e7b1c5d2a934'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('carrier_freight_settings',
        sa.Column('carrier_id', sa.Integer, sa.ForeignKey('carriers.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('tolerance_type', sa.String(10), nullable=False),
        sa.Column('tolerance', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=True),
        sa.Column('column_mapping', sa.JSON, nullable=False))
    op.create_table('carrier_invoices',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('company_id', sa.Integer, sa.ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('carrier_id', sa.Integer, sa.ForeignKey('carriers.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('warehouse_id', sa.Integer, sa.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('invoice_number', sa.String(100), nullable=False),
        sa.Column('invoice_date', sa.Date, nullable=False),
        sa.Column('period_from', sa.Date, nullable=False),
        sa.Column('period_to', sa.Date, nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_format', sa.String(8), nullable=False),
        sa.Column('file_encoding', sa.String(30), nullable=True),
        sa.Column('file_sha256', sa.String(64), nullable=False),
        sa.Column('file_content', sa.LargeBinary, nullable=False),
        sa.Column('tolerance_type', sa.String(10), nullable=False),
        sa.Column('tolerance', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('confirmed_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('company_id', 'carrier_id', 'invoice_number', name='uq_carrier_invoice_number'),
        sa.UniqueConstraint('company_id', 'carrier_id', 'file_sha256', name='uq_carrier_invoice_file'),
        sa.CheckConstraint("status IN ('pending','confirmed')", name='chk_carrier_invoice_status'))
    op.create_index("ix_carrier_invoices_company_id", "carrier_invoices", ["company_id"])
    op.create_index("ix_carrier_invoices_carrier_id", "carrier_invoices", ["carrier_id"])
    op.create_index("ix_carrier_invoices_warehouse_id", "carrier_invoices", ["warehouse_id"])
    op.create_table('carrier_invoice_lines',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('invoice_id', sa.Integer, sa.ForeignKey('carrier_invoices.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer, nullable=False),
        sa.Column('tracking_number', sa.String(100), nullable=False),
        sa.Column('shipping_date', sa.Date, nullable=False),
        sa.Column('service', sa.String(100), nullable=True),
        sa.Column('destination_country', sa.String(2), nullable=True),
        sa.Column('billable_weight', sa.Numeric(12, 3), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('charges', sa.JSON, nullable=False),
        sa.Column('dn_id', sa.Integer, sa.ForeignKey('dn.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('estimated_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('estimated_currency', sa.String(3), nullable=True),
        sa.Column('estimate_source', sa.String(30), nullable=True),
        sa.Column('review_status', sa.String(12), nullable=False),
        sa.Column('note', sa.String(500), nullable=True),
        sa.Column('reviewed_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('invoice_id', 'line_number', name='uq_carrier_invoice_line'),
        sa.CheckConstraint("review_status IN ('unreviewed','accepted','disputed','external')", name='chk_carrier_invoice_review'))
    op.create_index("ix_carrier_invoice_lines_invoice_id", "carrier_invoice_lines", ["invoice_id"])
    op.create_index("ix_carrier_invoice_lines_tracking_number", "carrier_invoice_lines", ["tracking_number"])
    op.create_index("ix_carrier_invoice_lines_dn_id", "carrier_invoice_lines", ["dn_id"])
    permissions = sa.table("permissions", sa.column("name", sa.String), sa.column("description", sa.String))
    for name, description in (("freight_read", "Read carrier invoices and reconciliation"), ("freight_edit", "Import, review and confirm carrier invoices")):
        if not op.get_bind().execute(sa.select(permissions.c.name).where(permissions.c.name == name)).first():
            op.bulk_insert(permissions, [{"name": name, "description": description}])

def downgrade():
    bind = op.get_bind()
    for table in ("carrier_invoices", "carrier_freight_settings"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot discard carrier invoices or configured freight settings")
    op.drop_table("carrier_invoice_lines")
    op.drop_table("carrier_invoices")
    op.drop_table("carrier_freight_settings")
    # Permission records are preserved because roles may reference them.
