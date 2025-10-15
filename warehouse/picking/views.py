from flask import g
from flask_restx import Resource
from extensions.error import ForbiddenException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,add_warehouse_filter,check_warehouse_access

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
    @api_ns.expect(picking_task_input_model)
    @api_ns.marshal_with(picking_task_model)
    def post(self):
        """
        Create a new Picking Task
        """
        data = api_ns.payload
        created_by = g.current_user.id
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
        task = PickingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Picking Task", 12001)
        return task, 200

    @permission_required(["all_access","company_all_access","picking_edit"])
    @api_ns.expect(picking_task_input_model)
    @api_ns.marshal_with(picking_task_model)
    def put(self, task_id):
        """
        Update a specific Picking Task
        """
        data = api_ns.payload
        updated_task = PickingTaskService.update_task(task_id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","picking_delete"])
    def delete(self, task_id):
        """
        Delete a specific Picking Task
        """
        PickingTaskService.delete_task(task_id)
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
        operator_id = g.current_user.id

        task = PickingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Picking Task", 12001) 
        updated_task = PickingTaskService.process_task(task_id,operator_id)
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
        operator_id = g.current_user.id
        task = PickingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Picking Task", 12001)
        updated_task = PickingTaskService.complete_task(task_id,operator_id)
        return updated_task



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class PickingTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","picking_read"])
    @api_ns.marshal_list_with(picking_task_detail_model)
    def get(self, task_id):
        """
        Get all Picking Task Details for a specific task
        """
        return PickingTaskService.list_task_details(task_id)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @api_ns.expect(picking_task_detail_input_model)
    @api_ns.marshal_with(picking_task_detail_model)
    def post(self, task_id):
        """
        Create a new Picking Task Detail under a specific Picking Task
        """
        created_by = g.current_user.id
        data = api_ns.payload
        new_detail = PickingTaskService.create_task_detail(task_id, data, created_by)
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class PickingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","picking_read"])
    @api_ns.marshal_with(picking_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Picking Task Detail under a specific Picking Task
        """
        return PickingTaskService.get_task_detail(task_id, detail_id)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @api_ns.expect(picking_task_detail_input_model)
    @api_ns.marshal_with(picking_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Picking Task Detail
        """
        data = api_ns.payload
        updated_detail = PickingTaskService.update_task_detail(task_id, detail_id, data)
        return updated_detail

    @permission_required(["all_access","company_all_access","picking_delete"])
    def delete(self, task_id, detail_id):
        """
        Delete a specific Picking Task Detail
        """
        PickingTaskService.delete_task_detail(task_id, detail_id)

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
    @api_ns.marshal_list_with(picking_batch_model)
    def get(self, task_id):
        """
        列出指定 PickingTask 下所有的 PickingBatch
        """
        return PickingTaskService.list_batches(task_id)

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
        operator_id = g.current_user.id

        task = PickingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Picking Task", 12001)
        new_batch = PickingTaskService.create_batch(task_id, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class PickingBatchItem(Resource):
    """
    对应 /picking/<task_id>/batches/<batch_id>/
    """
    @permission_required(["all_access","company_all_access","picking_read"])
    @api_ns.marshal_with(picking_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 PickingBatch
        """
        return PickingTaskService.get_batch(task_id, batch_id)

    @permission_required(["all_access","company_all_access","picking_edit"])
    @api_ns.expect(picking_batch_input_model)
    @api_ns.marshal_with(picking_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 PickingBatch
        - 要求对应的 PickingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        updated_batch = PickingTaskService.update_batch(task_id, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","picking_delete"])
    def delete(self, task_id, batch_id):
        """
        删除 PickingBatch
        - 要求对应的 PickingTask 必须是 in_progress 状态
        - 若下方已有 PickingTaskDetail，可根据业务要求自动级联删除或禁止删除
        """
        PickingTaskService.delete_batch(task_id, batch_id)
        return {"message": "Picking Batch deleted successfully"}, 200
    


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class PickingMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
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
    Get Sorting status overview statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        # 解析请求参数
        args = picking_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = PickingTaskService.get_status_overview(filters=filters)
        return stats, 200
