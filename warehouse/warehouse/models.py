from extensions.db import *
from extensions.db import UTCDateTime

class Warehouse(db.Model):
    """仓库信息表
    
    Attributes:
        name: 仓库名称 (唯一约束)
        address: 仓库地址 
        company_id: 所属公司ID (外键不可删除)
        is_active: 启用状态 (默认True)
        created_at: 创建时间 (自动记录)
        updated_at: 更新时间 (自动更新)
    """
    __tablename__ = 'warehouses'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_warehouse_company', 'company_id', 'is_active'),  # 公司维度查询
        db.Index('idx_warehouse_active', 'is_active'),  # 状态过滤

        db.UniqueConstraint('company_id', 'name', name='uq_company_warehouse'),  # 新增复合唯一约束
        
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, info={'description': '仓库ID'})
    name = db.Column(
        db.String(255), 
        nullable=False, 
        info={'description': '仓库名称（同公司下唯一）'}  
    )
    address = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '详细地址'}
    )
    phone = db.Column(
        db.String(20), 
        nullable=True,
    )
    zip_code = db.Column(
        db.String(10), 
        nullable=True,
        info={'description': '邮政编码'}
    )
    default_currency = db.Column(
        db.String(10),
        nullable=True,
        default='JPY',
        info={'description': '默认货币代码（ISO 4217标准）'}
    )
    # ---- 出口资料（仓库地址 ≠ 公司地址时印在单证的 Ship From 栏）----
    address_en = db.Column(
        db.String(500),
        nullable=True,
        info={'description': '英文地址（出口单证用）'}
    )
    country_code = db.Column(
        db.String(2),
        nullable=True,
        default='JP',
        server_default='JP',
        info={'description': '所在国家（ISO 3166-1 alpha-2）'}
    )
    contact_name_en = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '仓库联系人（英文）'}
    )
    # 结构化地址（doc/shipping-flow.md §6.2，发件人 / 揽收地点用；字段见 warehouse/common/address_format.py）。
    # 单行的 address 保留（只给了结构化字段时按国家格式生成），address_en 作为拉丁地址的单行写法保留
    admin_area = db.Column(db.String(100), nullable=True, info={'description': '州 / 省 / 都道府县'})
    locality = db.Column(db.String(100), nullable=True, info={'description': '城市 / 市区町村'})
    address_line1 = db.Column(db.String(255), nullable=True, info={'description': '街道 / 番地'})
    address_line2 = db.Column(db.String(255), nullable=True, info={'description': '建筑 / 房间'})
    latin = db.Column(
        db.JSON, nullable=True,
        info={'description': '拉丁转写 {name, organization, address_line1, address_line2, locality, admin_area}'}
    )
    # 仓库时区（IANA 名，如 Asia/Tokyo、America/Los_Angeles；doc/timezone-utc.md）。空 = 用默认时区
    # （WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC）。业务日期（实际发货日、单证日期、揽收日……）、不带时区的输入时刻、
    # 界面显示都按它；生效值见 effective_timezone（warehouse/common/clock.py）
    timezone = db.Column(
        db.String(64),
        nullable=True,
        info={'description': '仓库时区（IANA 名，空 = 默认时区）'}
    )
    company_id = db.Column(
        db.Integer, 
        db.ForeignKey('companies.id', ondelete='RESTRICT'),  # 阻止公司删除
        nullable=False,
        info={'description': '所属公司ID'}
    )
    manager_id = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='SET NULL'),  # 管理员删除后置空
        nullable=True,
        info={'description': '仓库管理员ID'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        info={'description': '启用状态（默认启用）'}
    )
    created_by = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 创建人不可删除
        nullable=False,
        info={'description': '创建人ID'}
    )
    created_at = db.Column(
        UTCDateTime, 
        default=db.func.now(),
        info={'description': '创建时间（自动记录）'}
    )
    updated_at = db.Column(
        UTCDateTime, 
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间（自动更新）'}
    )

    # 关系加载
    company = db.relationship(
        'Company', 
        backref=db.backref('warehouses', lazy='dynamic'),
        lazy='joined',  # 立即加载公司信息
        info={'description': '所属公司对象'}
    )
    
    manager = db.relationship(
        'User', 
        foreign_keys=[manager_id],
        backref=db.backref('managed_warehouses', lazy='dynamic'),
        lazy='joined',  # 立即加载管理员
        info={'description': '管理员对象'}
    )
    
    creator = db.relationship(
        'User',
        foreign_keys=[created_by],
        backref=db.backref('created_warehouses', lazy='dynamic'),
        lazy='joined',  # 立即加载创建者
        info={'description': '创建者对象'}
    )

    @property
    def effective_timezone(self) -> str:
        """生效时区：仓库设了的 → WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC"""
        from warehouse.common.clock import warehouse_timezone_name
        return warehouse_timezone_name(self)
