from flask_restx import Namespace, fields
from extensions import authorizations
from system.common import generate_input_fields, pagination_parser, create_pagination_model

# -----------------------------
# 定义 User 命名空间
# -----------------------------
api_ns = Namespace('user', description='User related operations', authorizations=authorizations)

# -----------------------------
# 定义登录请求模型
# -----------------------------
login_model = api_ns.model('Login', {
    'account': fields.String(required=True, description='User Name or Email'),
    'password': fields.String(required=True, description='User Password')
})

password_change_model = api_ns.model('PasswordChange', {
    'old_password': fields.String(required=True, description='Old Password'),
    'new_password': fields.String(required=True, description='New Password'),
})

# -----------------------------
# 定义用于响应的简单用户模型（仅包含 id 和 user_name）
# -----------------------------
user_simple_model = api_ns.model('UserSimple', {
    'id': fields.Integer(readOnly=True, description='User ID'),
    'user_name': fields.String(required=True, description='User Name'),
    'avatar': fields.String(description='User Avatar'),
})

# -----------------------------
# 定义完整的 User 输出模型
# -----------------------------
user_model = api_ns.model('User', {
    'id': fields.Integer(readOnly=True, description='User ID'),
    'user_name': fields.String(required=True, description='User Name'),
    'avatar': fields.String(description='User Avatar'),
    'email': fields.String(description='User Email'),
    # 'roles': fields.List(fields.String, description='User Roles'),
    'roles': fields.List(
        fields.String(attribute='name'), 
        attribute='roles', # 从User.roles中提取name属性
        description='User Roles'  
    ),
    'is_active': fields.Boolean(description='User Active Status'),
    'created_at': fields.DateTime(readOnly=True, description='User Created Time'),
    'updated_at': fields.DateTime(readOnly=True, description='User Updated Time'),
    'refresh_token_expires_at': fields.DateTime(description='Refresh Token Expires Time'),
    'type': fields.String(description='User Type'),
})

# -----------------------------
# 生成 User 输入模型：从 User 输出模型复制后删除只读字段，并添加 password 字段
# -----------------------------
user_input_fields = generate_input_fields(user_model)
# 添加输入时需要的额外字段
user_input_fields['password'] = fields.String(required=True, description='User Password')
user_input_model = api_ns.model('UserInput', user_input_fields)

# -----------------------------
# 定义用于输入的 Role 模型：从 Role 输出模型复制后删除只读字段
# -----------------------------
role_model = api_ns.model('Role', {
    'id': fields.Integer(readOnly=True, description='Role ID'),
    'name': fields.String(required=True, description='Role Name'),
    'permissions': fields.List(
        fields.String(attribute='name'), 
        attribute='permissions', # 从User.roles中提取name属性        
        description='Role Permissions'
        ),
    'description': fields.String(description='Role Description'),
    'is_active': fields.Boolean(description='Is the role active?'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
})

role_input_fields = generate_input_fields(role_model)
role_input_model = api_ns.model('RoleInput', role_input_fields)

# -----------------------------
# 定义 Permission 输出模型
# -----------------------------
permission_model = api_ns.model('Permission', {
    'id': fields.Integer(readOnly=True, description='Permission ID'),
    'name': fields.String(required=True, description='Permission Name'),
    'description': fields.String(description='Permission Description'),
    'is_active': fields.Boolean(description='Is the permission active?'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
})

# -----------------------------
# 定义 Permission 输入模型
# -----------------------------
permission_input_fields = generate_input_fields(permission_model)
permission_input_model = api_ns.model('PermissionInput', permission_input_fields)

# -----------------------------
# 定义请求参数解析器（分页相关）
# -----------------------------
pagination_parser = pagination_parser.copy()

# 创建分页模型
user_pagination_model = create_pagination_model(api_ns, user_model)
role_pagination_model = create_pagination_model(api_ns, role_model)
permission_pagination_model = create_pagination_model(api_ns, permission_model)
