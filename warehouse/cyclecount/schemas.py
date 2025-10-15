from flask_restx import Namespace, fields, inputs, reqparse
from extensions import authorizations
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model
from warehouse.goods.schemas import goods_simple_model as original_goods_model
from warehouse.location.schemas import location_simple_model as original_location_model
from warehouse.warehouse.schemas import warehouse_simple_model as original_warehouse_model
from .models import CycleCountTask, CycleCountTaskDetail

# ------------------------------------------------------------------------------
# 定义 Namespace
# ------------------------------------------------------------------------------
api_ns = Namespace(
    'cycle_count',
    description='Cycle Count Task operations',
    authorizations=authorizations
)

# ------------------------------------------------------------------------------
# 复用已有的模型 (User, Goods, Location)
# ------------------------------------------------------------------------------
user_model = api_ns.model('User', original_user_model)
goods_model = api_ns.model('Goods', original_goods_model)
location_model = api_ns.model('Location', original_location_model)
wareshouse_model = api_ns.model('Warehouse', original_warehouse_model)

# ------------------------------------------------------------------------------
# 1. Cycle Count Task Status Log 模型（输出模型）
# ------------------------------------------------------------------------------
cycle_count_task_status_log_model = api_ns.model('CycleCountTaskStatusLog', {
    'id': fields.Integer(readOnly=True, description='Status Log ID'),
    'task_id': fields.Integer(required=True, description='Associated cycle count task ID'),
    'old_status': fields.String(
        description='Old status',
        enum=CycleCountTask.CYCLE_COUNT_TASK_STATUSES
    ),
    'new_status': fields.String(
        description='New status',
        enum=CycleCountTask.CYCLE_COUNT_TASK_STATUSES
    ),
    'operator_id': fields.Integer(description='Operator ID who changed the status'),
    'changed_at': fields.DateTime(description='Status change time'),
    'operator': fields.Nested(user_model,readonly=True, description='Operator details')
})

# ------------------------------------------------------------------------------
# 2. Cycle Count Task Detail 模型（输出模型）
# ------------------------------------------------------------------------------
cycle_count_task_detail_fields = {
    'id': fields.Integer(readOnly=True, description='Cycle Count Task Detail ID'),
    'task_id': fields.Integer(required=True, description='Associated cycle count task ID'),
    'goods_id': fields.Integer(required=True, description='Associated goods ID'),
    'location_id': fields.Integer(required=True, description='Associated location ID'),
    'system_quantity': fields.Integer(description='System recorded quantity'),
    'actual_quantity': fields.Integer(description='Actual counted quantity'),
    'difference': fields.Integer(readonly=True,description='Difference between actual and expected quantity'),
    'operator_id': fields.Integer(description='Operator performing the count'),
    'updated_at': fields.DateTime(readonly=True,description='Last update time'),
    'completed_at': fields.DateTime(description='Completion time for this detail item'),
    'status': fields.String(
        description='Cycle Count Task Detail Status',
        enum=CycleCountTaskDetail.CYCLE_COUNT_TASK_DETAIL_STATUSES
    ),
    'goods': fields.Nested(goods_model,readonly=True, description='Goods details'),
    'location': fields.Nested(location_model,readonly=True, description='Location details'),
    'operator': fields.Nested(user_model,readonly=True, description='Operator details')
}
cycle_count_task_detail_model = api_ns.model('CycleCountTaskDetail', cycle_count_task_detail_fields)

# 生成 Cycle Count Task Detail 输入模型：复制后删除不允许输入的字段
cycle_count_task_detail_input_fields = generate_input_fields(cycle_count_task_detail_fields)
cycle_count_task_detail_input_model = api_ns.model('CycleCountTaskDetailInput', cycle_count_task_detail_input_fields)

# ------------------------------------------------------------------------------
# 3. Cycle Count Task 基础模型（输出模型）
# ------------------------------------------------------------------------------
cycle_count_task_base_fields = {
    'id': fields.Integer(readOnly=True, description='Cycle Count Task ID'),
    'task_name': fields.String(description='Task name'),
    'warehouse_id': fields.Integer(description='Warehouse ID to which this task belongs'),
    'scheduled_date': fields.DateTime(description='Scheduled date/time for counting'),
    'status': fields.String(
        description='Cycle count task status',
        enum=CycleCountTask.CYCLE_COUNT_TASK_STATUSES
    ),
    'is_active': fields.Boolean(description='Is the task active?'),
    'created_by': fields.Integer(description='User who created the task'),
    'created_at': fields.DateTime(readonly=True,description='Creation time'),
    'updated_at': fields.DateTime(readonly=True,description='Last update time'),
    'started_at': fields.DateTime(description='When the cycle count actually started'),
    'completed_at': fields.DateTime(description='When the cycle count completed'),
    'creator': fields.Nested(user_model,readonly=True, description='Creator details'),
    'warehouse': fields.Nested(wareshouse_model,readonly=True, description='Warehouse details'),
    'detail_count': fields.Integer(
        readOnly=True,
        attribute=lambda x: len(x.task_details),  # 直接计算关联的 details 数量
        description='Number of ASN detail items'
    ),
    'total_system_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.system_quantity if d.system_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的系统数量总和
        description='Sum of all system goods quantities in the cycle count task'
    ),
    'total_actual_quantity': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.actual_quantity if d.actual_quantity is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的实际数量总和
        description='Sum of all actual goods quantities in the cycle count task'
    ),
    'total_difference': fields.Integer(
        readOnly=True,
        attribute=lambda x: sum(
            (d.difference if d.difference is not None else 0)  # 处理 None 值
            for d in x.task_details
        ), # 计算所有明细的实际数量总和
        description='Sum of all differences in the cycle count task'
    ),
}
cycle_count_task_base_model = api_ns.model('CycleCountTaskBase', cycle_count_task_base_fields)

