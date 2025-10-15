from extensions import db

class SortingTask(db.Model):
    """分拣任务主表
    
    Attributes:
        asn_id: 关联到货单ID (外键约束不可删除)
        status: 任务状态 (pending/in_progress/completed)
        damage_quantity: 损坏数量 (必须≥0)
    """
    __tablename__ = 'sorting_tasks'
    
    __table_args__ = (
        db.Index('idx_sorting_status_time', 'status', 'created_at'),  # 状态时间联合查询
        db.CheckConstraint("status IN ('pending','in_progress','completed')", name='chk_sorting_status'),
    )

    SORTING_TASK_STATUSES = ('pending', 'in_progress', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    asn_id = db.Column(
        db.Integer,
        db.ForeignKey('asn.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '关联到货单ID'}
    )
    status = db.Column(
        db.Enum(*SORTING_TASK_STATUSES, name='sorting_task_status_enum'),
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
        info={'description': '创建时间'}
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
    asn = db.relationship(
        'ASN',
        backref=db.backref('sorting_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联到货单对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_sorting_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    task_details = db.relationship(
        'SortingTaskDetail',
        backref='sorting_task',
        lazy='select',
        order_by='desc(SortingTaskDetail.sorting_time)', # 按操作时间降序 
        cascade='all, delete-orphan',
        info={'description': '分拣明细集合'}
    )
    batches = db.relationship(
        'SortingBatch',
        backref='sorting_task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '关联分拣批次'}
    )
    status_logs = db.relationship(
        'SortingTaskStatusLog',
        backref='task',
        lazy='select',
        cascade='all, delete-orphan',
        info={'description': '状态变更记录'}
    )

class SortingTaskDetail(db.Model):
    """分拣任务明细表
    
    Attributes:
        sorted_quantity: 分拣数量 (必须≥0)
        damage_quantity: 损坏数量 (必须≥0)
    """
    __tablename__ = 'sorting_task_details'
    
    __table_args__ = (
        db.Index('idx_detail_goods_batch', 'goods_id', 'batch_id'),  # 商品批次组合索引
        db.CheckConstraint('sorted_quantity >= 0', name='chk_sorted_qty'),
        db.CheckConstraint('damage_quantity >= 0', name='chk_damage_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    sorting_task_id = db.Column(
        db.Integer,
        db.ForeignKey('sorting_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    batch_id = db.Column(
        db.Integer,
        db.ForeignKey('sorting_batches.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '批次ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '商品ID'}
    )
    sorted_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '合格品数量'}
    )
    damage_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '损坏品数量'}
    )
    sorting_time = db.Column(
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
        backref=db.backref('sorting_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    operator = db.relationship(
        'User',
        backref=db.backref('sorting_task_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )
    batch = db.relationship(
        'SortingBatch',
        backref=db.backref('details', lazy='select'),
        info={'description': '关联批次'}
    )

class SortingBatch(db.Model):
    """分拣批次记录表
    
    Attributes:
        operation_time: 操作时间 (自动记录)
        remark: 批次备注信息
    """
    __tablename__ = 'sorting_batches'
    
    __table_args__ = (
        db.Index('idx_batch_operator_time', 'operator_id', 'operation_time'),  # 操作审计索引
    )

    id = db.Column(db.Integer, primary_key=True)
    sorting_task_id = db.Column(
        db.Integer,
        db.ForeignKey('sorting_tasks.id', ondelete='CASCADE'),
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
        backref=db.backref('operated_sorting_batches', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作员对象'}
    )

class SortingTaskStatusLog(db.Model):
    """任务状态变更日志表
    
    Attributes:
        changed_at: 变更时间 (自动记录)
    """
    __tablename__ = 'sorting_task_status_logs'
    
    __table_args__ = (
        db.Index('idx_log_changed_time', 'changed_at'),  # 时间范围查询索引
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('sorting_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    old_status = db.Column(
        db.Enum(*SortingTask.SORTING_TASK_STATUSES, name='sorting_task_status_enum', create_type=False),
        nullable=True,
        info={'description': '原始状态'}
    )
    new_status = db.Column(
        db.Enum(*SortingTask.SORTING_TASK_STATUSES, name='sorting_task_status_enum', create_type=False),
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
        backref=db.backref('sorting_status_changes', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作人对象'}
    )