from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common.utils import generate_input_fields
from system.common import pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.carrier.schemas import carrier_simple_model,carrier_model
from warehouse.supplier.schemas import supplier_simple_model,supplier_model
from warehouse.warehouse.schemas import warehouse_simple_model,warehouse_model
from warehouse.common.spec_capture import SPEC_CAPTURE_FIELDS, SPEC_CAPTURE_MODES
from warehouse.goods.services import SPEC_SOURCES
from .models import ASN
from .spec_capture import asn_spec_pending_count, line_spec_pending, spec_capture_payload
from .stage import STAGES as INBOUND_STAGES, inbound_stage_of

# -----------------------------
# 初始化 ASN 命名空间
# -----------------------------
def _document_timezone(document):
    from warehouse.common.clock import warehouse_timezone_name
    return warehouse_timezone_name(document)


api_ns = Namespace('asn', description='ASN operations', authorizations=authorizations)
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# ASNDetail 模型定义
# -----------------------------
# 定义 ASNDetail 的完整字段字典
# asn_id / created_by 由路径与登录身份决定；actual / sorted / damage_quantity 是分拣完成时
# 聚合出的过程量，都不接受客户端输入（readOnly 会被 generate_input_fields 剔除）。
asn_detail_fields = {
    'id': fields.Integer(readOnly=True, description='ASN Detail ID'),
    'asn_id': fields.Integer(readOnly=True, description='Associated ASN ID'),
    'goods_id': fields.Integer(description='Associated Goods ID (or provide goods_code)'),
    'goods_code': fields.String(description='Goods code (alternative to goods_id)'),
    'quantity': fields.Integer(description='Expected quantity of goods (positive integer)'),
    'actual_quantity': fields.Integer(readOnly=True, description='Actual quantity received'),
    'sorted_quantity': fields.Integer(readOnly=True, description='Quantity sorted'),
    'damage_quantity': fields.Integer(readOnly=True, description='Quantity damaged'),
    'weight': fields.Float(description='Weight of goods (kg)'),
    'volume': fields.Float(description='Volume of goods (m³)'),
    'remark': fields.String(description='Remark for the ASN detail'),
    'created_by': fields.Integer(readOnly=True, description='User who created the ASN detail'),
    'create_time': fields.DateTime(readOnly=True, description='Creation time'),
    'update_time': fields.DateTime(readOnly=True, description='Last updated time'),
    'goods': fields.Nested(goods_model, readOnly=True, description='Details of the associated goods'),
    'creator': fields.Nested(user_model, readOnly=True, description='Details of the creator'),
    # 规格采集（README「Spec capture on receiving」）：待采集项按商品当前资料与单据的采集项 / 模式算出，不落库
    'spec_pending': fields.List(
        fields.String(enum=list(SPEC_CAPTURE_FIELDS)), readOnly=True, attribute=line_spec_pending,
        description='Specs still to capture for this line (weight / dimensions / origin_country); [] = nothing '
                    'pending (no capture requested, captured, skipped with a reason, ASN closed, or nothing arrived)'),
    'spec_captured_at': fields.DateTime(readOnly=True, description='When the specs were last captured / skipped'),
    'spec_captured_by': fields.Integer(readOnly=True, description='User who last captured / skipped the specs'),
    'spec_captured_by_user': fields.Nested(user_model, readOnly=True, allow_null=True, attribute='spec_capturer',
                                           description='User who last captured / skipped the specs'),
    'spec_capture_note': fields.String(readOnly=True,
                                       description='Skip reason; set = the specs not captured were skipped'),
}

# 输出模型：ASNDetail
asn_detail_model = api_ns.model('ASNDetail', asn_detail_fields)

# 生成输入模型：复制后删除不允许用户输入的字段
asn_detail_input_fields = generate_input_fields(asn_detail_fields)
asn_detail_input_model = api_ns.model('ASNDetailInput', asn_detail_input_fields)

# 规格采集请求：PUT /asn/<id>/details/<detail_id>/spec
asn_detail_spec_input_model = api_ns.model('ASNDetailSpecInput', {
    'weight': fields.Float(description='Weight in kg (> 0, at most 99999.999; 3 decimals)'),
    'length': fields.Integer(description='Length in mm (give length, width and height together)'),
    'width': fields.Integer(description='Width in mm'),
    'height': fields.Integer(description='Height in mm'),
    'origin_country': fields.String(description='Country of origin, ISO 3166-1 alpha-2 (e.g. "CN")'),
    'note': fields.String(description='Skip reason (at most 255 characters): the requested specs not given are '
                                      'skipped; required to skip, and in remeasure mode unless every requested spec '
                                      'is given'),
    'spec_source': fields.String(enum=list(SPEC_SOURCES),
                                 description='Source for goods.spec_updated; default "receiving" ("station" for a '
                                             'weighing station)'),
})

