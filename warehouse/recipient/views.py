from flask import g
from flask_restx import Resource, abort
from system.common import paginate, permission_required
from .schemas import api_ns, recipient_model, recipient_input_model, recipient_pagination_parser, pagination_model
from .services import RecipientService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class RecipientList(Resource):

    @permission_required(["all_access", "company_all_access", "recipient_read"])
    @api_ns.expect(recipient_pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of recipients"""
        args = recipient_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name'),
            'address': args.get('address'),
            'zip_code': args.get('zip_code'),
            'phone': args.get('phone'),
            'email': args.get('email'),
            'contact': args.get('contact'),
            'country': args.get('country')
        }

        # Check user type and get the company_id if user type is 'staff'
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            filters['company_id'] = company_id
        else:
            filters['company_id'] = args.get('company_id')

        # Get the filtered query using RecipientService
        query = RecipientService.list_recipients(filters)

        return paginate(query, page, per_page, get_all), 200

    @permission_required(["all_access", "company_all_access", "recipient_edit"])
    @api_ns.expect(recipient_input_model)
    @api_ns.marshal_with(recipient_model)
    def post(self):
        """Create a new recipient"""
        data = api_ns.payload
        created_by = g.current_user.id
        
        # Create the new recipient using RecipientService
        new_recipient = RecipientService.create_recipient(data, created_by)
        
        return new_recipient, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:recipient_id>')
class RecipientDetail(Resource):

    @permission_required(["all_access", "company_all_access", "recipient_read"])
    @api_ns.marshal_with(recipient_model)
    def get(self, recipient_id):
        """Get recipient details"""
        recipient = RecipientService.get_recipient(recipient_id)
        return recipient

    @permission_required(["all_access", "company_all_access", "recipient_edit"])
    @api_ns.expect(recipient_input_model)
    @api_ns.marshal_with(recipient_model)
    def put(self, recipient_id):
        """Update recipient details"""
        data = api_ns.payload

        updated_recipient = RecipientService.update_recipient(recipient_id, data)

        return updated_recipient

    @permission_required(["all_access", "company_all_access", "recipient_delete"])
    def delete(self, recipient_id):
        """Delete a recipient"""
        RecipientService.delete_recipient(recipient_id)

        return {"message": "Recipient deleted successfully"}, 200
