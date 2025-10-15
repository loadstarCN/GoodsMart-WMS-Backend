from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.recipient.schemas import recipient_model
from warehouse.carrier.schemas import carrier_simple_model as carrier_model
from warehouse.dn.schemas import dn_model
from .models import DeliveryTask

api_ns = Namespace(
    'delivery',
    description='Delivery related operations',
    authorizations=authorizations
)

# ------------------------------------------------------------------------------
# 1. 复用 User 模型
# ------------------------------------------------------------------------------
user_model = api_ns.model('User', original_user_model)

# ------------------------------------------------------------------------------
# 2. Delivery Task 状态日志模型
# ------------------------------------------------------------------------------
delivery_task_status_log_model = api_ns.model('DeliveryTaskStatusLog', {
    'id': fields.Integer(readOnly=True, description='Status Log ID'),
    'task_id': fields.Integer(required=True, description='Associated delivery task ID'),
    'old_status': fields.String(
        description='Old status',
        enum=DeliveryTask.DELIVERY_TASK_STATUSES
    ),
    'new_status': fields.String(
        description='New status',
        enum=DeliveryTask.DELIVERY_TASK_STATUSES
    ),
    'operator_id': fields.Integer(description='Operator ID'),
    'changed_at': fields.DateTime(description='Change time'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details')
})

# ------------------------------------------------------------------------------
# 3. Delivery Task 基础模型 (Base Model)
# ------------------------------------------------------------------------------
delivery_task_base_fields = {
    'id': fields.Integer(readOnly=True, description='Delivery Task ID'),
    'dn_id': fields.Integer(required=True, description='ID of the associated Delivery Note'),
    'recipient_id': fields.Integer(required=True, description='ID of the associated recipient'),
    'recipient_name': fields.String(attribute='recipient.name', readOnly=True, description='Recipient name'),
    'shipping_address': fields.String(required=True, description='Shipping address'),
    'expected_shipping_date': fields.Date(required=True, description='Expected shipping date'),
    'actual_shipping_date': fields.Date(description='Actual shipping date'),
    'transportation_mode': fields.String(
        description='Transportation mode used for delivery',
        enum=DeliveryTask.DELIVERY_TASK_TRANSPORTATION_MODES
    ),
    'carrier_id': fields.Integer(description='Carrier ID'),
    'carrier_name': fields.String(attribute='carrier.name', readOnly=True, description='Carrier name'),
    'tracking_number': fields.String(description='Tracking number'),
    'shipping_cost': fields.Float(description='Shipping cost'),
    'currency': fields.String(description='Currency code (ISO 4217 standard)', default='JPY'),
    'order_number': fields.String(description='Customer order number'),
    'status': fields.String(
        enum=DeliveryTask.DELIVERY_TASK_STATUSES,
        description='Delivery task status',
        example='pending'
    ),
    'remark': fields.String(description='Delivery remarks'),
    'is_active': fields.Boolean(description='Is the delivery active?'),
    'created_by': fields.Integer(description='Creator ID'),
    'created_at': fields.DateTime(readonly=True,description='Creation time'),
    'updated_at': fields.DateTime(readonly=True,description='Last update time'),
    'started_at': fields.DateTime(description='Delivery start time'),
    'completed_at': fields.DateTime(description='Delivery completion time'),
    'signed_at': fields.DateTime(description='Delivery sign time (if applicable)'),
    'creator': fields.Nested(user_model,readonly=True, description='Details of the creator'),
}
delivery_task_base_model = api_ns.model('DeliveryTaskBase', delivery_task_base_fields)

# ------------------------------------------------------------------------------
# 4. Delivery Task 主模型 - 继承基础模型并增加关联
# ------------------------------------------------------------------------------
delivery_task_model = api_ns.inherit('DeliveryTask', delivery_task_base_model, {
    'dn': fields.Nested(dn_model, readonly=True, description='Details of the associated Delivery Note'),
    'recipient': fields.Nested(recipient_model,readonly=True, description='Details of the associated recipient'),
    'carrier': fields.Nested(carrier_model,readonly=True, description='Details of the carrier'),
    'status_logs': fields.List(fields.Nested(delivery_task_status_log_model),readonly=True,description='Status change logs')
})

# ------------------------------------------------------------------------------
# 5. Delivery 输入模型 (POST/PUT)
#    通过复制基础字段字典，并删除只读字段生成输入模型
# ------------------------------------------------------------------------------
delivery_task_input_fields = generate_input_fields(delivery_task_base_fields)
delivery_task_input_model = api_ns.model('DeliveryTaskInput', delivery_task_input_fields)

delivery_task_complete_input_model = api_ns.model('DeliveryTaskCompleteInput', {
    'id': fields.Integer(readOnly=True, description='Delivery Task ID'),
    'tracking_number': fields.String(required=True, description='Tracking number'),
    'shipping_cost': fields.Float(required=True, description='Shipping cost'),
    'currency': fields.String(description='Currency code (ISO 4217 standard)', default='JPY'),
    'carrier_id': fields.Integer(description='Carrier ID'),
    'transportation_mode': fields.String(
        description='Transportation mode used for delivery',
        enum=DeliveryTask.DELIVERY_TASK_TRANSPORTATION_MODES
    ),
    'remark': fields.String(description='Delivery remarks'),
})

delivery_task_signed_input_model = api_ns.model('DeliveryTaskSignedInput', {
    'id': fields.Integer(readOnly=True, description='Delivery Task ID'),
    'signed_at': fields.DateTime(required=True, description='Sign time'),
})


# ------------------------------------------------------------------------------
# 6. 分页解析器: 添加 Delivery 专属过滤项
# ------------------------------------------------------------------------------
delivery_pagination_parser = pagination_parser.copy()
delivery_pagination_parser.add_argument('dn_id', type=int, location='args', help='ID of associated DN')
delivery_pagination_parser.add_argument('recipient_id', type=int, location='args', help='ID of recipient')
delivery_pagination_parser.add_argument('carrier_id', type=int, location='args', help='Carrier ID')
delivery_pagination_parser.add_argument('transportation_mode', type=str, location='args',choices=DeliveryTask.DELIVERY_TASK_TRANSPORTATION_MODES,help='Filter by transportation mode')
delivery_pagination_parser.add_argument('status', type=str, location='args',choices=DeliveryTask.DELIVERY_TASK_STATUSES,help='Filter by delivery status')
delivery_pagination_parser.add_argument('expected_shipping_date', type=inputs.date_from_iso8601,location='args', help='Expected shipping date')
delivery_pagination_parser.add_argument('actual_shipping_date', type=inputs.date_from_iso8601,location='args', help='Actual shipping date')
delivery_pagination_parser.add_argument('tracking_number', type=str,location='args', help='Tracking number')
delivery_pagination_parser.add_argument('order_number', type=str,location='args', help='Customer order number')
delivery_pagination_parser.add_argument('shipping_address', type=str,location='args', help='Shipping address')
delivery_pagination_parser.add_argument('is_active',type=inputs.boolean,help='Is the task active?',location='args')
delivery_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
delivery_pagination_parser.add_argument('keyword', type=str, help='Keyword search')

# ------------------------------------------------------------------------------
# 7. 分页模型: 使用基础模型构建
# ------------------------------------------------------------------------------
delivery_pagination_model = create_pagination_model(api_ns, delivery_task_base_model)
# ------------------------------------------------------------------------------
delivery_monthly_stats_parser =  reqparse.RequestParser()
delivery_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
delivery_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
