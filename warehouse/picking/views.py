from flask import g
from flask_restx import Resource
from extensions.error import BadRequestException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,add_warehouse_filter,get_warehouse_owned,require_positive_int, require_actor_user_id
from warehouse.dn.models import DN

from .models import PickingTask
from .schemas import (
    api_ns,
    picking_task_model,
    picking_task_detail_model,
    picking_pagination_parser,
    pagination_model,
    picking_task_input_model,
    picking_task_detail_input_model,
    picking_batch_model,
    picking_batch_input_model,
    picking_monthly_stats_parser
)

from .services import PickingTaskService


def _owned_task(task_id: int) -> PickingTask:
    """按 id 取拣货任务并经其 DN 校验仓库归属（须在 @warehouse_required() 之后调用）"""
    return get_warehouse_owned(PickingTask, task_id, warehouse_attr='dn.warehouse_id', what='Picking Task')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class PickingTaskList(Resource):

    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.expect(picking_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """
        Get all Picking Tasks with optional filters & pagination
        """
        args = picking_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包
        filters = {
            'dn_id': args.get('dn_id'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }

        filters = add_warehouse_filter(filters)
        query = PickingTaskService.list_tasks(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.expect(picking_task_input_model)
    @api_ns.marshal_with(picking_task_model)
    def post(self):
        """
        Create a new Picking Task (only `dn_id` is accepted; the DN must be accessible)
        """
        data = api_ns.payload
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 16015)
        get_warehouse_owned(DN, require_positive_int(data.get('dn_id'), 'dn_id', 16044), what='DN')
        created_by = require_actor_user_id()
        new_task = PickingTaskService.create_task(data, created_by)
        return new_task, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>')
class PickingTaskDetailView(Resource):

    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.marshal_with(picking_task_model)
    def get(self, task_id):
        """
        Get details of a specific Picking Task
        """
        return _owned_task(task_id), 200

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.expect(picking_task_input_model)
    @api_ns.marshal_with(picking_task_model)
    def put(self, task_id):
        """
        Update a specific Picking Task (status / is_active / dn_id are not editable)
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        updated_task = PickingTaskService.update_task(task.id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","picking_delete"])
    @warehouse_required()
    def delete(self, task_id):
        """
        Delete a specific Picking Task
        """
        task = _owned_task(task_id)
        PickingTaskService.delete_task(task.id)
        return {"message": "Picking Task deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class PickingTaskProcess(Resource):

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.marshal_with(picking_task_model)
    def put(self, task_id):
        """
        Process a specific Picking Task
        """
        operator_id = require_actor_user_id()
        task = _owned_task(task_id)
        updated_task = PickingTaskService.process_task(task, operator_id)
        return updated_task

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class PickingTaskComplete(Resource):

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.marshal_with(picking_task_model)
    def put(self, task_id):
        """
        Complete a specific Picking Task
        """
        operator_id = require_actor_user_id()
        task = _owned_task(task_id)
        updated_task = PickingTaskService.complete_task(task, operator_id)
        return updated_task



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class PickingTaskDetailList(Resource):
    """
    拣货明细只能经批次（POST /<task_id>/batches/）创建，这里只读。
    """

    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(picking_task_detail_model)
    def get(self, task_id):
        """
        Get all Picking Task Details for a specific task
        """
        return _owned_task(task_id).task_details


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class PickingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.marshal_with(picking_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Picking Task Detail under a specific Picking Task
        """
        task = _owned_task(task_id)
        return PickingTaskService.get_task_detail(task.id, detail_id)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.expect(picking_task_detail_input_model)
    @api_ns.marshal_with(picking_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Picking Task Detail (only `picked_quantity` can change)
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        updated_detail = PickingTaskService.update_task_detail(task.id, detail_id, data)
        return updated_detail

    @permission_required(["all_access","company_all_access","picking_delete"])
    @warehouse_required()
    def delete(self, task_id, detail_id):
        """
        Delete a specific Picking Task Detail
        """
        task = _owned_task(task_id)
        PickingTaskService.delete_task_detail(task.id, detail_id)

        return {"message": "Picking Task Detail deleted successfully"}, 200


# ------------------------------------------------------------------------------
#  新增批次管理接口
# ------------------------------------------------------------------------------
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/')
class PickingBatchList(Resource):
    """
    对应 /picking/<task_id>/batches/
    """

    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(picking_batch_model)
    def get(self, task_id):
        """
        列出指定 PickingTask 下所有的 PickingBatch
        """
        return _owned_task(task_id).batches

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.expect(picking_batch_input_model)
    @api_ns.marshal_with(picking_batch_model)
    def post(self, task_id):
        """
        创建新的 PickingBatch

        - 如果传入 data['details']，则同时批量创建 PickingTaskDetail
        - 要求对应的 PickingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        operator_id = require_actor_user_id()

        task = _owned_task(task_id)
        new_batch = PickingTaskService.create_batch(task, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class PickingBatchItem(Resource):
    """
    对应 /picking/<task_id>/batches/<batch_id>/
    """
    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.marshal_with(picking_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 PickingBatch
        """
        task = _owned_task(task_id)
        return PickingTaskService.get_batch(task.id, batch_id)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @warehouse_required()
    @api_ns.expect(picking_batch_input_model)
    @api_ns.marshal_with(picking_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 PickingBatch
        - 要求对应的 PickingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        task = _owned_task(task_id)
        updated_batch = PickingTaskService.update_batch(task.id, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","picking_delete"])
    @warehouse_required()
    def delete(self, task_id, batch_id):
        """
        删除 PickingBatch
        - 要求对应的 PickingTask 必须是 in_progress 状态
        - 若下方已有 PickingTaskDetail，可根据业务要求自动级联删除或禁止删除
        """
        task = _owned_task(task_id)
        PickingTaskService.delete_batch(task.id, batch_id)
        return {"message": "Picking Batch deleted successfully"}, 200



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class PickingMonthlyStats(Resource):
    """
    Get monthly Picking statistics
    """
    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    @api_ns.expect(picking_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = picking_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
        }

        filters = add_warehouse_filter(filters)
        stats = PickingTaskService.get_picking_monthly_stats(months=months,filters=filters)
        return stats, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class PickingStatusOverviewStats(Resource):
    """
    Get Picking status overview statistics
    """
    @permission_required(["all_access","company_all_access","picking_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        picking_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = PickingTaskService.get_status_overview(filters=filters)
        return stats, 200
