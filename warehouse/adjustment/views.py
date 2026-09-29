from flask import g
from flask_restx import Resource,abort
from extensions.db import get_object_or_404
from system.common import permission_required,paginate
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter,
    require_warehouse_scope, require_same_warehouse, get_warehouse_owned, require_fields,
)
from warehouse.cyclecount.models import CycleCountTask
from warehouse.location.models import Location
from .models import Adjustment

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


def _get_owned_adjustment(adjustment_id: int) -> Adjustment:
    """按 id 取调整单并校验仓库归属（须在 @warehouse_required() 之后）"""
    return get_warehouse_owned(Adjustment, adjustment_id, what='adjustment')


def _assert_location_in_warehouse(warehouse_id, location_id):
    """明细里的库位必须与调整单同仓库"""
    if location_id is None:
        return
    location = get_object_or_404(Location, location_id)
    require_same_warehouse(warehouse_id, location.warehouse_id, 'location')


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
    @warehouse_required()
    @api_ns.expect(adjustment_input_model)
    @api_ns.marshal_with(adjustment_model)
    def post(self):
        """
        Create a new Adjustment
        """
        data = api_ns.payload
        created_by = require_actor_user_id()

        # 请求体里的 warehouse_id 必须是调用方可访问的仓库；明细库位必须同仓库
        require_fields(data, 'warehouse_id')
        require_warehouse_scope(data['warehouse_id'], 'adjustment')
        for detail_data in data.get('details') or []:
            _assert_location_in_warehouse(data['warehouse_id'], detail_data.get('location_id'))

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
        return _get_owned_adjustment(adjustment_id)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.expect(adjustment_input_model)
    @api_ns.marshal_with(adjustment_model)
    def put(self, adjustment_id):
        """
        Update a specific Adjustment
        """
        data = api_ns.payload
        _get_owned_adjustment(adjustment_id)
        updated_adjustment = AdjustmentService.update_adjustment(adjustment_id, data)
        return updated_adjustment

    @permission_required(["all_access","company_all_access","adjustment_delete"])
    @warehouse_required()
    def delete(self, adjustment_id):
        """
        Delete a specific Adjustment
        """
        _get_owned_adjustment(adjustment_id)
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
        operator_id = require_actor_user_id()
        adjustment = _get_owned_adjustment(adjustment_id)

        updated_adjustment = AdjustmentService.complete_adjustment(adjustment, operator_id)
        return updated_adjustment


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/approve/')
class AdjustmentApprove(Resource):

    @permission_required(["all_access","company_all_access","adjustment_approve"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_model)
    def put(self, adjustment_id):
        """
        Approve a specific Adjustment（需要独立的 adjustment_approve 权限，且不能审批自己创建的单）
        """
        operator_id = require_actor_user_id()

        adjustment = _get_owned_adjustment(adjustment_id)

        updated_adjustment = AdjustmentService.approve_adjustment(adjustment,operator_id)

        return updated_adjustment

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/create_adjustment_by_cyclecount/<int:cyclecount_id>')
class AdjustmentCreate(Resource):

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_model)
    def post(self, cyclecount_id):
        """
        Create a new Adjustment from a Cycle Count
        """
        created_by = require_actor_user_id()

        get_warehouse_owned(CycleCountTask, cyclecount_id, what='cycle count task')

        new_adjustment = AdjustmentService.create_adjustment_from_cyclecount(cyclecount_id, created_by)

        return new_adjustment, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/details/')
class AdjustmentDetailList(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(adjustment_detail_model)
    def get(self, adjustment_id):
        """
        Get all Adjustment Details for a specific Adjustment
        """
        _get_owned_adjustment(adjustment_id)
        return AdjustmentService.list_adjustment_details(adjustment_id)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.expect(adjustment_detail_input_model)
    @api_ns.marshal_with(adjustment_detail_model)
    def post(self, adjustment_id):
        """
        Create a new Adjustment Detail under a specific Adjustment
        """
        created_by = require_actor_user_id()
        data = api_ns.payload
        adjustment = _get_owned_adjustment(adjustment_id)
        _assert_location_in_warehouse(adjustment.warehouse_id, data.get('location_id'))
        new_detail = AdjustmentService.create_adjustment_detail(adjustment_id, data, created_by)

        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:adjustment_id>/details/<int:detail_id>')
class AdjustmentDetailItem(Resource):

    @permission_required(["all_access","company_all_access","adjustment_read"])
    @warehouse_required()
    @api_ns.marshal_with(adjustment_detail_model)
    def get(self, adjustment_id, detail_id):
        """
        Get a specific Adjustment Detail under a specific Adjustment
        """
        _get_owned_adjustment(adjustment_id)
        return AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)

    @permission_required(["all_access","company_all_access","adjustment_edit"])
    @warehouse_required()
    @api_ns.expect(adjustment_detail_input_model)
    @api_ns.marshal_with(adjustment_detail_model)
    def put(self, adjustment_id, detail_id):
        """
        Update a specific Adjustment Detail
        """
        data = api_ns.payload
        adjustment = _get_owned_adjustment(adjustment_id)
        if 'location_id' in data:
            _assert_location_in_warehouse(adjustment.warehouse_id, data['location_id'])
        updated_detail = AdjustmentService.update_adjustment_detail(adjustment_id, detail_id, data)

        return updated_detail

    @permission_required(["all_access","company_all_access","adjustment_delete"])
    @warehouse_required()
    def delete(self, adjustment_id, detail_id):
        """
        Delete a specific Adjustment Detail
        """
        _get_owned_adjustment(adjustment_id)
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
    Get monthly Adjustment statistics
    """
    @permission_required(["all_access","company_all_access","adjustment_read"])
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
        stats = AdjustmentService.get_adjustment_monthly_stats(months=months,filters=filters)
        return stats, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class AdjustmentStatusOverviewStats(Resource):
    """
    Get Adjustment status overview statistics
    """
    @permission_required(["all_access","company_all_access","adjustment_read"])
    @warehouse_required()
    def get(self):
        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = AdjustmentService.get_status_overview(filters=filters)
        return stats, 200
