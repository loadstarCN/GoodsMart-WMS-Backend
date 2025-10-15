from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common.utils import generate_input_fields
from system.user.schemas import user_simple_model as original_user_model
from warehouse.delivery.schemas import dn_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from warehouse.location.schemas import location_simple_model as location_model
from system.common import pagination_parser, create_pagination_model
from .models import PickingTask

api_ns = Namespace(
    'picking',
    description='Picking Task related operations',
    authorizations=authorizations
)

# 复用用户模型
user_model = api_ns.model('User', original_user_model)

# ---------------------------------------------------------------------------------
# 1. Picking Task Status Log 输出模型
# ---------------------------------------------------------------------------------
picking_task_status_log_model = api_ns.model('PickingTaskStatusLog', {
    'id': fields.Integer(readOnly=True, description='Status Log ID'),
    'task_id': fields.Integer(required=True, description='Associated picking task ID'),
    'old_status': fields.String(description='Old status', enum=PickingTask.PICKING_TASK_STATUSES),
    'new_status': fields.String(description='New status', enum=PickingTask.PICKING_TASK_STATUSES),
    'operator_id': fields.Integer(description='Operator ID'),
    'changed_at': fields.DateTime(description='Change time'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details')
})

# ---------------------------------------------------------------------------------
# 2. 精简版 Detail 输出模型（用于批次场景）
# ---------------------------------------------------------------------------------
picking_task_detail_for_batch_model = api_ns.model('PickingTaskDetailForBatch', {
    'id': fields.Integer(readOnly=True, description='Picking detail ID'),
    'location_id': fields.Integer(description='Location ID'),
    'goods_id': fields.Integer(description='Goods ID'),
    'picked_quantity': fields.Integer(description='Picked quantity'),
    'picking_time': fields.DateTime(description='Picking time'),
    'operator_id': fields.Integer(description='Operator ID'),
    'location': fields.Nested(location_model, readOnly=True, description='Location details'),
    'goods': fields.Nested(goods_model, readOnly=True, description='Goods details'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details')
})

# ---------------------------------------------------------------------------------
# 3. Picking Batch 输出模型
# ---------------------------------------------------------------------------------
picking_batch_model = api_ns.model('PickingBatch', {
    'id': fields.Integer(readOnly=True, description='Picking batch ID'),
    'picking_task_id': fields.Integer(required=True, description='Associated picking task ID'),
    'operator_id': fields.Integer(required=True, description='Operator ID'),
    'operation_time': fields.DateTime(description='Operation time'),
    'remark': fields.String(description='Batch remarks'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details'),
    'details': fields.List(fields.Nested(picking_task_detail_for_batch_model),readOnly=True,description='List of associated picking details')
})

# ---------------------------------------------------------------------------------
# 4. 全量 Picking Task Detail 输出模型（包含 batch_id，但不再嵌套 batch）
# ---------------------------------------------------------------------------------
picking_task_detail_model = api_ns.inherit('PickingTaskDetail', picking_task_detail_for_batch_model, {
    'picking_task_id': fields.Integer(required=True, description='Associated picking task ID'),
    'batch_id': fields.Integer(description='Batch ID')
})

# ---------------------------------------------------------------------------------
# 5. Picking Task 基础信息输出模型
# ---------------------------------------------------------------------------------
picking_task_base_model = api_ns.model('PickingTaskBase', {
    'id': fields.Integer(readOnly=True, description='Picking task ID'),
    'dn_id': fields.Integer(required=True, description='Associated delivery notice ID'),
    'status': fields.String(
        enum=PickingTask.PICKING_TASK_STATUSES,
        description='Task status',
        example='pending'
    ),
    'is_active': fields.Boolean(description='Is active'),
    'created_by': fields.Integer(description='Creator ID'),
    'created_at': fields.DateTime(readOnly=True, description='Creation time'),
    'updated_at': fields.DateTime(readOnly=True, description='Last update time'),
    'started_at': fields.DateTime(description='Task start time'),
    'completed_at': fields.DateTime(description='Task completion time'),
    'creator': fields.Nested(user_model, readOnly=True, description='Creator details'),
    'expected_shipping_date': fields.Date(attribute='dn.expected_shipping_date', readOnly=True, description='Expected shipping date'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.task_details),  # 直接计算关联的 details 数量
        description='Number of picking detail items'
    ),
    'expected_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            detail.quantity 
            for detail in x.dn.details  # 通过关联的 ASN 对象遍历 details
            if detail.quantity is not None  # 处理空值
        ),
        description='Estimated Picking Quantity (Aggregated from DN Line Items)'
    ),

    'total_picked_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.picked_quantity if d.picked_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的数量总和
        description='Sum of all picked goods quantities in the picking task'
    ),
})

# ---------------------------------------------------------------------------------
# 6. Picking Task 主输出模型（继承基础模型，添加关联）
# ---------------------------------------------------------------------------------
picking_task_model = api_ns.inherit('PickingTask', picking_task_base_model, {
    'dn': fields.Nested(dn_model, readOnly=True, description='Associated delivery notice details'),
    'task_details': fields.List(fields.Nested(picking_task_detail_model), readOnly=True,description='List of picking details'),
    'batches': fields.List(fields.Nested(picking_batch_model), readOnly=True,description='List of associated picking batches'),
    'status_logs': fields.List(fields.Nested(picking_task_status_log_model), readOnly=True,description='Status change logs')
})

# ---------------------------------------------------------------------------------
# 7. 输入模型：创建/更新 PickingTask（从基础模型复制后删除只读/自动生成字段）
# ---------------------------------------------------------------------------------
picking_task_input_fields = generate_input_fields(picking_task_base_model)
picking_task_input_model = api_ns.model('PickingTaskInput', picking_task_input_fields)

# ---------------------------------------------------------------------------------
# 8. 输入模型：创建/更新 PickingTaskDetail（从全量 Detail 模型复制后删除不允许输入的字段）
# ---------------------------------------------------------------------------------
picking_task_detail_input_fields = generate_input_fields(picking_task_detail_model)
picking_task_detail_input_model = api_ns.model('PickingTaskDetailInput', picking_task_detail_input_fields)

# ---------------------------------------------------------------------------------
# 9. 输入模型：创建/更新 PickingBatch（从 Picking Batch 模型复制后删除不允许输入的字段）
# ---------------------------------------------------------------------------------
picking_batch_input_fields = generate_input_fields(picking_batch_model)
# 更新输入模型的 'details' 字段，使用 PickingTaskDetailInput 模型
picking_batch_input_fields['details'] = fields.List(
    fields.Nested(picking_task_detail_input_model),
    description='List of picking details associated with the batch'
)
picking_batch_input_model = api_ns.model('PickingBatchInput', picking_batch_input_fields)

# ---------------------------------------------------------------------------------
# 分页解析器 & 分页模型
# ---------------------------------------------------------------------------------
picking_pagination_parser = pagination_parser.copy()
picking_pagination_parser.add_argument('dn_id',type=int,help='Filter by delivery notice ID',location='args')
picking_pagination_parser.add_argument('status',type=str,choices=PickingTask.PICKING_TASK_STATUSES,help='Filter by status',location='args')
picking_pagination_parser.add_argument('is_active',type=inputs.boolean,help='Is the task active?',location='args')
picking_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
picking_pagination_parser.add_argument('keyword', type=str, help='Keyword search')

pagination_model = create_pagination_model(api_ns, picking_task_base_model)

picking_monthly_stats_parser =  reqparse.RequestParser()
picking_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
picking_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
