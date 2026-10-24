from extensions.db import *

class ASN(db.Model):
    """到货通知单主表
    
    Attributes:
        warehouse_id: 关联仓库ID (外键约束不可删除)
        supplier_id: 供应商ID (必须存在于suppliers表)
        status: 单据状态 (pending/received/completed/closed)
        asn_type: 单据类型 (inbound/return_from_customer/transfer)
    """
    __tablename__ = 'asn'
    
    __table_args__ = (
        db.Index('idx_asn_warehouse_status', 'warehouse_id', 'status'),  # 高频查询组合索引
        db.Index('idx_asn_expected_date', 'expected_arrival_date'),      # 预计到货日期索引
        db.Index('idx_asn_created_status', 'created_at', 'status'), 
        db.CheckConstraint("status IN ('pending','received','completed','closed')", name='chk_valid_status'),
        db.CheckConstraint("asn_type IN ('inbound','return_from_customer','transfer')", name='chk_valid_type')
    )

    ASN_TYPES = ('inbound', 'return_from_customer', 'transfer')
    ASN_STATUSES = ('pending', 'received', 'completed', 'closed')

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '关联仓库ID'}
    )
    supplier_id = db.Column(
        db.Integer,
        db.ForeignKey('suppliers.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '供应商ID'}
    )
    tracking_number = db.Column(
        db.String(100),
        nullable=True,
        index=True,  # 快递单号高频查询字段
        info={'description': '物流追踪号'}
    )
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='RESTRICT'),
        nullable=True,
        info={'description': '承运商ID'}
    )
    asn_type = db.Column(
        db.Enum(*ASN_TYPES, name='asn_type_enum'),
        nullable=False,
        default='inbound',
        info={'description': 'ASN类型'}
    )
    status = db.Column(
        db.Enum(*ASN_STATUSES, name='asn_status_enum'),
        nullable=False,
        default='pending',
        info={'description': '单据状态'}
    )
    expected_arrival_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '预计到达日期'}
    )
    order_number = db.Column(
        db.String(100),
        nullable=True,
        index=True,
        info={'description': '关联外部订单号'}
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
    api_key_id = db.Column(
        db.Integer,
        db.ForeignKey('api_keys.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        info={'description': '创建来源 API Key ID（用于定向 Webhook 推送）'}
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
    received_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '收货完成时间'}
    )
    completed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '流程完成时间'}
    )
    closed_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '单据关闭时间'}
    )
    # 运单号的最近一次承运商跟踪结果（入库流程手动刷新，warehouse/asn/tracking.py；doc/shipping-flow.md §7.1）。
    # 改运单号 / 承运商时清空
    tracking_status = db.Column(
        db.String(20),
        nullable=True,
        info={'description': '跟踪状态（TRACK_*：label_created / in_transit / out_for_delivery / delivered / '
                             'exception / unknown）'}
    )
    tracking_detail = db.Column(
        db.JSON,
        nullable=True,
        info={'description': '跟踪详情 {code, status_text, picked_up_at, delivered_at, estimated_delivery, events, '
                             'error}'}
    )
    tracking_synced_at = db.Column(
        db.DateTime,
        nullable=True,
        info={'description': '最近一次查询跟踪的时刻'}
    )

    def tracking_payload(self):
        """跟踪结果（没查过为 None）：{status, code, status_text, picked_up_at, delivered_at, estimated_delivery,
        events, error, synced_at, problem}（与出库运单的 tracking_payload 同一结构；入库不自动做事，problem 恒为 None）"""
        if self.tracking_synced_at is None:
            return None
        detail = self.tracking_detail if isinstance(self.tracking_detail, dict) else {}
        return {
            'status': self.tracking_status,
            'code': detail.get('code'),
            'status_text': detail.get('status_text'),
            'picked_up_at': detail.get('picked_up_at'),
            'delivered_at': detail.get('delivered_at'),
            'estimated_delivery': detail.get('estimated_delivery'),
            'events': detail.get('events') or [],
            'error': detail.get('error'),
            # 带时区偏移（同 warehouse.dn.services.event_time）
            'synced_at': (self.tracking_synced_at.astimezone().isoformat(timespec='seconds')
                          if self.tracking_synced_at else None),
            'problem': None,
        }

    def clear_tracking(self):
        """运单号 / 承运商变了：之前的跟踪结果不再适用"""
        self.tracking_status = None
        self.tracking_detail = None
        self.tracking_synced_at = None

    # 关系加载优化
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref('asns', lazy='dynamic'),
        lazy='joined',
        info={'description': '仓库对象'}
    )
    supplier = db.relationship(
        'Supplier',
        backref=db.backref('asns', lazy='dynamic'),
        lazy='joined',
        info={'description': '供应商对象'}
    )
    carrier = db.relationship(
        'Carrier',
        backref=db.backref('asns', lazy='dynamic'),
        lazy='joined',
        info={'description': '承运商对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_asns', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    details = db.relationship(
        'ASNDetail',
        backref='asn',
        lazy='select',  # 明细按需加载
        cascade='all, delete-orphan',
        info={'description': 'ASN明细集合'}
    )


class ASNDetail(db.Model):
    """ASN明细表
    
    Attributes:
        quantity: 预期数量 (必须≥0)
        actual_quantity: 实际到货数量 (必须≥0)
    """
    __tablename__ = 'asn_details'
    
    __table_args__ = (
        db.Index('idx_detail_goods_asn', 'goods_id', 'asn_id'),  # 高频查询组合索引
        db.CheckConstraint('quantity >= 0', name='chk_quantity'),
        db.CheckConstraint('actual_quantity >= 0', name='chk_actual_quantity'),
        db.CheckConstraint('sorted_quantity >= 0', name='chk_sorted_quantity'),
        db.CheckConstraint('damage_quantity >= 0', name='chk_damage_quantity')
    )

    id = db.Column(db.Integer, primary_key=True)
    asn_id = db.Column(
        db.Integer,
        db.ForeignKey('asn.id', ondelete='CASCADE'),  # 级联删除
        nullable=False,
        info={'description': 'ASN主单ID'}
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
        info={'description': '预期数量'}
    )
    actual_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=True,
        info={'description': '实际到货数量'}
    )
    sorted_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=True,
        info={'description': '已分拣的正常数量'}
    )
    damage_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=True,
        info={'description': '损坏数量'}
    )
    weight = db.Column(
        db.Float,
        default=0.0,
        nullable=True,
        info={'description': '商品重量(kg)'}
    )
    volume = db.Column(
        db.Float,
        default=0.0,
        nullable=True,
        info={'description': '商品体积(m³)'}
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
        backref=db.backref('asn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_asn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )