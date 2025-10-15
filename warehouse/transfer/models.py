from extensions import db

class TransferRecord(db.Model):
    """移库记录表
    
    Attributes:
        goods_id: 关联商品ID (外键约束不可删除)
        from_location_id: 起始库位ID (外键约束不可删除)
        to_location_id: 目标库位ID (外键约束不可删除)
        quantity: 移库数量 (必须大于0)
        operator_id: 操作人ID (外键约束不可删除)
        transfer_time: 移库时间 (自动记录操作时间)
        remark: 备注信息
    """
    __tablename__ = 'transfer_records'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_transfer_goods_time', 'goods_id', 'transfer_time'),        # 商品流转分析
        db.Index('idx_transfer_from_time', 'from_location_id', 'transfer_time'), # 起始库位负载分析
        db.Index('idx_transfer_to_time', 'to_location_id', 'transfer_time'),     # 目标库位接收分析
        db.Index('idx_transfer_operator_time', 'operator_id', 'transfer_time'),  # 操作员效率分析
        
        # 路径分析复合索引
        db.Index('idx_transfer_path', 'from_location_id', 'to_location_id', 'transfer_time'),
        
        # 数据完整性约束
        db.CheckConstraint('quantity > 0', name='chk_transfer_quantity')
    )

    id = db.Column(db.Integer, primary_key=True)
    goods_id = db.Column(
        db.Integer, 
        db.ForeignKey('goods.id', ondelete='RESTRICT'),  # 阻止商品删除
        nullable=False,
        info={'description': '关联商品ID'}
    )
    from_location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),  # 阻止库位删除
        nullable=False,
        info={'description': '起始库位ID'}
    )
    to_location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),  # 阻止库位删除
        nullable=False,
        info={'description': '目标库位ID'}
    )
    quantity = db.Column(
        db.Integer, 
        nullable=False,
        default=1,  # 合理默认值
        info={
            'check': 'quantity > 0',  # 应用层校验
            'description': '移库数量（必须>0）'
        }
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 阻止用户删除
        nullable=False,
        info={'description': '操作员ID'}
    )
    transfer_time = db.Column(
        db.DateTime, 
        default=db.func.now(),
        info={'description': '自动记录操作时间'}
    )
    remark = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '操作备注信息'}
    )

    # 关系定义优化
    goods = db.relationship(
        'Goods', 
        backref=db.backref('transfer_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载商品信息
        info={'description': '关联商品对象'}
    )
    
    from_location = db.relationship(
        'Location',
        foreign_keys=[from_location_id],
        backref=db.backref('transfer_from_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载起始库位
        info={'description': '来源库位对象'}
    )
    
    to_location = db.relationship(
        'Location',
        foreign_keys=[to_location_id],
        backref=db.backref('transfer_to_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载目标库位
        info={'description': '目标库位对象'}
    )
    
    operator = db.relationship(
        'User',
        backref=db.backref('executed_transfers', lazy='dynamic'),  # 动态反向查询
        info={'description': '操作员对象'}
    )