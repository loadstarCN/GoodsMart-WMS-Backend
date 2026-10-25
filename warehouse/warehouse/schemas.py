from flask_restx import Namespace, fields, inputs, reqparse
from extensions import authorizations
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.company.schemas import comapny_simple_model as company_model

# -----------------------------
# 定义 Warehouse 命名空间
# -----------------------------
api_ns = Namespace('warehouse', description='Warehouse operations', authorizations=authorizations)

# -----------------------------
# 注册并重用 User 模型到当前 namespace
# -----------------------------
user_model = api_ns.model('User', original_user_model)

def _effective_timezone(warehouse):
    """输出的 timezone：仓库的生效时区（仓库设了的 → WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC）"""
    from warehouse.common.clock import configured_timezone, default_timezone_name
    if isinstance(warehouse, dict):
        name = warehouse.get('timezone_configured', warehouse.get('timezone'))
        return name if isinstance(name, str) and name else default_timezone_name()
    return configured_timezone(warehouse) or default_timezone_name()


# 单据 / 任务里嵌的仓库对象都带生效时区（doc/timezone-utc.md §3），界面按它显示时间
warehouse_timezone_field = fields.String(
    attribute=_effective_timezone, readOnly=True,
    description='Effective IANA time zone of the warehouse (its own, else WMS_DEFAULT_TIMEZONE / WMS_TIMEZONE / '
                'UTC); times are shown and naive input times are read in this zone')

# -----------------------------
# 定义用于响应的简单 Warehouse 模型（仅包含 id 和 name）
# -----------------------------
warehouse_simple_model = api_ns.model('WarehouseSimple', {
    'id': fields.Integer(readOnly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse Name'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
    'timezone': warehouse_timezone_field,
})

# -----------------------------
# 定义 Warehouse 输出模型（完整字段）
# -----------------------------
warehouse_model = api_ns.model('Warehouse', {
    'id': fields.Integer(readOnly=True, description='Warehouse ID'),
    'name': fields.String(required=True, description='Warehouse Name'),
    'address': fields.String(description='Warehouse address in one line; generated in the country format when only '
                                         'the structured fields are given'),
    'zip_code': fields.String(description='Warehouse Zip Code (same as postal_code)'),
    'postal_code': fields.String(attribute='zip_code',
                                 description='Postal code (same field as zip_code; either name is accepted)'),
    # 结构化地址（doc/shipping-flow.md §6.2）
    'admin_area': fields.String(description='State / province / prefecture'),
    'locality': fields.String(description='City / municipality'),
    'address_line1': fields.String(description='Street / block number'),
    'address_line2': fields.String(description='Building / room'),
    'latin': fields.Raw(description='Latin transliteration {name, organization, address_line1, address_line2, '
                                    'locality, admin_area}; address_en stays the one-line Latin address (generated '
                                    'from latin when it is empty)'),
    'phone': fields.String(description='Warehouse Phone'),
    'default_currency': fields.String(description='Default Currency Code (ISO 4217 standard)', default='JPY'),
    # 出口资料（仓库地址 ≠ 公司地址时印在单证的 Ship From 栏）
    'address_en': fields.String(description='Warehouse address in English (export documents)'),
    # 输出不给默认值：库里为空就返回 null（出单证时回退用公司的国家）
    'country_code': fields.String(description='Country (ISO 3166-1 alpha-2); null when not set'),
    'contact_name_en': fields.String(description='Warehouse contact person (English)'),
    # 时区（doc/timezone-utc.md §2）：timezone 是生效值，timezone_configured 是仓库自己设的（可空）
    'timezone': warehouse_timezone_field,
    'timezone_configured': fields.String(attribute='timezone', readOnly=True,
                                         description='Time zone set on the warehouse itself; null = default'),
    'company_id': fields.Integer(required=True, description='ID of the associated company'),
    'manager_id': fields.Integer(description='Manager ID'),
    'is_active': fields.Boolean(description='Is the warehouse active?'),
    'created_by': fields.Integer(description='User ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated timestamp'),
    'company': fields.Nested(company_model, readonly=True, description='Associated company details'),
    'manager': fields.Nested(user_model, readonly=True, description='Manager details'),
    'creator': fields.Nested(user_model, readonly=True, description='Creator details')
})

# -----------------------------
# 生成 Warehouse 输入模型：
# 从输出模型复制字段后删除只读和关联字段
# -----------------------------
warehouse_input_fields = generate_input_fields(warehouse_model)
# 输入：新建时不传 country_code 走列默认值 JP（只影响文档）
warehouse_input_fields['country_code'] = fields.String(
    description='Country (ISO 3166-1 alpha-2); defaults to JP when omitted on create', default='JP')
# 输入的 timezone 是仓库自己的设置：IANA 名（如 Asia/Tokyo）；null / 空串 = 用默认时区；不合法 400 14022
warehouse_input_fields['timezone'] = fields.String(
    description='IANA time zone of the warehouse such as Asia/Tokyo or America/Los_Angeles; null or empty = use the '
                'default (WMS_DEFAULT_TIMEZONE / WMS_TIMEZONE / UTC)')
warehouse_input_model = api_ns.model('WarehouseInput', warehouse_input_fields)

# -----------------------------
# 用于查询的 Warehouse 参数模型
# -----------------------------


# -----------------------------
# 定义请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('company_id', type=int, help='ID of the associated company', location='args')
pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the warehouse active?', location='args')
pagination_parser.add_argument('name', type=str, help='Filter by Warehouse Name', location='args')



# -----------------------------
# 创建分页模型（基于输出模型）
# -----------------------------
pagination_model = create_pagination_model(api_ns, warehouse_model)
