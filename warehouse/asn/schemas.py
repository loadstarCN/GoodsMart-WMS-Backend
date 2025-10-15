from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common.utils import generate_input_fields
from system.common import pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.carrier.schemas import carrier_simple_model,carrier_model
from warehouse.supplier.schemas import supplier_simple_model,supplier_model
from warehouse.warehouse.schemas import warehouse_simple_model,warehouse_model
from .models import ASN

# -----------------------------
# 初始化 ASN 命名空间
# -----------------------------
api_ns = Namespace('asn', description='ASN operations', authorizations=authorizations)
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# ASNDetail 模型定义
# -----------------------------
# 定义 ASNDetail 的完整字段字典
asn_detail_fields = {
    'id': fields.Integer(readOnly=True, description='ASN Detail ID'),
    'asn_id': fields.Integer(required=True, description='Associated ASN ID'),
    'goods_id': fields.Integer(required=True, description='Associated Goods ID'),
    'quantity': fields.Integer(description='Expected quantity of goods'),
    'actual_quantity': fields.Integer(description='Actual quantity received'),
    'sorted_quantity': fields.Integer(description='Quantity sorted'),
    'damage_quantity': fields.Integer(description='Quantity damaged'),
    'weight': fields.Float(description='Weight of goods (kg)'),
    'volume': fields.Float(description='Volume of goods (m³)'),
    'remark': fields.String(description='Remark for the ASN detail'),
    'created_by': fields.Integer(description='User who created the ASN detail'),
    'create_time': fields.DateTime(readOnly=True, description='Creation time'),
    'update_time': fields.DateTime(readOnly=True, description='Last updated time'),
    'goods': fields.Nested(goods_model, readOnly=True, description='Details of the associated goods'),
    'creator': fields.Nested(user_model, readOnly=True, description='Details of the creator'),
}

# 输出模型：ASNDetail
asn_detail_model = api_ns.model('ASNDetail', asn_detail_fields)

# 生成输入模型：复制后删除不允许用户输入的字段
asn_detail_input_fields = generate_input_fields(asn_detail_fields)
asn_detail_input_model = api_ns.model('ASNDetailInput', asn_detail_input_fields)

# -----------------------------
# ASN 模型定义
# -----------------------------
# 定义 ASN 的完整字段字典
asn_fields = {
    'id': fields.Integer(readOnly=True, description='ASN ID'),
    'supplier_id': fields.Integer(required=True, description='Associated Supplier ID'),
    'tracking_number': fields.String(description='Tracking number of the shipment'),
    'carrier_id': fields.Integer(description='Associated Carrier ID'),
    'asn_type': fields.String(required=True, description='Type of ASN', enum=ASN.ASN_TYPES),
    'status': fields.String(description='Status of the ASN', enum=ASN.ASN_STATUSES),
    'expected_arrival_date': fields.Date(description='Expected arrival date'),
    'remark': fields.String(description='Remark for the ASN'),
    'is_active': fields.Boolean(description='Is the ASN active?'),
    'created_by': fields.Integer(description='User who created the ASN'),
    'created_at': fields.DateTime(readOnly=True, description='Creation time'),
    'updated_at': fields.DateTime(readOnly=True, description='Last updated time'),
    'received_at': fields.DateTime(readOnly=True, description='Received time'),
    'completed_at': fields.DateTime(readOnly=True, description='Completion time'),
    'closed_at': fields.DateTime(readOnly=True, description='Closure time'),
    'supplier': fields.Nested(supplier_simple_model,readOnly=True,  description='Details of the associated supplier'),
    'carrier': fields.Nested(carrier_simple_model,readOnly=True,  description='Details of the associated carrier'),
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
        description='Sum of all goods quantities in the ASN'
    ),
    'total_actual_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.actual_quantity if d.actual_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的实际数量总和
        description='Sum of all actual goods quantities in the ASN'
    ),
    'total_sorted_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.sorted_quantity if d.sorted_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的实际数量总和
        description='Sum of all sorted goods quantities in the ASN'
    ),
    'total_damage_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.damage_quantity if d.damage_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的实际数量总和
        description='Sum of all damaged goods quantities in the ASN'
    ),
    'total_weight': fields.Float(
        readOnly=True,
        attribute=lambda x: sum(
            (d.weight if d.weight is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的重量总和
        description='Sum of all goods weights in the ASN (kg)'
    ),
    'total_volume': fields.Float(
        readOnly=True,
        attribute=lambda x: sum(
            (d.volume if d.volume is not None else 0)  # 处理 None 值
            for d in x.details
        ),  # 计算所有明细的体积总和
        description='Sum of all goods volumes in the ASN (m³)'
    ),

}

# 输出模型：ASNBase
asn_base_model = api_ns.model('ASNBase', asn_fields)

# 生成 ASN 输入模型：复制后删除不允许用户输入的字段
asn_input_fields = generate_input_fields(asn_fields)
asn_input_base_model = api_ns.model('ASNInputBase', asn_input_fields)

# 3. 完整模型 (使用显式覆盖)
# 创建深拷贝避免污染原始字段定义
from copy import deepcopy
asn_full_fields = deepcopy(asn_fields)
# 覆盖需要扩展的字段
asn_full_fields.update({
    'supplier': fields.Nested(supplier_model, readOnly=True, description='Details of the associated supplier'),
    'carrier': fields.Nested(carrier_model, readOnly=True, description='Details of the associated carrier'),
    'warehouse': fields.Nested(warehouse_model, readOnly=True, description='Details of the associated warehouse'),
    'details': fields.List(fields.Nested(asn_detail_model), description='List of ASN details')
})

# 创建完整模型
asn_model = api_ns.model('ASN', asn_full_fields)

asn_input_model = api_ns.inherit('ASNInput', asn_input_base_model, {
    'details': fields.List(fields.Nested(asn_detail_input_model), description='List of ASN details')
})

# -----------------------------
# 分页解析器 & 分页模型
# -----------------------------
asn_pagination_parser = pagination_parser.copy()
asn_pagination_parser.add_argument('asn_type', type=str, help='Filter by ASN type', choices=ASN.ASN_TYPES)
asn_pagination_parser.add_argument('status', type=str, help='Filter by ASN status', choices=ASN.ASN_STATUSES)
asn_pagination_parser.add_argument('tracking_number', type=str, help='Filter by Tracking number')
asn_pagination_parser.add_argument('supplier_id', type=int, help='Filter by Supplier ID')
asn_pagination_parser.add_argument('carrier_id', type=int, help='Filter by Carrier ID')
asn_pagination_parser.add_argument('expected_arrival_date', type=inputs.date_from_iso8601, help='Filter by Expected arrival date')
asn_pagination_parser.add_argument('created_by', type=int, help='Filter by Creator ID')
asn_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the ASN active?')
asn_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
asn_pagination_parser.add_argument('keyword', type=str, help='Keyword search')

asn_pagination_model = create_pagination_model(api_ns, asn_base_model)


asn_monthly_stats_parser =  reqparse.RequestParser()
asn_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
asn_monthly_stats_parser.add_argument('supplier_id', type=int, help='Filter by Supplier ID')
asn_monthly_stats_parser.add_argument('carrier_id', type=int, help='Filter by Carrier ID')
asn_monthly_stats_parser.add_argument('asn_type', type=str, help='Filter by ASN type', choices=ASN.ASN_TYPES)
asn_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
