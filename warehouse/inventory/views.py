from flask_restx import Resource
from system.common import paginate,permission_required
from warehouse.common import warehouse_required, add_warehouse_filter, require_warehouse_scope
from .schemas import (
    api_ns, inventory_model, inventory_pagination_parser, pagination_model,
    pending_putaway_summary_model, pending_putaway_summary_parser,
)
from .pending import attach_pending_since
from .services import InventoryService


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
        filters = add_warehouse_filter({'company_id': args.get('company_id')})
        return InventoryService.pending_putaway_summary(filters, args.get('overdue_days')), 200

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