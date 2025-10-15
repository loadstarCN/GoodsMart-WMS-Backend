from extensions.db import *

class CycleCountTask(db.Model):
    """循环盘点主任务表"""
    __tablename__ = 'cycle_count_tasks'

    __table_args__ = (
        # 核心业务查询索引
        db.Index('idx_task_warehouse_status', 'warehouse_id', 'status'),  # 仓库维度查询
        db.Index('idx_task_scheduled_date', 'scheduled_date'),             # 计划时间排序
        db.CheckConstraint("status IN ('pending','in_progress','completed')", name='chk_valid_status'),
    )

    CYCLE_COUNT_TASK_STATUSES = ('pending', 'in_progress', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    task_name = db.Column(
        db.String(100), 
        nullable=True,
        info={'description': '任务名称（可空）'}
    )
    warehouse_id = db.Column(
        db.Integer, 
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),  # 阻止仓库删除
        nullable=False,
        index=True,
        info={'description': '关联仓库ID'}
    )
    scheduled_date = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '计划执行时间'}
    )
    status = db.Column(
        db.Enum(*CYCLE_COUNT_TASK_STATUSES, name='cycle_count_task_status_enum'),
        nullable=False,
        default='pending',
        info={'description': '任务状态'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,  # 高频过滤字段
        info={'description': '是否激活'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 用户删除保护
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
    started_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '实际开始时间'}
    )
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '完成时间'}
    )

    # 关系加载策略优化
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref('cycle_count_tasks', lazy='dynamic'),
        lazy='joined',  # 立即加载仓库信息
        info={'description': '仓库对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_cycle_count_tasks', lazy='dynamic'),
        lazy='joined',  # 立即加载创建人信息
        info={'description': '创建人对象'}
    )
    task_details = db.relationship(
        'CycleCountTaskDetail',
        backref='task',
        lazy='joined',  # 按需加载明细
        cascade='all, delete-orphan'
    )
    status_logs = db.relationship(
        'CycleCountTaskStatusLog',
        backref='task',
        lazy='dynamic',  # 动态加载日志
        cascade='all, delete-orphan'
    )


class CycleCountTaskDetail(db.Model):
    """盘点明细表"""
    __tablename__ = 'cycle_count_details'

    __table_args__ = (
        db.Index('idx_cycle_count_detail_goods_location', 'goods_id', 'location_id'),  # 高频查询组合
        db.Index('idx_cycle_count_detail_task_status', 'task_id', 'status'),           # 任务维度分析
        db.CheckConstraint('actual_quantity >= 0', name='chk_cycle_count_detail_quantity'),
        db.CheckConstraint('difference = actual_quantity - system_quantity', name='chk_cycle_count_detail_difference')  # 计算约束
    )

    CYCLE_COUNT_TASK_DETAIL_STATUSES = ('pending', 'completed')

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('cycle_count_tasks.id', ondelete='CASCADE'),  # 级联删除
        nullable=False,
        info={'description': '主任务ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),  # 商品删除保护
        nullable=False,
        info={'description': '商品ID'}
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),  # 库位删除保护
        nullable=False,
        info={'description': '库位ID'}
    )
    system_quantity = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '系统库存数量'}
    )
    actual_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '实际盘点数量'}
    )
    difference = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '系统计算差异'}
    )
    operator_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=True,
        info={'description': '操作人ID'}
    )
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间'}
    )
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '明细完成时间'}
    )
    status = db.Column(
        db.Enum(*CYCLE_COUNT_TASK_DETAIL_STATUSES, name='cycle_count_task_detail_status_enum'),
        nullable=False,
        default='pending',
        info={'description': '明细状态'}
    )

    # 关系优化
    goods = db.relationship(
        'Goods',
        backref=db.backref('cycle_count_task_details', lazy='dynamic'),
        lazy='joined',  # 立即加载商品信息
        info={'description': '商品对象'}
    )
    location = db.relationship(
        'Location',
        backref=db.backref('cycle_count_task_details', lazy='dynamic'),
        lazy='joined',  # 立即加载库位信息
        info={'description': '库位对象'}
    )
    operator = db.relationship(
        'User',
        backref=db.backref('operated_cycle_count_task_details', lazy='dynamic'),
        lazy='joined',  # 立即加载操作人信息
        info={'description': '操作人对象'}
    )


class CycleCountTaskStatusLog(db.Model):
    """任务状态变更日志表"""
    __tablename__ = 'cycle_count_task_status_logs'

    __table_args__ = (
        db.Index('idx_log_task_changed', 'task_id', 'changed_at'),  # 时间排序查询
        db.Index('idx_log_operator', 'operator_id')
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('cycle_count_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    old_status = db.Column(
        db.Enum(*CycleCountTask.CYCLE_COUNT_TASK_STATUSES, 
                name='cycle_count_task_status_enum',
                create_type=False),  # 复用枚举类型
        nullable=True,
        info={'description': '原状态'}
    )
    new_status = db.Column(
        db.Enum(*CycleCountTask.CYCLE_COUNT_TASK_STATUSES, 
                name='cycle_count_task_status_enum', 
                create_type=False),
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
        index=True,  # 高频查询字段
        info={'description': '变更时间'}
    )

    # 关系优化
    operator = db.relationship(
        'User',
        backref=db.backref('cycle_count_status_changes', lazy='dynamic'),
        lazy='joined',  # 立即加载操作人信息
        info={'description': '操作人对象'}
    )