# 生成 Cycle Count Task 输入模型：复制后删除不允许用户输入的字段
cycle_count_task_input_fields = generate_input_fields(cycle_count_task_base_fields)
cycle_count_task_input_model = api_ns.model('CycleCountTaskInput', cycle_count_task_input_fields)
# 添加task_details字段
cycle_count_task_input_model['task_details'] = fields.List(
    fields.Nested(cycle_count_task_detail_input_model),
    description='List of associated cycle count details'
)

# ------------------------------------------------------------------------------
# 4. Cycle Count Task 主模型（输出模型）
# ------------------------------------------------------------------------------
cycle_count_task_model = api_ns.inherit('CycleCountTask', cycle_count_task_base_model, {
    'task_details': fields.List(
        fields.Nested(cycle_count_task_detail_model),
        description='List of associated cycle count details'
    ),
    'status_logs': fields.List(
        fields.Nested(cycle_count_task_status_log_model),
        description='Status change logs for the cycle count task'
    )
})

# ------------------------------------------------------------------------------
# CycleCount Task 盘点时候保存的input模型
# ------------------------------------------------------------------------------

cycle_count_batch_save_detail_model = api_ns.model('CycleCountTaskBatchSaveDetail', {
    'id': fields.Integer(required=True, description='Detail ID'),
    'actual_quantity': fields.Integer(description='Actual counted quantity'),
})

# 批量保存输入模型
cycle_count_task_batch_save_input_model = api_ns.model('CycleCountTaskBatchSaveInput', {
    'task_id': fields.Integer(required=True, description='任务ID'),
    'details': fields.List(
        fields.Nested(cycle_count_batch_save_detail_model),
        required=True,
        description='盘点明细列表'
    )
})


# ------------------------------------------------------------------------------
# 5. 分页解析器与分页模型
# ------------------------------------------------------------------------------
cycle_count_pagination_parser = pagination_parser.copy()
cycle_count_pagination_parser.add_argument('task_name', type=str, location='args', help='Task name filter')
cycle_count_pagination_parser.add_argument('status', type=str, location='args',choices=CycleCountTask.CYCLE_COUNT_TASK_STATUSES,help='Cycle Count Task Status')
cycle_count_pagination_parser.add_argument('is_active', type=inputs.boolean, location='args',help='Is the task active?')
cycle_count_pagination_parser.add_argument('scheduled_start_date', type=inputs.datetime_from_iso8601, location='args',help='Scheduled date/time start')
cycle_count_pagination_parser.add_argument('scheduled_end_date', type=inputs.datetime_from_iso8601, location='args',help='Scheduled date/time end')
cycle_count_pagination_parser.add_argument('warehouse_id', type=int, location='args',help='Warehouse ID')

# 根据 goods 和 location 查询 Cycle Count Task Detail 列表
cycle_count_detail_pagination_parser = pagination_parser.copy()
cycle_count_detail_pagination_parser.add_argument('status', type=str, location='args',choices=CycleCountTaskDetail.CYCLE_COUNT_TASK_DETAIL_STATUSES,help='Cycle Count Task Detail Status')
cycle_count_detail_pagination_parser.add_argument('operator_id', type=int, location='args',help='Operator ID')
cycle_count_detail_pagination_parser.add_argument('goods_id', type=int, location='args',help='Goods ID')
cycle_count_detail_pagination_parser.add_argument('location_id', type=int, location='args',help='Location ID')
cycle_count_detail_pagination_parser.add_argument('warehouse_id', type=int, required=False, help='Filter by warehouse ID', location='args')


cycle_count_pagination_model = create_pagination_model(api_ns, cycle_count_task_base_model)
cycle_count_detail_pagination_model = create_pagination_model(api_ns, cycle_count_task_detail_model)

cycle_count_monthly_stats_parser =  reqparse.RequestParser()
cycle_count_monthly_stats_parser.add_argument('months', type=int, help='Number of months to look back', default=6)
cycle_count_monthly_stats_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
