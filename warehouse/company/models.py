from extensions.db import *

class Company(db.Model):
    """企业信息表
    
    Attributes:
        name: 企业名称 (唯一约束)
        email: 企业邮箱 (格式校验)
        is_active: 启用状态 (默认True)
        created_at: 创建时间 (自动记录)
        updated_at: 更新时间 (自动更新)
    """
    __tablename__ = 'companies'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_company_name', 'name'),  # 名称查询加速
        db.Index('idx_company_active', 'is_active'),  # 状态过滤 
        db.Index('idx_company_expired_at', 'expired_at'),  # 过期时间排序     
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(255), 
        nullable=False, 
        unique=True,
        info={'description': '企业名称（唯一）'}
    )
    email = db.Column(
        db.String(255), 
        nullable=True, 
        unique=True,
        info={'description': '企业邮箱（格式校验）'}
    )
    phone = db.Column(
        db.String(20), 
        nullable=True,
        info={'description': '联系电话'}
    )
    address = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '注册地址'}
    )
    zip_code = db.Column(
        db.String(10), 
        nullable=True,
        info={'description': '邮政编码'}
    )
    logo = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '企业Logo存储路径'}
    )
    default_currency = db.Column(
        db.String(10),
        nullable=True,
        default='JPY',
        info={'description': '默认显示货币代码（ISO 4217标准）'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        info={'description': '启用状态（默认激活）'}
    )
    created_by = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 创建人不可删除
        nullable=False,
        info={'description': '创建人ID'}
    )
    created_at = db.Column(
        db.DateTime, 
        default=db.func.now(),
        info={'description': '创建时间（自动记录）'}
    )
    updated_at = db.Column(
        db.DateTime, 
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间（自动更新）'}
    )

    expired_at = db.Column(
        db.DateTime, 
        nullable=True,
        info={'description': '账户过期时间'}
    )

    # ---- 出口资料（商业发票 / 装箱单的 Shipper / Exporter 栏）----
    legal_name_en = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '英文法定名称（出口单证用）'}
    )
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
    tax_id_label = db.Column(
        db.String(40),
        nullable=True,
        info={'description': '税号在单证上的标签（如 Corporate No.）'}
    )
    tax_id = db.Column(
        db.String(40),
        nullable=True,
        info={'description': '出口方税号 / 法人编号'}
    )
    export_contact_name = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '出口联系人（英文）'}
    )
    export_signatory_name = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '单证签字人姓名（英文）'}
    )
    export_signatory_title = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '单证签字人职务（英文）'}
    )

    # 关系加载优化
    creator = db.relationship(
        'User', 
        backref=db.backref('created_companies', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载创建者信息
        info={'description': '企业创建者对象'}
    )