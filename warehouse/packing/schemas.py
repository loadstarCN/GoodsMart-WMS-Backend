from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from warehouse.goods.schemas import goods_simple_model as goods_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.dn.schemas import dn_model
from .models import PackingTask  # 假设此处含有 PACKING_TASK_STATUSES 等常量

api_ns = Namespace(
    'packing',
    description='Packing operations',
    authorizations=authorizations
)

# 1. 复用用户模型
user_model = api_ns.model('User', original_user_model)

# ---------------------------------------------------------------------------------
# 1. Packing Task Status Log 序列化模型（输出）
# ---------------------------------------------------------------------------------
packing_task_status_log_model = api_ns.model('PackingTaskStatusLog', {
    'id': fields.Integer(readOnly=True, description='Status Log ID'),
    'task_id': fields.Integer(required=True, description='Associated packing task ID'),
    'old_status': fields.String(description='Old status',enum=PackingTask.PACKING_TASK_STATUSES),
    'new_status': fields.String(description='New status',enum=PackingTask.PACKING_TASK_STATUSES),
    'operator_id': fields.Integer(description='Operator ID'),
    'changed_at': fields.DateTime(description='Change time'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details')
})

# ---------------------------------------------------------------------------------
# 2. 精简版 Detail 模型（用于批次场景）
# ---------------------------------------------------------------------------------
packing_task_detail_for_batch_model = api_ns.model('PackingTaskDetailForBatch', {
    'id': fields.Integer(readOnly=True, description='Packing detail ID'),
    'goods_id': fields.Integer(description='Goods ID'),
    'packed_quantity': fields.Integer(description='Quantity that has been packed'),
    'packing_time': fields.DateTime(description='Time of packing'),
    'operator_id': fields.Integer(description='Operator ID'),
    'goods': fields.Nested(goods_model, readOnly=True, description='Goods details'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details')
})

# ---------------------------------------------------------------------------------
# 3. Packing Batch 序列化模型（输出）
# ---------------------------------------------------------------------------------
packing_batch_model = api_ns.model('PackingBatch', {
    'id': fields.Integer(readOnly=True, description='Packing batch ID'),
    'packing_task_id': fields.Integer(required=True, description='Associated packing task ID'),
    'operator_id': fields.Integer(required=True, description='Operator ID'),
    'operation_time': fields.DateTime(description='Operation time'),
    'remark': fields.String(description='Batch remarks'),
    'operator': fields.Nested(user_model,readOnly=True, description='Operator details'),
    'details': fields.List(fields.Nested(packing_task_detail_for_batch_model),readOnly=True,description='List of associated packing details for this batch')
})

# ---------------------------------------------------------------------------------
# 4. 全量 Packing Task Detail 模型（包含 batch_id，但不再嵌套 batch）
# ---------------------------------------------------------------------------------
packing_task_detail_model = api_ns.inherit('PackingTaskDetail',packing_task_detail_for_batch_model, {
    'packing_task_id': fields.Integer(required=True, description='Associated packing task ID'),
    'batch_id': fields.Integer(description='Associated packing batch ID (if applicable)'),
})

# ---------------------------------------------------------------------------------
# 5. Packing Task 基础信息模型（输出）
# ---------------------------------------------------------------------------------
packing_task_base_model = api_ns.model('PackingTaskBase', {
    'id': fields.Integer(readOnly=True, description='Packing Task ID'),
    'dn_id': fields.Integer(required=True, description='Associated delivery notice ID'),
    'status': fields.String(
        enum=PackingTask.PACKING_TASK_STATUSES,
        description='Packing task status',
        example='pending'
    ),
    'is_active': fields.Boolean(description='Is the task active?'),
    'created_by': fields.Integer(description='Creator ID'),
    'created_at': fields.DateTime(readOnly=True,description='Creation time of the packing task'),
    'updated_at': fields.DateTime(readOnly=True,description='Last update time of the packing task'),
    'started_at': fields.DateTime(description='Task start time'),
    'completed_at': fields.DateTime(description='Task completion time'),
    'creator': fields.Nested(user_model,readOnly=True, description='Details of the creator'),
    'expected_shipping_date': fields.Date(attribute='dn.expected_shipping_date', readOnly=True, description='Expected shipping date'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.task_details),  # 直接计算关联的 details 数量
        description='Number of packing detail items'
    ),
    'expected_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            detail.picked_quantity 
            for detail in x.dn.details  # 通过关联的 ASN 对象遍历 details
            if detail.picked_quantity is not None  # 处理空值
        ),
        description='Estimated Picking Quantity (Aggregated from DN Line Items)'
    ),
    'total_packed_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.packed_quantity if d.packed_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的数量总和
        description='Sum of all packed goods quantities in the packing task'
    ),
})

# ---------------------------------------------------------------------------------
# 6. Packing Task 主序列化模型（输出，继承基础模型，添加关联信息）
# ---------------------------------------------------------------------------------
packing_task_model = api_ns.inherit('PackingTask', packing_task_base_model, {
    'dn': fields.Nested(dn_model, description='Details of the associated delivery notice'),
    'task_details': fields.List(fields.Nested(packing_task_detail_model),readOnly=True,description='List of packing details'),
    'batches': fields.List(fields.Nested(packing_batch_model),readOnly=True,description='List of associated packing batches'),
    'status_logs': fields.List(fields.Nested(packing_task_status_log_model),readOnly=True,description='Status change logs')
})

# ---------------------------------------------------------------------------------
# 7. 输入模型：创建/更新 PackingTask（从基础模型复制后删除只读及自动生成字段）
# ---------------------------------------------------------------------------------
packing_task_input_fields = generate_input_fields(packing_task_base_model)
packing_task_input_model = api_ns.model('PackingTaskInput', packing_task_input_fields)

# ---------------------------------------------------------------------------------
# 8. 输入模型：创建/更新 PackingTaskDetail（从全量 Detail 模型复制后删除不允许输入的字段）
# ---------------------------------------------------------------------------------
packing_task_detail_input_fields = generate_input_fields(packing_task_detail_model)
packing_task_detail_input_model = api_ns.model('PackingTaskDetailInput', packing_task_detail_input_fields)

# ---------------------------------------------------------------------------------
# 9. 输入模型：创建/更新 PackingBatch（从 Packing Batch 模型复制后删除不允许输入的字段）
# ---------------------------------------------------------------------------------
packing_batch_input_fields = generate_input_fields(packing_batch_model)
# 更新输入模型的 'details' 字段，使用 PackingTaskDetailInput 模型
packing_batch_input_fields['details'] = fields.List(
    fields.Nested(packing_task_detail_input_model),
    description='List of packing details associated with the batch'
)
packing_batch_input_model = api_ns.model('PackingBatchInput', packing_batch_input_fields)

# ---------------------------------------------------------------------------------
# 分页解析器 & 分页模型
# ---------------------------------------------------------------------------------
packing_pagination_parser = pagination_parser.copy()
packing_pagination_parser.add_argument('dn_id',type=int,help='Filter by delivery notice ID',location='args')
packing_pagination_parser.add_argument('status',type=str,choices=PackingTask.PACKING_TASK_STATUSES,help='Filter by status',location='args')
packing_pagination_parser.add_argument('is_active',type=inputs.boolean,help='Is the task active?',location='args')
packing_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
packing_pagination_parser.add_argument('keyword', type=str, help='Keyword search')

pagination_model = create_pagination_model(api_ns, packing_task_base_model)

packing_monthly_stats_parser =  reqparse.RequestParser()
packing_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
packing_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
