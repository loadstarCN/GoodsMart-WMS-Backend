from extensions.db import *

class DN(db.Model):
    """发货单主表
    
    Attributes:
        warehouse_id: 关联仓库ID (外键约束不可删除)
        status: 单据状态 (pending/in_progress/picked/packed/delivered/completed/closed)
        dn_type: 单据类型 (shipping/return_to_supplier/damage_to_supplier/transfer)
    """
    __tablename__ = 'dn'
    
    __table_args__ = (
        db.Index('idx_dn_warehouse_status', 'warehouse_id', 'status'),  # 仓库维度查询
        db.Index('idx_dn_expected_date', 'expected_shipping_date'),    # 预计发货日期索引
        db.CheckConstraint("status IN ('pending','in_progress','picked','packed','delivered','completed','closed')", 
                          name='chk_valid_dn_status'),
        db.CheckConstraint("dn_type IN ('shipping','return_to_supplier','damage_to_supplier','transfer')", 
                          name='chk_valid_dn_dn_type'),
          
        
    )

    DN_TYPES = ('shipping', 'return_to_supplier', 'damage_to_supplier', 'transfer')
    DN_STATUSES = ('pending','in_progress', 'picked', 'packed', 'delivered', 'completed','closed')
    DN_TRANSPORTATION_MODES = ('express', 'pickup', 'courier', 'air', 'sea', 'land', 'rail', 'drone')

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '仓库ID'}
    )
    recipient_id = db.Column(
        db.Integer,
        db.ForeignKey('recipients.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '收货人ID'}
    )
    shipping_address = db.Column(
        db.String(255),
        nullable=False,
        info={'description': '完整发货地址'}
    )
    expected_shipping_date = db.Column(
        db.Date,
        nullable=False,
        info={'description': '预计发货日期'}
    )
    dn_type = db.Column(
        db.Enum(*DN_TYPES, name='dn_type_enum'),
        nullable=False,
        default='shipping',
        info={'description': '发货单类型'}
    )
    order_number = db.Column(
        db.String(50),
        nullable=True,
        index=True,  # 客户订单号高频查询
        info={'description': '客户订单号'}
    )

    transportation_mode = db.Column(
        db.Enum(*DN_TRANSPORTATION_MODES, name='dn_transportation_mode_enum'),
        nullable=True,
        info={'description': '运输方式枚举'}
    )

    packaging_info = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '包装信息'}
    )
    special_handling = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '特殊处理信息'}
    )
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='RESTRICT'),
        nullable=True,
        info={'description': '承运商ID'}
    )
    status = db.Column(
        db.Enum(*DN_STATUSES, name='dn_status_enum'),
        nullable=False,
        default='pending',
        info={'description': 'DN单据状态'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '备注信息'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,  # 激活状态高频过滤
        info={'description': '是否有效'}
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
        index=True,  # 时间字段索引
        info={'description': '最后更新时间'}
    )
    # 状态时间字段统一命名规范
    started_at = db.Column(db.DateTime, nullable=True, info={'description': '开始处理时间'})
    picked_at = db.Column(db.DateTime, nullable=True, info={'description': '拣货完成时间'})
    packed_at = db.Column(db.DateTime, nullable=True, info={'description': '打包完成时间'})
    delivered_at = db.Column(db.DateTime, nullable=True, info={'description': '发货时间'})
    completed_at = db.Column(db.DateTime, nullable=True, info={'description': '流程完成时间'})
    closed_at = db.Column(db.DateTime, nullable=True, info={'description': '单据关闭时间'})

    # 关系加载策略优化
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '仓库对象'}
    )
    recipient = db.relationship(
        'Recipient',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '收货人对象'}
    )
    carrier = db.relationship(
        'Carrier',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '承运商对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    details = db.relationship(
        'DNDetail',
        backref='dn',
        lazy='select',  # 按需加载明细
        cascade='all, delete-orphan',
        info={'description': '发货明细集合'}
    )


class DNDetail(db.Model):
    """发货明细表
    
    Attributes:
        quantity: 计划数量 (必须≥0)
        picked_quantity: 已拣数量 (必须≤quantity)
    """
    __tablename__ = 'dn_details'
    
    __table_args__ = (
        db.Index('idx_dn_detail_goods_dn', 'goods_id', 'dn_id'),  # 商品维度查询
        db.CheckConstraint('quantity >= 0', name='chk_dn_detail_quantity'),
        db.CheckConstraint('picked_quantity <= quantity', name='chk_dn_detail_picked_qty'),
        db.CheckConstraint('packed_quantity <= picked_quantity', name='chk_dn_detail_packed_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer,
        db.ForeignKey('dn.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主单ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '商品ID'}
    )
    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '计划出库数量'}
    )
    picked_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已拣选数量'}
    )
    packed_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已打包数量'}
    )
    delivered_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已发货数量'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '明细备注'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '创建人ID'}
    )
    create_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '创建时间'}
    )
    update_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间'}
    )

    # 关系优化
    goods = db.relationship(
        'Goods',
        backref=db.backref('dn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_dn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )