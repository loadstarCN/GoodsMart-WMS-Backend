from flask_restx import Namespace, fields, inputs
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model
from .methods import available_methods

# 初始化 Namespace
api_ns = Namespace('carrier', description='Carrier operations')

def _service_options(carrier):
    """承运商声明的运单服务选项（与 DN 的 shipping_options 校验同一口径，warehouse/shipping/options.py）"""
    from warehouse.shipping.options import schema_for
    return schema_for(carrier)


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
                                    description='Waybills: api (created by the integration) / file (CSV exported in '
                                                "the carrier software's format, tracking numbers imported back; needs "
                                                'file_preset or file_template) / manual (tracking number entered or '
                                                'scanned)'),
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
    # 文件方式（doc/shipping-flow.md §6.1；校验见 warehouse/carrier/file_settings.py，不合法 400 16135）
    'file_preset': fields.String(description='File preset code (GET /warehouse/carrier/file-presets), e.g. '
                                             'jp.yamato_b2cloud; null = none'),
    'file_template': fields.Raw(description='File template changes merged over the preset (objects merged key by key, '
                                            'lists replaced, null removes a key); the whole template without a preset; '
                                            'null = the preset as is'),
    'file_account': fields.Raw(description='Account values declared by the template account_fields {key: text}, '
                                           'e.g. the Yamato billing customer code'),
    'file_mode': fields.Raw(attribute='file_mode', readonly=True,
                            description='File mode summary: {configured, ready, preset: {code, name, verified, exists} '
                                        '| null, error, service_options, account_fields, missing_account_fields, '
                                        'can_import, tracking_url_template, tracking_url_fallback, encoding}'),
    'service_options': fields.Raw(attribute=lambda c: _service_options(c), readonly=True,
                                  description='Shipping service options declared for this carrier (what DN '
                                              'shipping_options are checked against: the file template for '
                                              'waybill_method file, else the integration plugin): [{key, kind: '
                                              'select | date | money | text, label, values?, default?, help?, '
                                              'max_length?}]'),
    'available_methods': fields.Raw(attribute=lambda c: available_methods(c), readonly=True,
                                    description='Methods that can be chosen now: {waybill: [...], pickup: [...], '
                                                'tracking: [...]}; api only with an enabled integration whose plugin '
                                                'supports it; file only with a file preset / template'),
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
