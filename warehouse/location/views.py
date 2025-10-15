# views.py（修改后）
from amqp import NotFound
from flask import g
from flask_restx import Resource, abort
from extensions.db import *
from extensions.error import ForbiddenException
from system.common import paginate, permission_required
from warehouse.common import warehouse_required, add_warehouse_filter,check_warehouse_access
from .schemas import api_ns, location_model, location_input_model, location_pagination_parser, pagination_model
from .services import LocationService

@api_ns.doc(security="jsonWebToken")    
@api_ns.route('/')
class LocationList(Resource):

    @permission_required(["all_access", "company_all_access", "location_read"])
    @warehouse_required()
    @api_ns.expect(location_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of locations"""        
        args = location_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # 获取是否返回全部数据的标志
        
        # 构建过滤字典
        filters = {
            'location_type': args.get('location_type'),
            'code': args.get('code'),
            'is_active': args.get('is_active'),
        }
        query = add_warehouse_filter(filters)

        # 使用服务类获取查询对象
        query = LocationService.list_locations(filters)
        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "location_edit"])
    @api_ns.expect(location_input_model)
    @api_ns.marshal_with(location_model)
    def post(self):
        """Create a new location"""
        data = api_ns.payload
        created_by = g.current_user.id
        new_location = LocationService.create_location(data,created_by)
        return new_location, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:location_id>')
class LocationDetail(Resource):

    @permission_required(["all_access", "company_all_access", "location_read"])
    @warehouse_required()
    @api_ns.marshal_with(location_model)
    def get(self, location_id):
        """Get location details"""        
        location = LocationService.get_location(location_id)
        # 仓库权限检查（需根据实际权限系统调整）
        if not check_warehouse_access(location.warehouse_id):
            raise ForbiddenException("You do not have access to this location", 12001)
        return location

    @permission_required(["all_access", "company_all_access", "location_edit"])
    @api_ns.expect(location_input_model)
    @api_ns.marshal_with(location_model)
    def put(self, location_id):
        """Update location details"""
        data = api_ns.payload
        updated_location = LocationService.update_location(location_id, data)
        return updated_location

    @permission_required(["all_access", "company_all_access", "location_delete"])
    def delete(self, location_id):
        """Delete a location"""
        LocationService.delete_location(location_id)
        return {"message": "Location deleted successfully"}, 200
