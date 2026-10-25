from flask import g, request
from flask_restx import Resource
from system.common import paginate,permission_required
from warehouse.common import warehouse_required, add_warehouse_filter, require_warehouse_scope
from .schemas import (
    api_ns, inventory_model, inventory_pagination_parser, pagination_model,
    pending_putaway_summary_model, pending_putaway_summary_parser,
)
from .availability import availability, parse_codes
from .pending import attach_pending_since
from .services import InventoryService

# 发货单批量添加要查可售量：建发货单的人（dn_edit）不一定有库存列表权限
_AVAILABILITY_PERMISSIONS = ["all_access", "company_all_access", "inventory_read", "dn_edit"]


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class InventoryList(Resource):

    @permission_required(["all_access","company_all_access","inventory_read"])
    @warehouse_required()
    @api_ns.marshal_with(pagination_model)
    @api_ns.expect(inventory_pagination_parser)
    def get(self):
        """Get a paginated list of inventory"""
        args = inventory_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data
        
        # 将筛选参数打包到 dict 中
        filters = {
            'goods_id': args.get('goods_id'),
            'goods_codes': args.get('goods_codes'),
            'low_stock_threshold': args.get('low_stock_threshold'),
            'high_stock_threshold': args.get('high_stock_threshold'),
            'keyword': args.get('keyword'),
            # true：只返回还有待上架的行（sorted_stock > 0）
            'pending_putaway': args.get('pending_putaway'),
            # true：可售低于补货阈值 / 高于积压阈值（阈值 -1 不算）
            'below_low_threshold': args.get('below_low_threshold'),
            'above_high_threshold': args.get('above_high_threshold'),
        }

        filters = add_warehouse_filter(filters)

        query = InventoryService.list_inventories(filters)

        result = paginate(query, page, per_page, get_all)
        # 每行的 pending_since（最早一件待上架的进入时刻，先进先上估算）：一页几条查询
        attach_pending_since(result['items'])
        return result, 200

    # @permission_required(["all_access","company_all_access","inventory_edit"])
    # @api_ns.expect(inventory_input_model)
    # @api_ns.marshal_with(inventory_model)
    # def post(self):
    #     """Create a new inventory record"""
    #     from .services import InventoryService
    #     data = api_ns.payload
    #     new_inventory = InventoryService.create_inventory(data)
    #     return new_inventory, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/pending-putaway/summary')
class InventoryPendingPutawaySummary(Resource):

    # 首页 / 侧栏的数字：做上架的角色（putaway_read / putaway_edit）也能看
    @permission_required(["all_access", "company_all_access", "inventory_read", "putaway_read", "putaway_edit"])
    @warehouse_required()
    @api_ns.expect(pending_putaway_summary_parser)
    @api_ns.marshal_with(pending_putaway_summary_model)
    def get(self):
        """Summary of the stock waiting to be put away (for the home page / sidebar badges)

        Scope is the same as GET /inventory/: the warehouse in `warehouse_id` / `X-WAREHOUSE-ID`, else every
        warehouse the caller can access (staff / API keys: their own company); `company_id` narrows it further.
        `keyword` filters the goods like GET /inventory/ (code / name / manufacturer / category / tags / brand).
        Counts only rows with `sorted_stock > 0`:
        - `inventory_count`: rows (= `total` of `GET /inventory/?pending_putaway=true` with the same scope)
        - `goods_count`: distinct goods
        - `total_quantity`: items waiting (sum of `sorted_stock`)
        - `damaged_quantity`: of which damaged (sum of `sorted_damage_stock`; can only go to damaged locations)
        - `good_quantity`: `total_quantity - damaged_quantity`
        - `overdue_quantity` / `overdue_inventory_count`: items / rows waiting longer than `overdue_days`
          (query parameter, default 3, 0-365), first-in-first-out like `pending_since`
        - `oldest_pending_since`: earliest `pending_since` (UTC, +00:00)
        """
        args = pending_putaway_summary_parser.parse_args()
        filters = add_warehouse_filter({'company_id': args.get('company_id'), 'keyword': args.get('keyword')})
        return InventoryService.pending_putaway_summary(filters, args.get('overdue_days')), 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/availability', '/availability/')
class InventoryAvailability(Resource):
    """发货单批量添加：按仓库批量查可售量（warehouse/inventory/availability.py）"""

    @permission_required(_AVAILABILITY_PERMISSIONS)
    @warehouse_required(force_warehouse=True)
    def get(self):
        """Available-for-sale quantity of many goods codes in one warehouse (adding goods to a DN in bulk)

        Warehouse: `warehouse_id` or the X-WAREHOUSE-ID header (required, else 400 14003). `goods_codes`: comma
        separated; trimmed, de-duplicated, at most 1,000; exact match on the goods of the warehouse's company
        (POST with a JSON body {goods_codes: [...]} for long lists).
        → {warehouse_id, items: [{goods_id, code, name, unit, is_active, thumbnail_url, available_stock_for_sale
        (= onhand_stock - locked_stock - dn_stock, standard locations only; 0 without an inventory row), onhand_stock,
        dn_stock, locked_stock}] (goods found, in the requested order), not_found: [codes not found]}.
        Permission: inventory_read or dn_edit.
        400 15019: goods_codes missing / more than 1,000 (field goods_codes).
        """
        return availability(g.warehouse, parse_codes(request.args.get('goods_codes'))), 200

    @permission_required(_AVAILABILITY_PERMISSIONS)
    @warehouse_required(force_warehouse=True)
    def post(self):
        """Same as GET, codes in the JSON body {goods_codes: [...]} (warehouse: warehouse_id query / X-WAREHOUSE-ID)"""
        body = request.get_json(silent=True)
        return availability(g.warehouse, parse_codes(body.get('goods_codes') if isinstance(body, dict) else None)), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/goods/<int:goods_id>/warehouse/<int:warehouse_id>')
class InventoryDetail(Resource):

    @permission_required(["all_access","company_all_access","inventory_read"])
    @warehouse_required()
    @api_ns.marshal_with(inventory_model)
    def get(self,goods_id,warehouse_id):
        """
        Get inventory details by Goods ID.
        - `goods_id`: Required, the ID of the goods.
        - `warehouse_id`: Required, the ID of the warehouse. This can be provided either as `warehouse_id` in the request or via the `X-Warehouse-ID` header.
        """
        # 路径里的 warehouse_id 必须在调用方可访问范围内（B-24）
        require_warehouse_scope(warehouse_id, 'Inventory')
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        attach_pending_since([inventory])
        return inventory, 200

    # @permission_required(["all_access","company_all_access","inventory_edit"])
    # @api_ns.expect(inventory_input_model)
    # @api_ns.marshal_with(inventory_model)
    # def put(self, goods_id, warehouse_id):
    #     """Update inventory details"""
    #     from .services import InventoryService
    #     data = api_ns.payload
    #     inventory = InventoryService.update_inventory(goods_id, warehouse_id, data)
    #     return inventory

    # @permission_required(["all_access","company_all_access","inventory_delete"])
    # def delete(self, goods_id, warehouse_id):
    #     """Delete an inventory record by Goods ID"""
    #     from .services import InventoryService
    #     InventoryService.delete_inventory(goods_id, warehouse_id)
    #     return {"message": "Inventory record deleted successfully"}, 200