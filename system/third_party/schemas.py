from flask_restx import Namespace, fields
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model

# 创建一个命名空间，用于第三方 API Key 操作
api_ns = Namespace('third_party', description='User related operations', authorizations=authorizations)

# -----------------------------
# 创建 API Key 的请求模型（用于创建）
# -----------------------------
api_key_create_model = api_ns.model('APIKeyCreate', {
    'user_id': fields.Integer(required=True, description='User ID'),
    'system_name': fields.String(required=True, description='Third-party system name'),
    'permissions': fields.Raw(description='Permissions')
})

# -----------------------------
# 登录模型
# -----------------------------
api_key_login_model = api_ns.model('APIKeyLogin', {
    'api_key': fields.String(required=True, description='API Key')
})

# -----------------------------
# API Key 的响应模型（输出模型）
# -----------------------------
api_key_model = api_ns.model('APIKey', {
    'id': fields.Integer(readonly=True, description='API Key ID'),
    'key': fields.String(description='API Key'),
    'system_name': fields.String(description='Third-party system name'),
    'is_active': fields.Boolean(description='Whether the API Key is active'),
    'permissions': fields.Raw(description='Permissions'),
    'user_id': fields.Integer(description='User ID')
})

# -----------------------------
# 生成 APIKey 输入模型：
# 从输出模型复制后删除只读或自动生成的字段
# -----------------------------
api_key_input_fields = api_key_model.copy()
for key in ['id', 'key']:
    api_key_input_fields.pop(key, None)
api_key_input_model = api_ns.model('APIKeyInput', api_key_input_fields)

# -----------------------------
# 生成 APIKey 更新模型：
# 从输出模型复制后删除不允许更新的字段（例如：id、key、user_id）
# -----------------------------
api_key_update_fields = api_key_model.copy()
for key in ['id', 'key', 'user_id']:
    api_key_update_fields.pop(key, None)
api_key_update_model = api_ns.model('APIKeyUpdate', api_key_update_fields)

# -----------------------------
# 定义分页解析器
# -----------------------------
pagination_parser = pagination_parser.copy()

# -----------------------------
# 创建分页模型
# -----------------------------
api_key_pagination_model = create_pagination_model(api_ns, api_key_model)
