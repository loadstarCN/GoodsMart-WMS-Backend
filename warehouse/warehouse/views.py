from flask import g
from flask_restx import Resource, abort
from extensions.error import BadRequestException, ForbiddenException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id, get_actor_context
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Warehouse
from .schemas import api_ns, warehouse_model, warehouse_input_model, pagination_parser, pagination_model

from .services import WarehouseService


@api_ns.route('/<int:warehouse_id>/zones')
class WarehouseZones(Resource):
    @permission_required(['all_access', 'company_all_access', 'location_read'])
    def get(self, warehouse_id):
        from warehouse.location.models import Location
        from warehouse.stock.queries import location_stock, location_summary, shelf_of
        get_company_owned(Warehouse, warehouse_id)
        scope = _restricted_warehouse_ids()
        if scope is not None and warehouse_id not in scope:
            raise ForbiddenException('Warehouse is outside your scope', 12001)
        is_super, permissions, _ = get_actor_context()
        inventory_visible = is_super or 'company_all_access' in permissions or 'inventory_read' in permissions
        rows = (location_stock({'warehouse_id': warehouse_id, 'include_empty': True}) if inventory_visible else
                [(location, []) for location in Location.query.filter_by(warehouse_id=warehouse_id, is_active=True).order_by(Location.code).all()])
        zones = {}
        for location, stocks in rows:
            code = shelf_of(location.code)
            zone = zones.setdefault(code, dict(code=code, first_code=location.code, last_code=location.code,
                location_types=set(), locations=0, empty=0, quantity=0, capacity_m3=0.,
                used_volume_m3=0., volume_unknown_quantity=0, sized_locations=0))
            zone['last_code'] = location.code
            zone['location_types'].add(location.location_type)
            zone['locations'] += 1
            if inventory_visible:
                summary = location_summary(location, stocks)
                zone['empty'] += int(summary['is_empty'])
                zone['quantity'] += summary['quantity']
                if summary['capacity_m3']:
                    zone['sized_locations'] += 1
                    zone['capacity_m3'] += summary['capacity_m3']
                    zone['used_volume_m3'] += summary['used_volume_m3'] or 0
                    zone['volume_unknown_quantity'] += summary['volume_unknown_quantity'] or 0
        for zone in zones.values():
            zone['location_types'] = sorted(zone['location_types'])
            zone['volume_utilization'] = (round(zone['used_volume_m3'] / zone['capacity_m3'], 4)
                                          if zone['capacity_m3'] else None)
            if not inventory_visible:
                for key in ('empty', 'quantity', 'capacity_m3', 'used_volume_m3', 'volume_unknown_quantity', 'sized_locations'):
                    zone[key] = None
        return {'items': list(zones.values()), 'total': len(zones), 'locations': len(rows),
                'inventory_visible': bool(inventory_visible)}


@api_ns.route('/<int:warehouse_id>/overview')
class WarehouseOverview(Resource):
    @permission_required(['all_access', 'company_all_access', 'warehouse_read'])
    def get(self, warehouse_id):
        from sqlalchemy import func
        from extensions import db
        from warehouse.common.clock import local_today
        from warehouse.location.models import Location
        from warehouse.inventory.models import Inventory
        from warehouse.asn.models import ASN
        from warehouse.dn.models import DN

        warehouse = get_company_owned(Warehouse, warehouse_id)
        scope = _restricted_warehouse_ids()
        if scope is not None and warehouse_id not in scope:
            raise ForbiddenException('Permission denied: warehouse is outside your scope', 12001)
        is_super, permissions, _ = get_actor_context()
        allowed = lambda permission: is_super or 'company_all_access' in permissions or permission in permissions
        today = local_today(warehouse)
        result = {'date': today.isoformat()}
        if allowed('location_read'):
            result['locations'] = Location.query.filter_by(warehouse_id=warehouse_id, is_active=True).count()
        if allowed('inventory_read'):
            count, quantity = db.session.query(func.count(), func.coalesce(func.sum(Inventory.onhand_stock), 0)).filter(
                Inventory.warehouse_id == warehouse_id, Inventory.onhand_stock > 0).one()
            result['goods_count'], result['onhand'] = count, quantity
        if allowed('dn_read'):
            result['dispatch_today'] = DN.query.filter(DN.warehouse_id == warehouse_id, DN.is_active.is_(True),
                DN.expected_shipping_date == today, DN.status.in_(['pending', 'in_progress', 'picked', 'packed'])).count()
        if allowed('asn_read'):
            result['receive_today'] = ASN.query.filter(ASN.warehouse_id == warehouse_id, ASN.is_active.is_(True),
                ASN.expected_arrival_date == today, ASN.status == 'pending').count()
        return result, 200


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
