from extensions import db  
from werkzeug.security import generate_password_hash, check_password_hash
from extensions.db import UTCDateTime


# 定义 Role 与 Permission 的多对多关联表（增加级联策略）
role_permissions = db.Table(
    'role_permissions',
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
    db.Index('idx_role_perm', 'role_id', 'permission_id')  # 复合索引优化关联查询
)

class Permission(db.Model):
    """系统权限表（原子操作权限）"""
    __tablename__ = 'permissions'
    
    __table_args__ = (
        db.UniqueConstraint('name', name='uq_permission_name'),  # 权限名称全局唯一
        db.Index('idx_perm_name', 'name'),  # 权限名称查询加速
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(100), 
        nullable=False,
        info={'description': '权限标识符（如 manage_users）'}
    )
    description = db.Column(
        db.String(255),
        info={'description': '权限功能描述'}
    )

# 用户与角色模型
user_roles = db.Table(
    'user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    db.Index('idx_user_role', 'user_id', 'role_id')  # 复合索引优化
)

class Role(db.Model):
    """系统角色表（权限集合）"""
    __tablename__ = 'roles'
    
    __table_args__ = (
        db.UniqueConstraint('name', name='uq_role_name'),  # 角色名称全局唯一
        db.Index('idx_role_active', 'is_active'),  # 状态过滤优化
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(80), 
        nullable=False,
        info={'description': '角色名称（如 admin）'}
    )
    description = db.Column(
        db.String(255),
        info={'description': '角色描述'}
    )
    
    is_active = db.Column(
        db.Boolean, 
        default=True,
        info={'description': '是否启用（默认激活）'}
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

    permissions = db.relationship(
        'Permission',
        secondary=role_permissions,
        backref=db.backref('roles', lazy='dynamic'),
        lazy='joined',  # 立即加载权限列表
        info={'description': '包含的权限集合'}
    )

class User(db.Model):
    """系统用户表（支持多态继承）"""
    __tablename__ = 'users'
    
    __table_args__ = (
        db.Index('idx_user_active', 'is_active'),  # 状态过滤优化
        db.Index('idx_user_type', 'type'),  # 多态类型查询优化
    )

    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(
        db.String(255), 
        unique=True,
        nullable=False,
        info={'description': '用户名（唯一标识）'}
    )
    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        info={'description': '已验证邮箱地址'}
    )
    password_hash = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '加密后的密码'}
    )

    avatar = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '角色头像'}
    )
    is_active = db.Column(
        db.Boolean, 
        default=True,
        info={'description': '账户激活状态'}
    )
    created_at = db.Column(
        UTCDateTime, 
        default=db.func.now(),
        info={'description': '注册时间（自动记录）'}
    )
    updated_at = db.Column(
        UTCDateTime, 
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间（自动更新）'}
    )
    refresh_token_expires_at = db.Column(
        UTCDateTime,
        nullable=True,
        info={'description': '刷新令牌过期时间'}
    )
    type = db.Column(
        db.String(50),
        info={'description': '用户类型（用于多态继承）'}
    )

    roles = db.relationship(
        'Role',
        secondary=user_roles,
        backref=db.backref('users', lazy='dynamic'),
        lazy='select',  # 按需加载角色
        info={'description': '关联的角色集合'}
    )

    __mapper_args__ = {
        'polymorphic_identity': 'user',
        'polymorphic_on': type
    }

    
    @property
    def active_roles(self):
        """生效中的角色：停用（is_active=False）的角色既不授予权限，也不算持有该角色。
        roles 关系本身保留全部分配（含停用的），供用户 / 员工管理页显示与编辑。"""
        return [role for role in self.roles if role.is_active is not False]

    def active_permission_names(self) -> set:
        """生效角色的权限名集合（鉴权统一用这个，不要直接遍历 self.roles）"""
        return {p.name for role in self.active_roles for p in role.permissions}

    def to_dict(self):
        return {
            "id": self.id,
            "user_name": self.user_name,
            "avatar": self.avatar,
            "email": self.email,
            # 登录 / 刷新返回的是生效中的角色
            "roles": [role.name for role in self.active_roles],
            "type": self.type,
            "is_active": self.is_active,
        }

    def get_roles(self):
        """返回用户生效中的角色名"""
        return [role.name for role in self.active_roles]

    # 检查是否拥有指定角色（停用的角色不算）
    def has_role(self, role_name):
        return role_name in {role.name for role in self.active_roles}

    def get_permissions(self):
        """返回用户生效角色的权限"""
        return sorted(self.active_permission_names())

    def has_permission(self, permission_name):
        """权限检查（只看生效中的角色）"""
        return permission_name in self.active_permission_names()

    @property
    def company_name(self):
        """获取用户所属公司名称（仅 Staff 类型有值）"""
        if hasattr(self, 'company') and self.company:
            return self.company.name
        return None

    def set_password(self, password):
        """设置用户密码"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """验证密码（未设置密码的账号永远不能用密码登录）"""
        if not self.password_hash or password is None:
            return False
        return check_password_hash(self.password_hash, password)

    
   