from extensions import db

class Inventory(db.Model):
    """库存核心表（按商品+仓库维度管理）"""
    __tablename__ = 'inventory'

    __table_args__ = (
        db.Index('idx_inventory_goods_warehouse', 'goods_id', 'warehouse_id'),  # 商品+仓库维度查询        
        db.Index('idx_low_stock', 'low_stock_threshold'),  # 低库存预警查询加速
        db.Index('idx_high_stock', 'high_stock_threshold'),  # 高库存积压分析
        db.CheckConstraint('total_stock >= 0', name='chk_non_negative_total_stock'),
    )

    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        primary_key=True,
        nullable=False,
        index=True,
        info={'description': '商品ID（禁止删除有库存的商品）'}
    )
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),
        primary_key=True,
        nullable=False,
        index=True,
        info={'description': '仓库ID（禁止删除有库存的仓库）'}
    )
    
    # 库存状态字段（增加非负约束）
    total_stock = db.Column(
        db.Integer, 
        default=0,
        nullable=False,
        info={'description': '总库存量（=可用+锁定+损坏）'}
    )
    onhand_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '可售库存（≥0）'}
    )
    locked_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '订单锁定库存（预扣）'}
    )
    damage_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '残次品库存（需隔离）'}
    )
    return_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '退换货待上架库存'}
    )
    
    # 阈值管理（增加索引）
    low_stock_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=-1,
        info={'description': '补货阈值（触发采购建议）'}
    )
    high_stock_threshold = db.Column(
        db.Integer,
        nullable=False,
        default=-1,
        info={'description': '积压阈值（触发促销建议）'}
    )

    # 物流过程字段（增加流程校验）
    asn_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '在途库存（ASN预到货量）'}
    )
    received_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已签收未分拣库存（不计入总库存）',}
    )
    sorted_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '分拣完成待上架库存（计入总库存）'}
    )
    
    # 出库流程字段（增加状态校验）
    dn_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '发货单预扣总量'}
    )
    picked_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已拣货未打包量'}
    )
    packed_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已打包未发货量'}
    )
    delivered_stock = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已发货在途量'}
    )

    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '库存备注'}
    )
    # 时间字段（优化索引策略）
    create_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        index=True,
        info={'description': '首次入库时间'}
    )
    update_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        index=True,
        info={'description': '最后更新时间'}
    )

    # 关系加载策略优化
    goods = db.relationship(
        'Goods', 
        back_populates='inventories',
        lazy='joined'  # 预加载商品信息
    )
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref(
            'inventories', 
            lazy='dynamic',  # 动态加载仓库库存
        ),
        lazy='joined'
    )

    # 计算可售卖库存
    @property
    def available_stock_for_sale(self):
        """计算可售卖库存（可用库存 - 锁定库存- DN库存）"""
        return self.onhand_stock - self.locked_stock- self.dn_stock
    
    # 计算可用库存
    @property
    def available_stock(self):
        return self.onhand_stock+self.damage_stock+self.return_stock - self.locked_stock - self.dn_stock
    
    def __repr__(self):
        return (
            f"<Inventory {self.goods_id}@{self.warehouse_id}>\n"            
            f"  Stock Status:\n"
            f"  - Total Stock  : {self.total_stock}\n"
            f"  - Onhand       : {self.onhand_stock}\n"
            f"  - Locked       : {self.locked_stock}\n"
            f"  - Damaged      : {self.damage_stock}\n"
            f"  - Return       : {self.return_stock}\n\n"
            f"  ASN Process Flow:\n"
            f"  - ASN          : {self.asn_stock}\n"
            f"  - Received     : {self.received_stock}\n"
            f"  - Sorted       : {self.sorted_stock}\n\n"
            f"  DN Process Flow:\n"
            f"  - DN           : {self.dn_stock}\n"
            f"  - Picked       : {self.picked_stock}\n"
            f"  - Packed       : {self.packed_stock}\n"
            f"  - Delivered    : {self.delivered_stock}\n\n"
            f"  Thresholds     : {self.low_stock_threshold}~{self.high_stock_threshold}"
    )