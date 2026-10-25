from flask_restx import Namespace, fields, inputs
from system.common import pagination_parser,create_pagination_model
from warehouse.goods.schemas import goods_model,goods_simple_model
from warehouse.warehouse.schemas import warehouse_simple_model as warehouse_model
from .pending import MAX_OVERDUE_DAYS, PENDING_PUTAWAY_OVERDUE_DAYS

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
    'sorted_damage_stock': fields.Integer(
        default=0, readOnly=True,
        description='Damaged part of sorted_stock (waiting to be put away; only to damaged locations). '
                    'Good items waiting = sorted_stock - sorted_damage_stock (the only part that can go to '
                    'standard / return locations, else 400 15016)'),
    'pending_since': fields.DateTime(
        readOnly=True,
        description='When the oldest item still waiting to be put away entered sorted_stock (UTC, +00:00; '
                    'first-in-first-out estimate from sorting completion / short-pack and DN-cancel returns / '
                    'removals); null when nothing is waiting'),
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
inventory_pagination_parser.add_argument(
    'below_low_threshold', type=inputs.boolean, location='args',
    help='true: available_stock_for_sale < low_stock_threshold (thresholds of -1 are disabled and never match)')
inventory_pagination_parser.add_argument(
    'above_high_threshold', type=inputs.boolean, location='args',
    help='true: available_stock_for_sale > high_stock_threshold (thresholds of -1 never match)')
inventory_pagination_parser.add_argument(
    'pending_putaway', type=inputs.boolean, location='args',
    help='true: only rows with stock waiting to be put away (sorted_stock > 0; '
         'sorted_damage_stock of it can only go to damaged locations)')

# 待上架汇总（首页 / 侧栏的数字）：范围与库存列表相同（X-WAREHOUSE-ID / warehouse_id → 该仓库；否则调用方可访问的仓库）
pending_putaway_summary_parser = api_ns.parser()
pending_putaway_summary_parser.add_argument('warehouse_id', type=int, location='args',
                                            help='Warehouse ID (or the X-WAREHOUSE-ID header)')
pending_putaway_summary_parser.add_argument('keyword', type=str, location='args',
                                            help='Goods keyword, same as GET /inventory/ (code / name / manufacturer '
                                                 '/ category / tags / brand)')
pending_putaway_summary_parser.add_argument('company_id', type=int, location='args',
                                            help='Only warehouses of this company (platform users; staff / API keys '
                                                 'are already limited to their own company)')
pending_putaway_summary_parser.add_argument(
    'overdue_days', type=inputs.int_range(0, MAX_OVERDUE_DAYS), location='args', default=PENDING_PUTAWAY_OVERDUE_DAYS,
    help=f'Items waiting longer than this many days are overdue (default {PENDING_PUTAWAY_OVERDUE_DAYS}, '
         f'0-{MAX_OVERDUE_DAYS})')

pending_putaway_summary_model = api_ns.model('PendingPutawaySummary', {
    'warehouse_id': fields.Integer(description='The warehouse the summary is for (null = all accessible warehouses)'),
    'company_id': fields.Integer(description='company_id filter given (null = none)'),
    'inventory_count': fields.Integer(description='Inventory rows with stock waiting to be put away '
                                                  '(= total of GET /inventory/?pending_putaway=true)'),
    'goods_count': fields.Integer(description='Distinct goods with stock waiting to be put away'),
    'total_quantity': fields.Integer(description='Items waiting to be put away (sum of sorted_stock)'),
    'damaged_quantity': fields.Integer(description='Of which damaged (sum of sorted_damage_stock; '
                                                   'damaged locations only)'),
    'good_quantity': fields.Integer(description='Of which good (total_quantity - damaged_quantity)'),
    'overdue_days': fields.Integer(description='Overdue threshold used, in days'),
    'overdue_quantity': fields.Integer(description='Items waiting longer than overdue_days (first-in-first-out)'),
    'overdue_inventory_count': fields.Integer(description='Inventory rows with at least one overdue item'),
    'oldest_pending_since': fields.DateTime(description='Earliest pending_since of the rows (UTC, +00:00; '
                                                        'null = nothing waiting / unknown)'),
})

# 创建分页模型
pagination_model = create_pagination_model(api_ns, inventory_base_model)