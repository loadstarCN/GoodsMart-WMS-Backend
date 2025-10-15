from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from warehouse.warehouse.schemas import warehouse_simple_model as warehouse_model
from system.user.schemas import user_simple_model as original_user_model
from .models import Location

# 初始化 Namespace
api_ns = Namespace(
    'location',
    description='Location related operations',
    authorizations=authorizations
)

# 注册并重用 User 模型
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义 Location 的完整字段字典
# -----------------------------
location_simple_fields = {
    'id': fields.Integer(readOnly=True, description='Location ID'),    
    'code': fields.String(required=True, description='Location Code'),
    'location_type': fields.String(
        required=True,
        description='Location type (e.g., standard, damaged, return)',
        enum=Location.LOCATION_TYPES
    ),
    'warehouse_id': fields.Integer(required=True, description='Associated Warehouse ID'),
    'warehouse_name': fields.String(attribute='warehouse.name', readOnly=True, description='Warehouse Name'),
    'is_active': fields.Boolean(description='Is the location active?')
}

location_fields = dict(location_simple_fields)
location_fields.update({
    'description': fields.String(description='Location Description'),
    'width': fields.Float(description='Width in meters'),
    'depth': fields.Float(description='Depth in meters'),
    'height': fields.Float(description='Height in meters'),
    'capacity': fields.Float(description='Maximum weight capacity in kilograms'),    
    'created_by': fields.Integer(description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
})

# -----------------------------
# 定义 Location 输出模型（简单模型）
# -----------------------------
location_simple_model = api_ns.model('LocationSimple', location_simple_fields)

# -----------------------------
# 定义 Location 输出模型（基础模型）
# -----------------------------
location_base_model = api_ns.model('LocationBase', location_fields)

# -----------------------------
# 生成 Location 输入模型：复制字段后删除不允许用户输入的字段
# -----------------------------
location_input_fields = generate_input_fields(location_fields)
location_input_model = api_ns.model('LocationInput', location_input_fields)

# -----------------------------
# 扩展 Location 输出模型，添加关联仓库信息
# -----------------------------
location_model = api_ns.inherit('LocationModel', location_base_model, {
    'warehouse': fields.Nested(warehouse_model, readOnly=True, description='Associated Warehouse details')
})

# -----------------------------
# 请求参数解析器 & 分页模型
# -----------------------------
location_pagination_parser = pagination_parser.copy()
location_pagination_parser.add_argument('code', type=str, help='Filter by Location Code', location='args')
location_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID', location='args')
location_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status', location='args')
location_pagination_parser.add_argument('location_type', type=str, help='Filter by Location Type', location='args', choices=Location.LOCATION_TYPES)

pagination_model = create_pagination_model(api_ns, location_base_model)
