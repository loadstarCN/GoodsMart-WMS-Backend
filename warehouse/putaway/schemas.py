from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.location.schemas import location_simple_model as location_model


# -----------------------------
# 创建 Putaway 命名空间
# -----------------------------
api_ns = Namespace('putaway', description='Putaway Record related operations', authorizations=authorizations)
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义基础输出模型：PutawayRecordBase
# -----------------------------
putaway_record_base_model = api_ns.model('PutawayRecordBase', {
    'id': fields.Integer(readOnly=True, description='Putaway Record ID'),
    'goods_id': fields.Integer(required=True, description='ID of the Goods being put away'),
    'goods_code': fields.String(attribute = 'goods.code',readOnly=True, description='Code of the Goods being put away'),
    'location_id': fields.Integer(required=True, description='ID of the Location where goods are put away'),
    'location_code': fields.String(attribute = 'location.code',readOnly=True, description='Code of the Location where goods are put away'),
    'quantity': fields.Integer(required=True, description='Quantity of goods put away'),
    'operator_id': fields.Integer(required=True, description='ID of the operator performing the putaway'),
    'operator_name': fields.String(attribute ='operator.user_name',readOnly=True, description='Name of the operator performing the putaway'),
    'putaway_time': fields.DateTime(readOnly=True, description='Timestamp of the putaway operation'),
    'remark': fields.String(description='Additional remark for the putaway record'),
})

# -----------------------------
# 输出模型扩展：添加关联字段，生成 PutawayRecord
# -----------------------------
putaway_record_model = api_ns.inherit('PutawayRecord', putaway_record_base_model, {
    'goods': fields.Nested(goods_model, readonly=True, description='Details of the associated goods'),
    'location': fields.Nested(location_model, readonly=True, description='Details of the putaway location'),
    'operator': fields.Nested(user_model, readonly=True, description='Details of the operator'),
})

# -----------------------------
# 生成输入模型：从基础模型复制后删除只读字段，生成 PutawayRecordInput
# -----------------------------
putaway_record_input_fields = generate_input_fields(putaway_record_base_model)
putaway_record_input_model = api_ns.model('PutawayRecordInput', putaway_record_input_fields)

# -----------------------------
# 定义请求参数解析器 & 分页模型
# -----------------------------
putaway_pagination_parser = pagination_parser.copy()
putaway_pagination_parser.add_argument('goods_id', type=int, help='ID of the Goods being put away', location='args')
putaway_pagination_parser.add_argument('location_id', type=int, help='ID of the Location where goods are put away', location='args')
putaway_pagination_parser.add_argument('operator_id', type=int, help='ID of the operator performing the putaway', location='args')
putaway_pagination_parser.add_argument('start_time', type=inputs.date_from_iso8601, help='Start time of the putaway operation', location='args')
putaway_pagination_parser.add_argument('end_time', type=inputs.date_from_iso8601, help='End time of the putaway operation', location='args')
putaway_pagination_parser.add_argument('location_code', type=str, help='Code of the Location where goods are put away', location='args')
putaway_pagination_parser.add_argument('goods_code', type=str, help='Code of the Goods being put away', location='args')
putaway_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in goods name, location code, goods code', location='args')
putaway_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID', location='args')

putaway_pagination_model = create_pagination_model(api_ns, putaway_record_model)
