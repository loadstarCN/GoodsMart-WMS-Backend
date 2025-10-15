from flask_restx import Namespace, fields
from system.common import pagination_parser,create_pagination_model
from warehouse.goods.schemas import goods_model,goods_simple_model
from warehouse.warehouse.schemas import warehouse_simple_model as warehouse_model

# 创建命名空间
api_ns = Namespace('inventory', description='Inventory related operations')

# Inventory 序列化器
inventory_base_model = api_ns.model('InventoryBase', {
    'goods_id': fields.Integer(readOnly=True, description='Associated Goods ID (Primary Key)'),
    'warehouse_id': fields.Integer(readOnly=True, description='Associated Warehouse ID (Primary Key)'),
    'total_stock': fields.Integer(default=0, description='Total stock quantity'),
    'onhand_stock': fields.Integer(default=0, description='Available stock quantity'),
    'locked_stock': fields.Integer(default=0, description='Locked stock quantity'),
    'damage_stock': fields.Integer(default=0, description='Damaged stock quantity'),
    'return_stock': fields.Integer(default=0, description='Backorder stock quantity'),
    'available_stock': fields.Integer(readOnly=True, description='Available stock quantity (calculated)'),
    'available_stock_for_sale': fields.Integer(readOnly=True, description='Available stock for sale (calculated)'),
    'low_stock_threshold': fields.Integer(description='Low Stock Warning Threshold (-1 to disable)'),
    'high_stock_threshold': fields.Integer(description='High Stock Warning Threshold (-1 to disable)'),

    # 收货相关字段
    'asn_stock': fields.Integer(default=0, description='ASN stock quantity (expected arrivals)'),
    'received_stock': fields.Integer(default=0, description='Received stock quantity'),
    'sorted_stock': fields.Integer(default=0, description='Receiving sort stock quantity'),
    # 发货相关字段
    'dn_stock': fields.Integer(default=0, description='DN stock quantity (pending shipments)'),
    'picked_stock': fields.Integer(default=0, description='Pick stock quantity (to be picked)'),
    'packed_stock': fields.Integer(default=0, description='Packed stock quantity (completed picks)'),
    'delivered_stock': fields.Integer(default=0, description='Delivered stock quantity (ready for delivery)'),
    'remark': fields.String(default="", description='remark about the inventory'),
    'create_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'update_at': fields.DateTime(readOnly=True, description='Last update timestamp'),
    'goods': fields.Nested(goods_simple_model,readOnly=True,  description='Details of the associated goods'),
    'warehouse': fields.Nested(warehouse_model,readOnly=True,  description='Details of the associated warehouse'),
})

inventory_model = api_ns.inherit('Inventory', inventory_base_model, {
    'goods': fields.Nested(goods_model,readOnly=True,  description='Details of the associated goods'),
    'warehouse': fields.Nested(warehouse_model,readOnly=True,  description='Details of the associated warehouse'),
})

# Inventory 创建模型
inventory_input_model = api_ns.model('InventoryInput', {
    'goods_id': fields.Integer(required=True, description='Associated Goods ID'),
    'warehouse_id': fields.Integer(required=True, description='Associated Warehouse ID'),
    'total_stock': fields.Integer(default=0, description='Total stock quantity'),
    'onhand_stock': fields.Integer(default=0, description='Available stock quantity'),
    'damage_stock': fields.Integer(default=0, description='Damaged stock quantity'),
    'return_stock': fields.Integer(default=0, description='Backorder stock quantity'),
    
    'low_stock_threshold': fields.Integer(description='Low Stock Warning Threshold (-1 to disable)'),
    'high_stock_threshold': fields.Integer(description='High Stock Warning Threshold (-1 to disable)'),

    # 收货相关字段
    'asn_stock': fields.Integer(default=0, description='ASN stock quantity (expected arrivals)'),
    'received_stock': fields.Integer(default=0, description='Received stock quantity'),
    'sorted_stock': fields.Integer(default=0, description='Receiving sort stock quantity'),
    # 发货相关字段
    'dn_stock': fields.Integer(default=0, description='DN stock quantity (pending shipments)'),
    'pick_stock': fields.Integer(default=0, description='Pick stock quantity (to be picked)'),
    'picked_stock': fields.Integer(default=0, description='Picked stock quantity (completed picks)'),
    'delivery_stock': fields.Integer(default=0, description='Delivery stock quantity (ready for delivery)'),
    'remark': fields.String(default="", description='remark about the inventory'),
})

# Inventory 列表分页模型
inventory_pagination_parser = pagination_parser.copy()
inventory_pagination_parser.add_argument('goods_id', type=int, help='Associated Goods ID', location='args')
inventory_pagination_parser.add_argument('warehouse_id', type=int, help='Associated Warehouse ID', location='args')
inventory_pagination_parser.add_argument('goods_codes', type=lambda s: [code.strip() for code in s.split(',')], help='Goods Codes (comma separated)', location='args')
inventory_pagination_parser.add_argument('low_stock_threshold', type=int, help='Low Stock Warning Threshold', location='args')
inventory_pagination_parser.add_argument('high_stock_threshold', type=int, help='High Stock Warning Threshold', location='args')
inventory_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in goods name, code, manufacturer, category, tags, brand', location='args')

# 创建分页模型
pagination_model = create_pagination_model(api_ns, inventory_base_model)