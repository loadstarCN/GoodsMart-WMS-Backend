from extensions.db import *

class Adjustment(db.Model):
    """库存调整主表
    
    Attributes:
        warehouse_id: 关联仓库ID (外键约束不可删除)
        status: 审批状态 (pending/approved/completed)
        created_by: 创建人ID (必须存在于users表)
        updated_at: 自动更新时间 (每次修改自动更新)
    """
    __tablename__ = 'adjustments'
    
    __table_args__ = (
        # 核心业务查询索引
        db.Index('idx_adjustment_warehouse_status', 'warehouse_id', 'status'),
        db.Index('idx_adjustment_created_time', 'created_at'),
        db.CheckConstraint("status IN ('pending','approved','completed')", name='chk_valid_status')
    )

    AdjustmentStatuses = ('pending', 'approved', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),  # 阻止仓库删除
        nullable=False,
        info={'description': '关联仓库ID'}
    )
    adjustment_reason = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '调整原因'}
    )
    status = db.Column(
        db.Enum(*AdjustmentStatuses, name='adjustment_status_enum'),
        nullable=False,
        default='pending',
        info={'description': '审批状态'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,  # 高频查询字段加索引
        info={'description': '是否激活'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 阻止用户删除
        nullable=False,
        info={'description': '创建人ID'}
    )
    approved_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 阻止用户删除
        nullable=True,
        info={'description': '审批人ID'}
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 阻止用户删除
        nullable=True,
        info={'description': '操作人ID'}
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
        index=True,  # 时间字段加索引
        info={'description': '最后更新时间'}
    )    
    approved_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '审批时间'}
    )    
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '完成时间'}
    )
    

    # 关系定义优化（使用joined加载策略）
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref('adjustments', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联仓库对象'}
    )
    creator = db.relationship(
        'User',
        foreign_keys=[created_by],
        backref=db.backref('adjustments', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    approver = db.relationship(
        'User',
        foreign_keys=[approved_by],
        backref=db.backref('approved_adjustments', lazy='dynamic'),
        lazy='joined',
        info={'description': '审批人对象'}
    )
    operator = db.relationship(
        'User',
        foreign_keys=[operator_id],
        backref=db.backref('operated_adjustments', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作人对象'}
    )
    details = db.relationship(
        'AdjustmentDetail',
        backref='adjustment',
        lazy='select',  # 明细按需加载
        cascade='all, delete-orphan',
        info={'description': '调整明细集合'}
    )


class AdjustmentDetail(db.Model):
    """库存调整明细表
    
    Attributes:
        actual_quantity: 实际数量 (必须≥0)
        adjustment_quantity: 系统自动计算的差异数量
    """
    __tablename__ = 'adjustment_details'
    
    __table_args__ = (
        db.Index('idx_detail_goods_location', 'goods_id', 'location_id'),
        db.CheckConstraint('actual_quantity >= 0', name='chk_actual_quantity'),
        db.CheckConstraint('adjustment_quantity = actual_quantity - system_quantity', 
                         name='chk_adjustment_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    adjustment_id = db.Column(
        db.Integer,
        db.ForeignKey('adjustments.id', ondelete='CASCADE'),  # 级联删除
        nullable=False,
        info={'description': '关联调整任务ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),  # 阻止商品删除
        nullable=False,
        info={'description': '商品ID'}
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),  # 阻止库位删除
        nullable=True,
        info={'description': '库位ID'}
    )
    system_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '系统库存数量'}
    )
    actual_quantity = db.Column(
        db.Integer,
        nullable=False,
        info={'description': '实际盘点数量'}
    )
    adjustment_quantity = db.Column(
        db.Integer,
        nullable=False,
        info={'description': '系统计算差异数量'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '调整备注'}
    )

    # 关系定义优化
    goods = db.relationship(
        'Goods',
        backref=db.backref('adjustment_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联商品对象'}
    )
    location = db.relationship(
        'Location',
        backref=db.backref('adjustment_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联库位对象'}
    )