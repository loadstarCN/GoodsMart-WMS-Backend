import time
from flask import g
from flask_restx import Resource
from system.common import paginate,permission_required
from warehouse.common import warehouse_required
from warehouse.common.utils import add_warehouse_filter
from .schemas import api_ns, inventory_model, inventory_pagination_parser,pagination_model
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
        }

        filters = add_warehouse_filter(filters)

        query = InventoryService.list_inventories(filters)
        
        return paginate(query, page, per_page, get_all), 200

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

        return InventoryService.get_inventory(goods_id,warehouse_id), 200

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