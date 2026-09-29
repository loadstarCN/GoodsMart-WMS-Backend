from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.carrier.schemas import carrier_simple_model,carrier_model
from warehouse.recipient.schemas import recipient_base_model,recipient_model
from warehouse.warehouse.schemas import warehouse_simple_model,warehouse_model
from .models import DN  

# 定义 DN 命名空间（类似于 ASN 的 api_ns）
api_ns = Namespace('dn', description='Delivery Note (DN) related operations', authorizations=authorizations)

# 在当前 namespace 注册 user_model
user_model = api_ns.model('User', original_user_model)

# --------------------------------------------
# DNDetail 序列化 / 反序列化
# --------------------------------------------

# 1) DNDetail 序列化模型
dn_detail_fields = {
    'id': fields.Integer(readOnly=True, description='DN Detail ID'),
    'dn_id': fields.Integer(required=True, description='Associated DN ID'),
    'goods_id': fields.Integer(required=True, description='Associated Goods ID'),
    'goods_code': fields.String(description='Goods code (alternative to goods_id)'),
    'quantity': fields.Integer(description='Planned delivery quantity'),
    'picked_quantity': fields.Integer(description='Quantity that has been picked'),
    'packed_quantity': fields.Integer(description='Quantity that has been packed'),
    'delivered_quantity': fields.Integer(description='Quantity that has been delivered'),
    'remark': fields.String(description='remark for the DN detail'),

    # 只读字段（Nested）
    'goods': fields.Nested(goods_model, readOnly=True, description='Details of the associated goods'),
}

dn_detail_model = api_ns.model('DNDetail', dn_detail_fields)

# --------------------------------------------
# 海外件：报关快照 / 箱子 / 出口单证
# --------------------------------------------

dn_package_model = api_ns.model('DNPackage', {
    'package_no': fields.Integer(description='Package number (1..99, consecutive)'),
    'gross_weight_kg': fields.Float(description='Gross weight in kg (0.01 - 999.999, 3 decimals)'),
    'length_mm': fields.Integer(description='Length in mm (1 - 3000)'),
    'width_mm': fields.Integer(description='Width in mm (1 - 3000)'),
    'height_mm': fields.Integer(description='Height in mm (1 - 3000)'),
    'remark': fields.String(description='Remark'),
})

dn_packages_input_model = api_ns.model('DNPackagesInput', {
    'packages': fields.List(fields.Nested(dn_package_model), required=True,
                            description='All packages of the DN (full replacement, 1 - 99 items)'),
})

dn_packages_result_model = api_ns.model('DNPackagesResult', {
    'packages': fields.List(fields.Nested(dn_package_model)),
    'voided_documents': fields.List(fields.Integer, description='IDs of customs documents voided by this change'),
})

dn_document_meta_model = api_ns.model('DNCustomsDocument', {
    'id': fields.Integer(readOnly=True),
    'dn_id': fields.Integer(readOnly=True),
    'doc_type': fields.String(enum=['commercial_invoice', 'packing_list']),
    'version': fields.Integer(),
    'document_number': fields.String(),
    'invoice_date': fields.String(
        attribute=lambda x: x.invoice_date.isoformat() if getattr(x, 'invoice_date', None) else None,
        description='YYYY-MM-DD (DOCUMENT_TIMEZONE)'),
    'issued_at': fields.DateTime(),
    'issued_by': fields.Integer(description='User ID of the issuer'),
    'status': fields.String(enum=['issued', 'void']),
    'sha256': fields.String(description='SHA-256 of the PDF file'),
    'size_bytes': fields.Integer(),
    'file_name': fields.String(),
    'voided_at': fields.DateTime(),
    'void_reason': fields.String(description='packages_changed / customs_changed / data_changed'),
})

dn_customs_consignee_model = api_ns.model('DNCustomsConsignee', {
    'name': fields.String(), 'company': fields.String(),
    'address_line1': fields.String(), 'address_line2': fields.String(),
    'city': fields.String(), 'state': fields.String(), 'postal_code': fields.String(),
    'country': fields.String(description='ISO 3166-1 alpha-2'), 'phone': fields.String(),
})

