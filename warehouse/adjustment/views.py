from flask import g
from flask_restx import Resource,abort
from extensions.error import ForbiddenException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,add_warehouse_filter
from warehouse.common.permissions import check_warehouse_access
from warehouse.cyclecount.services import CycleCountTaskService

from .schemas import (
    api_ns,
    adjustment_model,
    adjustment_detail_model,
    adjustment_pagination_model,
    adjustment_input_model,
    adjustment_detail_input_model,
    adjustment_pagination_parser,
    adjustment_detail_pagination_parser,
    adjustment_detail_pagination_model,
    adjustment_monthly_stats_parser
)
from .services import AdjustmentService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class AdjustmentList(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @warehouse_required()
    @api_ns.expect(adjustment_pagination_parser)
    @api_ns.marshal_with(adjustment_pagination_model)
    def get(self):
        """
        Get all Adjustments with optional filters & pagination
        """
        args = adjustment_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'adjustment_reason': args.get('adjustment_reason'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            'created_by': args.get('created_by'),
            'warehouse_id': args.get('warehouse_id')
        }
        filters = add_warehouse_filter(filters)

        query = AdjustmentService.list_adjustments(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @api_ns.expect(adjustment_input_model)
    @api_ns.marshal_with(adjustment_model)
    def post(self):
        """
        Create a new Adjustment
        """
        data = api_ns.payload
        created_by = g.current_user.id
        new_adjustment = AdjustmentService.create_adjustment(data, created_by)
        return new_adjustment, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>')
class AdjustmentDetailView(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_model)
    def get(self, adjustment_id):
        """
        Get details of a specific Adjustment
        """
        adjustment = AdjustmentService.get_adjustment(adjustment_id)
        if not check_warehouse_access(adjustment.warehouse_id):
            raise ForbiddenException("You do not have access to this warehouse.", 12001)
        return adjustment

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @api_ns.expect(adjustment_input_model)
    @api_ns.marshal_with(adjustment_model)
    def put(self, adjustment_id):
        """
        Update a specific Adjustment
        """
        data = api_ns.payload
        updated_adjustment = AdjustmentService.update_adjustment(adjustment_id, data)
        return updated_adjustment

    @permission_required(["all_access","company_all_access","adjustment_delete"])
    def delete(self, adjustment_id):
        """
        Delete a specific Adjustment
        """
        AdjustmentService.delete_adjustment(adjustment_id)
        return {"message": "Adjustment deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/complete/')
class AdjustmentComplete(Resource):
    
    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_model)
    def put(self, adjustment_id):
        """
        Complete a specific Adjustment
        """
        operator_id = g.current_user.id
        adjustment = AdjustmentService.get_adjustment(adjustment_id)            
        if not check_warehouse_access(adjustment.warehouse_id):
            raise ForbiddenException("You do not have access to this warehouse.", 12001)
            raise ForbiddenException("You do not have access to this warehouse.", 12001)

        updated_adjustment = AdjustmentService.complete_adjustment(adjustment, operator_id)
        return updated_adjustment
    

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/approve/')
class AdjustmentComplete(Resource):
    
    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_model)
    def put(self, adjustment_id):
        """
        Complete a specific Adjustment
        """
        operator_id = g.current_user.id
        
        adjustment = AdjustmentService.get_adjustment(adjustment_id)            
        if not check_warehouse_access(adjustment.warehouse_id):
            raise ForbiddenException("You do not have access to this warehouse.", 12001)
        
        updated_adjustment = AdjustmentService.approve_adjustment(adjustment,operator_id)

        return updated_adjustment

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/create_adjustment_by_cyclecount/<int:cyclecount_id>')
class AdjustmentCreate(Resource):

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.expect(adjustment_input_model)
    @api_ns.marshal_with(adjustment_model)
    def post(self, cyclecount_id):
        """
        Create a new Adjustment from a Cycle Count
        """
        created_by = g.current_user.id
        
        cyclecount = CycleCountTaskService.get_task(cyclecount_id)
        if not check_warehouse_access(cyclecount.warehouse_id):
            raise ForbiddenException("You do not have access to this warehouse.", 12001)
        
        new_adjustment = AdjustmentService.create_adjustment_from_cyclecount(cyclecount_id, created_by)

        return new_adjustment, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/details/')
class AdjustmentDetailList(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @api_ns.marshal_list_with(adjustment_detail_model)
    def get(self, adjustment_id):
        """
        Get all Adjustment Details for a specific Adjustment
        """
        return AdjustmentService.list_adjustment_details(adjustment_id)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @api_ns.expect(adjustment_detail_input_model)
    @api_ns.marshal_with(adjustment_detail_model)
    def post(self, adjustment_id):
        """
        Create a new Adjustment Detail under a specific Adjustment
        """
        created_by = g.current_user.id
        data = api_ns.payload
        new_detail = AdjustmentService.create_adjustment_detail(adjustment_id, data, created_by)

        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/details/<int:detail_id>')
class AdjustmentDetailItem(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @api_ns.marshal_with(adjustment_detail_model)
    def get(self, adjustment_id, detail_id):
        """
        Get a specific Adjustment Detail under a specific Adjustment
        """
        return AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @api_ns.expect(adjustment_detail_input_model)
    @api_ns.marshal_with(adjustment_detail_model)
    def put(self, adjustment_id, detail_id):
        """
        Update a specific Adjustment Detail
        """
        data = api_ns.payload
        updated_detail = AdjustmentService.update_adjustment_detail(adjustment_id, detail_id, data)
        
        return updated_detail

    @permission_required(["all_access","company_all_access","adjustment_delete"])
    def delete(self, adjustment_id, detail_id):
        """
        Delete a specific Adjustment Detail
        """
        AdjustmentService.delete_adjustment_detail(adjustment_id, detail_id)

        return {"message": "Adjustment Detail deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/details/search/')
class AdjustmentDetailSearch(Resource):

    @permission_required(["all_access", "company_all_access", "adjustment_read"])
    @warehouse_required()
    @api_ns.expect(adjustment_detail_pagination_parser)
    @api_ns.marshal_with(adjustment_detail_pagination_model)
    def get(self):
        """
        List Adjustment Details filtered by goods and location.
        """
        args = adjustment_detail_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'goods_id': args.get('goods_id'),
            'location_id': args.get('location_id'),
        }

        filters = add_warehouse_filter(filters)
        query = AdjustmentService.search_adjustment_details(filters)
        return paginate(query, page, per_page)
    

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class AdjustmentMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.expect(adjustment_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = adjustment_monthly_stats_parser.parse_args()
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
class AdjustmentStatusOverviewStats(Resource):
    """
    Get Sorting status overview statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        # 解析请求参数
        args = adjustment_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = CycleCountTaskService.get_status_overview(filters=filters)
        return stats, 200
