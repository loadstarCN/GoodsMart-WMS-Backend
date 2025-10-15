from extensions.db import *

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