dn_customs_line_model = api_ns.model('DNCustomsLine', {
    'goods_code': fields.String(required=True, description='Must match a goods code in the DN details'),
    'quantity': fields.Integer(description='Ordered quantity (recorded only; invoice uses packed quantity)'),
    'unit_value': fields.Float(description='Unit value in the invoice currency'),
    'total_value': fields.Float(description='Recorded only'),
    'description_en': fields.String(description='English description (ASCII)'),
    'hs_code': fields.String(description='HS code, 6 - 10 digits'),
    'jp_export_code': fields.String(
        description='Optional 9-digit Japanese export statistics code (first 6 digits = HS); not printed'),
    'origin_country': fields.String(description='Recorded only; the goods master data is authoritative'),
    'quantity_unit': fields.String(default='PCS'),
})

dn_customs_input_model = api_ns.model('DNCustomsInput', {
    'invoice_number': fields.String(description='Defaults to the DN order_number'),
    'currency': fields.String(example='JPY'),
    'incoterm': fields.String(example='DAP'),
    'export_reason': fields.String(example='SALE'),
    'recipient_country': fields.String(description='ISO 3166-1 alpha-2; falls back to consignee.country'),
    'recipient_tax_id': fields.String(),
    'recipient_tax_id_type': fields.String(
        description='EORI / PCCC / KR_BRN / CPF / CNPJ / EIN / USCC / TW_UBN / VAT / OTHER'),
    'freight_charge': fields.Integer(description='Freight printed on the invoice and added to the total'),
    'consignee': fields.Nested(dn_customs_consignee_model),
    'lines': fields.List(fields.Nested(dn_customs_line_model)),
})

# 2) DNDetail 输入模型
dn_detail_input_fields = generate_input_fields(dn_detail_fields)
dn_detail_input_model = api_ns.model('DNDetailInput', dn_detail_input_fields)

# --------------------------------------------
# DN 序列化 / 反序列化
# --------------------------------------------

# 3) DN 基础模型 (Base) - 用于序列化基本字段
dn_fields = {
    'id': fields.Integer(readOnly=True, description='DN ID'),
    'recipient_id': fields.Integer(required=True, description='ID of the associated recipient'),
    'shipping_address': fields.String(required=True, description='Shipping address for the DN'),
    'expected_shipping_date': fields.Date(required=True, description='Expected shipping date'),
    'carrier_id': fields.Integer(description='ID of the carrier company'),
    'dn_type': fields.String(description='Type of DN', enum=DN.DN_TYPES),
    'status': fields.String(description='Status of the DN', enum=DN.DN_STATUSES),
    'order_number': fields.String(description='Associated customer order number'),
    'transportation_mode': fields.String(description='Transportation mode', enum=DN.DN_TRANSPORTATION_MODES),
    'packaging_info': fields.String(description='Packaging information'),
    'special_handling': fields.String(description='Special handling requirements'),
    'remark': fields.String(description='Additional remarks about the DN'),
    'is_active': fields.Boolean(description='Is the DN active?'),

    # 只读字段
    'created_by': fields.Integer(readOnly=True, description='ID of the creator'),
    'created_at': fields.DateTime(readOnly=True, description='Creation time of the DN'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated time of the DN'),
    'started_at': fields.DateTime(readOnly=True, description='Start processing time'),
    'picked_at': fields.DateTime(readOnly=True, description='Picking completion time'),
    'packed_at': fields.DateTime(readOnly=True, description='Packing completion time'),
    'delivered_at': fields.DateTime(readOnly=True, description='Shipping time'),
    'completed_at': fields.DateTime(readOnly=True, description='Process completion time'),
    'closed_at': fields.DateTime(readOnly=True, description='DN closure time'),

    # 嵌套关系（如 supplier、carrier 等）可在子模型补充
    'recipient': fields.Nested(recipient_base_model, readOnly=True, description='Details of the associated recipient'),
    'carrier': fields.Nested(carrier_simple_model, readOnly=True, description='Carrier company details'),
    'creator': fields.Nested(user_model, readOnly=True, description='Details of the creator'),
    'warehouse_id': fields.Integer(required=True, description='Associated Warehouse ID'),
    'warehouse': fields.Nested(warehouse_simple_model, readOnly=True, description='Details of the associated warehouse'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.details),  # 直接计算关联的 details 数量
        description='Number of ASN detail items'
    ),
    'total_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.quantity if d.quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的数量总和
        description='Total quantity of goods in the DN'
    ),
    'total_picked_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.picked_quantity if d.picked_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), 
        description='Total quantity of goods that has been picked'
    ),
    'total_packed_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.packed_quantity if d.packed_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ),
        description='Total quantity of goods that has been packed'
    ),
    'total_delivered_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.delivered_quantity if d.delivered_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ),
        description='Total quantity of goods that has been delivered'
    ),
    'is_export': fields.Boolean(
        readOnly=True,
        attribute=lambda x: x.customs is not None,
        description='Export shipment (has a customs snapshot)'
    ),
    
}

