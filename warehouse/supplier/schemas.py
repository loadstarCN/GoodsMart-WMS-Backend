from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model  # 新增：导入 Company 序列化器

# -----------------------------
# 定义 Supplier 命名空间
# -----------------------------
api_ns = Namespace('supplier', description='Supplier operations', authorizations=authorizations)

# -----------------------------
# 注册 user_model 到当前命名空间
# -----------------------------
user_model = api_ns.model('User', original_user_model)


# -----------------------------
# 定义用于响应的简单 Supplier 模型（仅包含 id 和 name）
# -----------------------------
supplier_simple_model = api_ns.model('SupplierSimple', {
    'id': fields.Integer(readOnly=True, description='Supplier ID'),
    'name': fields.String(required=True, description='Supplier Name'),
})


# -----------------------------
# 定义 Supplier 输出序列化器（完整字段）
# -----------------------------
supplier_fields = {
    'id': fields.Integer(readOnly=True, description='Supplier ID'),
    'name': fields.String(required=True, description='Supplier Name'),
    'address': fields.String(description='Supplier Address'),
    'zip_code': fields.String(description='Supplier Zip Code'),
    'phone': fields.String(description='Supplier Phone'),
    'email': fields.String(description='Supplier Email'),
    'contact': fields.String(description='Contact Person'),
    'is_active': fields.Boolean(description='Is the supplier active?'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}
supplier_model = api_ns.model('Supplier', supplier_fields)

# -----------------------------
# 生成 Supplier 输入序列化器：
# 从输出模型复制后删除只读和关联字段
# -----------------------------
supplier_input_fields = generate_input_fields(supplier_fields)
supplier_input_model = api_ns.model('SupplierInput', supplier_input_fields)

# -----------------------------
# 定义请求参数解析器
# -----------------------------
supplier_pagination_parser = pagination_parser.copy()
supplier_pagination_parser.add_argument('name', type=str, help='Filter by Supplier Name')
supplier_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status')
supplier_pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID')

# -----------------------------
# 创建分页模型
# -----------------------------
pagination_model = create_pagination_model(api_ns, supplier_model)
