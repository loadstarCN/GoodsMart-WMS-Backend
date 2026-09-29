from flask import g
from flask_restx import Resource
from warehouse.common import warehouse_required, add_warehouse_filter, get_warehouse_owned, require_fields, require_actor_user_id
from warehouse.asn.models import ASN

from .models import SortingTask
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


def _owned_task(task_id: int) -> SortingTask:
    """按 id 取分拣任务并校验其 ASN 所在仓库在调用方可访问范围内（须在 @warehouse_required() 之后调用）"""
    return get_warehouse_owned(SortingTask, task_id, warehouse_attr='asn.warehouse_id', what='Sorting Task')


def _owned_asn(asn_id) -> ASN:
    """body 里引用的 ASN 也必须在调用方可访问范围内"""
    require_fields({'asn_id': asn_id}, 'asn_id')
    return get_warehouse_owned(ASN, asn_id, what='ASN')


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
    @warehouse_required()
    @api_ns.expect(sorting_task_input_model)
    @api_ns.marshal_with(sorting_task_model)
    def post(self):
        """
        Create a new Sorting Task (status / is_active are ignored)
        """
        data = api_ns.payload
        _owned_asn(data.get('asn_id'))
        created_by = require_actor_user_id()
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
        return _owned_task(task_id), 200

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_task_input_model)
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Update a specific Sorting Task (only asn_id; status / is_active are ignored)
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        if data.get('asn_id'):
            _owned_asn(data['asn_id'])
        updated_task = SortingTaskService.update_task(task, data)
        return updated_task

    @permission_required(["all_access","company_all_access","sorting_delete"])
    @warehouse_required()
    def delete(self, task_id):
        """
        Delete a specific Sorting Task
        """
        SortingTaskService.delete_task(_owned_task(task_id))
        return {"message": "Sorting Task deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class SortingTaskProcess(Resource):

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Start a specific Sorting Task (pending -> in_progress)
        """
        operator_id = require_actor_user_id()
        updated_task = SortingTaskService.process_task(_owned_task(task_id), operator_id)
        return updated_task

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class SortingTaskComplete(Resource):

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_model)
    def put(self, task_id):
        """
        Complete a specific Sorting Task (in_progress -> completed) and its ASN
        """
        operator_id = require_actor_user_id()
        updated_task = SortingTaskService.complete_task(_owned_task(task_id), operator_id)
        return updated_task



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class SortingTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(sorting_task_detail_model)
    def get(self, task_id):
        """
        Get all Sorting Task Details for a specific task
        """
        return SortingTaskService.list_task_details(_owned_task(task_id))

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_task_detail_input_model)
    @api_ns.marshal_with(sorting_task_detail_model)
    def post(self, task_id):
        """
        Create a new Sorting Task Detail under a specific Sorting Task
        (goods must be part of the ASN; cumulative quantity must not exceed the plan)
        """
        task = _owned_task(task_id)
        created_by = require_actor_user_id()
        data = api_ns.payload
        new_detail = SortingTaskService.create_task_detail(task, data, created_by)
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class SortingTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Sorting Task Detail under a specific Sorting Task
        """
        task = _owned_task(task_id)
        return SortingTaskService.get_task_detail(task.id, detail_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_task_detail_input_model)
    @api_ns.marshal_with(sorting_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Sorting Task Detail
        """
        task = _owned_task(task_id)
        data = api_ns.payload
        updated_detail = SortingTaskService.update_task_detail(task, detail_id, data)
        return updated_detail

    @permission_required(["all_access","company_all_access","sorting_delete"])
    @warehouse_required()
    def delete(self, task_id, detail_id):
        """
        Delete a specific Sorting Task Detail
        """
        task = _owned_task(task_id)
        SortingTaskService.delete_task_detail(task, detail_id)

        return {"message": "Sorting Task Detail deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/')
class SortingBatchList(Resource):
    """
    对应 /sorting/<task_id>/batches/
    """

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(sorting_batch_model)
    def get(self, task_id):
        """
        列出指定 SortingTask 下所有的 SortingBatch
        """
        return SortingTaskService.list_batches(_owned_task(task_id))

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_batch_input_model)
    @api_ns.marshal_with(sorting_batch_model)
    def post(self, task_id):
        """
        创建新的 SortingBatch
        - 如果传入 data['details']，则同时批量创建 SortingTaskDetail
        - 要求对应的 SortingTask 必须是 in_progress 状态
        - 明细商品必须在 ASN 明细内，累计分拣量不得超过 ASN 计划量
        """
        data = api_ns.payload or {}
        operator_id = require_actor_user_id()

        task = _owned_task(task_id)
        new_batch = SortingTaskService.create_batch(task, data, operator_id)

        return new_batch, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/batches/<int:batch_id>')
class SortingBatchItem(Resource):
    """
    对应 /sorting/<task_id>/batches/<batch_id>/
    """

    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.marshal_with(sorting_batch_model)
    def get(self, task_id, batch_id):
        """
        获取单个 SortingBatch
        """
        task = _owned_task(task_id)
        return SortingTaskService.get_batch(task.id, batch_id)

    @permission_required(["all_access","company_all_access","sorting_edit"])
    @warehouse_required()
    @api_ns.expect(sorting_batch_input_model)
    @api_ns.marshal_with(sorting_batch_model)
    def put(self, task_id, batch_id):
        """
        更新 SortingBatch
        - 要求对应的 SortingTask 必须是 in_progress 状态
        """
        task = _owned_task(task_id)
        data = api_ns.payload or {}
        updated_batch = SortingTaskService.update_batch(task, batch_id, data)
        return updated_batch

    @permission_required(["all_access","company_all_access","sorting_delete"])
    @warehouse_required()
    def delete(self, task_id, batch_id):
        """
        删除 SortingBatch
        - 要求对应的 SortingTask 必须是 in_progress 状态
        - 批次下的 SortingTaskDetail 随之级联删除
        """
        task = _owned_task(task_id)
        SortingTaskService.delete_batch(task, batch_id)
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
        args = sorting_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {
            'sorting_type': args.get('sorting_type'),
        }
        filters = add_warehouse_filter(filters)
        stats = SortingTaskService.get_status_overview(filters=filters)
        return stats, 200
