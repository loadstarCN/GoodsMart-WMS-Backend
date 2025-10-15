from flask import g
from werkzeug.exceptions import NotFound
from flask_restx import Resource,abort
from extensions import cache
from extensions.error import ForbiddenException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,check_warehouse_access,add_warehouse_filter
from .schemas import (
    api_ns,
    asn_model,
    asn_detail_model,
    asn_input_model,
    asn_input_base_model,
    asn_detail_input_model,
    asn_pagination_parser,
    asn_pagination_model,
    asn_monthly_stats_parser
)
from .services import ASNService


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class ASNList(Resource):

    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.expect(asn_pagination_parser)
    @api_ns.marshal_with(asn_pagination_model)
    def get(self):
        """
        Get all ASNs with optional filters & pagination
        """
        args = asn_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包到 dict 中
        filters = {
            'asn_type': args.get('asn_type'),
            'status': args.get('status'),
            'tracking_number': args.get('tracking_number'),
            'supplier_id': args.get('supplier_id'),
            'carrier_id': args.get('carrier_id'),            
            'expected_arrival_date': args.get('expected_arrival_date'),
            'created_by': args.get('created_by'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }

        filters = add_warehouse_filter(filters)
        query = ASNService.list_asns(filters)
        return paginate(query, page, per_page), 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @api_ns.expect(asn_input_model)
    @api_ns.marshal_with(asn_model)
    def post(self):
        """
        Create a new ASN
        - `asn_type`: Defaults to `inbound` if not provided by the frontend
        - `expected_arrival_date`: Can be null
        - `status`: Defaults to `pending` if not provided by the frontend
        - `remark`: Can be null
        - `details`: A list of ASN details, can be empty
        """

        data = api_ns.payload
        created_by = g.current_user.id
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
        asn = ASNService.get_asn(asn_id)
        if not check_warehouse_access(asn.warehouse_id):
            raise ForbiddenException("You do not have access to this ASN", 12001)
        return asn, 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @api_ns.expect(asn_input_base_model)
    @api_ns.marshal_with(asn_model)
    def put(self, asn_id):
        """
        Update a specific ASN
        """
        data = api_ns.payload
        updated_asn = ASNService.update_asn(asn_id, data)
        return updated_asn, 200

    @permission_required(["all_access","company_all_access","asn_delete"])
    def delete(self, asn_id):
        """
        Delete a specific ASN
        """
        ASNService.delete_asn(asn_id)
        
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
        updated_asn = ASNService.get_asn(asn_id)
        if not check_warehouse_access(updated_asn.warehouse_id):
            raise ForbiddenException("You do not have access to this ASN", 12001) 
        
        updated_asn = ASNService.receive_asn(updated_asn)
        
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
        updated_asn = ASNService.get_asn(asn_id)
        if not check_warehouse_access(updated_asn.warehouse_id):
            raise ForbiddenException("You do not have access to this ASN", 12001)
        updated_asn = ASNService.close_asn(updated_asn)
        
        return updated_asn, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/details/')
class ASNDetailList(Resource):
    """
    操作指定 ASN 下的 ASNDetail 列表
    """

    @permission_required(["all_access","company_all_access","asn_read"])
    @api_ns.marshal_list_with(asn_detail_model)
    def get(self, asn_id):
        """
        Get all ASNDetails for a specific ASN
        """
        return ASNService.list_asn_details(asn_id), 200

    @permission_required(["all_access","company_all_access","asn_edit"])
    @api_ns.expect(asn_detail_input_model)
    @api_ns.marshal_with(asn_detail_model)
    def post(self, asn_id):
        """
        Create a new ASNDetail under a specific ASN
        """
        data = api_ns.payload
        new_detail = ASNService.create_asn_detail(asn_id, data, g.current_user.id)
        
        return new_detail, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/details/<int:detail_id>')
class ASNDetailItem(Resource):
    """
    操作某个指定的 ASNDetail 记录
    """

    @permission_required(["all_access","company_all_access","asn_read"])
    @api_ns.marshal_with(asn_detail_model)
    def get(self, asn_id, detail_id):
        """
        Get a specific ASNDetail under a specific ASN
        """
        detail = ASNService.get_asn_detail(asn_id, detail_id)
        return detail

    @permission_required(["all_access","company_all_access","asn_edit"])
    @api_ns.expect(asn_detail_input_model)
    @api_ns.marshal_with(asn_detail_model)
    def put(self, asn_id, detail_id):
        """
        Update a specific ASNDetail
        """
        data = api_ns.payload
        updated_detail = ASNService.update_asn_detail(asn_id, detail_id, data)

        return updated_detail, 200

    @permission_required(["all_access","company_all_access","asn_delete"])
    def delete(self, asn_id, detail_id):
        """
        Delete a specific ASNDetail
        """
        ASNService.delete_asn_detail(asn_id, detail_id)
        
        return {"message": "ASNDetail deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class ASNMonthlyStats(Resource):
    """
    Get monthly ASN statistics
    """
    @permission_required(["all_access","company_all_access","asn_read"])
    @warehouse_required()
    @api_ns.expect(asn_monthly_stats_parser)
    @cache.cached(timeout=60, query_string=True)
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
    @cache.cached(timeout=60, query_string=True)
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
