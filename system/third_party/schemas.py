from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model

# 创建一个命名空间，用于第三方 API Key 操作
api_ns = Namespace('third_party', description='User related operations', authorizations=authorizations)

# -----------------------------
# 创建 / 更新 API Key 的请求模型
# -----------------------------
api_key_create_model = api_ns.model('APIKeyCreate', {
    'user_id': fields.Integer(description='User ID'),
    'system_name': fields.String(required=True, description='Third-party system name'),
    'permissions': fields.List(fields.String, description='Permission names'),
    'company_id': fields.Integer(description='Company ID (super admin only; others are forced to their own company)'),
    'is_active': fields.Boolean(description='Whether the API Key is active'),
    'webhook_url': fields.String(description='Webhook callback URL (https only in production)'),
    'webhook_secret': fields.String(description='Webhook HMAC-SHA256 signing secret (write-only; empty keeps current)'),
})

# -----------------------------
# API Key 的响应模型（不含明文 key 与 webhook_secret）
# -----------------------------
api_key_model = api_ns.model('APIKey', {
    'id': fields.Integer(readonly=True, description='API Key ID'),
    'key_prefix': fields.String(readonly=True, description='First 8 characters of the key'),
    'system_name': fields.String(description='Third-party system name'),
    'is_active': fields.Boolean(description='Whether the API Key is active'),
    'permissions': fields.Raw(description='Permissions'),
    'user_id': fields.Integer(description='User ID'),
    'company_id': fields.Integer(description='Company ID'),
    'webhook_url': fields.String(description='Webhook callback URL'),
    'has_webhook_secret': fields.Boolean(readonly=True, description='Whether a webhook secret is configured'),
})

# 创建响应：额外返回一次明文 key
api_key_created_model = api_ns.inherit('APIKeyCreated', api_key_model, {
    'key': fields.String(attribute='plain_key', readonly=True, description='Plain API key — shown only once'),
})

# -----------------------------
# 定义分页解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('company_id', type=int, location='args', help='Filter by company ID')
pagination_parser.add_argument('is_active', type=inputs.boolean, location='args', help='Filter by active status')
pagination_parser.add_argument('system_name', type=str, location='args', help='Filter by system name')
pagination_parser.add_argument('keyword', type=str, location='args', help='Search in system name / key prefix')

# -----------------------------
# 创建分页模型
# -----------------------------
api_key_pagination_model = create_pagination_model(api_ns, api_key_model)
