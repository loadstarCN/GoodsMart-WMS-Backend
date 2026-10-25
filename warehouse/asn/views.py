from flask import g
from flask_restx import Resource
from extensions import cache
from system.common import permission_required,paginate
from system.third_party.utils import get_api_key_company_id
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter, require_warehouse_scope, get_warehouse_owned, require_fields,
    scoped_cache_key,
)
from .models import ASN
from .schemas import (
    api_ns,
    asn_model,
    asn_detail_model,
    asn_input_model,
    asn_input_base_model,
    asn_detail_input_model,
    asn_detail_spec_input_model,
    asn_pagination_parser,
    asn_pagination_model,
    asn_monthly_stats_parser
)
from .services import ASNService
from .spec_capture import SpecCaptureService
from .stage import attach as attach_inbound_stage


def _owned_asn(asn_id: int) -> ASN:
    """按 id 取 ASN 并校验其仓库在调用方可访问范围内（须在 @warehouse_required() 之后调用）"""
    return get_warehouse_owned(ASN, asn_id, what='ASN')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class ASNList(Resource):

    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.expect(asn_pagination_parser)
    @api_ns.marshal_with(asn_pagination_model)
    def get(self):
        """
        Get all ASNs with optional filters & pagination.
        Each item also has `inbound_stage` (expected / in_transit / receiving / sorting / putaway / completed / closed /
        exception; same definition as GET /asn/<id>/flow); `inbound_stage=<stage>` filters by it (evaluated in SQL).
        `spec_pending=true` lists only ASNs with lines whose specs are still to capture (`spec_pending_count` > 0).
        """
        args = asn_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包到 dict 中
        filters = {
            'asn_type': args.get('asn_type'),
            'status': args.get('status'),
            'tracking_number': args.get('tracking_number'),
            'order_number': args.get('order_number'),
            'supplier_id': args.get('supplier_id'),
            'carrier_id': args.get('carrier_id'),
            'expected_arrival_date': args.get('expected_arrival_date'),
            'created_by': args.get('created_by'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword'),
            'inbound_stage': args.get('inbound_stage'),
            'spec_pending': args.get('spec_pending'),
        }

        filters = add_warehouse_filter(filters)
        query = ASNService.list_asns(filters)
        result = paginate(query, page, per_page)
        # 每行的 inbound_stage 整页一条查询算好
        result['items'] = attach_inbound_stage(result['items'])
        return result, 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.expect(asn_input_model)
    @api_ns.marshal_with(asn_model)
    def post(self):
        """
        Create a new ASN
        - `warehouse_id`: Required, must be accessible to the caller
        - `asn_type`: Defaults to `inbound` if not provided by the frontend
        - `expected_arrival_date`: Can be null
        - `remark`: Can be null
        - `details`: A list of ASN details, can be empty
        - `spec_capture`: `{fields, mode}` specs to capture on receiving; omitted parts are copied from the warehouse
          defaults (400 17020 if invalid)
        - `status` / `is_active` and per-detail process quantities are ignored
        """

        data = api_ns.payload
        require_fields(data, 'warehouse_id')
        require_warehouse_scope(data['warehouse_id'], 'warehouse')

        # API Key 认证时强制注入 company_id（防止跨公司操作）
        api_company_id = get_api_key_company_id()
        if api_company_id:
            data['company_id'] = api_company_id
        # 记录创建来源 API Key（用于定向 Webhook 推送）；JWT 路径下不允许客户端自带
        api_key = g.current_system.get('api_key') if g.current_system else None
        data['api_key_id'] = api_key.id if api_key else None
        created_by = require_actor_user_id()
        new_asn = ASNService.create_asn(data, created_by)
        return new_asn, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>')
class ASNDetailView(Resource):

    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.marshal_with(asn_model)
    def get(self, asn_id):
        """
        Get details of a specific ASN
        """
        return _owned_asn(asn_id), 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.expect(asn_input_base_model)
    @api_ns.marshal_with(asn_model)
    def put(self, asn_id):
        """
        Update a specific ASN (status / is_active are ignored; use the action endpoints)
        """
        asn = _owned_asn(asn_id)
        data = api_ns.payload
        updated_asn = ASNService.update_asn(asn, data)
        return updated_asn, 200

    @permission_required(["all_access","company_all_access","asn_delete"])
    @warehouse_required()
    def delete(self, asn_id):
        """
        Delete a specific ASN
        """
        asn = _owned_asn(asn_id)
        ASNService.delete_asn(asn)

        return {"message": "ASN deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/receive/')
class ASNReceiveResource(Resource):
    """
    A resource to mark a specific ASN as 'received'.
    Calls the ASNService.receive_asn(asn_id) method.
    """

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(asn_model)
    def put(self, asn_id):
        """
        Mark a specific ASN as 'received'.
        - Returns 404 if ASN not found.
        """
        updated_asn = ASNService.receive_asn(_owned_asn(asn_id))

        return updated_asn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/close/')
class ASNCloseResource(Resource):
    """
    A resource to mark a specific ASN as 'closed'.
    Calls the ASNService.close_asn(asn_id) method.
    """

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(asn_model)
    def put(self, asn_id):
        """
        Mark a specific ASN as 'closed'.
        - Returns 404 if ASN not found.
        """
        updated_asn = ASNService.close_asn(_owned_asn(asn_id))

        return updated_asn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/cancel/')
class ASNCancelResource(Resource):
    """
    取消 ASN：pending 直接关闭；received 且分拣尚未开始时回滚签收库存并关闭；其它状态 409。
    """

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.marshal_with(asn_model)
    def put(self, asn_id):
        """
        Cancel a specific ASN and release its inventory reservation.
        - Returns 404 if ASN not found, 409 if it can no longer be cancelled.
        """
        updated_asn = ASNService.cancel_asn(_owned_asn(asn_id))

        return updated_asn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/details/')
class ASNDetailList(Resource):
    """
    操作指定 ASN 下的 ASNDetail 列表
    """

    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.marshal_list_with(asn_detail_model)
    def get(self, asn_id):
        """
        Get all ASNDetails for a specific ASN
        """
        return ASNService.list_asn_details(_owned_asn(asn_id)), 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.expect(asn_detail_input_model)
    @api_ns.marshal_with(asn_detail_model)
    def post(self, asn_id):
        """
        Create a new ASNDetail under a specific ASN (goods must belong to the same company)
        """
        asn = _owned_asn(asn_id)
        data = api_ns.payload
        new_detail = ASNService.create_asn_detail(asn, data, require_actor_user_id())

        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/details/<int:detail_id>')
class ASNDetailItem(Resource):
    """
    操作某个指定的 ASNDetail 记录
    """

    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.marshal_with(asn_detail_model)
    def get(self, asn_id, detail_id):
        """
        Get a specific ASNDetail under a specific ASN
        """
        asn = _owned_asn(asn_id)
        detail = ASNService.get_asn_detail(asn.id, detail_id)
        return detail

    @permission_required(["all_access","company_all_access","asn_edit"])
    @warehouse_required()
    @api_ns.expect(asn_detail_input_model)
    @api_ns.marshal_with(asn_detail_model)
    def put(self, asn_id, detail_id):
        """
        Update a specific ASNDetail (process quantities are ignored)
        """
        asn = _owned_asn(asn_id)
        data = api_ns.payload
        updated_detail = ASNService.update_asn_detail(asn, detail_id, data)

        return updated_detail, 200

    @permission_required(["all_access","company_all_access","asn_delete"])
    @warehouse_required()
    def delete(self, asn_id, detail_id):
        """
        Delete a specific ASNDetail
        """
        asn = _owned_asn(asn_id)
        ASNService.delete_asn_detail(asn, detail_id)

        return {"message": "ASNDetail deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/details/<int:detail_id>/spec')
class ASNDetailSpec(Resource):
    """
    收货时采集（或跳过）一行明细的规格（warehouse/asn/spec_capture.py）
    """

    # 与只改原产国的接口（PUT /goods/<id>/origin-country）同一组权限
    @permission_required(["all_access","company_all_access","goods_edit","sorting_edit","packing_edit"])
    @warehouse_required()
    @api_ns.expect(asn_detail_spec_input_model)
    @api_ns.marshal_with(asn_detail_model)
    def put(self, asn_id, detail_id):
        """
        Capture (or skip) the specs of an ASN line while the ASN is received (else 409 17023).
        Requires any of goods_edit, sorting_edit or packing_edit (same as PUT /goods/<id>/origin-country).
        - weight (kg), length / width / height (mm, all three together), origin_country (ISO 3166-1 alpha-2): the
          given values are written to the goods master data (empty / null values are ignored, nothing is cleared;
          invalid numbers 400 16100, invalid country 400 10014) and emit goods.spec_updated when they change
          (source = spec_source, default "receiving")
        - note: skip reason; the requested specs not given are skipped. Neither a value nor a note -> 400 17022;
          remeasure mode without a note must give every requested spec -> else 400 17022 details {missing}
        - The line records spec_captured_at / spec_captured_by and spec_capture_note (cleared when no note is given)
        Returns the line with its new spec_pending.
        """
        asn = _owned_asn(asn_id)
        return SpecCaptureService.capture(asn, detail_id, api_ns.payload), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class ASNMonthlyStats(Resource):
    """
    Get monthly ASN statistics
    """
    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.expect(asn_monthly_stats_parser)
    @cache.cached(timeout=60, make_cache_key=scoped_cache_key)
    def get(self):
        # 解析请求参数
        args = asn_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
            'asn_type': args.get('asn_type'),
            'supplier_id': args.get('supplier_id'),
            'carrier_id': args.get('carrier_id'),
        }

        filters = add_warehouse_filter(filters)
        stats = ASNService.get_asn_monthly_stats(months,filters=filters)
        return stats, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class ASNStatusOverviewStats(Resource):
    """
    Get ASN status overview statistics
    """
    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @cache.cached(timeout=60, make_cache_key=scoped_cache_key)
    def get(self):
        # 解析请求参数
        args = asn_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {
            'asn_type': args.get('asn_type'),
            'supplier_id': args.get('supplier_id'),
            'carrier_id': args.get('carrier_id'),
        }
        filters = add_warehouse_filter(filters)
        stats = ASNService.get_status_overview(filters=filters)
        return stats, 200
