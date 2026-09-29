from flask import g
from flask_restx import Resource
from extensions import cache
from extensions.error import BadRequestException
from system.common import permission_required,paginate
from system.third_party.utils import get_api_key_company_id
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter,
    get_warehouse_owned, require_warehouse_scope,
)

from .models import DN
from .schemas import (
    api_ns,
    dn_model,
    dn_detail_model,
    dn_input_model,
    dn_input_base_model,
    dn_detail_input_model,
    dn_pagination_parser,
    dn_pagination_model,
    dn_monthly_stats_parser
)
from .services import DNService


def _owned_dn(dn_id: int) -> DN:
    """按 id 取 DN 并校验仓库归属（须在 @warehouse_required() 之后调用）：不存在 404、越权 403"""
    return get_warehouse_owned(DN, dn_id, what='DN')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class DNList(Resource):

    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @api_ns.expect(dn_pagination_parser)
    @api_ns.marshal_with(dn_pagination_model)
    def get(self):
        """
        Get all DNs with optional filters & pagination
        """
        args = dn_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包到 dict 中
        filters = {
            'dn_type': args.get('dn_type'),
            'status': args.get('status'),
            'order_number': args.get('order_number'),
            'carrier_id': args.get('carrier_id'),
            'recipient_id': args.get('recipient_id'),
            'created_by': args.get('created_by'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }

        filters = add_warehouse_filter(filters)
        query = DNService.list_dns(filters)
        return paginate(query, page, per_page), 200

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.expect(dn_input_model)
    @api_ns.marshal_with(dn_model)
    def post(self):
        """
        Create a new DN
        - `dn_type`: Defaults to `shipping` if not provided by the frontend
        - `status`: Always `pending`; status / is_active / *_quantity in the body are ignored
        - `details`: A non-empty list of DN details
        - `warehouse_id` must be accessible to the caller; recipient / carrier / goods must belong to the same company
        """
        data = api_ns.payload
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 16015)
        # 请求体里的仓库必须在调用方可访问范围内（防止把预占打到别家仓库）
        if data.get('warehouse_id') is not None:
            require_warehouse_scope(data.get('warehouse_id'), 'warehouse')
        # API Key 认证时强制注入 company_id（防止跨公司操作）
        api_company_id = get_api_key_company_id()
        if api_company_id:
            data['company_id'] = api_company_id
        # 记录创建来源 API Key（用于定向 Webhook 推送）
        if g.current_system and g.current_system.get('api_key'):
            data['api_key_id'] = g.current_system['api_key'].id
        created_by = require_actor_user_id()
        new_dn = DNService.create_dn(data, created_by)
        return new_dn, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>')
class DNDetailView(Resource):

    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @api_ns.marshal_with(dn_model)
    def get(self, dn_id):
        """
        Get details of a specific DN
        """
        return _owned_dn(dn_id), 200

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.expect(dn_input_base_model)
    @api_ns.marshal_with(dn_model)
    def put(self, dn_id):
        """
        Update a specific DN (only allowed if status == 'pending' by default).
        `status` / `is_active` / `created_by` / `*_at` in the body are ignored.
        """
        dn = _owned_dn(dn_id)
        data = api_ns.payload
        updated_dn = DNService.update_dn(dn, data)
        return updated_dn, 200

    @permission_required(["all_access","company_all_access","dn_delete"])
    @warehouse_required()
    def delete(self, dn_id):
        """
        Delete a specific DN (only allowed if status == 'pending' by default)
        """
        dn = _owned_dn(dn_id)
        DNService.delete_dn(dn)

        return {"message": "DN deleted successfully"}, 200



@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/progress/')
class DNProgressResource(Resource):
    """
    A resource to mark a specific DN as 'progress'.
    Calls the DNService.progress_dn(dn_id) method.
    """

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(dn_model)
    def put(self, dn_id):
        """
        Mark a specific DN as 'in_progress' and create its picking task.
        - Returns 404 if DN not found.
        """
        updated_dn = DNService.progress_dn(_owned_dn(dn_id))
        return updated_dn, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/close/')
