from flask import g
from flask_restx import Resource, abort
from extensions.error import BadRequestException, ForbiddenException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id, get_actor_context
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Warehouse
from .schemas import api_ns, warehouse_model, warehouse_input_model, pagination_parser, pagination_model

from .services import WarehouseService


def _restricted_warehouse_ids():
    """仓库写操作的范围（B-75）。

    返回 None = 不受限：平台管理员（all_access）、持 company_all_access 的公司管理员、
    非员工调用方（平台用户、未绑定员工的公司级 API Key——本就覆盖全公司仓库）。
    其余（只有 warehouse_edit / warehouse_delete 的员工，或绑定了这类员工的 Key）返回其被分配的
    仓库 id 集合（含已停用的：停用不改变归属，否则停用后自己就再也启用不回来）。
    """
    is_super, perms, _ = get_actor_context()
    if is_super or 'company_all_access' in perms:
        return None
    actor = g.get('current_user')
    if actor is None or getattr(actor, 'type', None) != 'staff':
        return None
    from extensions.db import db
    from warehouse.staff.models import Staff
    staff = db.session.get(Staff, actor.id)
    return {w.id for w in staff.warehouses} if staff else set()


def _require_company_admin(action):
    """新建 / 删除仓库、改负责人（会把仓库加进负责人的可访问列表）只允许公司管理员 / 平台管理员"""
    if _restricted_warehouse_ids() is not None:
        raise ForbiddenException(f"Only company administrators can {action}", 16104)

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
        """Create a new warehouse (company administrators only)"""
        data = api_ns.payload
        _require_company_admin("create warehouses")
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
        """Update warehouse details (non-admins: only their own warehouses, manager unchanged)"""
        data = api_ns.payload
        warehouse = get_company_owned(Warehouse, warehouse_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)

        # 非公司管理员：只能改分配给自己的仓库；负责人原样提交可以，改了 → 16104
        # （Web 编辑页会把整条记录连同 manager_id 提交回来）
        scope = _restricted_warehouse_ids()
        if scope is not None:
            if warehouse.id not in scope:
                raise ForbiddenException("Permission denied: warehouse is outside your scope", 12001)
            if 'manager_id' in data and (data.get('manager_id') or None) != warehouse.manager_id:
                _require_company_admin("change the warehouse manager")

        updated_warehouse = WarehouseService.update_warehouse(warehouse_id, data)

        return updated_warehouse

    @permission_required(["all_access", "company_all_access", "warehouse_delete"])
    def delete(self, warehouse_id):
        """Delete a warehouse (company administrators only; 409 44004 while still referenced)"""
        get_company_owned(Warehouse, warehouse_id)
        _require_company_admin("delete warehouses")
        WarehouseService.delete_warehouse(warehouse_id)

        return {"message": "Warehouse deleted successfully"}, 200
