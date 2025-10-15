from flask import g
from flask_restx import Resource,abort
from extensions.error import ForbiddenException
from warehouse.common import warehouse_required,add_warehouse_filter,check_warehouse_access
from .schemas import (
    api_ns,
    packing_task_model,
    packing_task_detail_model,
    packing_pagination_parser,
    pagination_model,
    packing_task_input_model,
    packing_task_detail_input_model,
    packing_batch_model,
    packing_batch_input_model,
    packing_monthly_stats_parser
)
from .services import PackingTaskService
from system.common import permission_required
from system.common import paginate


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class PackingTaskList(Resource):

    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    @api_ns.expect(packing_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """
        获取所有打包任务，并支持过滤和分页
        """
        args = packing_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 打包筛选参数
        filters = {
            'dn_id': args.get('dn_id'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }

        query = PackingTaskService.list_tasks(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @api_ns.expect(packing_task_input_model)
    @api_ns.marshal_with(packing_task_model)
    def post(self):
        """
        创建一个新的打包任务
        """
        data = api_ns.payload
        created_by = g.current_user.id
        new_task = PackingTaskService.create_task(data, created_by)
        return new_task, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>')
class PackingTaskDetailView(Resource):

    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    @api_ns.marshal_with(packing_task_model)
    def get(self, task_id):
        """
        获取指定打包任务的详细信息
        """
        task = PackingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Packing Task", 12001)

        return task, 200

    @permission_required(["all_access","company_all_access","packing_edit"])
    @api_ns.expect(packing_task_input_model)
    @api_ns.marshal_with(packing_task_model)
    def put(self, task_id):
        """
        更新指定的打包任务
        """
        data = api_ns.payload
        updated_task = PackingTaskService.update_task(task_id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","packing_delete"])
    def delete(self, task_id):
        """
        删除指定的打包任务
        """
        PackingTaskService.delete_task(task_id)
        return {"message": "Packing Task deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class PackingTaskProcess(Resource):

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.marshal_with(packing_task_model)
    def put(self, task_id):
        """
        处理指定的打包任务
        """
        operator_id = g.current_user.id

        updated_task = PackingTaskService.get_task(task_id)
        if not check_warehouse_access(updated_task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Packing Task", 12001)
        
        updated_task = PackingTaskService.process_task(task_id,operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class PackingTaskComplete(Resource):

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.marshal_with(packing_task_model)
    def put(self, task_id):
        """
        完成指定的打包任务
        """
        operator_id = g.current_user.id

        updated_task = PackingTaskService.get_task(task_id)
        if not check_warehouse_access(updated_task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Packing Task", 12001)
        
        updated_task = PackingTaskService.complete_task(task_id,operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class PackingTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","packing_read"])
    @api_ns.marshal_list_with(packing_task_detail_model)
    def get(self, task_id):
        """
        获取指定打包任务的所有打包任务详情
        """
        return PackingTaskService.list_task_details(task_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @api_ns.expect(packing_task_detail_input_model)
    @api_ns.marshal_with(packing_task_detail_model)
    def post(self, task_id):
        """
        创建指定打包任务的打包任务详情
        """
        created_by = g.current_user.id
        data = api_ns.payload
        new_detail = PackingTaskService.create_task_detail(task_id, data, created_by)
        
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class PackingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","packing_read"])
    @api_ns.marshal_with(packing_task_detail_model)
    def get(self, task_id, detail_id):
        """
        获取指定打包任务下的指定打包任务详情
        """
        return PackingTaskService.get_task_detail(task_id, detail_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @api_ns.expect(packing_task_detail_input_model)
    @api_ns.marshal_with(packing_task_detail_model)
    def put(self, task_id, detail_id):
        """
        更新指定打包任务下的指定打包任务详情
        """
        data = api_ns.payload
        updated_detail = PackingTaskService.update_task_detail(task_id, detail_id, data)
        
        return updated_detail

    @permission_required(["all_access","company_all_access","packing_delete"])
    def delete(self, task_id, detail_id):
        """
        删除指定打包任务下的指定打包任务详情
        """
        PackingTaskService.delete_task_detail(task_id, detail_id)
        
        return {"message": "Packing Task Detail deleted successfully"}, 200


# ------------------------------------------------------------------------------
# 批次管理接口
# ------------------------------------------------------------------------------
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/')
class PackingBatchList(Resource):
    """
    对应 /packing/<task_id>/batches/
    """
    @permission_required(["all_access","company_all_access","packing_read"])
    @api_ns.marshal_list_with(packing_batch_model)
    def get(self, task_id):
        """
        列出指定 PackingTask 下所有的 PackingBatch
        """
        return PackingTaskService.list_batches(task_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_batch_input_model)
    @api_ns.marshal_with(packing_batch_model)
    def post(self, task_id):
        """
        创建新的 PackingBatch

        - 如果传入 data['details']，则同时批量创建 PackingTaskDetail
        - 要求对应的 PackingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        operator_id = g.current_user.id

        task = PackingTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Packing Task", 12001)

        new_batch = PackingTaskService.create_batch(task_id, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class PackingBatchItem(Resource):
    """
    对应 /packing/<task_id>/batches/<batch_id>/
    """

    @permission_required(["all_access","company_all_access","packing_read"])
    @api_ns.marshal_with(packing_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 PackingBatch
        """
        return PackingTaskService.get_batch(task_id, batch_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @api_ns.expect(packing_batch_input_model)
    @api_ns.marshal_with(packing_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 PackingBatch
        - 要求对应的 PackingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        updated_batch = PackingTaskService.update_batch(task_id, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","packing_delete"])
    def delete(self, task_id, batch_id):
        """
        删除 PackingBatch
        - 要求对应的 PackingTask 必须是 in_progress 状态
        - 若下方已有 PackingTaskDetail，可根据业务要求自动级联删除或禁止删除
        """
        PackingTaskService.delete_batch(task_id, batch_id)
        return {"message": "Packing Batch deleted successfully"}, 200



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class PackingMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.expect(packing_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = packing_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
        }

        filters = add_warehouse_filter(filters)
        stats = PackingTaskService.get_packing_monthly_stats(months=months,filters=filters)
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
        args = packing_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = PackingTaskService.get_status_overview(filters=filters)
        return stats, 200
