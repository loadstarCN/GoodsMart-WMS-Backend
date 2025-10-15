from flask import g
from flask_restx import Resource, abort
from extensions.error import ForbiddenException
from system.common import paginate, permission_required
from .schemas import api_ns, warehouse_model, warehouse_input_model, pagination_parser, pagination_model

from .services import WarehouseService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class WarehouseList(Resource):

    @permission_required(["all_access", "company_all_access", "warehouse_read"])

    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of warehouses"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name'),
        }

        # Check user type and get the company_id if user type is 'staff'
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            filters['company_id'] = company_id
        else:
            filters['company_id'] = args.get('company_id')

        # Get the filtered query using WarehouseService
        query = WarehouseService.list_warehouses(filters)
        
        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "warehouse_edit"])
    @api_ns.expect(warehouse_input_model)
    @api_ns.marshal_with(warehouse_model)
    def post(self):
        """Create a new warehouse"""
        data = api_ns.payload
        created_by = g.current_user.id
        # Check if the user is a staff member and restrict access to their company
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            if data.get('company_id') != company_id:
                raise ForbiddenException("You do not have permission to create a warehouse in this company.", 12001)
                
        # Create the new warehouse using WarehouseService
        new_warehouse = WarehouseService.create_warehouse(data, created_by)
        
        return new_warehouse, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:warehouse_id>')
class WarehouseDetail(Resource):

    @permission_required(["all_access", "company_all_access", "warehouse_read"])
    @api_ns.marshal_with(warehouse_model)
    def get(self, warehouse_id):
        """Get warehouse details"""
        user = g.current_user
        if user.type == 'staff':
            # Staff can only access their own company's warehouse information
            if user.company_id != WarehouseService.get_warehouse(warehouse_id).company_id:
                raise ForbiddenException("You do not have permission to view this warehouse.", 12001)
        warehouse = WarehouseService.get_warehouse(warehouse_id)
        return warehouse

    @permission_required(["all_access", "company_all_access", "warehouse_edit"])
    @api_ns.expect(warehouse_input_model)
    @api_ns.marshal_with(warehouse_model)
    def put(self, warehouse_id):
        """Update warehouse details"""
        data = api_ns.payload
        user = g.current_user
        if user.type == 'staff':
            # Staff can only update their own company's warehouse information
            if user.company_id != WarehouseService.get_warehouse(warehouse_id).company_id:
                raise ForbiddenException("You do not have permission to update this warehouse.", 12001)

        updated_warehouse = WarehouseService.update_warehouse(warehouse_id, data)

        return updated_warehouse

    @permission_required(["all_access", "company_all_access", "warehouse_delete"])
    def delete(self, warehouse_id):
        """Delete a warehouse"""
        user = g.current_user
        if user.type == 'staff':
            # Staff can only delete their own company's warehouse information
            if user.company_id != WarehouseService.get_warehouse(warehouse_id).company_id:
                raise ForbiddenException("You do not have permission to delete this warehouse.", 12001)
        WarehouseService.delete_warehouse(warehouse_id)
        
        return {"message": "Warehouse deleted successfully"}, 200
