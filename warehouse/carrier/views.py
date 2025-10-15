from flask import g
from flask_restx import Resource, abort
from extensions import db
from system.common import paginate,permission_required
from warehouse.carrier.services import CarrierService
from warehouse.common import warehouse_required
from warehouse.warehouse.models import Warehouse
from .models import Carrier
from .schemas import api_ns, carrier_model,carrier_input_model, carrier_pagination_parser,pagination_model


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class CarrierList(Resource):
    
    @permission_required(["all_access", "company_all_access", "carrier_read"])
    @api_ns.expect(carrier_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of carriers"""
        args = carrier_pagination_parser.parse_args()
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
        
        # Get the filtered query using CarrierService
        query = CarrierService.list_carriers(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_input_model)
    @api_ns.marshal_with(carrier_model)
    def post(self):
        """Create a new carrier"""
        data = api_ns.payload
        created_by = g.current_user.id
        
        # Create the new carrier using CarrierService
        new_carrier = CarrierService.create_carrier(data, created_by)
        
        return new_carrier, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:carrier_id>')
class CarrierDetail(Resource):

    @permission_required(["all_access", "company_all_access", "carrier_read"])
    @api_ns.marshal_with(carrier_model)
    def get(self, carrier_id):
        """Get carrier details"""
        carrier = CarrierService.get_carrier(carrier_id)
        return carrier

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_input_model)
    @api_ns.marshal_with(carrier_model)
    def put(self, carrier_id):
        """Update carrier details"""
        data = api_ns.payload

        updated_carrier = CarrierService.update_carrier(carrier_id, data)

        return updated_carrier

    @permission_required(["all_access", "company_all_access", "carrier_delete"])
    def delete(self, carrier_id):
        """Delete a carrier"""
        CarrierService.delete_carrier(carrier_id)

        return {"message": "Carrier deleted successfully"}, 200