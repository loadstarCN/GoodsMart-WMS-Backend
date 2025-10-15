from extensions import db

class Payment(db.Model):
    """支付记录主表
    
    Attributes:
        retry_count: 支付重试次数（机器学习优化基础）
        next_retry_at: 下次重试时间（支持动态窗口预测）
    """
    __tablename__ = 'payments'
    
    __table_args__ = (
        db.Index('idx_payment_status_time', 'status', 'payment_time'),  # 状态时间联合查询
        db.Index('idx_carrier_payments', 'carrier_id', 'created_at'),  # 承运商维度分析
        db.CheckConstraint("amount > 0", name='chk_positive_amount'),
        db.CheckConstraint("created_at <= updated_at", name='chk_time_sequence'),
        db.CheckConstraint("payment_time >= created_at", name='chk_payment_time')
    )

    PAYMENT_METHOD = ('bank_transfer', 'online_payment', 'cash', 'other')
    PAYMENT_STATUS = ('pending', 'paid', 'canceled', 'failed')  # 新增失败状态

    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(
        db.Integer,
        db.ForeignKey('delivery_tasks.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '关联发货单ID'}
    )
    amount = db.Column(
        db.Numeric(12, 2),  # 精确金额类型
        nullable=False,
        info={'description': '支付金额（含两位小数）'}
    )
    currency = db.Column(
        db.String(10),
        nullable=False,
        default='JPY',
        info={'description': '货币代码（ISO 4217标准）'}
    )
    payment_method = db.Column(
        db.Enum(*PAYMENT_METHOD, name='payment_method_enum'),
        nullable=False,
        default='bank_transfer',
        index=True,
        info={'description': '支付方式'}
    )
    status = db.Column(
        db.Enum(*PAYMENT_STATUS, name='payment_status_enum'),
        nullable=False,
        default='pending',
        index=True,
        info={'description': '支付状态'}
    )
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '承运商ID'}
    )
    payment_time = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '实际支付时间'}
    )
    remark = db.Column(
        db.Text,  # 扩展备注长度
        nullable=True,
        info={'description': '支付备注'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,
        info={'description': '是否激活'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '创建人ID'}
    )
    created_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '创建时间'}
    )
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        index=True,
        info={'description': '最后更新时间'}
    )
 
    # 关系加载
    delivery = db.relationship(
        'DeliveryTask',
        backref=db.backref('payments', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联发货单对象'}
    )
    carrier = db.relationship(
        'Carrier',
        backref=db.backref('payments', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联承运商对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_payments', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
