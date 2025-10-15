from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.location.schemas import location_simple_model as location_model
from .models import Adjustment

# -----------------------------
# 初始化 Inventory Adjustment 命名空间
# -----------------------------
api_ns = Namespace(
    'adjustment',
    description='Inventory Adjustment related operations',
    authorizations=authorizations
)
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# Adjustment Detail 模型定义
# -----------------------------
# 定义完整的 Adjustment Detail 字段字典
adjustment_detail_fields = {
    'id': fields.Integer(readOnly=True, description='Adjustment Detail ID'),
    'adjustment_id': fields.Integer(required=True, description='ID of the associated adjustment task'),
    'goods_id': fields.Integer(required=True, description='ID of the goods being adjusted'),
    'location_id': fields.Integer(description='Location ID (if applicable)'),
    'system_quantity': fields.Integer(description='System recorded quantity'),
    'actual_quantity': fields.Integer(required=True, description='Actual quantity counted'),
    'adjustment_quantity': fields.Integer(description='Difference between system and actual quantity'),
    'remark': fields.String(description='Additional remarks about the adjustment detail'),
    'goods': fields.Nested(goods_model, readonly=True, description='Details of the goods'),
    'location': fields.Nested(location_model, readonly=True, description='Details of the location (if applicable)')
}

# 输出模型：完整的 Adjustment Detail 模型
adjustment_detail_model = api_ns.model('AdjustmentDetail', adjustment_detail_fields)

# 生成输入模型：复制后删除不需要的字段
adjustment_detail_input_fields = generate_input_fields(adjustment_detail_fields)
adjustment_detail_input_model = api_ns.model('AdjustmentDetailInput', adjustment_detail_input_fields)

# -----------------------------
# Adjustment 模型定义
# -----------------------------
# 定义完整的 Adjustment 字段字典
adjustment_fields = {
    'id': fields.Integer(readOnly=True, description='Inventory Adjustment ID'),
    'warehouse_id': fields.Integer(required=True, description='ID of the associated warehouse'),
    'adjustment_reason': fields.String(description='Reason for adjustment'),
    'status': fields.String(
        enum=Adjustment.AdjustmentStatuses,
        description='Adjustment status (pending, completed)',
        default='pending'
    ),
    'is_active': fields.Boolean(description='Whether the adjustment is active', default=True),
    'created_by': fields.Integer(description='Creator ID'),
    'approved_by': fields.Integer(description='Approver ID'),
    'operator_id': fields.Integer(description='Operator ID'),
    'created_at': fields.DateTime(readOnly=True, description='Creation time'),
    'updated_at': fields.DateTime(readOnly=True, description='Last update time'),
    'approved_at': fields.DateTime(description='Approval time'),
    'completed_at': fields.DateTime(description='Completion time'),
    'creator': fields.Nested(user_model, readonly=True, description='Details of the creator'),
    'approver': fields.Nested(user_model, readonly=True, description='Details of the approver'),
    'operator': fields.Nested(user_model, readonly=True, description='Details of the operator'),
    'warehouse': fields.Nested(location_model, readonly=True, description='Details of the warehouse'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.details),  # 直接计算关联的 details 数量
        description='Number of ASN detail items'
    ),
    'total_actual_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.actual_quantity if d.actual_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ),  # 计算所有明细的数量总和
        description='Sum of all goods quantities in the ASN'
    ),
    'total_system_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.system_quantity if d.system_quantity is not None else 0)  # 处理 None 值
            for d in x.details
        ), # 计算所有明细的数量总和
        description='Sum of all system goods quantities in the ASN'
    ),

}

# 输出模型：完整的 Adjustment 基础模型
adjustment_base_model = api_ns.model('AdjustmentBase', adjustment_fields)

# 生成输入模型：复制后删除不需要输入的字段
adjustment_input_fields = generate_input_fields(adjustment_fields)
adjustment_input_model = api_ns.model('AdjustmentInput', adjustment_input_fields)

# 定义完整的 Adjustment 模型（包含 details 嵌套字段）
adjustment_model = api_ns.inherit('Adjustment', adjustment_base_model, {
    'details': fields.List(fields.Nested(adjustment_detail_model), description='Details of the adjustment')
})

# -----------------------------
# 分页解析器 & 分页模型
# -----------------------------
adjustment_pagination_parser = pagination_parser.copy()
adjustment_pagination_parser.add_argument('status', type=str, location='args', help='Filter by adjustment status', choices=Adjustment.AdjustmentStatuses)
adjustment_pagination_parser.add_argument('is_active', type=inputs.boolean, location='args', help='Filter by active status')
adjustment_pagination_parser.add_argument('created_by', type=int, location='args', help='Filter by creator ID')
adjustment_pagination_parser.add_argument('warehouse_id', type=int, location='args', help='Filter by warehouse ID')
adjustment_pagination_parser.add_argument('adjustment_reason', type=str, location='args', help='Filter by adjustment reason')

# 根据 goods 和 location 查询 Adjustment Detail 列表
adjustment_detail_pagination_parser = pagination_parser.copy()
adjustment_detail_pagination_parser.add_argument('goods_id', type=int, required=False, help='Filter by goods ID', location='args')
adjustment_detail_pagination_parser.add_argument('location_id', type=int, required=False, help='Filter by location ID', location='args')
adjustment_detail_pagination_parser.add_argument('warehouse_id', type=int, required=False, help='Filter by warehouse ID', location='args')

adjustment_pagination_model = create_pagination_model(api_ns, adjustment_base_model)
adjustment_detail_pagination_model = create_pagination_model(api_ns, adjustment_detail_model)


adjustment_monthly_stats_parser =  reqparse.RequestParser()
adjustment_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
adjustment_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
