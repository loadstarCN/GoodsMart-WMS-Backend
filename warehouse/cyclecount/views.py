from flask import g
from flask_restx import Resource,abort
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,check_warehouse_access, check_goods_access, check_location_access,add_warehouse_filter
from warehouse.goods.services import GoodsLocationService, GoodsService
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
    @api_ns.expect(cycle_count_task_input_model)
    @api_ns.marshal_with(cycle_count_task_model)
    def post(self):
        """
        Create a new Cycle Count Task
        """
        data = api_ns.payload
        created_by = g.current_user.id
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
        task = CycleCountTaskService.get_task(task_id)
        if not check_warehouse_access(task.warehouse_id):
            raise ForbiddenException("You do not have access to this task", 12001)
        
        return task, 200
    
    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @api_ns.expect(cycle_count_task_input_model)
    @api_ns.marshal_with(cycle_count_task_model)
    def put(self, task_id):
        """
        Update a specific Cycle Count Task
        """
        data = api_ns.payload
        updated_task = CycleCountTaskService.update_task(task_id, data)
        return updated_task

    @permission_required(["all_access","company_all_access","cycle_count_delete"])
    def delete(self, task_id):
        """
        Delete a specific Cycle Count Task
        """
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
        operator_id = g.current_user.id

        task = CycleCountTaskService.get_task(task_id)
        if not check_warehouse_access(task.warehouse_id):
            raise ForbiddenException("You do not have access to this task", 12001) 
        
        updated_task = CycleCountTaskService.process_task(task_id,operator_id)
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
        operator_id = g.current_user.id

        task = CycleCountTaskService.get_task(task_id)
        if not check_warehouse_access(task.warehouse_id):
            raise ForbiddenException("You do not have access to this task", 12001)
        
        updated_task = CycleCountTaskService.complete_task(task_id,operator_id)
        return updated_task


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/')
class CycleCountTaskDetailList(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @api_ns.marshal_list_with(cycle_count_task_detail_model)
    def get(self, task_id):
        """
        Get all Cycle Count Task Details for a specific task
        """
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
        created_by = g.current_user.id

        # 需要判断商品和库位是否存在，以及是否是在允许的仓库中
        goods_id = data.get('goods_id')
        location_id = data.get('location_id')        
        if not GoodsLocationService.is_goods_in_location(goods_id, location_id):
            raise NotFoundException("Goods not found in the specified location", 13003)
       
        # 验证员工用户对指定商品的访问权限
        if not check_goods_access(goods_id):
            raise ForbiddenException("You do not have access to this Goods", 12001)
        
        # 验证员工用户对指定库位的访问权限
        if not check_location_access(location_id):
            raise ForbiddenException("You do not have access to this Location", 12001)
        
        new_detail = CycleCountTaskService.create_task_detail(task_id, data, created_by)

        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/details/<int:detail_id>')
class CycleCountTaskDetailItem(Resource):

    @permission_required(["all_access","company_all_access","cycle_count_read"])
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def get(self, task_id, detail_id):
        """
        Get a specific Cycle Count Task Detail under a specific Cycle Count Task
        """
        return CycleCountTaskService.get_task_detail(task_id, detail_id)


    @permission_required(["all_access","company_all_access","cycle_count_edit"])
    @api_ns.expect(cycle_count_task_detail_input_model)
    @api_ns.marshal_with(cycle_count_task_detail_model)
    def put(self, task_id, detail_id):
        """
        Update a specific Cycle Count Task Detail
        """
        data = api_ns.payload
        updated_detail = CycleCountTaskService.update_task_detail(task_id, detail_id, data)
        
        return updated_detail

    @permission_required(["all_access","company_all_access","cycle_count_delete"])
    def delete(self, task_id, detail_id):
        """
        Delete a specific Cycle Count Task Detail
        """
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
        created_by = g.current_user.id

        task = CycleCountTaskService.get_task(task_id)
        if not check_warehouse_access(task.warehouse_id):
            raise ForbiddenException("You do not have access to this task", 12001)
        
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
        operator_id = g.current_user.id

        detail = CycleCountTaskService.get_task_detail(task_id, detail_id)        
        if not check_warehouse_access(detail.task.warehouse_id):
            raise ForbiddenException("You do not have access to this task", 12001)
        
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
class PackingMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
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
class PickingStatusOverviewStats(Resource):
    """
    Get Sorting status overview statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        # 解析请求参数
        args = cycle_count_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = CycleCountTaskService.get_status_overview(filters=filters)
        return stats, 200
