from flask_restx import Namespace, fields, inputs,reqparse
from extensions import authorizations
from system.user.schemas import user_simple_model as original_user_model
from warehouse.asn.schemas import asn_model
from warehouse.goods.schemas import goods_simple_model as goods_model
from system.common import pagination_parser, create_pagination_model,generate_input_fields
from .models import SortingTask

# -----------------------------
# 定义 Sorting 命名空间
# -----------------------------
api_ns = Namespace('sorting', description='Sorting Task operations', authorizations=authorizations)

# 复用系统中的 user_model
user_model = api_ns.model('User', original_user_model)

# ---------------------------------------------------------------------------------
# 1. Sorting Task Status Log 的输出模型
# ---------------------------------------------------------------------------------
sorting_task_status_log_model = api_ns.model('SortingTaskStatusLog', {
    'id': fields.Integer(readOnly=True, description='Status Log ID'),
    'task_id': fields.Integer(required=True, description='Associated Sorting Task ID'),
    'old_status': fields.String(description='Old Status'),
    'new_status': fields.String(description='New Status'),
    'operator_id': fields.Integer(description='User who changed the status'),
    'changed_at': fields.DateTime(description='Time the status changed'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details'),
})

# ---------------------------------------------------------------------------------
# 2. 专门给“Batch 下的 Details”使用的精简版 Detail 输出模型
#    - 去掉 batch_id、batch 等可能导致循环引用或无用信息的字段
# ---------------------------------------------------------------------------------
sorting_task_detail_for_batch_model = api_ns.model('SortingTaskDetailForBatch', {
    'id': fields.Integer(readOnly=True, description='Sorting Task Detail ID'),
    'goods_id': fields.Integer(required=True, description='Associated ASN Detail ID'),
    'sorted_quantity': fields.Integer(description='Quantity sorted in this task'),
    'damage_quantity': fields.Integer(description='Quantity damaged in this task'),
    'sorting_time': fields.DateTime(description='Time of sorting operation'),
    'operator_id': fields.Integer(description='Operator performing the sorting'),
    'goods': fields.Nested(goods_model, readOnly=True, description='Goods details'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details'),
})

# ---------------------------------------------------------------------------------
# 3. Sorting Batch 的输出模型
#    - 可直接查看对应的 detail 列表（details）
# ---------------------------------------------------------------------------------
sorting_batch_model = api_ns.model('SortingBatch', {
    'id': fields.Integer(readOnly=True, description='Sorting Batch ID'),
    'sorting_task_id': fields.Integer(required=True, description='Associated Sorting Task ID'),
    'operator_id': fields.Integer(description='Operator who performed the sorting batch'),
    'operation_time': fields.DateTime(description='Time of the sorting batch'),
    'remark': fields.String(description='Remark for the sorting batch'),
    'operator': fields.Nested(user_model, readOnly=True, description='Operator details'),
    'details': fields.List(
        fields.Nested(sorting_task_detail_for_batch_model),
        readOnly=True, 
        description='List of details associated to this batch'
    )
})

# ---------------------------------------------------------------------------------
# 4. 全量的 Sorting Task Detail 输出模型（包含 batch_id，但不再嵌套 batch）
#    - 用于在 SortingTask 主体下查看明细，或单独操作 detail
# ---------------------------------------------------------------------------------
sorting_task_detail_model = api_ns.inherit('SortingTaskDetail', sorting_task_detail_for_batch_model, {
    'batch_id': fields.Integer(description='Associated Sorting Batch ID')
})

# ---------------------------------------------------------------------------------
# 5. SortingTask 基础信息输出模型
# ---------------------------------------------------------------------------------
sorting_task_base_model = api_ns.model('SortingTaskBase', {
    'id': fields.Integer(readOnly=True, description='Sorting Task ID'),
    'asn_id': fields.Integer(required=True, description='Associated ASN ID'),
    'status': fields.String(description='Sorting Task Status', enum=SortingTask.SORTING_TASK_STATUSES),
    'is_active': fields.Boolean(description='Is the task active?'),
    'created_by': fields.Integer(description='User who created the task'),
    'created_at': fields.DateTime(readOnly=True, description='Task creation time'),
    'updated_at': fields.DateTime(readOnly=True, description='Last update time'),
    'started_at': fields.DateTime(description='The time the task started'),
    'completed_at': fields.DateTime(description='The time the task was completed'),
    'creator': fields.Nested(user_model, readOnly=True, description='Creator details'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.task_details),  # 直接计算关联的 details 数量
        description='Number of  sorting detail items'
    ),

    'expected_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            detail.quantity 
            for detail in x.asn.details  # 通过关联的 ASN 对象遍历 details
            if detail.quantity is not None  # 处理空值
        ),
        description='Estimated Arrival Quantity (Aggregated from ASN Line Items)'
    ),
    'total_sorted_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.sorted_quantity if d.sorted_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的实际数量总和
        description='Sum of all sorted goods quantities in the task'
    ),
        
    'total_damage_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.damage_quantity if d.damage_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的实际数量总和
        description='Sum of all damaged goods quantities in the task'
    ),
})

