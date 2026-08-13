import hashlib

from extensions.db import db

class Recipient(db.Model):
    """收件人信息表
    
    Attributes:
        name: 收件方名称
        country: 国家代码 (ISO 3166-1 alpha-2)
        is_active: 启用状态 (默认激活)
        company_id: 所属公司ID (外键不可删除)
    """
    __tablename__ = 'recipients'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_recipient_company_country', 'company_id', 'country'),  # 公司+国家组合查询
        db.Index('idx_recipient_geo', 'country', 'zip_code'),  # 地理维度查询加速
        
        db.UniqueConstraint('company_id', 'name', name='uq_company_recipient'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(255), 
        nullable=False,
        info={'description': '收件方名称'}
    )
    address = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '详细地址（含街道信息）'}
    )
    zip_code = db.Column(
        db.String(10), 
        nullable=True,
        info={'description': '邮政编码'}
    )
    phone = db.Column(
        db.String(20), 
        nullable=True,
        info={'description': '联系电话'}
    )
    email = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '联系邮箱'}
    )
    contact = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '对接联系人姓名'}
    )
    country = db.Column(
        db.String(2), 
        nullable=False,
        info={'description': '国家代码（ISO 3166标准）'}
    )
    is_active = db.Column(
        db.Boolean, 
        default=True,
        info={'description': '启用状态（默认激活）'}
    )
    company_id = db.Column(
        db.Integer, 
        db.ForeignKey('companies.id', ondelete='RESTRICT'),  # 公司删除保护
        nullable=False,
        info={'description': '所属公司ID'}
    )
    created_by = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 创建人不可删除
        nullable=True,
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

    # 关系加载优化
    creator = db.relationship(
        'User', 
        foreign_keys=[created_by],
        backref=db.backref('created_recipient_records', lazy='dynamic'),  # 动态反向查询
        lazy='joined',
        info={'description': '记录创建者对象'}
    )
    
    company = db.relationship(
        'Company', 
        backref=db.backref('recipients', lazy='dynamic'),  # 支持链式过滤
        lazy='joined',
        info={'description': '所属公司对象'}
    )

    external_mapping = db.relationship(
        'RecipientExternalMapping',
        back_populates='recipient',
        uselist=False,
        cascade='all, delete-orphan',
        lazy='joined',
    )

    @property
    def display_name(self):
        return (
            self.external_mapping.display_name
            if self.external_mapping else self.name
        )

    @property
    def external_reference(self):
        return (
            self.external_mapping.external_reference
            if self.external_mapping else None
        )

    @property
    def display_email(self):
        return (
            self.external_mapping.email
            if self.external_mapping else self.email
        )

    @staticmethod
    def integration_storage_name(display_name, external_reference):
        """生成不暴露给 API/UI 的稳定技术名，绕开历史姓名唯一约束。"""
        suffix = hashlib.sha256(
            str(external_reference).encode('utf-8')
        ).hexdigest()[:16]
        prefix = str(display_name or 'Recipient')[:230]
        return f'{prefix} [ext:{suffix}]'


class RecipientExternalMapping(db.Model):
    """外部配送地址与历史 Recipient 的一对一映射。"""
    __tablename__ = 'recipient_external_mappings'
    __table_args__ = (
        db.UniqueConstraint(
            'company_id', 'external_reference',
            name='uq_recipient_external_mapping_company_reference',
        ),
        db.UniqueConstraint(
            'recipient_id', name='uq_recipient_external_mapping_recipient'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    external_reference = db.Column(db.String(100), nullable=False)
    recipient_id = db.Column(
        db.Integer,
        db.ForeignKey('recipients.id', ondelete='CASCADE'),
        nullable=False,
    )
    display_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(
        db.DateTime, default=db.func.now(), onupdate=db.func.now()
    )

    recipient = db.relationship('Recipient', back_populates='external_mapping')

