from extensions.db import db, UTCDateTime


class TransferOrder(db.Model):
    __tablename__ = 'warehouse_transfer_orders'
    id = db.Column(db.Integer, primary_key=True)
    source_warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False, index=True)
    destination_warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='planned')
    reason = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=db.func.now())
    first_shipped_at = db.Column(UTCDateTime)
    lines = db.relationship('TransferOrderLine', backref='order', lazy='selectin')


class TransferOrderLine(db.Model):
    __tablename__ = 'warehouse_transfer_lines'
    __table_args__ = (db.CheckConstraint('quantity >= shipped + cancelled AND cancelled >= 0 AND shipped >= received + lost AND received >= 0 AND lost >= 0', name='chk_transfer_order_quantities'),)
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('warehouse_transfer_orders.id', ondelete='RESTRICT'), nullable=False, index=True)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'))
    source_location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    cancelled = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    shipped = db.Column(db.Integer, nullable=False, default=0)
    received = db.Column(db.Integer, nullable=False, default=0)
    lost = db.Column(db.Integer, nullable=False, default=0)


class StockOperationEvent(db.Model):
    """Immutable command receipt and physical handover history; never delete parent documents."""
    __tablename__ = 'stock_operation_events'
    __table_args__ = (db.UniqueConstraint('kind', 'document_id', 'request_id', name='uq_stock_operation_request'),)
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False)
    document_id = db.Column(db.Integer, nullable=False, index=True)
    request_id = db.Column(db.String(64), nullable=False)
    result_id = db.Column(db.Integer)
    action = db.Column(db.String(24), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    operator_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=db.func.now())
