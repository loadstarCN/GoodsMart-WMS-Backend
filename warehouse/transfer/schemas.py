from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.location.schemas import location_simple_model as location_model
from system.user.schemas import user_simple_model as original_user_model
from system.common import pagination_parser, create_pagination_model, generate_input_fields

# -----------------------------
# 定义 Transfer 命名空间
# -----------------------------
api_ns = Namespace('transfer', description='Transfer Record related operations', authorizations=authorizations)

# -----------------------------
# 注册并重用模型
# -----------------------------
user_model = api_ns.model('User', original_user_model)
goods_model = api_ns.model('Goods', goods_model)
location_model = api_ns.model('Location', location_model)

# -----------------------------
# 1. 定义 TransferRecord 的基础输出模型
# -----------------------------
transfer_record_base_model = api_ns.model('TransferRecordBase', {
    'id': fields.Integer(readOnly=True, description='Transfer Record ID'),
    'goods_id': fields.Integer(required=True, description='ID of the Goods being transferred'),
    'goods_code': fields.String(attribute = 'goods.code',readOnly=True, description='Code of the Goods being put away'),
    'from_location_id': fields.Integer(required=True, description='ID of the source Location'),
    'from_location_code': fields.String(attribute = 'from_location.code',readOnly=True, description='Code of the source Location'),
    'to_location_id': fields.Integer(required=True, description='ID of the destination Location'),
    'to_location_code': fields.String(attribute = 'to_location.code',readOnly=True, description='Code of the destination Location'),
    'quantity': fields.Integer(required=True, description='Quantity of goods transferred'),
    'operator_id': fields.Integer(required=True, description='ID of the operator performing the transfer'),
    'operator_name': fields.String(attribute ='operator.user_name',readOnly=True, description='Name of the operator performing the transfer'),
    'transfer_time': fields.DateTime(readOnly=True, description='Timestamp of the transfer operation'),
    'remark': fields.String(description='Additional remark for the transfer record'),
})

# -----------------------------
# 2. 定义 TransferRecord 的输出模型（扩展关联信息）
# -----------------------------
transfer_record_model = api_ns.inherit('TransferRecord', transfer_record_base_model, {
    'goods': fields.Nested(goods_model,readOnly=True,  description='Details of the associated goods'),
    'from_location': fields.Nested(location_model, readOnly=True, description='Details of the source location'),
    'to_location': fields.Nested(location_model,readOnly=True,  description='Details of the destination location'),
    'operator': fields.Nested(user_model, readOnly=True, description='Details of the operator'),
})

# -----------------------------
# 3. 生成 TransferRecord 的输入模型
#    通过复制基础模型字段后删除只读字段（id、transfer_time）
# -----------------------------
transfer_record_input_fields = generate_input_fields(transfer_record_base_model)
transfer_record_input_model = api_ns.model('TransferRecordInput', transfer_record_input_fields)

# -----------------------------
# 4. 定义 TransferRecord 分页解析器
# -----------------------------
transfer_pagination_parser = pagination_parser.copy()
transfer_pagination_parser.add_argument('goods_id', type=int, help='ID of the Goods being transferred', location='args')
transfer_pagination_parser.add_argument('from_location_id', type=int, help='ID of the source Location', location='args')
transfer_pagination_parser.add_argument('to_location_id', type=int, help='ID of the destination Location', location='args')
transfer_pagination_parser.add_argument('location_id', type=int, help='ID of the Location', location='args')
transfer_pagination_parser.add_argument('operator_id', type=int, help='ID of the operator performing the transfer', location='args')
transfer_pagination_parser.add_argument('start_time', type=inputs.date_from_iso8601, help='Start time of the transfer operation', location='args')
transfer_pagination_parser.add_argument('end_time', type=inputs.date_from_iso8601, help='End time of the transfer operation', location='args')
transfer_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in goods name, source location code, destination location code', location='args')
transfer_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID', location='args')

# -----------------------------
# 5. 创建分页模型（基于基础输出模型）
# -----------------------------
transfer_pagination_model = create_pagination_model(api_ns, transfer_record_model)
