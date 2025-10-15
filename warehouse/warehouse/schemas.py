from flask_restx import Namespace, fields, inputs, reqparse
from extensions import authorizations
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model

# -----------------------------
# 定义 Warehouse 命名空间
# -----------------------------
api_ns = Namespace('warehouse', description='Warehouse operations', authorizations=authorizations)

# -----------------------------
# 注册并重用 User 模型到当前 namespace
# -----------------------------
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Warehouse 模型（仅包含 id 和 name）
# -----------------------------
warehouse_simple_model = api_ns.model('WarehouseSimple', {
    'id': fields.Integer(readOnly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse Name'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
})

# -----------------------------
# 定义 Warehouse 输出模型（完整字段）
# -----------------------------
warehouse_model = api_ns.model('Warehouse', {
    'id': fields.Integer(readOnly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse Name'),
    'address': fields.String(description='Warehouse Address'),
    'zip_code': fields.String(description='Warehouse Zip Code'),
    'phone': fields.String(description='Warehouse Phone'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'manager_id': fields.Integer(description='Manager ID'),
    'is_active': fields.Boolean(description='Is the warehouse active?'),
    'created_by': fields.Integer(description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'manager': fields.Nested(user_model, readonly=True, description='Manager details'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
})

# -----------------------------
# 生成 Warehouse 输入模型：
# 从输出模型复制字段后删除只读和关联字段
# -----------------------------
warehouse_input_fields = generate_input_fields(warehouse_model)
warehouse_input_model = api_ns.model('WarehouseInput', warehouse_input_fields)

# -----------------------------
# 用于查询的 Warehouse 参数模型
# -----------------------------


# -----------------------------
# 定义请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('company_id', type=int, help='ID of the associated company', location='args')
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the warehouse active?', location='args')
pagination_parser.add_argument('name', type=str, help='Filter by Warehouse Name', location='args')



# -----------------------------
# 创建分页模型（基于输出模型）
# -----------------------------
pagination_model = create_pagination_model(api_ns, warehouse_model)
