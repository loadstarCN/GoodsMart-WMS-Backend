from flask import g
from flask_restx import Resource
from extensions.error import BadRequestException
from warehouse.common import warehouse_required,add_warehouse_filter,get_warehouse_owned,require_positive_int, require_actor_user_id
from warehouse.dn.models import DN
from .models import PackingTask
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


def _owned_task(task_id: int) -> PackingTask:
    """按 id 取打包任务并经其 DN 校验仓库归属（须在 @warehouse_required() 之后调用）"""
    return get_warehouse_owned(PackingTask, task_id, warehouse_attr='dn.warehouse_id', what='Packing Task')


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
        filters = add_warehouse_filter(filters)

        query = PackingTaskService.list_tasks(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_task_input_model)
    @api_ns.marshal_with(packing_task_model)
    def post(self):
        """
        创建一个新的打包任务（只接受 dn_id；DN 必须在可访问仓库内）
        """
        data = api_ns.payload
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 16015)
        get_warehouse_owned(DN, require_positive_int(data.get('dn_id'), 'dn_id', 16044), what='DN')
        created_by = require_actor_user_id()
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
        return _owned_task(task_id), 200

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_task_input_model)
    @api_ns.marshal_with(packing_task_model)
    def put(self, task_id):
        """
        更新指定的打包任务（status / is_active / dn_id 不可由此修改）
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        updated_task = PackingTaskService.update_task(task.id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","packing_delete"])
    @warehouse_required()
    def delete(self, task_id):
        """
        删除指定的打包任务
        """
        task = _owned_task(task_id)
        PackingTaskService.delete_task(task.id)
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
        operator_id = require_actor_user_id()
        task = _owned_task(task_id)
        updated_task = PackingTaskService.process_task(task, operator_id)
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
        operator_id = require_actor_user_id()
        task = _owned_task(task_id)
        updated_task = PackingTaskService.complete_task(task, operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class PackingTaskDetailList(Resource):
    """
    打包明细只能经批次（POST /<task_id>/batches/）创建，这里只读。
    """

    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(packing_task_detail_model)
    def get(self, task_id):
        """
        获取指定打包任务的所有打包任务详情
        """
        return _owned_task(task_id).task_details


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class PackingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    @api_ns.marshal_with(packing_task_detail_model)
    def get(self, task_id, detail_id):
        """
        获取指定打包任务下的指定打包任务详情
        """
        task = _owned_task(task_id)
        return PackingTaskService.get_task_detail(task.id, detail_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_task_detail_input_model)
    @api_ns.marshal_with(packing_task_detail_model)
    def put(self, task_id, detail_id):
        """
        更新指定打包任务下的指定打包任务详情（只允许改 packed_quantity / packing_time）
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        updated_detail = PackingTaskService.update_task_detail(task.id, detail_id, data)

        return updated_detail

    @permission_required(["all_access","company_all_access","packing_delete"])
    @warehouse_required()
    def delete(self, task_id, detail_id):
        """
        删除指定打包任务下的指定打包任务详情
        """
        task = _owned_task(task_id)
        PackingTaskService.delete_task_detail(task.id, detail_id)

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
    @warehouse_required()
    @api_ns.marshal_list_with(packing_batch_model)
    def get(self, task_id):
        """
        列出指定 PackingTask 下所有的 PackingBatch
        """
        return _owned_task(task_id).batches

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_batch_input_model)
    @api_ns.marshal_with(packing_batch_model)
    def post(self, task_id):
        """
        创建新的 PackingBatch

        - 如果传入 data['details']，则同时批量创建 PackingTaskDetail
        - 商品必须在 DN 明细内，累计打包量不得超过已拣量
        - 要求对应的 PackingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        operator_id = require_actor_user_id()

        task = _owned_task(task_id)
        new_batch = PackingTaskService.create_batch(task, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class PackingBatchItem(Resource):
    """
    对应 /packing/<task_id>/batches/<batch_id>/
    """

    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    @api_ns.marshal_with(packing_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 PackingBatch
        """
        task = _owned_task(task_id)
        return PackingTaskService.get_batch(task.id, batch_id)

    @permission_required(["all_access","company_all_access","packing_edit"])
    @warehouse_required()
    @api_ns.expect(packing_batch_input_model)
    @api_ns.marshal_with(packing_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 PackingBatch
        - 要求对应的 PackingTask 必须是 in_progress 状态
        """
        data = api_ns.payload or {}
        task = _owned_task(task_id)
        updated_batch = PackingTaskService.update_batch(task.id, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","packing_delete"])
    @warehouse_required()
    def delete(self, task_id, batch_id):
        """
        删除 PackingBatch
        - 要求对应的 PackingTask 必须是 in_progress 状态
        - 若下方已有 PackingTaskDetail，可根据业务要求自动级联删除或禁止删除
        """
        task = _owned_task(task_id)
        PackingTaskService.delete_batch(task.id, batch_id)
        return {"message": "Packing Batch deleted successfully"}, 200



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class PackingMonthlyStats(Resource):
    """
    Get monthly Packing statistics
    """
    @permission_required(["all_access","company_all_access","packing_read"])
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
class PackingStatusOverviewStats(Resource):
    """
    Get Packing status overview statistics
    """
    @permission_required(["all_access","company_all_access","packing_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        packing_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = PackingTaskService.get_status_overview(filters=filters)
        return stats, 200
