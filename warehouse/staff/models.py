from extensions import db
from system.user.models import User

# 优化多对多关联表（复合索引加速查询）
staff_warehouse = db.Table(
    'staff_warehouse',
    db.Column('staff_id', db.Integer, db.ForeignKey('staff.id'), primary_key=True, index=True),
    db.Column('warehouse_id', db.Integer, db.ForeignKey('warehouses.id'), primary_key=True, index=True),
    db.Index('idx_staff_warehouse', 'staff_id', 'warehouse_id', unique=True)  # 防止重复关联
)

class Staff(User):
    """员工主表（继承用户模型）
    
    核心特性：
    - 公司级唯一员工编号（跨部门不重复）
    - 仓库管辖关系预加载
    - 复合唯一约束保障数据完整性
    """
    __tablename__ = 'staff'

    __table_args__ = (
        db.Index('idx_staff_company', 'company_id'),  # 公司查询加速
        db.Index('idx_staff_employee', 'employee_number'),  # 工号快速检索
        db.UniqueConstraint('company_id', 'employee_number', name='uq_company_empno'),  # 公司级唯一
    )

    id = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='CASCADE'),  # 级联删除
        primary_key=True,
        info={'description': '主键，关联用户ID'}
    )
    company_id = db.Column(
        db.Integer,
        db.ForeignKey('companies.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '所属公司ID（不可删除关联公司）'}
    )
    
    department_id = db.Column(
        db.Integer,
        db.ForeignKey('departments.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        info={'description': '所属部门ID'}
    )
    position = db.Column(
        db.String(64),  # 优化字段长度
        nullable=True,
        info={'description': '职位名称'}
    )
    employee_number = db.Column(
        db.String(20),
        nullable=True,  # 强制非空
        info={'description': '员工编号（公司内唯一）'}
    )
    hire_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '入职日期'}
    )
    phone = db.Column(
        db.String(20),
        nullable=True,
        unique=True,  # 唯一约束
        info={'description': '手机号码（唯一）'}
    )
    openid = db.Column(
        db.String(100),
        nullable=True,
        unique=True,  # 唯一约束
        index=True,
        info={'description': '微信OpenID（唯一）'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        info={'description': '记录创建人ID'}
    )

    # 关系加载策略优化
    warehouses = db.relationship(
        'Warehouse',
        secondary=staff_warehouse,
        backref=db.backref('staff_members', lazy='dynamic'),
        lazy='joined',  
        info={'description': '管辖仓库集合（JOIN预加载）'}
    )
    company = db.relationship(
        'Company',
        backref=db.backref('staff', lazy='dynamic'),  # 动态加载
        lazy='joined',
        info={'description': '关联公司对象'}
    )
    department = db.relationship(
        'Department',
        backref=db.backref('staff', lazy='dynamic'),  # 动态加载
        lazy='joined',
        info={'description': '关联部门对象'}
    )
    creator = db.relationship(
        'User',
        foreign_keys=[created_by],
        backref=db.backref('created_staff_records', lazy='dynamic'),  # 动态加载
        lazy='joined',
        info={'description': '创建人对象'}
    )
    

    __mapper_args__ = {
        'inherit_condition': id == User.id,
        'polymorphic_identity': 'staff',
    } 