dn_base_model = api_ns.model('DNBase', dn_fields)

# 4) DN 序列化模型
# 创建深拷贝避免污染原始字段定义
from copy import deepcopy
asn_full_fields = deepcopy(dn_fields)
# 覆盖需要扩展的字段
asn_full_fields.update({
    'recipient': fields.Nested(recipient_model, readOnly=True, description='Details of the associated recipient'),
    'carrier': fields.Nested(carrier_model, readOnly=True, description='Details of the associated carrier'),
    'warehouse': fields.Nested(warehouse_model, readOnly=True, description='Details of the associated warehouse'),
    'details': fields.List(fields.Nested(dn_detail_model), description='List of ASN details'),
    'customs': fields.Raw(
        readOnly=True,
        attribute=lambda x: x.customs.to_dict() if x.customs is not None else None,
        description='Customs snapshot (export shipments only)'
    ),
    'packages': fields.List(fields.Nested(dn_package_model), readOnly=True, description='Packages'),
    'customs_documents': fields.List(
        fields.Nested(dn_document_meta_model), readOnly=True,
        description='Customs documents (commercial invoice / packing list), newest first'
    ),
})

# 创建完整模型
dn_model = api_ns.model('DN', asn_full_fields)

# 5) DN 输入基础模型
# 生成 DN 输入模型：复制后删除不允许用户输入的字段
dn_input_fields = generate_input_fields(dn_fields)
dn_input_fields['carrier_code'] = fields.String(
    description='Carrier code used by external integrations (e.g. yamato)'
)
dn_input_base_model = api_ns.model('DNInputBase', dn_input_fields)

# 6) DN 输入模型（可包含明细输入）
dn_input_model = api_ns.inherit('DNInput', dn_input_base_model, {
    'details': fields.List(fields.Nested(dn_detail_input_model), description='List of DN details'),
    'customs': fields.Nested(
        dn_customs_input_model, allow_null=True,
        description='Customs snapshot for export shipments; omit for domestic shipments'
    ),
})

# --------------------------------------------
# DN 分页 Parser & 分页模型
# --------------------------------------------

# 7) DN 专用的分页解析器
dn_pagination_parser = pagination_parser.copy()
dn_pagination_parser.add_argument('dn_type', type=str, help='Filter by DN type', choices=DN.DN_TYPES)
dn_pagination_parser.add_argument('status', type=str, help='Filter by DN status', choices=DN.DN_STATUSES)
dn_pagination_parser.add_argument('order_number', type=str, help='Filter by order number')
dn_pagination_parser.add_argument('carrier_id', type=int, help='Filter by carrier ID')
dn_pagination_parser.add_argument('recipient_id', type=int, help='Filter by recipient ID')
dn_pagination_parser.add_argument('expected_shipping_date', type=inputs.date_from_iso8601, help='Filter by expected shipping date')
dn_pagination_parser.add_argument('created_by', type=int, help='Filter by creator ID')
dn_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by DN active status')
dn_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by warehouse ID')
dn_pagination_parser.add_argument('keyword', type=str, help='Keyword search')

# 8) DN 分页模型
dn_pagination_model = create_pagination_model(api_ns, dn_base_model)

dn_monthly_stats_parser =  reqparse.RequestParser()
dn_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
dn_monthly_stats_parser.add_argument('recipient_id', type=int, help='Filter by Recipient ID')
dn_monthly_stats_parser.add_argument('carrier_id', type=int, help='Filter by Carrier ID')
dn_monthly_stats_parser.add_argument('dn_type', type=str, help='Filter by DN type', choices=DN.DN_TYPES)
dn_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')

dn_customs_documents_parser = reqparse.RequestParser()
dn_customs_documents_parser.add_argument('status', type=str, choices=('issued', 'void'), location='args',
                                         help='Filter by document status')
