from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from warehouse.company.schemas import comapny_simple_model as company_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.department.schemas import department_simple_model as department_model
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from warehouse.warehouse.schemas import warehouse_simple_model as warehouse_model
from werkzeug.datastructures import FileStorage

# -----------------------------
# 定义 Staff 命名空间
# -----------------------------
api_ns = Namespace(
    'staff',
    description='Staff related operations',
    authorizations=authorizations
)

# -----------------------------
# 注册并重用 User 模型
# -----------------------------
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Staff 模型（仅包含 id 和 name）
# -----------------------------
staff_simple_model = api_ns.model('StaffSimple', {
    'id': fields.Integer(readOnly=True, description='Staff ID'),
    'user_name': fields.String(required=True, description='Staff Name'),
})

# -----------------------------
# 定义 Staff 输出模型
# -----------------------------
staff_model = api_ns.model('Staff', {
    'id': fields.Integer(readOnly=True, description='Staff ID'),
    'user_name': fields.String(required=True, description='Staff Name'),
    'email': fields.String(required=True, description='User Email'),
    'avatar': fields.String(description='User Avatar'),
    'roles': fields.List(
        fields.String(attribute='name'), 
        attribute='roles', # 从User.roles中提取name属性
        description='User Roles'  
    ),
    'is_active': fields.Boolean(description='User Active Status'),
    'created_at': fields.DateTime(readonly=True, description='User Created Time'),
    'updated_at': fields.DateTime(readonly=True, description='User Updated Time'),
    
    # 扩展属性
    'phone': fields.String(description='Staff Phone Number'),
    'position': fields.String(description='Staff Position'),
    'employee_number': fields.String(description='Employee Number'),
    'openid': fields.String(description='OpenID'),
    'hire_date': fields.Date(description='Hire Date'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'department_id': fields.Integer(description='ID of the associated department'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'department': fields.Nested(department_model, readonly=True, description='Associated department details'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details'),
    'warehouses': fields.List(fields.Nested(warehouse_model, readonly=True, description='Associated warehouse details'))
})

# -----------------------------
# 生成 Staff 输入模型：从输出模型复制后删除只读及关联字段，
# 并补充输入时额外需要的字段（如密码、warehouse_ids）
# -----------------------------
staff_input_fields = generate_input_fields(staff_model)
# 补充输入模型独有字段
staff_input_fields['password'] = fields.String(required=False, description='User Password')
staff_input_fields['warehouse_ids'] = fields.List(fields.Integer, description='List of associated warehouse IDs')
staff_input_model = api_ns.model('StaffInput', staff_input_fields)

# -----------------------------
# 定义请求参数解析器 & 分页模型
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID', location='args')
pagination_parser.add_argument('department_id', type=int, help='Filter by Department ID', location='args')
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status', location='args')
pagination_parser.add_argument('user_name', type=str, help='Filter by User Name', location='args')
pagination_parser.add_argument('email', type=str, help='Filter by User Email', location='args')
pagination_parser.add_argument('phone', type=str, help='Filter by User Phone', location='args')
pagination_parser.add_argument('position', type=str, help='Filter by Staff Position', location='args')
pagination_parser.add_argument('employee_number', type=str, help='Filter by Employee Number', location='args')
pagination_parser.add_argument('openid', type=str, help='Filter by OpenID', location='args')
pagination_parser.add_argument('hire_date', type=inputs.date_from_iso8601, help='Filter by Hire Date', location='args')
pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID', location='args')
pagination_parser.add_argument('keyword', type=str, help='Search by keyword in user_name or email ,phone,position,employee_number', location='args')

pagination_model = create_pagination_model(api_ns, staff_model)



# 创建上传文件的请求解析器
upload_parser = reqparse.RequestParser()
upload_parser.add_argument('Authorization', 
                          location='headers',  # 关键点：从headers提取
                          help='Bearer token required')
upload_parser.add_argument('X-API-KEY',
                          location='headers',  # 关键点：从headers提取
                          help='API Key required')
upload_parser.add_argument('file', 
                          location='files', 
                          type=FileStorage, 
                          required=True)
