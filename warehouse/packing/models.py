from extensions import db

class PackingTask(db.Model):
    """打包任务主表
    
    Attributes:
        dn_id: 关联发货单ID (外键约束不可删除)
        status: 任务状态 (pending/in_progress/completed)
        is_active: 是否激活 (默认True)
        created_by: 创建人ID (必须存在于users表)
    """
    __tablename__ = 'packing_tasks'
    
    __table_args__ = (
        db.Index('idx_packing_status_time', 'status', 'created_at'),  # 状态时间联合查询
        db.Index('idx_packing_created', 'created_at'),  # 创建时间排序
        db.CheckConstraint("status IN ('pending','in_progress','completed')", name='chk_packing_valid_status'),
    )

    PACKING_TASK_STATUSES = ('pending', 'in_progress', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer, 
        db.ForeignKey('dn.id', ondelete='RESTRICT'),  # 阻止发货单删除
        nullable=False,
        info={'description': '关联发货单ID'}
    )
    status = db.Column(
        db.Enum(*PACKING_TASK_STATUSES, name='packing_task_status_enum'),
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
        backref=db.backref('packing_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联发货单对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_packing_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    task_details = db.relationship(
        'PackingTaskDetail',
        backref='packing_task',
        lazy='select',
        order_by='desc(PackingTaskDetail.packing_time)', # 按操作时间降序 
        cascade='all, delete-orphan',
        info={'description': '打包明细集合'}
    )
    batches = db.relationship(
        'PackingBatch',
        backref='packing_task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '关联打包批次'}
    )
    status_logs = db.relationship(
        'PackingTaskStatusLog',
        backref='task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '状态变更记录'}
    )

class PackingTaskDetail(db.Model):
    """打包任务明细表
    
    Attributes:
        packed_quantity: 已打包数量 (必须≥0)
        packing_time: 打包时间 (自动记录)
    """
    __tablename__ = 'packing_task_details'
    
    __table_args__ = (
        db.Index('idx_packing_detail_goods', 'goods_id', 'packing_task_id'),  # 商品维度查询
        db.CheckConstraint('packed_quantity >= 0', name='chk_packed_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    packing_task_id = db.Column(
        db.Integer,
        db.ForeignKey('packing_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    batch_id = db.Column(
        db.Integer,
        db.ForeignKey('packing_batches.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '批次ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '商品ID'}
    )
    packed_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '实际打包数量'}
    )
    packing_time = db.Column(
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
    goods = db.relationship(
        'Goods',
        backref=db.backref('packing_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    operator = db.relationship(
        'User',
        backref=db.backref('packing_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )
    batch = db.relationship(
        'PackingBatch',
        backref=db.backref('details', lazy='select'),
        info={'description': '关联批次'}
    )

class PackingBatch(db.Model):
    """打包批次记录表
    
    Attributes:
        operation_time: 操作时间 (自动记录)
        remark: 批次备注信息
    """
    __tablename__ = 'packing_batches'
    
    __table_args__ = (
        db.Index('idx_packing_batch_operator', 'operator_id', 'operation_time'),  # 操作审计
    )

    id = db.Column(db.Integer, primary_key=True)
    packing_task_id = db.Column(
        db.Integer,
        db.ForeignKey('packing_tasks.id', ondelete='CASCADE'),
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
        backref=db.backref('operated_packing_batches', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )

class PackingTaskStatusLog(db.Model):
    """任务状态变更日志表
    
    Attributes:
        changed_at: 变更时间 (自动记录)
    """
    __tablename__ = 'packing_task_status_logs'
    
    __table_args__ = (
        db.Index('idx_packing_log_changed_time', 'changed_at'),  # 时间范围查询
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('packing_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    old_status = db.Column(
        db.Enum(*PackingTask.PACKING_TASK_STATUSES, name='packing_task_status_enum', create_type=False),
        nullable=True,
        info={'description': '原始状态'}
    )
    new_status = db.Column(
        db.Enum(*PackingTask.PACKING_TASK_STATUSES, name='packing_task_status_enum', create_type=False),
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
        backref=db.backref('packing_status_changes', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作人对象'}
    )