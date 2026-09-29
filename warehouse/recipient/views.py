from flask import g
from flask_restx import Resource
from extensions.error import BadRequestException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Recipient
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
            'external_reference': args.get('external_reference'),
            'address': args.get('address'),
            'zip_code': args.get('zip_code'),
            'phone': args.get('phone'),
            'email': args.get('email'),
            'contact': args.get('contact'),
            'country': args.get('country'),
            'keyword': args.get('keyword'),
            # 员工 / 公司级 API Key 只能看本公司；平台管理员可按参数筛选
            'company_id': get_actor_company_id() or args.get('company_id'),
        }

        # Get the filtered query using RecipientService
        query = RecipientService.list_recipients(filters)

        return paginate(query, page, per_page, get_all), 200

    @permission_required(["all_access", "company_all_access", "recipient_edit"])
    @api_ns.expect(recipient_input_model)
    @api_ns.marshal_with(recipient_model)
    def post(self):
        """Create a new recipient"""
        data = api_ns.payload

        # 非平台管理员强制落在自己公司，不接受请求体里的 company_id
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            data['company_id'] = actor_company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

        created_by = require_actor_user_id()

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
        return get_company_owned(Recipient, recipient_id)

    @permission_required(["all_access", "company_all_access", "recipient_edit"])
    @api_ns.expect(recipient_input_model)
    @api_ns.marshal_with(recipient_model)
    def put(self, recipient_id):
        """Update recipient details"""
        data = api_ns.payload
        get_company_owned(Recipient, recipient_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)

        updated_recipient = RecipientService.update_recipient(recipient_id, data)

        return updated_recipient

    @permission_required(["all_access", "company_all_access", "recipient_delete"])
    def delete(self, recipient_id):
        """Delete a recipient"""
        get_company_owned(Recipient, recipient_id)
        RecipientService.delete_recipient(recipient_id)

        return {"message": "Recipient deleted successfully"}, 200
