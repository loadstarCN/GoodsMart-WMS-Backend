from extensions.db import db, UTCDateTime


class CustomerReturn(db.Model):
    __tablename__ = 'customer_returns'
    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False, index=True)
    original_dn_id = db.Column(db.Integer, db.ForeignKey('dn.id', ondelete='RESTRICT'), index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('recipients.id', ondelete='RESTRICT'), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    exception_reason = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default='authorized')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=db.func.now())
    lines = db.relationship('CustomerReturnLine', backref='document', lazy='selectin')


class CustomerReturnLine(db.Model):
    __tablename__ = 'customer_return_lines'
    __table_args__ = (db.CheckConstraint('quantity >= received AND received >= disposed AND disposed >= 0', name='chk_customer_return_quantities'),)
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey('customer_returns.id', ondelete='RESTRICT'), nullable=False, index=True)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'))
    quantity = db.Column(db.Integer, nullable=False)
    received = db.Column(db.Integer, nullable=False, default=0)
    disposed = db.Column(db.Integer, nullable=False, default=0)


class ReturnHolding(db.Model):
    __tablename__ = 'customer_return_holdings'
    __table_args__ = (db.UniqueConstraint('line_id','location_id',name='uq_return_holding_location'),db.CheckConstraint('quantity >= 0',name='chk_return_holding_quantity'))
    id = db.Column(db.Integer, primary_key=True)
    line_id = db.Column(db.Integer, db.ForeignKey('customer_return_lines.id', ondelete='RESTRICT'), nullable=False, index=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)


class ReturnDisposition(db.Model):
    __tablename__ = 'customer_return_dispositions'
    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey('customer_returns.id', ondelete='RESTRICT'), nullable=False, index=True)
    line_id = db.Column(db.Integer, db.ForeignKey('customer_return_lines.id', ondelete='RESTRICT'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'), nullable=False)
    target_location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'))
    action = db.Column(db.String(24), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    restored_quantity = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    status = db.Column(db.String(16), nullable=False, default='pending')
    payload = db.Column(db.JSON, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'))
    reviewed_at = db.Column(UTCDateTime)
    review_reason = db.Column(db.String(255))
    linked_dn_id = db.Column(db.Integer, db.ForeignKey('dn.id', ondelete='RESTRICT'))
