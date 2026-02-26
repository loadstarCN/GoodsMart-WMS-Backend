from flask_restx import Namespace, fields, inputs
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.delivery.schemas import delivery_task_model
from warehouse.carrier.schemas import carrier_simple_model as carrier_model

api_ns = Namespace(
    'payment',
    description='Payment related operations',
)

# 注册用户模型
user_model = api_ns.model('User', original_user_model)

# Payment 输出模型
payment_model = api_ns.model('Payment', {
    'id': fields.Integer(readOnly=True, description='Payment ID'),
    'delivery_id': fields.Integer(required=True, description='ID of the associated delivery'),
    'amount': fields.Float(required=True, description='Payment amount'),
    'currency': fields.String(required=True, description='Currency code (ISO 4217 standard)', default='USD'),
    'payment_method': fields.String(
        required=True, 
        description='Payment method', 
        enum=['bank_transfer', 'online_payment', 'cash', 'other']
    ),
    'status': fields.String(
        required=True, 
        description='Payment status', 
        enum=['pending', 'paid', 'canceled', 'failed']
    ),
    'carrier_id': fields.Integer(required=True, description='ID of the associated carrier'),
    'payment_time': fields.DateTime(description='Payment timestamp'),
    'remark': fields.String(description='Additional remark for payment'),
    'is_active': fields.Boolean(description='Is the payment active?', default=True),
    'created_by': fields.Integer(required=True, description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    # 关联关系（仅用于输出）
    'delivery': fields.Nested(delivery_task_model, readonly=True, description='Associated delivery details'),
    'carrier': fields.Nested(carrier_model, readonly=True, description='Associated carrier details'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
})

# -------------------------------------------------------------------
# PaymentInput 模型：从输出模型复制后删除只读及关联字段
# -------------------------------------------------------------------
payment_input_fields = generate_input_fields(payment_model)
payment_input_model = api_ns.model('PaymentInput', payment_input_fields)

# -------------------------------------------------------------------
# PaymentUpdate 模型：从输出模型复制后删除不允许更新的字段
# -------------------------------------------------------------------
payment_update_fields = payment_input_fields.copy()
for key in ['delivery_id', 'amount', 'carrier_id', 'created_by', 'status']:
    payment_update_fields.pop(key, None)
payment_update_model = api_ns.model('PaymentUpdate', payment_update_fields)

# -------------------------------------------------------------------
# 分页解析器 & 分页模型
# -------------------------------------------------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('delivery_id', type=int, help='ID of the associated delivery', location='args')
pagination_parser.add_argument('carrier_id', type=int, help='ID of the associated carrier', location='args')
pagination_parser.add_argument('status', type=str, help='Payment status', location='args', choices=['pending', 'paid', 'canceled', 'failed'])
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the payment active?', location='args')

pagination_model = create_pagination_model(api_ns, payment_model)
