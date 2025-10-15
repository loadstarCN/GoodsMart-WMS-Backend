from extensions.db import *

class Department(db.Model):
    """部门信息表
    
    Attributes:
        name: 部门名称 (同一公司下唯一)
        company_id: 所属公司ID (外键不可删除)
        is_active: 启用状态 (默认True)
        created_at: 创建时间 (自动记录)
        updated_at: 更新时间 (自动更新)
    """
    __tablename__ = 'departments'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_department_company_active', 'company_id', 'is_active'),  # 公司维度状态查询
        db.Index('idx_department_name', 'name'),  # 名称查询加速
        
        # 数据完整性约束
        db.UniqueConstraint('company_id', 'name', name='uq_company_department'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(255), 
        nullable=False,
        info={'description': '部门名称（同公司下唯一）'}
    )
    description = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '部门描述（最长255字符）'}
    )
    company_id = db.Column(
        db.Integer, 
        db.ForeignKey('companies.id', ondelete='RESTRICT'),  # 阻止公司删除
        nullable=False,
        info={'description': '所属公司ID'}
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

    # 关系加载
    company = db.relationship(
        'Company', 
        backref=db.backref('departments', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载公司信息
        info={'description': '所属公司对象'}
    )
    
    creator = db.relationship(
        'User',
        backref=db.backref('created_departments', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载创建者
        info={'description': '部门创建者对象'}
    )