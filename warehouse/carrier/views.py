from flask import g
from flask_restx import Resource, abort
from extensions import db
from extensions.error import BadRequestException
from system.common import paginate,permission_required
from system.common.permissions import get_actor_company_id
from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.services import CarrierService
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Carrier
from .schemas import (
    api_ns, carrier_model, carrier_input_model, carrier_pagination_parser, pagination_model,
    carrier_integration_input_model,
)


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
            'name': args.get('name'),
            # 员工 / 公司级 API Key 只能看本公司；平台管理员可按参数筛选
            'company_id': get_actor_company_id() or args.get('company_id'),
        }

        # Get the filtered query using CarrierService
        query = CarrierService.list_carriers(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_input_model)
    @api_ns.marshal_with(carrier_model)
    def post(self):
        """Create a new carrier"""
        data = api_ns.payload

        # 非平台管理员强制落在自己公司，不接受请求体里的 company_id
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            data['company_id'] = actor_company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

        created_by = require_actor_user_id()

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
        return get_company_owned(Carrier, carrier_id)

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_input_model)
    @api_ns.marshal_with(carrier_model)
    def put(self, carrier_id):
        """Update carrier details"""
        data = api_ns.payload
        get_company_owned(Carrier, carrier_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)

        updated_carrier = CarrierService.update_carrier(carrier_id, data)

        return updated_carrier

    @permission_required(["all_access", "company_all_access", "carrier_delete"])
    def delete(self, carrier_id):
        """Delete a carrier"""
        get_company_owned(Carrier, carrier_id)
        CarrierService.delete_carrier(carrier_id)

        return {"message": "Carrier deleted successfully"}, 200


# ------------------------------------------------------------------
# 在线对接（自动建运单）：插件列表、按承运商读写设置、测试连接
# ------------------------------------------------------------------

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/integrations')
class CarrierIntegrationList(Resource):

    @permission_required(["all_access", "company_all_access", "carrier_read"])
    def get(self):
        """Available carrier integrations: [{code, name, portal_name, portal_url, label_formats,
        fields: [{key, kind: text|secret|select|boolean, label: {locale: text}, required, default, options, help,
        group: account|shipping|label}]}]"""
        return CarrierIntegrationService.available()


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:carrier_id>/integration')
class CarrierIntegrationResource(Resource):

    @permission_required(["all_access", "company_all_access", "carrier_read"])
    def get(self, carrier_id):
        """Integration of a carrier: {adapter, name, enabled, settings, secrets: {key: is set}, ready,
        problems: [{code, message}], updated_at, updated_by}, or null. Saved keys are never returned."""
        return CarrierIntegrationService.payload(get_company_owned(Carrier, carrier_id))

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_integration_input_model)
    def put(self, carrier_id):
        """Save the integration of a carrier (same shape as GET; adapter empty = remove).
        Invalid settings → 400 16124 (field adapter / settings.<key> / secrets.<key> / enabled);
        removing or changing it while the carrier has unfinished shipments → 409 16125"""
        carrier = get_company_owned(Carrier, carrier_id)
        return CarrierIntegrationService.save(carrier, api_ns.payload or {}, require_actor_user_id())

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    def delete(self, carrier_id):
        """Remove the integration of a carrier (409 16125 while it has unfinished shipments)"""
        carrier = get_company_owned(Carrier, carrier_id)
        CarrierIntegrationService.save(carrier, {'adapter': None}, require_actor_user_id())
        return None, 204


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:carrier_id>/integration/test')
class CarrierIntegrationTest(Resource):

    @permission_required(["all_access", "company_all_access", "carrier_edit"])
    @api_ns.expect(carrier_integration_input_model)
    def post(self, carrier_id):
        """Connect to the carrier once with the given settings (keys left empty use the saved ones), without
        saving: {ok, message, errors?, missing?}. A failed connection is also 200 (ok = false)."""
        carrier = get_company_owned(Carrier, carrier_id)
        return CarrierIntegrationService.test(carrier, api_ns.payload or None)
