from flask_restx import Namespace, fields, inputs, reqparse
from werkzeug.datastructures import FileStorage
from system.common import generate_input_fields,pagination_parser, create_pagination_model
from system.user.schemas import user_simple_model as original_user_model  # 用户序列化器
from warehouse.location.schemas import location_simple_model  # 位置序列化器
from warehouse.company.schemas import comapny_simple_model as company_model  # 公司序列化器

# 初始化 Namespace
api_ns = Namespace('goods', description='Goods operations')

# 定义并重用 User 序列化器
user_model = api_ns.model('User', original_user_model)

# -----------------------------
# Goods 模型定义
# -----------------------------
goods_simple_model = api_ns.model('GoodsSimple', {
    'id': fields.Integer(readOnly=True, description='Goods ID'),
    'code': fields.String(required=True, description='Goods Code'),
    'name': fields.String(required=True, description='Goods Name'),
    'thumbnail_url': fields.String(description='Goods Thumbnail URL'),
    'price': fields.Float(description='Goods Price'),
    'discount_price': fields.Float(description='Discounted Price'),
    'brand': fields.String(description='Brand Information'),
    'manufacturer': fields.String(description='Manufacturer Information'),
})


# 基础 Goods 模型（只包含基本字段）
goods_base_model = api_ns.model('GoodsBase', {
    'id': fields.Integer(readOnly=True, description='Goods ID'),
    'company_id': fields.Integer(required=True,description='Company ID'),
    'company_name': fields.String(attribute='company.name', readOnly=True, description='Company Name'),
    'code': fields.String(required=True, description='Goods Code'),
    'name': fields.String(required=True, description='Goods Name'),
    'description': fields.String(description='Goods Description'),
    'unit': fields.String(description='Unit of Measurement', default='pcs'),
    'weight': fields.Float(description='Weight in kilograms'),
    'length': fields.Float(description='Length in centimeters'),
    'width': fields.Float(description='Width in centimeters'),
    'height': fields.Float(description='Height in centimeters'),
    'manufacturer': fields.String(description='Manufacturer Information'),
    'brand': fields.String(description='Brand Information'),
    'image_url': fields.String(description='Goods Image URL'),
    'thumbnail_url': fields.String(description='Goods Thumbnail URL'),
    'category': fields.String(description='Goods Category'),
    'tags': fields.String(description='Goods Tags (comma-separated)'),
    'price': fields.Float(description='Goods Price'),
    'discount_price': fields.Float(description='Discounted Price'),
    'currency': fields.String(description='Currency Code (ISO 4217 standard)', default='JPY'),
    'expiration_date': fields.Date(description='Expiration Date'),
    'production_date': fields.Date(description='Production Date'),
    'extra_data': fields.Raw(description='Additional Information in JSON format'),
    'is_active': fields.Boolean(description='Is the goods active?'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last update timestamp'),
    'created_by': fields.Integer(description='ID of the creator'),
    'creator': fields.Nested(user_model, readonly=True,  description='User name of the creator'),
})

# 复制基础模型的字段定义，用于构建输入模型
input_fields = generate_input_fields(goods_base_model)
goods_input_model = api_ns.model('GoodsInput', input_fields)

# -----------------------------
# GoodsLocation 模型定义
# -----------------------------

# GoodsLocation 模型
goods_location_base_model = api_ns.model('GoodsLocationBase', {
    'id': fields.Integer(readOnly=True, description='Goods Location ID'),
    'quantity': fields.Integer(description='Quantity in the location'),
    'created_at': fields.DateTime(readOnly=True, description='Creation timestamp'),
    'updated_at': fields.DateTime(readOnly=True, description='Last update timestamp'),
    'creator': fields.Nested(user_model, readonly=True,  description='User name of the creator'),
    'goods_id': fields.Integer(description='Goods ID'),
    'goods_code': fields.String(attribute='goods.code', readOnly=True, description='Goods Code'),
    'location_id': fields.Integer(description='Location ID'),
    'location_code': fields.String(attribute='location.code', readOnly=True, description='Location Code'),
    'warehouse_id': fields.Integer(attribute='location.warehouse.id', readOnly=True, description='Warehouse ID'),
    'warehouse_name': fields.String(attribute='location.warehouse.name', readOnly=True, description='Warehouse Name'),
})

goods_location_model = api_ns.inherit('GoodsLocation', goods_location_base_model, {
    'goods': fields.Nested(goods_base_model, readonly=True, description='Goods Details'),
    'location': fields.Nested(location_simple_model, readonly=True, description='Location Details')
})


# 使用删除字段的方法构建 GoodsLocation 输入模型
location_input_fields = generate_input_fields(goods_location_base_model)
goods_location_input_model = api_ns.model('GoodsLocationInput', location_input_fields)

# -----------------------------
# Goods 总模型（包含 locations 嵌套字段）
# -----------------------------
goods_model = api_ns.inherit('Goods', goods_base_model, {
    'company': fields.List(fields.Nested(company_model), description='Company Details'),
    'storage_records': fields.List(fields.Nested(goods_location_base_model), description='Storage records of the goods')
})

# -----------------------------
# 分页解析器 & 分页模型
# -----------------------------

# Goods 分页解析器（扩展基本分页参数，增加 Goods-specific 过滤条件）
goods_pagination_parser = pagination_parser.copy()
goods_pagination_parser.add_argument('name', type=str, help='Filter by goods name (partial match)')
goods_pagination_parser.add_argument('code', type=str, help='Filter by goods code (exact match)')
goods_pagination_parser.add_argument('is_active', type=inputs.boolean, help='Filter by active status')
goods_pagination_parser.add_argument('manufacturer', type=str, help='Filter by manufacturer name (partial match)')
goods_pagination_parser.add_argument('category', type=str, help='Filter by goods category')
goods_pagination_parser.add_argument('tags', type=str, help='Filter by goods tags (comma-separated)')
goods_pagination_parser.add_argument('price_min', type=float, help='Filter by minimum price')
goods_pagination_parser.add_argument('price_max', type=float, help='Filter by maximum price')
goods_pagination_parser.add_argument('discount_price_min', type=float, help='Filter by minimum discount price')
goods_pagination_parser.add_argument('discount_price_max', type=float, help='Filter by maximum discount price')
goods_pagination_parser.add_argument('currency', type=str, help='Filter by currency code (ISO 4217 standard)')
goods_pagination_parser.add_argument('brand', type=str, help='Filter by brand name (partial match)')
goods_pagination_parser.add_argument('expiration_date_min', type=inputs.date_from_iso8601, help='Filter by minimum expiration date')
goods_pagination_parser.add_argument('expiration_date_max', type=inputs.date_from_iso8601, help='Filter by maximum expiration date')
goods_pagination_parser.add_argument('production_date_min', type=inputs.date_from_iso8601, help='Filter by minimum production date')
goods_pagination_parser.add_argument('production_date_max', type=inputs.date_from_iso8601, help='Filter by maximum production date')
goods_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')
goods_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in name, code, manufacturer, category, tags, brand')
goods_pagination_parser.add_argument('goods_codes', type=lambda s: [code.strip() for code in s.split(',')], help='Goods Codes (comma separated)')
goods_pagination_parser.add_argument('company_id', type=int, help='Filter by Company ID')


# GoodsLocation 分页解析器
goods_location_pagination_parser = pagination_parser.copy()
goods_location_pagination_parser.add_argument('goods_id', type=int, help='Filter by Goods ID')
goods_location_pagination_parser.add_argument('goods_code', type=str, help='Filter by Goods Code')
goods_location_pagination_parser.add_argument('goods_name', type=str, help='Filter by Goods Name')
goods_location_pagination_parser.add_argument('location_id', type=int, help='Filter by Location ID')
goods_location_pagination_parser.add_argument('location_code', type=str, help='Filter by Location Code')
goods_location_pagination_parser.add_argument('quantity_min', type=int, help='Filter by minimum quantity')
goods_location_pagination_parser.add_argument('quantity_max', type=int, help='Filter by maximum quantity')
goods_location_pagination_parser.add_argument('keyword', type=str, help='Search by keyword in goods name, code, manufacturer, category, tags, brand, and location code')
goods_location_pagination_parser.add_argument('warehouse_id', type=int, help='Filter by Warehouse ID')


# 创建上传文件的请求解析器
upload_parser = reqparse.RequestParser()
upload_parser.add_argument('Authorization', 
                          location='headers',  # 关键点：从headers提取
                          help='Bearer token required')
upload_parser.add_argument('X-API-KEY',
                          location='headers',  # 关键点：从headers提取
                          help='API Key required')
upload_parser.add_argument('file', 
                          location='files', 
                          type=FileStorage, 
                          required=True)

goods_bulk_upload_parser = upload_parser.copy()
goods_bulk_upload_parser.add_argument(
                            'overwrite',
                            type=str,
                            choices=('skip', 'active', 'append', 'override'),  # 限定四种策略
                            default='skip',
                            help="Duplicate handling: skip/active/append/override"
                        )

goods_bulk_upload_parser.add_argument('company_id',
                          type=int, 
                          required=True, 
                          help='Company ID is required')


# 创建分页模型
goods_pagination_model = create_pagination_model(api_ns, goods_base_model)
goods_location_pagination_model = create_pagination_model(api_ns, goods_location_model)
