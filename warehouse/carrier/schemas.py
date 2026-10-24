from flask_restx import Namespace, fields, inputs
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model
from .methods import available_methods

# 初始化 Namespace
api_ns = Namespace('carrier', description='Carrier operations')

# 定义并重用 User 序列化器
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# 定义用于响应的简单 Carrier 模型（仅包含 id 和 name）
# -----------------------------
carrier_simple_model = api_ns.model('CarrierSimple', {
    'id': fields.Integer(readOnly=True, description='Carrier ID'),
    'name': fields.String(required=True, description='Carrier Name'),
    'code': fields.String(description='Carrier Code'),
})


# -----------------------------
# 定义 Carrier 的完整字段字典
# -----------------------------
carrier_fields = {
    'id': fields.Integer(readOnly=True, description='Carrier ID'),
    'name': fields.String(required=True, description='Carrier Name'),
    'code': fields.String(description='Carrier Code (e.g. yamato)'),
    'address': fields.String(description='Carrier Address'),
    'zip_code': fields.String(description='Carrier Zip Code'),
    'phone': fields.String(description='Carrier Phone'),
    'email': fields.String(description='Carrier Email'),
    'contact': fields.String(description='Contact Person'),
    'is_active': fields.Boolean(description='Is the carrier active?'),
    'integration': fields.Raw(attribute='integration_summary', readonly=True,
                              description='Online integration (automatic shipments): {adapter, name, enabled} or null'),
    # 对接方式（doc/shipping-flow.md §2；校验见 warehouse/carrier/methods.py，不合法 400 16135）
    'waybill_method': fields.String(enum=['api', 'file', 'manual'], default='manual',
                                    description='Waybills: api (created by the integration) / manual (tracking number '
                                                'entered or scanned); file is phase 2'),
    'pickup_method': fields.String(enum=['api', 'manual', 'scheduled', 'dropoff', 'none'], default='none',
                                   description='Pickups: api (booked by the integration) / manual (booked by phone or '
                                               'website, recorded in WMS) / scheduled (regular pickup) / dropoff / none'),
    'pickup_schedule': fields.Raw(description='scheduled: {weekdays: [1..7] (ISO, 1 = Monday), ready_time: HH:MM, '
                                              'close_time: HH:MM} or null'),
    'pickup_contact': fields.String(description='Phone / URL / note for booking pickups (shown for manual)'),
    'tracking_method': fields.String(enum=['api', 'link', 'none'], default='link',
                                     description='Tracking: api (queried by the integration; auto ship / sign) / '
                                                 'link (public tracking page only) / none'),
    'tracking_url_template': fields.String(description='Public tracking page with {tracking_number}; empty = the '
                                                       "integration's built-in one"),
    'auto_sign_days': fields.Integer(description='Sign automatically this many days (1-90) after handover when the '
                                                 'shipment is not tracked through the API; null = off'),
    'available_methods': fields.Raw(attribute=lambda c: available_methods(c), readonly=True,
                                    description='Methods that can be chosen now: {waybill: [...], pickup: [...], '
                                                'tracking: [...]}; api only with an enabled integration whose plugin '
                                                'supports it'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
}

# 输出模型：Carrier 模型
carrier_model = api_ns.model('Carrier', carrier_fields)

# 生成输入模型：复制后删除不允许用户输入的字段
carrier_input_fields = generate_input_fields(carrier_fields)
carrier_input_model = api_ns.model('CarrierInput', carrier_input_fields)

# -----------------------------
# 请求参数解析器与分页模型
# -----------------------------
carrier_pagination_parser = pagination_parser.copy()
carrier_pagination_parser.add_argument('name', type=str, help='Filter by Carrier Name')
carrier_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by Active Status')
carrier_pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID')

pagination_model = create_pagination_model(api_ns, carrier_model)


# -----------------------------
# 在线对接（自动建运单）设置
# -----------------------------
carrier_integration_input_model = api_ns.model('CarrierIntegrationInput', {
    'adapter': fields.String(description='Integration code (GET /carrier/integrations); empty or null removes the '
                                         'integration'),
    'enabled': fields.Boolean(description='false: no new shipments (existing ones can still be cancelled)'),
    'settings': fields.Raw(description='Settings except keys: {field key: value} (see the integration fields)'),
    'secrets': fields.Raw(description='Keys: {field key: value}; omitted or empty keeps the saved value. '
                                      'Saved keys are never returned'),
})
