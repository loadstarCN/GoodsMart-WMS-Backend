from flask import g
from flask_restx import Resource, abort
from system.common import paginate, permission_required
from .schemas import api_ns, supplier_model, supplier_input_model, supplier_pagination_parser, pagination_model
from .services import SupplierService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class SupplierList(Resource):

    @permission_required(["all_access", "company_all_access", "supplier_read"])
    @api_ns.expect(supplier_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of suppliers"""
        args = supplier_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name')
        }

        # Check user type and get the company_id if user type is 'staff'
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            filters['company_id'] = company_id
        else:
            filters['company_id'] = args.get('company_id')
        
        # Get the filtered query using SupplierService
        query = SupplierService.list_suppliers(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "supplier_edit"])
    @api_ns.expect(supplier_input_model)
    @api_ns.marshal_with(supplier_model)
    def post(self):
        """Create a new supplier"""
        data = api_ns.payload
        created_by = g.current_user.id
        
        # Create the new supplier using SupplierService
        new_supplier = SupplierService.create_supplier(data, created_by)
        
        return new_supplier, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:supplier_id>')
class SupplierDetail(Resource):

    @permission_required(["all_access", "company_all_access", "supplier_read"])
    @api_ns.marshal_with(supplier_model)
    def get(self, supplier_id):
        """Get supplier details"""
        supplier = SupplierService.get_supplier(supplier_id)
        return supplier

    @permission_required(["all_access", "company_all_access", "supplier_edit"])
    @api_ns.expect(supplier_input_model)
    @api_ns.marshal_with(supplier_model)
    def put(self, supplier_id):
        """Update supplier details"""
        data = api_ns.payload

        updated_supplier = SupplierService.update_supplier(supplier_id, data)

        return updated_supplier

    @permission_required(["all_access", "company_all_access", "supplier_delete"])
    def delete(self, supplier_id):
        """Delete a supplier"""
        SupplierService.delete_supplier(supplier_id)

        return {"message": "Supplier deleted successfully"}, 200
