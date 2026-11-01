from extensions import db
from extensions.db import UTCDateTime


class ReceiptPendingStock(db.Model):
    """Prospectively recorded receipt source; never backfilled from current stock."""
    __tablename__ = 'receipt_pending_stock'
    __table_args__ = (
        db.CheckConstraint('good_remaining >= 0 AND damaged_remaining >= 0', name='chk_receipt_pending_nonnegative'),
        db.CheckConstraint('good_remaining <= good_received AND damaged_remaining <= damaged_received', name='chk_receipt_pending_received'),
        db.CheckConstraint("(source_type = 'asn' AND asn_id IS NOT NULL AND dn_id IS NULL) OR "
                           "(source_type IN ('pack_short_return','dn_cancel_return') AND asn_id IS NULL AND dn_id IS NOT NULL AND entered_at IS NOT NULL)",
                           name='chk_pending_source_document'),
        db.Index('idx_receipt_pending_scope', 'warehouse_id', 'goods_id', 'lot_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    asn_id = db.Column(db.Integer, db.ForeignKey('asn.id', ondelete='RESTRICT'), nullable=True, index=True)
    dn_id = db.Column(db.Integer, db.ForeignKey('dn.id', ondelete='RESTRICT'), nullable=True, index=True)
    source_type = db.Column(db.String(32), nullable=False, default='asn', server_default='asn')
    entered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True)
    good_received = db.Column(db.Integer, nullable=False)
    damaged_received = db.Column(db.Integer, nullable=False)
    good_remaining = db.Column(db.Integer, nullable=False)
    damaged_remaining = db.Column(db.Integer, nullable=False)
    asn = db.relationship('ASN')
    dn = db.relationship('DN')


class PutawayRecord(db.Model):
    """货品上架记录表
    
    Attributes:
        goods_id: 关联商品ID (外键约束不可删除)
        location_id: 目标库位ID (必须存在于locations表)
        quantity: 上架数量 (必须大于0)
        putaway_time: 上架时间 (自动记录操作时间)
        operator_id: 操作员ID (必须存在于users表)
        remark: 备注信息
    """
    __tablename__ = 'putaway_records'

    __table_args__ = (
        # 核心查询索引
        db.Index('idx_putaway_goods_time', 'goods_id', 'putaway_time'),  # 商品维度分析
        db.Index('idx_putaway_location_time', 'location_id', 'putaway_time'),  # 库位维度分析
        db.Index('idx_putaway_operator_time', 'operator_id', 'putaway_time'),  # 操作员效率分析
        db.CheckConstraint('quantity > 0', name='chk_removal_quantity')     # 数据库层校验
    )

    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True)
    receipt_source_id = db.Column(db.Integer, db.ForeignKey('receipt_pending_stock.id', ondelete='RESTRICT'), nullable=True, index=True)
    receipt_source = db.relationship('ReceiptPendingStock')
    lot = db.relationship('GoodsLot', lazy='joined')

    id = db.Column(db.Integer, primary_key=True)
    goods_id = db.Column(
        db.Integer, 
        db.ForeignKey('goods.id', ondelete='RESTRICT'),  # 阻止商品删除
        nullable=False,
        info={'description': '关联商品ID'}
    )
    location_id = db.Column(
        db.Integer, 
        db.ForeignKey('locations.id', ondelete='RESTRICT'),  # 阻止库位删除
        nullable=False,
        info={'description': '目标库位ID'}
    )
    quantity = db.Column(
        db.Integer, 
        nullable=False,
        default=1,  # 合理默认值
        info={'check': 'quantity > 0'},  # 数量必须大于0
    )
    operator_id = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 阻止用户删除
        nullable=False,
        info={'description': '操作员ID'}
    )
    putaway_time = db.Column(
        UTCDateTime, 
        default=db.func.now(),
        info={'description': '自动记录上架时间'}
    )
    remark = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '操作备注信息'}
    )

    # 关系定义优化
    goods = db.relationship(
        'Goods', 
        backref=db.backref('putaway_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载商品信息
        info={'description': '关联商品对象'}
    )
    
    location = db.relationship(
        'Location',
        backref=db.backref('putaway_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载库位信息
        info={'description': '目标库位对象'}
    )
    
    operator = db.relationship(
        'User',
        backref=db.backref('putaway_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载操作员信息
        info={'description': '操作员对象'}
    )
