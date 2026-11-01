"""Carrier invoices preserve the imported file and reconciliation decisions."""
from extensions.db import db, UTCDateTime


class CarrierFreightSettings(db.Model):
    __tablename__ = 'carrier_freight_settings'
    carrier_id = db.Column(db.Integer, db.ForeignKey('carriers.id', ondelete='CASCADE'), primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    tolerance_type = db.Column(db.String(10), nullable=False, default='amount')
    tolerance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=True)
    column_mapping = db.Column(db.JSON, nullable=False, default=dict)


class CarrierInvoice(db.Model):
    __tablename__ = 'carrier_invoices'
    __table_args__ = (
        db.UniqueConstraint('company_id', 'carrier_id', 'invoice_number', name='uq_carrier_invoice_number'),
        db.UniqueConstraint('company_id', 'carrier_id', 'file_sha256', name='uq_carrier_invoice_file'),
        db.CheckConstraint("status IN ('pending','confirmed')", name='chk_carrier_invoice_status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False, index=True)
    carrier_id = db.Column(db.Integer, db.ForeignKey('carriers.id', ondelete='RESTRICT'), nullable=False, index=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=True, index=True)
    invoice_number = db.Column(db.String(100), nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    period_from = db.Column(db.Date, nullable=False)
    period_to = db.Column(db.Date, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    status = db.Column(db.String(10), nullable=False, default='pending')
    revision = db.Column(db.Integer, nullable=False, default=1)
    file_name = db.Column(db.String(255), nullable=False)
    file_format = db.Column(db.String(8), nullable=False)
    file_encoding = db.Column(db.String(30), nullable=True)
    file_sha256 = db.Column(db.String(64), nullable=False)
    file_content = db.Column(db.LargeBinary, nullable=False)
    tolerance_type = db.Column(db.String(10), nullable=False)
    tolerance = db.Column(db.Numeric(12, 2), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(UTCDateTime, nullable=False, default=db.func.now())
    confirmed_at = db.Column(UTCDateTime, nullable=True)
    carrier = db.relationship('Carrier', lazy='joined')
    lines = db.relationship('CarrierInvoiceLine', back_populates='invoice', cascade='all, delete-orphan', order_by='CarrierInvoiceLine.line_number')


class CarrierInvoiceLine(db.Model):
    __tablename__ = 'carrier_invoice_lines'
    __table_args__ = (
        db.UniqueConstraint('invoice_id', 'line_number', name='uq_carrier_invoice_line'),
        db.CheckConstraint("review_status IN ('unreviewed','accepted','disputed','external')", name='chk_carrier_invoice_review'),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('carrier_invoices.id', ondelete='CASCADE'), nullable=False, index=True)
    line_number = db.Column(db.Integer, nullable=False)
    tracking_number = db.Column(db.String(100), nullable=False, index=True)
    shipping_date = db.Column(db.Date, nullable=False)
    service = db.Column(db.String(100), nullable=True)
    destination_country = db.Column(db.String(2), nullable=True)
    billable_weight = db.Column(db.Numeric(12, 3), nullable=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    charges = db.Column(db.JSON, nullable=False, default=dict)
    dn_id = db.Column(db.Integer, db.ForeignKey('dn.id', ondelete='RESTRICT'), nullable=True, index=True)
    estimated_amount = db.Column(db.Numeric(14, 2), nullable=True)
    estimated_currency = db.Column(db.String(3), nullable=True)
    estimate_source = db.Column(db.String(30), nullable=True)
    review_status = db.Column(db.String(12), nullable=False, default='unreviewed')
    note = db.Column(db.String(500), nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = db.Column(UTCDateTime, nullable=True)
    invoice = db.relationship('CarrierInvoice', back_populates='lines')
