from flask import g
from flask_restx import Resource,abort
from extensions.db import get_object_or_404
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import permission_required,paginate
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter,
    require_warehouse_scope, require_same_warehouse, get_warehouse_owned, require_fields,
)
from warehouse.goods.services import GoodsLocationService, GoodsService
from warehouse.location.models import Location
from .models import CycleCountTask
from .schemas import (
    api_ns,
    cycle_count_task_model,
    cycle_count_task_detail_model,
    cycle_count_pagination_parser,
    cycle_count_pagination_model,
    cycle_count_task_input_model,
    cycle_count_task_detail_input_model,
    cycle_count_detail_pagination_parser,
    cycle_count_detail_pagination_model,
    cycle_count_monthly_stats_parser,
    cycle_count_task_batch_save_input_model,
    cycle_count_monthly_stats_parser
)
from .services import CycleCountTaskService


def _get_owned_task(task_id: int) -> CycleCountTask:
    """按 id 取盘点任务并校验仓库归属（须在 @warehouse_required() 之后）"""
    return get_warehouse_owned(CycleCountTask, task_id, what='cycle count task')


def _assert_location_in_warehouse(warehouse_id, location_id):
    """明细里的库位必须存在且与任务同仓库（location_id 在明细上 NOT NULL，缺失直接 400）"""
    require_fields({'location_id': location_id}, 'location_id')
    location = get_object_or_404(Location, location_id)
    require_same_warehouse(warehouse_id, location.warehouse_id, 'location')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class CycleCountTaskList(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.expect(cycle_count_pagination_parser)
    @api_ns.marshal_with(cycle_count_pagination_model)
    def get(self):
        """
        Get all Cycle Count Tasks with optional filters & pagination
        """
        args = cycle_count_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包
        filters = {
            'task_name': args.get('task_name'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            'scheduled_date': args.get('scheduled_date'),
            'warehouse_id': args.get('warehouse_id'),
            'keyword': args.get('keyword')
        }

        filters = add_warehouse_filter(filters)
        query = CycleCountTaskService.list_tasks(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.expect(cycle_count_task_input_model)
    @api_ns.marshal_with(cycle_count_task_model)
    def post(self):
        """
        Create a new Cycle Count Task
        """
        data = api_ns.payload
        created_by = require_actor_user_id()

        # 请求体里的 warehouse_id 必须是调用方可访问的仓库
        require_fields(data, 'warehouse_id')
        require_warehouse_scope(data['warehouse_id'], 'cycle count task')
        for detail_data in data.get('task_details') or []:
            _assert_location_in_warehouse(data['warehouse_id'], detail_data.get('location_id'))

        new_task = CycleCountTaskService.create_task(data, created_by)
        return new_task, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>')
class CycleCountTaskDetailView(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.marshal_with(cycle_count_task_model)
    def get(self, task_id):
        """
        Get details of a specific Cycle Count Task
        """
        return _get_owned_task(task_id), 200

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.expect(cycle_count_task_input_model)
    @api_ns.marshal_with(cycle_count_task_model)
    def put(self, task_id):
        """
        Update a specific Cycle Count Task
        """
        data = api_ns.payload
        _get_owned_task(task_id)
        updated_task = CycleCountTaskService.update_task(task_id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","cycle_count_delete"])
    @warehouse_required()
    def delete(self, task_id):
        """
        Delete a specific Cycle Count Task
        """
        _get_owned_task(task_id)
        CycleCountTaskService.delete_task(task_id)

        return {"message": "Cycle Count Task deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class CycleCountTaskProcess(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.marshal_with(cycle_count_task_model)
    def put(self, task_id):
        """
        Update a specific Cycle Count Task to 'in_progress'
        """
        operator_id = require_actor_user_id()

        task = _get_owned_task(task_id)

        updated_task = CycleCountTaskService.process_task(task,operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class CycleCountTaskComplete(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.marshal_with(cycle_count_task_model)
    def put(self, task_id):
        """
        Complete a specific Cycle Count Task
        """
        operator_id = require_actor_user_id()

        task = _get_owned_task(task_id)

        updated_task = CycleCountTaskService.complete_task(task,operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class CycleCountTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(cycle_count_task_detail_model)
    def get(self, task_id):
        """
        Get all Cycle Count Task Details for a specific task
        """
        _get_owned_task(task_id)
        return CycleCountTaskService.list_task_details(task_id)

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.expect(cycle_count_task_detail_input_model)
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def post(self, task_id):
        """
        Create a new Cycle Count Task Detail under a specific Cycle Count Task
        """
        data = api_ns.payload
        created_by = require_actor_user_id()

        # 任务归属 + 库位必须在任务所在仓库 + 商品必须在该库位上有存放记录
        task = _get_owned_task(task_id)
        require_fields(data, 'goods_id', 'location_id')
        goods_id = data['goods_id']
        location_id = data['location_id']
        _assert_location_in_warehouse(task.warehouse_id, location_id)
        if not GoodsLocationService.is_goods_in_location(goods_id, location_id):
            raise NotFoundException("Goods not found in the specified location", 13003)

        new_detail = CycleCountTaskService.create_task_detail(task_id, data, created_by)

        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class CycleCountTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Cycle Count Task Detail under a specific Cycle Count Task
        """
        _get_owned_task(task_id)
        return CycleCountTaskService.get_task_detail(task_id, detail_id)


    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.expect(cycle_count_task_detail_input_model)
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Cycle Count Task Detail
        """
        data = api_ns.payload
        task = _get_owned_task(task_id)
        if 'location_id' in data:
            _assert_location_in_warehouse(task.warehouse_id, data['location_id'])
        updated_detail = CycleCountTaskService.update_task_detail(task_id, detail_id, data)

        return updated_detail

    @permission_required(["all_access","company_all_access","cycle_count_delete"])
    @warehouse_required()
    def delete(self, task_id, detail_id):
        """
        Delete a specific Cycle Count Task Detail
        """
        _get_owned_task(task_id)
        CycleCountTaskService.delete_task_detail(task_id, detail_id)

        return {"message": "Cycle Count Task Detail deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details-batch-save/')
class CycleCountTaskDetailBatchSave(Resource):
    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.expect(cycle_count_task_batch_save_input_model)
    @api_ns.marshal_with(cycle_count_task_model)
    def post(self, task_id):
        """
        Batch save Cycle Count Task Details
        """
        data = api_ns.payload
        created_by = require_actor_user_id()

        _get_owned_task(task_id)

        if 'details' not in data:
            raise BadRequestException("Missing 'details' in request data", 14004)
        else:
            if data['details'] is None or len(data['details']) == 0:
                raise BadRequestException("No details provided in request data", 14005)

        new_task = CycleCountTaskService.batch_save_task_details(task_id, data['details'], created_by)

        return new_task, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>/complete/')
class CycleCountTaskDetailComplete(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @warehouse_required()
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Complete a specific Cycle Count Task Detail
        """
        operator_id = require_actor_user_id()

        _get_owned_task(task_id)

        updated_detail = CycleCountTaskService.complete_task_detail(task_id, detail_id, operator_id)

        return updated_detail


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/details/search/')
class CycleCountTaskDetailSearch(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.expect(cycle_count_detail_pagination_parser)
    @api_ns.marshal_with(cycle_count_detail_pagination_model)
    def get(self):
        """
        Search Cycle Count Task Details with optional filters & pagination
        """
        args = cycle_count_detail_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包
        filters = {
            'status': args.get('status'),
            'operator_id': args.get('operator_id'),
            'goods_id': args.get('goods_id'),
            'location_id': args.get('location_id'),
        }

        filters = add_warehouse_filter(filters)
        query = CycleCountTaskService.search_task_details(filters)
        return paginate(query, page, per_page)



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class CycleCountMonthlyStats(Resource):
    """
    Get monthly Cycle Count statistics
    """
    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    @api_ns.expect(cycle_count_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = cycle_count_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
        }

        filters = add_warehouse_filter(filters)
        stats = CycleCountTaskService.get_cyclecount_monthly_stats(months=months,filters=filters)
        return stats, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class CycleCountStatusOverviewStats(Resource):
    """
    Get Cycle Count status overview statistics
    """
    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @warehouse_required()
    def get(self):
        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = CycleCountTaskService.get_status_overview(filters=filters)
        return stats, 200
