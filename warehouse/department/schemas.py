from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model

# -----------------------------
# 初始化 Department 命名空间
# -----------------------------
api_ns = Namespace(
    'department',
    description='Department related operations',
    authorizations=authorizations
)

# 注册并重用 User 模型
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Department 模型（仅包含 id 和 name）
# -----------------------------
department_simple_model = api_ns.model('DepartmentSimple', {
    'id': fields.Integer(readOnly=True, description='Department ID'),
    'name': fields.String(required=True, description='Department Name'),
})


# -----------------------------
# Department 模型定义
# -----------------------------
# 定义 Department 的完整字段字典
department_fields = {
    'id': fields.Integer(readOnly=True, description='Department ID'),
    'name': fields.String(required=True, description='Department Name'),
    'description': fields.String(description='Department Description'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'is_active': fields.Boolean(description='Is the department active?'),
    'created_by': fields.Integer(description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}

# 输出模型
department_model = api_ns.model('Department', department_fields)

# 生成输入模型：复制后删除只读及关联字段
department_input_fields = generate_input_fields(department_fields)
department_input_model = api_ns.model('DepartmentInput', department_input_fields)

# -----------------------------
# 请求参数解析器 & 分页模型
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('company_id', type=int, help='ID of the associated company', location='args')
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the department active?', location='args')
pagination_parser.add_argument('name', type=str, help='Name of the department', location='args')

pagination_model = create_pagination_model(api_ns, department_model)