class DNCloseResource(Resource):
    """
    A resource to mark a specific DN as 'closed'.
    Calls the DNService.close_dn(dn_id) method.
    """

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(dn_model)
    def put(self, dn_id):
        """
        Mark a specific DN as 'closed'.
        - Returns 404 if DN not found.
        """
        updated_dn = DNService.close_dn(_owned_dn(dn_id))

        return updated_dn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/cancel/')
class DNCancelResource(Resource):
    """
    Cancel a DN and release its stock reservation.
    """

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(dn_model)
    def put(self, dn_id):
        """
        Cancel a specific DN.
        - pending: same as close
        - in_progress: allowed only if picking has not started (no batches); picking task is deactivated
        - other statuses: 409
        """
        updated_dn = DNService.cancel_dn(_owned_dn(dn_id))
        return updated_dn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/details/')
class DNDetailList(Resource):
    """
    操作指定 DN 下的 DNDetail 列表
    """

    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(dn_detail_model)
    def get(self, dn_id):
        """
        Get all DNDetails for a specific DN
        """
        return _owned_dn(dn_id).details, 200

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.expect(dn_detail_input_model)
    @api_ns.marshal_with(dn_detail_model)
    def post(self, dn_id):
        """
        Create a new DNDetail under a specific DN
        """
        dn = _owned_dn(dn_id)
        data = api_ns.payload
        new_detail = DNService.create_dn_detail(dn.id, data, require_actor_user_id())
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/details/<int:detail_id>')
class DNDetailItem(Resource):
    """
    操作某个指定的 DNDetail 记录
    """
    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @api_ns.marshal_with(dn_detail_model)
    def get(self, dn_id, detail_id):
        """
        Get a specific DNDetail under a specific DN
        """
        dn = _owned_dn(dn_id)
        detail = DNService.get_dn_detail(dn.id, detail_id)
        return detail, 200

    @permission_required(["all_access","company_all_access","dn_edit"])
    @warehouse_required()
    @api_ns.expect(dn_detail_input_model)
    @api_ns.marshal_with(dn_detail_model)
    def put(self, dn_id, detail_id):
        """
        Update a specific DNDetail (quantity / goods / remark only)
        """
        dn = _owned_dn(dn_id)
        data = api_ns.payload
        updated_detail = DNService.update_dn_detail(dn.id, detail_id, data)
        return updated_detail, 200

    @permission_required(["all_access","company_all_access","dn_delete"])
    @warehouse_required()
    def delete(self, dn_id, detail_id):
        """
        Delete a specific DNDetail
        """
        dn = _owned_dn(dn_id)
        DNService.delete_dn_detail(dn.id, detail_id)
        return {"message": "DNDetail deleted successfully"}, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class DNMonthlyStats(Resource):
    """
    Get monthly DN statistics
    """
    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @api_ns.expect(dn_monthly_stats_parser)
    @cache.cached(timeout=60, query_string=True)
    def get(self):
        # 解析请求参数
        args = dn_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
            'dn_type': args.get('dn_type'),
            'recipient_id': args.get('recipient_id'),
            'carrier_id': args.get('carrier_id'),
        }

        filters = add_warehouse_filter(filters)
        stats = DNService.get_dn_monthly_stats(months,filters=filters)
        return stats, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class DNStatusOverviewStats(Resource):
    """
    Get DN status overview statistics
    """
    @permission_required(["all_access","company_all_access","dn_read"])
    @warehouse_required()
    @cache.cached(timeout=60, query_string=True)
    def get(self):
        # 解析请求参数
        args = dn_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {
            'dn_type': args.get('dn_type'),
            'recipient_id': args.get('recipient_id'),
            'carrier_id': args.get('carrier_id'),
        }
        filters = add_warehouse_filter(filters)
        stats = DNService.get_status_overview(filters=filters)
        return stats, 200
