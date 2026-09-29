from flask import g
from flask_restx import Resource, abort
from extensions.error import BadRequestException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Warehouse
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
            # 员工 / 公司级 API Key 只能看本公司；平台管理员可按参数筛选
            'company_id': get_actor_company_id() or args.get('company_id'),
        }

        # Get the filtered query using WarehouseService
        query = WarehouseService.list_warehouses(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "warehouse_edit"])
    @api_ns.expect(warehouse_input_model)
    @api_ns.marshal_with(warehouse_model)
    def post(self):
        """Create a new warehouse"""
        data = api_ns.payload
        created_by = require_actor_user_id()
        # 非平台管理员只能在自己公司建仓库：请求体里的 company_id 直接被覆盖
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            data['company_id'] = actor_company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

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
        return get_company_owned(Warehouse, warehouse_id)

    @permission_required(["all_access", "company_all_access", "warehouse_edit"])
    @api_ns.expect(warehouse_input_model)
    @api_ns.marshal_with(warehouse_model)
    def put(self, warehouse_id):
        """Update warehouse details"""
        data = api_ns.payload
        get_company_owned(Warehouse, warehouse_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)

        updated_warehouse = WarehouseService.update_warehouse(warehouse_id, data)

        return updated_warehouse

    @permission_required(["all_access", "company_all_access", "warehouse_delete"])
    def delete(self, warehouse_id):
        """Delete a warehouse"""
        get_company_owned(Warehouse, warehouse_id)
        WarehouseService.delete_warehouse(warehouse_id)

        return {"message": "Warehouse deleted successfully"}, 200
