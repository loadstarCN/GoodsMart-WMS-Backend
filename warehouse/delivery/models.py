from extensions.db import *
from sqlalchemy import Index, CheckConstraint

class DeliveryTask(db.Model):
    """配送任务主表"""
    __tablename__ = 'delivery_tasks'

    __table_args__ = (
        # 核心业务查询索引
        Index('idx_delivery_status', 'status'),  # 状态过滤
        Index('idx_transportation_mode', 'transportation_mode'),  # 运输方式分析
        Index('idx_expected_shipping_date', 'expected_shipping_date'),  # 发货日期排序
        CheckConstraint('actual_shipping_date >= expected_shipping_date', 
                       name='chk_shipping_date'),  # 时间逻辑校验
        CheckConstraint('shipping_cost >= 0', name='chk_shipping_cost')
    )

    DELIVERY_TASK_STATUSES = ('pending', 'in_progress', 'completed', 'signed')
    DELIVERY_TASK_TRANSPORTATION_MODES = ('express', 'pickup', 'courier', 'air', 'sea', 'land', 'rail', 'drone')

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer, 
        db.ForeignKey('dn.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '关联发货单ID'}
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
        index=True,
        info={'description': '计划发货日期'}
    )
    actual_shipping_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '实际发货日期'}
    )
    transportation_mode = db.Column(
        db.Enum(*DELIVERY_TASK_TRANSPORTATION_MODES, name='delivery_task_transportation_mode_enum'),
        nullable=True,
        info={'description': '运输方式枚举'}
    )
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='RESTRICT'),
        nullable=True,
        info={'description': '承运商ID'}
    )
    tracking_number = db.Column(
        db.String(100),
        nullable=True,
        index=True,
        info={'description': '物流追踪号'}
    )
    shipping_cost = db.Column(
        db.Float,
        nullable=True,
        default=0.0,
        info={'description': '运费金额'}
    )
    currency = db.Column(
        db.String(10),
        nullable=True,
        default='JPY',
        info={'description': '货币代码（ISO 4217标准）'}
    )
    order_number = db.Column(
        db.String(50),
        nullable=True,
        index=True,
        info={'description': '客户订单号'}
    )
    status = db.Column(
        db.Enum(*DELIVERY_TASK_STATUSES, name='delivery_task_status_enum'),
        nullable=False,
        default='pending',
        info={'description': '任务状态'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '操作备注'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,
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
        info={'description': '最后更新时间'}
    )
    started_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '任务开始时间'}
    )
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '任务完成时间'}
    )
    signed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '客户签收时间'}
    )

    # 关系加载策略优化
    dn = db.relationship(
        'DN', 
        backref=db.backref('delivery_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联发货单对象'}
    )
    recipient = db.relationship(
        'Recipient',
        backref=db.backref('delivery_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '收货人对象'}
    )
    carrier = db.relationship(
        'Carrier',
        backref=db.backref('delivery_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '承运商对象'}
    )
    creator = db.relationship(
        'User', 
        backref=db.backref('created_delivery_tasks', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    status_logs = db.relationship(
        'DeliveryTaskStatusLog',
        backref='task',
        lazy='dynamic',
        cascade='all, delete-orphan',
        info={'description': '状态变更历史'}
    )


class DeliveryTaskStatusLog(db.Model):
    """任务状态变更日志表"""
    __tablename__ = 'delivery_task_status_logs'

    __table_args__ = (
        Index('idx_log_task_operator', 'task_id', 'operator_id'),
        Index('idx_delivery_log_changed_time', 'changed_at')
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey('delivery_tasks.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主任务ID'}
    )
    old_status = db.Column(
        db.Enum(*DeliveryTask.DELIVERY_TASK_STATUSES, 
                name='delivery_task_status_enum',
                create_type=False),
        nullable=True,
        info={'description': '原始状态'}
    )
    new_status = db.Column(
        db.Enum(*DeliveryTask.DELIVERY_TASK_STATUSES, 
                name='delivery_task_status_enum',
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
        info={'description': '变更时间'}
    )

    # 关系优化
    operator = db.relationship(
        'User',
        backref=db.backref('delivery_status_changes', lazy='dynamic'),
        lazy='joined',
        info={'description': '操作人对象'}
    )