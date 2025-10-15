from flask import g
from flask_restx import Resource
from extensions.error import ForbiddenException
from warehouse.common import warehouse_required,add_warehouse_filter,check_warehouse_access

from .schemas import (
    api_ns,
    sorting_task_model,
    sorting_task_detail_model,
    sorting_pagination_parser,
    pagination_model,
    sorting_task_input_model,
    sorting_task_detail_input_model,
    sorting_batch_input_model,
    sorting_batch_model,
    sorting_monthly_stats_parser
)
from .services import SortingTaskService
from system.common import permission_required
from system.common import paginate


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class SortingTaskList(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.expect(sorting_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """
        Get all Sorting Tasks with optional filters & pagination
        """
        args = sorting_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包
        filters = {
            'asn_id': args.get('asn_id'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }

        filters = add_warehouse_filter(filters)
        query = SortingTaskService.list_tasks(filters)
        return paginate(query, page, per_page),200

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @api_ns.expect(sorting_task_input_model)
    @api_ns.marshal_with(sorting_task_model)
    def post(self):
        """
        Create a new Sorting Task
        """
        data = api_ns.payload
        created_by = g.current_user.id
        new_task = SortingTaskService.create_task(data, created_by)
        return new_task, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>')
class SortingTaskDetailView(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_model)
    def get(self, task_id):
        """
        Get details of a specific Sorting Task
        """
        task = SortingTaskService.get_task(task_id)
        if not check_warehouse_access(task.asn.warehouse_id):
            raise ForbiddenException("You do not have access to this Sorting Task", 12001)
        return task, 200

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @api_ns.expect(sorting_task_input_model)
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Update a specific Sorting Task
        """
        data = api_ns.payload
        updated_task = SortingTaskService.update_task(task_id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","sorting_delete"])
    def delete(self, task_id):
        """
        Delete a specific Sorting Task
        """
        SortingTaskService.delete_task(task_id)
        return {"message": "Sorting Task deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class SortingTaskProcess(Resource):
    
    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Update a specific Sorting Task
        """
        operator_id = g.current_user.id
        updated_task = SortingTaskService.get_task(task_id)
        if not check_warehouse_access(updated_task.asn.warehouse_id):
            raise ForbiddenException("You do not have access to this Sorting Task", 12001)
        updated_task = SortingTaskService.process_task(updated_task, operator_id)
        return updated_task
    
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class SortingTaskComplete(Resource):
    
    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Update a specific Sorting Task
        """
        operator_id = g.current_user.id
        updated_task = SortingTaskService.get_task(task_id)
        if not check_warehouse_access(updated_task.asn.warehouse_id):
            raise ForbiddenException("You do not have access to this Sorting Task", 12001)
        
        updated_task = SortingTaskService.complete_task(updated_task, operator_id)
        return updated_task



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class SortingTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @api_ns.marshal_list_with(sorting_task_detail_model)
    def get(self, task_id):
        """
        Get all Sorting Task Details for a specific task
        """
        return SortingTaskService.list_task_details(task_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @api_ns.expect(sorting_task_detail_input_model)
    @api_ns.marshal_with(sorting_task_detail_model)
    def post(self, task_id):
        """
        Create a new Sorting Task Detail under a specific Sorting Task
        """
        created_by = g.current_user.id
        data = api_ns.payload
        new_detail = SortingTaskService.create_task_detail(task_id, data, created_by)
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class SortingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @api_ns.marshal_with(sorting_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Sorting Task Detail under a specific Sorting Task
        """
        return SortingTaskService.get_task_detail(task_id, detail_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @api_ns.expect(sorting_task_detail_input_model)
    @api_ns.marshal_with(sorting_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Sorting Task Detail
        """
        data = api_ns.payload
        updated_detail = SortingTaskService.update_task_detail(task_id, detail_id, data)
        return updated_detail

    @permission_required(["all_access","company_all_access","sorting_delete"])
    def delete(self, task_id, detail_id):
        """
        Delete a specific Sorting Task Detail
        """
        SortingTaskService.delete_task_detail(task_id, detail_id)

        return {"message": "Sorting Task Detail deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/')
class SortingBatchList(Resource):
    """
    对应 /sorting/<task_id>/batches/
    """

    @permission_required(["all_access","company_all_access","sorting_read"])
    @api_ns.marshal_list_with(sorting_batch_model)
    def get(self, task_id):
        """
        列出指定 SortingTask 下所有的 SortingBatch
        """
        return SortingTaskService.list_batches(task_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_batch_input_model)
    @api_ns.marshal_with(sorting_batch_model)
    def post(self, task_id):
        """
        创建新的 SortingBatch
        - 如果传入 data['details']，则同时批量创建 SortingTaskDetail
        - 要求对应的 SortingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        operator_id = g.current_user.id


        task = SortingTaskService.get_task(task_id)
        if not check_warehouse_access(task.asn.warehouse_id):
            raise ForbiddenException("You do not have access to this Sorting Task", 12001)

        new_batch = SortingTaskService.create_batch(task_id, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class SortingBatchItem(Resource):
    """
    对应 /sorting/<task_id>/batches/<batch_id>/
    """

    @permission_required(["all_access","company_all_access","sorting_read"])
    @api_ns.marshal_with(sorting_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 SortingBatch
        """
        return SortingTaskService.get_batch(task_id, batch_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @api_ns.expect(sorting_batch_input_model)
    @api_ns.marshal_with(sorting_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 SortingBatch
        - 要求对应的 SortingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        updated_batch = SortingTaskService.update_batch(task_id, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","sorting_delete"])
    def delete(self, task_id, batch_id):
        """
        删除 SortingBatch
        - 要求对应的 SortingTask 必须是 in_progress 状态
        - 若下方已有 SortingTaskDetail，可根据业务要求自动级联删除或禁止删除
        """
        SortingTaskService.delete_batch(task_id, batch_id)
        return {"message": "Sorting Batch deleted successfully"}, 200
    

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class SortingMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.expect(sorting_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = sorting_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
            'sorting_type': args.get('sorting_type')
        }

        filters = add_warehouse_filter(filters)
        stats = SortingTaskService.get_sorting_monthly_stats(months=months,filters=filters)
        return stats, 200
    
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class SortingStatusOverviewStats(Resource):
    """
    Get Sorting status overview statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        # 解析请求参数
        args = sorting_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {
            'sorting_type': args.get('sorting_type'),
        }
        filters = add_warehouse_filter(filters)
        stats = SortingTaskService.get_status_overview(filters=filters)
        return stats, 200
