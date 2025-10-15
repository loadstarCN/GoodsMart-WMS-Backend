from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model

# -----------------------------
# 初始化 Recipient 命名空间
# -----------------------------
api_ns = Namespace('recipient', description='Recipient operations', authorizations=authorizations)

# -----------------------------
# 注册并重用 User 模型
# -----------------------------
user_model = api_ns.model('User', original_user_model)

recipient_base_model = api_ns.model('RecipientBase', {
    'id': fields.Integer(readOnly=True, description='Recipient ID'),
    'name': fields.String(required=True, description='Recipient Name'),
})

# -----------------------------
# 定义 Recipient 输出模型（完整字段）
# -----------------------------
recipient_fields = {
    'id': fields.Integer(readOnly=True, description='Recipient ID'),
    'name': fields.String(required=True, description='Recipient Name'),
    'address': fields.String(description='Recipient Address'),
    'zip_code': fields.String(description='Recipient Zip Code'),
    'phone': fields.String(description='Recipient Phone'),
    'email': fields.String(description='Recipient Email'),
    'contact': fields.String(description='Contact Person'),
    'country': fields.String(required=True, description='Country Code (e.g., cn, jp)'),
    'is_active': fields.Boolean(description='Is the recipient active?'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}
recipient_model = api_ns.model('Recipient', recipient_fields)

# -----------------------------
# 生成 Recipient 输入模型：从输出模型复制后删除只读及关联字段
# -----------------------------
recipient_input_fields = generate_input_fields(recipient_fields)
recipient_input_model = api_ns.model('RecipientInput', recipient_input_fields)

# -----------------------------
# 定义 Recipient 请求参数解析器 & 分页模型
# -----------------------------
recipient_pagination_parser = pagination_parser.copy()
recipient_pagination_parser.add_argument('name', type=str, help='Filter by Recipient Name', location='args')
recipient_pagination_parser.add_argument('address', type=str, help='Filter by Recipient Address', location='args')
recipient_pagination_parser.add_argument('zip_code', type=str, help='Filter by Recipient Zip Code', location='args')
recipient_pagination_parser.add_argument('phone', type=str, help='Filter by Recipient Phone', location='args')
recipient_pagination_parser.add_argument('email', type=str, help='Filter by Recipient Email', location='args')
recipient_pagination_parser.add_argument('contact', type=str, help='Filter by Contact Person', location='args')
recipient_pagination_parser.add_argument('country', type=str, help='Filter by Country Code', location='args')
recipient_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status', location='args')
recipient_pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID', location='args')

pagination_model = create_pagination_model(api_ns, recipient_model)
