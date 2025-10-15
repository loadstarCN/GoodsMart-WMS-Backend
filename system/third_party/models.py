from extensions import db
from sqlalchemy.dialects.postgresql import JSON

class APIKey(db.Model):
    """API密钥管理表
    
    Attributes:
        key: 密钥值 (全局唯一加密存储)
        system_name: 归属系统名称 (业务维度标识)
        is_active: 激活状态 (默认启用)
        permissions: 权限配置 (结构化JSON存储)
        user_id: 关联用户ID (允许空值)
    """
    __tablename__ = 'api_keys'

    __table_args__ = (
        # 复合索引优化
        db.Index('idx_api_key_system', 'key', 'system_name'),  # 高频查询组合
        # 添加外键约束命名（可选优化）
        db.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name='fk_api_keys_user_id',
            ondelete='CASCADE'
        ),
        
    )

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(
        db.String(128), 
        unique=True,
        nullable=False,
        info={'description': 'API密钥（SHA256加密存储）'}
    )
    system_name = db.Column(
        db.String(255), 
        nullable=False,
        info={'description': '归属系统标识（字母数字组合）'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,  # 状态过滤优化
        info={'description': '激活状态（默认启用）'}
    )

    permissions = db.Column(
        JSON,
        default=list,
        info={'description': 'JSON格式权限配置'}
    )
    
    user_id = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='CASCADE'),  # 用户删除级联
        index=True,  # 关联查询优化
        info={'description': '关联用户ID（可空）'}
    )

    # 关系加载策略优化
    user = db.relationship(
        'User', 
        backref=db.backref(
            'api_keys', 
            lazy='dynamic',  # 动态加载防止内存膨胀
            order_by='desc(APIKey.id)'
        ),
        lazy='joined',  # 立即加载用户信息
        info={'description': '关联用户对象'}
    )

    


    def __init__(self, key, system_name, user_id=None, permissions=None):
        self.key = key
        self.system_name = system_name
        self.user_id = user_id
        self.permissions = permissions or []  # 如果未提供，则默认为空字典
    
    def has_permission(self, permission):
        """Check if the specified module has the action permission"""     
        return permission in self.permissions
        
        
        
