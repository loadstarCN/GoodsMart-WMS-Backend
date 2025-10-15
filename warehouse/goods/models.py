from extensions import db

class Goods(db.Model):
    """商品主表（支持多公司多仓库管理）"""
    __tablename__ = 'goods'

    __table_args__ = (
        db.UniqueConstraint('code', 'company_id', name='uix_code_company'),
        db.Index('idx_goods_code', 'code'),          # 商品编码快速检索
        db.Index('idx_goods_company', 'company_id'),  # 公司查询加速
        db.Index('idx_goods_name', 'name'),          # 商品名称模糊查询
        db.Index('idx_goods_tags', 'tags'),          # 标签维度分析
        db.Index('idx_goods_category', 'category'),  # 分类查询加速
        db.Index('idx_goods_brand', 'brand'),        # 品牌维度分析
        db.Index('idx_goods_manufacturer', 'manufacturer'),  # 厂商模糊查询
        db.Index('idx_price_range', 'price'),       # 价格区间查询
        db.CheckConstraint("price >= 0", name='chk_positive_price'),
        db.CheckConstraint("discount_price <= price", name='chk_discount_price'),        
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer,
        db.ForeignKey('companies.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联公司ID（禁止删除有商品的公司）'}
    )
    code = db.Column(
        db.String(50),
        nullable=False,
        info={'description': '商品编码（公司内唯一）'}
    )
    name = db.Column(
        db.String(100),
        nullable=False,
        index=True,
        info={'description': '商品名称（支持模糊查询）'}
    )
    description = db.Column(
        db.Text,  # 扩展描述长度
        nullable=True,
        info={'description': '商品详细描述'}
    )
    unit = db.Column(
        db.String(20),
        nullable=False,
        default='pcs',
        info={'description': '计量单位（默认：件）'}
    )
    # 尺寸优化为毫米单位
    weight = db.Column(
        db.Numeric(8, 3),  # 最大99999.999千克
        nullable=True,
        info={'description': '重量（千克，三位小数）'}
    )
    length = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '长度（毫米）'}
    )
    width = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '宽度（毫米）'}
    )
    height = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '高度（毫米）'}
    )
    manufacturer = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '生产厂商全称'}
    )
    brand = db.Column(
        db.String(255),
        nullable=True,
        index=True,
        info={'description': '品牌名称（支持品牌维度分析）'}
    )
    image_url = db.Column(
        db.String(512),  # 扩展URL长度
        nullable=True,
        info={'description': '高清图片URL'}
    )
    thumbnail_url = db.Column(
        db.String(512),
        nullable=True,
        info={'description': '缩略图URL（尺寸建议：300x300）'}
    )
    category = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '商品分类（三级分类示例：家电/厨房电器/电饭煲）'}
    )
    tags = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '商品标签（示例：["促销","新品"]）'}
    )
    price = db.Column(
        db.Numeric(12, 2),  # 精确金额
        nullable=True,
        info={'description': '标准售价（含两位小数）'}
    )
    discount_price = db.Column(
        db.Numeric(12, 2),
        nullable=True,
        info={'description': '促销价格（需≤标准售价）'}
    )
    currency = db.Column(
        db.String(10),
        nullable=False,
        default='JPY',
        info={'description': '货币代码（ISO 4217标准）'}
    )
    expiration_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '保质期截止日（仅限食品/药品）'}
    )
    production_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '生产日期（ISO 8601格式）'}
    )
    extra_data = db.Column(
        db.JSON,  # 使用JSONB类型（PostgreSQL）
        nullable=True,
        info={'description': '扩展数据（示例：{"color":"red","material":"metal"}）'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,
        info={'description': '是否上架'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
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

    # 关系加载策略
    company = db.relationship(
        'Company',
        backref=db.backref('goods', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联公司对象'}
    )
    storage_records = db.relationship(
        'GoodsLocation',
        backref='goods',
        lazy='dynamic',
        cascade='all, delete-orphan',
        info={'description': '库位存储记录'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('goods_created', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    inventories = db.relationship(
        'Inventory',
        back_populates='goods',
        lazy='select',
        info={'description': '库存变动记录'}
    )

class GoodsLocation(db.Model):
    """商品库位存储表"""
    __tablename__ = 'goods_locations'

    __table_args__ = (
        db.UniqueConstraint('goods_id', 'location_id', name='uix_goods_location'),
        db.Index('idx_goods_location_goods', 'goods_id'),
        db.Index('idx_goods_location_location', 'location_id'),
        db.CheckConstraint("quantity >= 0", name='chk_non_negative_quantity'),
    )

    id = db.Column(db.Integer, primary_key=True)
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联商品ID（禁止删除有库存的商品）'}
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联库位ID（禁止删除有库存的库位）'}
    )
    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '当前库存数量'}
    )
    created_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '入库时间'}
    )
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间'}
    )

    # 关系加载策略
    location = db.relationship(
        'Location',
        backref=db.backref('stored_goods', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联库位对象'}
    )