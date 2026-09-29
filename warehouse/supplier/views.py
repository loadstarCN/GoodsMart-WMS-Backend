from flask import g
from flask_restx import Resource, abort
from extensions.error import BadRequestException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Supplier
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
            'name': args.get('name'),
            # 员工 / 公司级 API Key 只能看本公司；平台管理员可按参数筛选
            'company_id': get_actor_company_id() or args.get('company_id'),
        }

        # Get the filtered query using SupplierService
        query = SupplierService.list_suppliers(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "supplier_edit"])
    @api_ns.expect(supplier_input_model)
    @api_ns.marshal_with(supplier_model)
    def post(self):
        """Create a new supplier"""
        data = api_ns.payload

        # 非平台管理员强制落在自己公司，不接受请求体里的 company_id
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            data['company_id'] = actor_company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

        created_by = require_actor_user_id()

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
        return get_company_owned(Supplier, supplier_id)

    @permission_required(["all_access", "company_all_access", "supplier_edit"])
    @api_ns.expect(supplier_input_model)
    @api_ns.marshal_with(supplier_model)
    def put(self, supplier_id):
        """Update supplier details"""
        data = api_ns.payload
        get_company_owned(Supplier, supplier_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)

        updated_supplier = SupplierService.update_supplier(supplier_id, data)

        return updated_supplier

    @permission_required(["all_access", "company_all_access", "supplier_delete"])
    def delete(self, supplier_id):
        """Delete a supplier"""
        get_company_owned(Supplier, supplier_id)
        SupplierService.delete_supplier(supplier_id)

        return {"message": "Supplier deleted successfully"}, 200