# ---------------------------------------------------------------------------------
# 6. Sorting Task 主输出模型（继承自基础模型，添加关联信息）
# ---------------------------------------------------------------------------------
sorting_task_model = api_ns.inherit('SortingTask', sorting_task_base_model, {
    'asn': fields.Nested(asn_model, readOnly=True, description='Details of the associated ASN'),
    'task_details': fields.List(fields.Nested(sorting_task_detail_model),readOnly=True,  description='Sorting Task Details'),
    'batches': fields.List(fields.Nested(sorting_batch_model), readOnly=True, description='Sorting Batches of this task'),
    'status_logs': fields.List(fields.Nested(sorting_task_status_log_model), readOnly=True, description='Logs of status changes'),
})

# ---------------------------------------------------------------------------------
# 7. 输入模型：创建/更新 SortingTask
#    - 从基础输出模型复制后删除只读/自动生成字段
# ---------------------------------------------------------------------------------
sorting_task_input_fields = generate_input_fields(sorting_task_base_model)
sorting_task_input_model = api_ns.model('SortingTaskInput', sorting_task_input_fields)

# ---------------------------------------------------------------------------------
# 8. 输入模型：创建/更新 SortingTaskDetail
#    - 从全量 Detail 输出模型复制后删除不允许输入的字段
# ---------------------------------------------------------------------------------
sorting_task_detail_input_fields = generate_input_fields(sorting_task_detail_model)
sorting_task_detail_input_model = api_ns.model('SortingTaskDetailInput', sorting_task_detail_input_fields)

# ---------------------------------------------------------------------------------
# 9. 输入模型：创建/更新 SortingBatch
#    - 从 Sorting Batch 输出模型复制后删除不允许输入的字段，并更新 details 字段
# ---------------------------------------------------------------------------------
sorting_batch_input_fields = generate_input_fields(sorting_batch_model)
sorting_batch_input_fields.pop('details')


sorting_batch_input_fields['details'] = fields.List(
    fields.Nested(sorting_task_detail_input_model),
    description='List of sorting details to create with this batch'
)
sorting_batch_input_model = api_ns.model('SortingBatchInput', sorting_batch_input_fields)

# ---------------------------------------------------------------------------------
# 10. 分页解析器 & 分页模型
# ---------------------------------------------------------------------------------
sorting_pagination_parser = pagination_parser.copy()
sorting_pagination_parser.add_argument('asn_id',type=int,help='Associated ASN ID',location='args')
sorting_pagination_parser.add_argument('status',type=str,help='Sorting Task Status',location='args',choices=SortingTask.SORTING_TASK_STATUSES)
sorting_pagination_parser.add_argument('is_active',type=inputs.boolean,help='Is the task active?',location='args')
sorting_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
sorting_pagination_parser.add_argument('keyword', type=str, help='Keyword search')


pagination_model = create_pagination_model(api_ns, sorting_task_base_model)



sorting_monthly_stats_parser =  reqparse.RequestParser()
sorting_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
sorting_monthly_stats_parser.add_argument('sorting_type', type=str, help='Filter by Sorting type', choices=SortingTask.SORTING_TASK_STATUSES)
sorting_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
