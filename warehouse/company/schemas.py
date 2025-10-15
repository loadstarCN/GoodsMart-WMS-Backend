from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from werkzeug.datastructures import FileStorage
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model

# -----------------------------
# 初始化 Company 命名空间
# -----------------------------
api_ns = Namespace(
    'company',
    description='Company related operations',
    authorizations=authorizations
)

# 注册并重用 User 序列化器
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Company 模型（仅包含 id 和 name,logo）
# -----------------------------
comapny_simple_model = api_ns.model('CompanySimple', {
    'id': fields.Integer(readOnly=True, description='Company ID'),
    'name': fields.String(required=True, description='Company Name'),
    'logo': fields.String(description='Company Logo'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
    'expired_at': fields.DateTime(description='Expiration timestamp')
})

# -----------------------------
# Company 模型定义
# -----------------------------
# 定义 Company 的完整字段字典
company_fields = {
    'id': fields.Integer(readOnly=True, description='Company ID'),
    'name': fields.String(required=True, description='Company Name'),
    'email': fields.String(description='Company Email'),
    'phone': fields.String(description='Company Phone Number'),
    'address': fields.String(description='Company Address'),
    'zip_code': fields.String(description='Postal Code'),
    'logo': fields.String(description='Company Logo'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
    'is_active': fields.Boolean(description='Is the company active?'),
    'created_by': fields.Integer(description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'expired_at': fields.DateTime(description='Expiration timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}

# 输出模型：Company 模型
company_model = api_ns.model('Company', company_fields)

# 生成输入模型：复制后删除不允许用户输入的字段
company_input_fields = generate_input_fields(company_fields)
company_input_model = api_ns.model('CompanyInput', company_input_fields)

# -----------------------------
# 请求参数解析器 & 分页模型
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the company active?', location='args')
pagination_parser.add_argument('expired_at_start', type=inputs.date_from_iso8601, help='Expiration timestamp', location='args')
pagination_parser.add_argument('expired_at_end', type=inputs.date_from_iso8601, help='Expiration timestamp', location='args')

pagination_model = create_pagination_model(api_ns, company_model)


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
