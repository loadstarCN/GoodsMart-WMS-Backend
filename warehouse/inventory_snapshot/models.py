from extensions import db

class InventorySnapshot(db.Model):
    """库存快照表（按商品+仓库+时间维度管理）"""
    __tablename__ = 'inventory_snapshot'

    __table_args__ = (
        db.Index('idx_inventory_snapshot_goods_warehouse_location_time', 'goods_id', 'warehouse_id', 'location_id', 'snapshot_time'),  # 商品+仓库+时间
    )
    id = db.Column(db.Integer, primary_key=True)

    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '商品ID（禁止删除有库存的商品）'}
    )
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '仓库ID（禁止删除有库存的仓库）'}
    )

    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '库位ID（禁止删除有库存的库位）'}
    )

    quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '快照库存量'}
    )

    snapshot_time = db.Column(
        db.DateTime,
        nullable=False,
        info={'description': '快照时间'}
    )

    # 关系加载策略优化
    goods = db.relationship(
        'Goods', 
        backref=db.backref(
            'inventory_snapshots', 
            lazy='dynamic',  # 动态加载仓库库存
        ),
        lazy='joined'  # 预加载商品信息
    )
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref(
            'inventory_snapshots', 
            lazy='dynamic',  # 动态加载仓库库存
        ),
        lazy='joined'
    )

    location = db.relationship(
        'Location',
        backref=db.backref(
            'inventory_snapshots', 
            lazy='dynamic',  # 动态加载库位库存
        ),
        lazy='joined'
    )

    
    