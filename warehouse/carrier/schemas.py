from flask_restx import Namespace, fields, inputs
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model

# 初始化 Namespace
api_ns = Namespace('carrier', description='Carrier operations')

# 定义并重用 User 序列化器
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Carrier 模型（仅包含 id 和 name）
# -----------------------------
carrier_simple_model = api_ns.model('CarrierSimple', {
    'id': fields.Integer(readOnly=True, description='Carrier ID'),
    'name': fields.String(required=True, description='Carrier Name'),
})


# -----------------------------
# 定义 Carrier 的完整字段字典
# -----------------------------
carrier_fields = {
    'id': fields.Integer(readOnly=True, description='Carrier ID'),
    'name': fields.String(required=True, description='Carrier Name'),
    'address': fields.String(description='Carrier Address'),
    'zip_code': fields.String(description='Carrier Zip Code'),
    'phone': fields.String(description='Carrier Phone'),
    'email': fields.String(description='Carrier Email'),
    'contact': fields.String(description='Contact Person'),
    'is_active': fields.Boolean(description='Is the carrier active?'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}

# 输出模型：Carrier 模型
carrier_model = api_ns.model('Carrier', carrier_fields)

# 生成输入模型：复制后删除不允许用户输入的字段
carrier_input_fields = generate_input_fields(carrier_fields)
carrier_input_model = api_ns.model('CarrierInput', carrier_input_fields)

# -----------------------------
# 请求参数解析器与分页模型
# -----------------------------
carrier_pagination_parser = pagination_parser.copy()
carrier_pagination_parser.add_argument('name', type=str, help='Filter by Carrier Name')
carrier_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status')
carrier_pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID')

pagination_model = create_pagination_model(api_ns, carrier_model)
