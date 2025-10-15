from extensions import db  
from werkzeug.security import generate_password_hash, check_password_hash


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
        db.DateTime, 
        default=db.func.now(),
        info={'description': '注册时间（自动记录）'}
    )
    updated_at = db.Column(
        db.DateTime, 
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间（自动更新）'}
    )
    refresh_token_expires_at = db.Column(
        db.DateTime,
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

    
    def to_dict(self):
        return {
            "id": self.id,
            "user_name": self.user_name,
            "avatar": self.avatar,
            "email": self.email,
            "roles": [role.name for role in self.roles],
            "type": self.type,
            "is_active": self.is_active,
        }

    def get_roles(self):
        """返回用户所有角色"""
        return [role.name for role in self.roles]

    # 检查是否拥有指定角色
    def has_role(self, role_name):
        return role_name in {role.name for role in self.roles}

    def get_permissions(self):
        """返回用户所有角色的权限"""
        return [permission.name for role in self.roles for permission in role.permissions]
    
    def has_permission(self, permission_name):
        """缓存优化版权限检查"""
        return permission_name in {p.name for role in self.roles for p in role.permissions}

    def set_password(self, password):
        """设置用户密码"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)

    
   