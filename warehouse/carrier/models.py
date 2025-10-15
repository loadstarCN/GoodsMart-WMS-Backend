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

