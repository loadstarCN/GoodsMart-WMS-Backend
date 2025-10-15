from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.location.schemas import location_simple_model as location_model
from system.common import pagination_parser, create_pagination_model,generate_input_fields

# -----------------------------
# 定义 Removal 命名空间
# -----------------------------
api_ns = Namespace(
    'removal',
    description='Removal record related operations',
    authorizations=authorizations
)

# 在当前 namespace 注册 user_model
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 1. 定义 RemovalRecord 的基础输出模型
# -----------------------------
removal_record_base_model = api_ns.model('RemovalRecordBase', {
    'id': fields.Integer(readOnly=True, description='Removal Record ID'),
    'goods_id': fields.Integer(required=True, description='ID of the removed goods'),
    'goods_code': fields.String(attribute='goods.code', readOnly=True, description='Code of the removed goods'),
    'location_id': fields.Integer(required=True, description='ID of the location where goods were removed'),
    'location_code': fields.String(attribute='location.code', readOnly=True, description='Code of the location where goods were removed'),
    'quantity': fields.Integer(required=True, description='Quantity of goods removed'),
    'operator_id': fields.Integer(required=True, description='ID of the operator who performed the removal'),
    'operator_name': fields.String(attribute='operator.user_name', readOnly=True, description='Name of the operator who performed the removal'),
    'removal_time': fields.DateTime(readOnly=True, description='Time of the removal'),
    'reason': fields.String(required=True, description='Reason for the removal (e.g., return, damage, transfer)'),
    'remark': fields.String(description='Additional remarks about the removal operation'),
})
# -----------------------------
# 2. 定义用于序列化输出的 RemovalRecord 模型（继承基础模型）
# -----------------------------
removal_record_model = api_ns.inherit('RemovalRecord', removal_record_base_model, {
    'goods': fields.Nested(goods_model, readonly=True, description='Details of the removed goods'),
    'location': fields.Nested(location_model, readonly=True, description='Details of the location'),
    'operator': fields.Nested(user_model, readonly=True, description='Details of the operator'),
})

# -----------------------------
# 3. 生成用于创建/更新时的输入模型
#    从基础模型复制后删除只读字段
# -----------------------------
removal_record_input_fields = generate_input_fields(removal_record_base_model)
removal_record_input_model = api_ns.model('RemovalRecordInput', removal_record_input_fields)

# -----------------------------
# 4. 定义 Removal 专用的分页 Parser
# -----------------------------
removal_pagination_parser = pagination_parser.copy()
removal_pagination_parser.add_argument('goods_id', type=int, help='ID of the removed goods', location='args')
removal_pagination_parser.add_argument('location_id', type=int, help='ID of the location where goods were removed', location='args')
removal_pagination_parser.add_argument('operator_id', type=int, help='ID of the operator who performed the removal', location='args')
removal_pagination_parser.add_argument('start_time', type=inputs.date_from_iso8601, help='Start time of the removal operation', location='args')
removal_pagination_parser.add_argument('end_time', type=inputs.date_from_iso8601, help='End time of the removal operation', location='args')
removal_pagination_parser.add_argument('location_code', type=str, help='Code of the location where goods were removed', location='args')
removal_pagination_parser.add_argument('goods_code', type=str, help='Code of the removed goods', location='args')
removal_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in goods name, location code, goods code', location='args')
removal_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID', location='args')

# -----------------------------
# 5. 创建分页模型（基于基础输出模型）
# -----------------------------
removal_pagination_model = create_pagination_model(api_ns, removal_record_model)
