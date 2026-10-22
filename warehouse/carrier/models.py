from extensions.db import *
from sqlalchemy.orm import validates
import re

class Carrier(db.Model):
    """承运商信息表
    
    Attributes:
        name: 承运商名称 (同一公司下唯一)
        email: 联系邮箱 (格式校验)
        is_active: 启用状态 (默认激活)
        company_id: 所属公司ID (外键不可删除)
    """
    __tablename__ = 'carriers'

    __table_args__ = (
        # 核心业务索引（参考网页1的优化器选择策略）
        db.Index('idx_carrier_company_active', 'company_id', 'is_active'),  # 公司维度状态查询
        db.Index('idx_carrier_geo', 'zip_code'),  # 地理维度查询加速
        
        # 数据完整性约束（借鉴网页3的模型变换思想）
        db.UniqueConstraint('company_id', 'name', name='uq_company_carrier'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(255),
        nullable=False,
        info={'description': '承运商名称（同公司下唯一）'}
    )
    code = db.Column(
        db.String(30),
        nullable=True,
        info={'description': '承运商编码（如 yamato），用于跨系统匹配'}
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

    # 关系加载优化（参考网页2的元模型加速策略）
    creator = db.relationship(
        'User', 
        foreign_keys=[created_by],
        backref=db.backref('created_carrier_records', lazy='dynamic'),
        lazy='joined',
        info={'description': '记录创建者对象'}
    )
    
    company = db.relationship(
        'Company', 
        backref=db.backref('carriers', lazy='dynamic'),
        lazy='joined',
        info={'description': '所属公司对象'}
    )


    @property
    def integration_summary(self):
        """在线对接（自动建运单）的概要：{adapter, name, enabled}；没有对接为 None"""
        integration = self.integration
        if integration is None:
            return None
        from .integrations import display_name
        return {'adapter': integration.adapter, 'name': display_name(integration.adapter),
                'enabled': bool(integration.enabled)}


class CarrierIntegration(db.Model):
    """承运商的在线对接设置（自动建运单）：一个承运商至多一条。

    adapter 是对接插件的代码（如 fedex，见 warehouse/carrier/integrations）。settings 是不保密的设置（账号、环境、
    服务类型、面单……），secrets 是密钥（API Key 等）的 JSON 用 Fernet 加密后的密文（密钥见 SETTINGS_ENCRYPTION_KEY），
    接口永远不返回明文。enabled = False 时不能新建运单，已建的运单仍可以取消。
    """
    __tablename__ = 'carrier_integrations'

    id = db.Column(db.Integer, primary_key=True)
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='CASCADE'),
        nullable=False, unique=True,
        info={'description': '承运商ID'}
    )
    adapter = db.Column(db.String(30), nullable=False, info={'description': '对接插件代码（如 fedex）'})
    enabled = db.Column(db.Boolean, nullable=False, default=True, info={'description': '启用（可以新建运单）'})
    settings = db.Column(db.JSON, nullable=True, info={'description': '不保密的设置'})
    secrets = db.Column(db.Text, nullable=True, info={'description': '密钥 JSON 的密文（Fernet）'})
    updated_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        info={'description': '最后修改人ID'}
    )
    created_at = db.Column(db.DateTime, default=db.func.now(), info={'description': '创建时间'})
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now(),
                           info={'description': '最后修改时间'})

    carrier = db.relationship(
        'Carrier',
        backref=db.backref('integration', uselist=False, lazy='select', cascade='all, delete-orphan',
                           passive_deletes=True),
        info={'description': '承运商'}
    )