# 单据上的采集请求 {fields, mode}
asn_spec_capture_model = api_ns.model('ASNSpecCapture', {
    'fields': fields.List(fields.String(enum=list(SPEC_CAPTURE_FIELDS)),
                          description='Specs to capture on receiving: weight / dimensions (length, width, height) / '
                                      'origin_country; [] = none'),
    'mode': fields.String(enum=list(SPEC_CAPTURE_MODES),
                          description='missing = only what the goods master data lacks (default); '
                                      'remeasure = measure every requested spec again'),
})

# -----------------------------
# ASN 模型定义
# -----------------------------
# 定义 ASN 的完整字段字典
# status / is_active / created_by 只能经 receive / close / cancel 等动作端点变更，不接受客户端输入
asn_fields = {
    'id': fields.Integer(readOnly=True, description='ASN ID'),
    'supplier_id': fields.Integer(required=True, description='Associated Supplier ID'),
    'tracking_number': fields.String(description='Tracking number of the shipment'),
    'carrier_id': fields.Integer(description='Associated Carrier ID'),
    'asn_type': fields.String(description='Type of ASN (defaults to inbound)', enum=ASN.ASN_TYPES),
    'status': fields.String(readOnly=True, description='Status of the ASN', enum=ASN.ASN_STATUSES),
    'expected_arrival_date': fields.Date(description='Expected arrival date'),
    'order_number': fields.String(description='Associated external order number'),
    'remark': fields.String(description='Remark for the ASN'),
    'is_active': fields.Boolean(readOnly=True, description='Is the ASN active?'),
    'created_by': fields.Integer(readOnly=True, description='User who created the ASN'),
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
    # 规格采集请求（建单 / 修改时可传；建单没传取仓库默认值）与待采集行数
    'spec_capture': fields.Nested(
        asn_spec_capture_model, attribute=spec_capture_payload,
        description='Specs to capture on receiving {fields, mode}. On create, omitted parts are copied from the '
                    'warehouse defaults (a snapshot); on update only the given keys change. Invalid -> 400 17020'),
    'spec_pending_count': fields.Integer(
        readOnly=True, attribute=asn_spec_pending_count,
        description='Number of lines with specs still to capture (same rule as details[].spec_pending)'),
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
    # 单据所属仓库的生效时区（doc/timezone-utc.md §3）：界面按它显示本单的时刻、解释输入的时刻
    'timezone': fields.String(
        readOnly=True, attribute=lambda x: _document_timezone(x),
        description='Effective IANA time zone of the ASN warehouse; times are shown and naive input times are read '
                    'in this zone'),
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
asn_pagination_parser.add_argument('order_number', type=str, help='Filter by order number')
asn_pagination_parser.add_argument('supplier_id', type=int, help='Filter by Supplier ID')
asn_pagination_parser.add_argument('carrier_id', type=int, help='Filter by Carrier ID')
asn_pagination_parser.add_argument('expected_arrival_date', type=inputs.date_from_iso8601, help='Filter by Expected arrival date')
asn_pagination_parser.add_argument('created_by', type=int, help='Filter by Creator ID')
asn_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Is the ASN active?')
asn_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
asn_pagination_parser.add_argument('keyword', type=str, help='Keyword search')
asn_pagination_parser.add_argument('inbound_stage', type=str, choices=INBOUND_STAGES, location='args',
                                   help='Filter by inbound stage (same definition as GET /asn/<id>/flow stage)')
asn_pagination_parser.add_argument('spec_pending', type=inputs.boolean, location='args',
                                   help='true: only ASNs with lines whose specs are still to capture; false: none')

# 列表行另带入库阶段（见 warehouse/asn/stage.py）：列表接口整页一条查询算好，挂在 _inbound_stage 上
asn_list_model = api_ns.inherit('ASNListItem', asn_base_model, {
    'inbound_stage': fields.String(
        readOnly=True, enum=list(INBOUND_STAGES), attribute=inbound_stage_of,
        description='expected / in_transit / receiving / sorting / putaway / completed / closed / exception'),
})
asn_pagination_model = create_pagination_model(api_ns, asn_list_model)


asn_monthly_stats_parser =  reqparse.RequestParser()
asn_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
asn_monthly_stats_parser.add_argument('supplier_id', type=int, help='Filter by Supplier ID')
asn_monthly_stats_parser.add_argument('carrier_id', type=int, help='Filter by Carrier ID')
asn_monthly_stats_parser.add_argument('asn_type', type=str, help='Filter by ASN type', choices=ASN.ASN_TYPES)
asn_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
