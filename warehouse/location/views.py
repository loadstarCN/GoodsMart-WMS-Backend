# views.py（修改后）
from flask import g
from flask_restx import Resource, abort
from extensions.db import *
from system.common import paginate, permission_required
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter, require_warehouse_scope, get_warehouse_owned, require_fields,
)
from .models import Location
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
            'zone': args.get('zone'),
            'is_active': args.get('is_active'),
        }
        query = add_warehouse_filter(filters)

        # 使用服务类获取查询对象
        query = LocationService.list_locations(filters)
        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "location_edit"])
    @warehouse_required()
    @api_ns.expect(location_input_model)
    @api_ns.marshal_with(location_model)
    def post(self):
        """Create a new location"""
        data = api_ns.payload
        # 请求体里的 warehouse_id 必须是调用方可访问的仓库
        require_fields(data, 'warehouse_id')
        require_warehouse_scope(data['warehouse_id'], 'location')
        created_by = require_actor_user_id()
        new_location = LocationService.create_location(data,created_by)
        return new_location, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/zones')
class LocationZones(Resource):
    @permission_required(["all_access", "company_all_access", "location_read"])
    @warehouse_required()
    def get(self):
        """Code-derived zones in the current warehouse scope, before table filters."""
        filters = {}
        add_warehouse_filter(filters)
        query = Location.query
        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))
        zone = LocationService.zone_expression().label('zone')
        rows = query.with_entities(zone, Location.is_active, db.func.count(Location.id).label('count')) \
            .group_by(zone, Location.is_active).order_by(zone).all()
        groups = {}
        for row in rows:
            item = groups.setdefault(row.zone, {'zone': row.zone, 'active': 0, 'inactive': 0})
            item['active' if row.is_active else 'inactive'] += row.count
        return {'items': list(groups.values())}

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:location_id>')
class LocationDetail(Resource):

    @permission_required(["all_access", "company_all_access", "location_read"])
    @warehouse_required()
    @api_ns.marshal_with(location_model)
    def get(self, location_id):
        """Get location details"""
        return get_warehouse_owned(Location, location_id)

    @permission_required(["all_access", "company_all_access", "location_edit"])
    @warehouse_required()
    @api_ns.expect(location_input_model)
    @api_ns.marshal_with(location_model)
    def put(self, location_id):
        """Update location details"""
        data = api_ns.payload
        get_warehouse_owned(Location, location_id)
        # 库位不允许通过更新接口迁移到别的仓库
        data.pop('warehouse_id', None)
        updated_location = LocationService.update_location(location_id, data)
        return updated_location

    @permission_required(["all_access", "company_all_access", "location_delete"])
    @warehouse_required()
    def delete(self, location_id):
        """Delete a location"""
        get_warehouse_owned(Location, location_id)
        LocationService.delete_location(location_id)
        return {"message": "Location deleted successfully"}, 200
