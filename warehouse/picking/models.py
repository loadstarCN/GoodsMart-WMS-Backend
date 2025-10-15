from extensions import db

class PickingTask(db.Model):
    """拣货任务主表
    
    Attributes:
        dn_id: 关联发货单ID (外键约束不可删除)
        status: 任务状态 (pending/in_progress/completed)
        is_active: 是否激活 (默认True)
        created_by: 创建人ID (必须存在于users表)
    """
    __tablename__ = 'picking_tasks'
    
    __table_args__ = (
        db.Index('idx_picking_status_time', 'status', 'created_at'),  # 状态时间联合查询
        db.CheckConstraint("status IN ('pending','in_progress','completed')", name='chk_picking_valid_status'),
    )

    PICKING_TASK_STATUSES = ('pending', 'in_progress', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer, 
        db.ForeignKey('dn.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '关联发货单ID'}
    )
    status = db.Column(
        db.Enum(*PICKING_TASK_STATUSES, name='picking_task_status_enum'),
        nullable=False,
        default='pending',
        index=True
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
        info={'description': '任务创建时间'}
    )
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        index=True,
        info={'description': '最后更新时间'}
    )
    started_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '实际开始时间'}
    )
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '实际完成时间'}
    )

    # 关系加载策略优化
    dn = db.relationship(
        'DN', 
        backref=db.backref('picking_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联发货单对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_picking_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    task_details = db.relationship(
        'PickingTaskDetail',
        backref='picking_task',
        lazy='select',
        order_by='desc(PickingTaskDetail.picking_time)', # 按操作时间降序 
        cascade='all, delete-orphan',
        info={'description': '拣货明细集合'}
    )
    batches = db.relationship(
        'PickingBatch',
        backref='picking_task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '关联拣货批次'}
    )
    status_logs = db.relationship(
        'PickingTaskStatusLog',
        backref='task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '状态变更记录'}
    )

class PickingTaskDetail(db.Model):
    """拣货任务明细表
    
    Attributes:
        picked_quantity: 已拣数量 (必须≥0)
        picking_time: 拣货时间 (自动记录)
    """
    __tablename__ = 'picking_task_details'
    
    __table_args__ = (
        db.Index('idx_picking_detail_goods_location', 'goods_id', 'location_id'),  # 商品库位组合索引
        db.CheckConstraint('picked_quantity >= 0', name='chk_picked_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    picking_task_id = db.Column(
        db.Integer,
        db.ForeignKey('picking_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    batch_id = db.Column(
        db.Integer,
        db.ForeignKey('picking_batches.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '批次ID'}
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '库位ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '商品ID'}
    )
    picked_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '实际拣货数量'}
    )
    picking_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '操作时间'}
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '操作员ID'}
    )

    # 关系优化
    location = db.relationship(
        'Location',
        backref=db.backref('picking_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '库位对象'}
    )
    goods = db.relationship(
        'Goods',
        backref=db.backref('picking_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    operator = db.relationship(
        'User',
        backref=db.backref('picking_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )
    batch = db.relationship(
        'PickingBatch',
        backref=db.backref('details', lazy='select'),
        info={'description': '关联批次'}
    )

class PickingBatch(db.Model):
    """拣货批次记录表
    
    Attributes:
        operation_time: 操作时间 (自动记录)
        remark: 批次备注信息
    """
    __tablename__ = 'picking_batches'
    
    __table_args__ = (
        db.Index('idx_picking_batch_operator', 'operator_id', 'operation_time'),  # 操作审计索引
    )

    id = db.Column(db.Integer, primary_key=True)
    picking_task_id = db.Column(
        db.Integer,
        db.ForeignKey('picking_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '操作员ID'}
    )
    operation_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        nullable=False,
        info={'description': '操作时间'}
    )
    remark = db.Column(
        db.Text,
        nullable=True,
        info={'description': '批次备注'}
    )

    operator = db.relationship(
        'User',
        backref=db.backref('operated_picking_batches', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )

class PickingTaskStatusLog(db.Model):
    """任务状态变更日志表
    
    Attributes:
        changed_at: 变更时间 (自动记录)
    """
    __tablename__ = 'picking_task_status_logs'
    
    __table_args__ = (
        db.Index('idx_picking_log_changed_time', 'changed_at'),  # 时间范围查询索引
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('picking_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    old_status = db.Column(
        db.Enum(*PickingTask.PICKING_TASK_STATUSES, name='picking_task_status_enum', create_type=False),
        nullable=True,
        info={'description': '原始状态'}
    )
    new_status = db.Column(
        db.Enum(*PickingTask.PICKING_TASK_STATUSES, name='picking_task_status_enum', create_type=False),
        nullable=False,
        info={'description': '新状态'}
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '操作人ID'}
    )
    changed_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        index=True,
        info={'description': '变更时间'}
    )

    operator = db.relationship(
        'User',
        backref=db.backref('picking_status_changes', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作人对象'}
    )