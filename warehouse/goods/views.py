import csv
from flask import g
from flask_restx import Resource,abort

from extensions import oss
from extensions.cache import cache
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import paginate,permission_required,parse_date
from warehouse.common import warehouse_required,add_warehouse_filter,check_goods_access,check_warehouse_access
from warehouse.company.services import CompanyService
from .schemas import (
    api_ns,
    goods_model,
    goods_location_model,
    goods_input_model,
    goods_location_input_model,
    goods_pagination_parser,
    goods_location_pagination_parser,
    goods_location_pagination_model,
    goods_pagination_model,
    upload_parser,
    goods_bulk_upload_parser,
)

from .services import GoodsService, GoodsLocationService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class GoodsList(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @warehouse_required()
    @api_ns.expect(goods_pagination_parser)
    @api_ns.marshal_with(goods_pagination_model) 
    # @cache.memoize(timeout=60)
    def get(self):
        """Get a paginated list of goods"""
        args = goods_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 将筛选参数打包到 dict 中
        filters = {
            'code': args.get('code'),
            'name': args.get('name'),
            'is_active': args.get('is_active'),
            'manufacturer': args.get('manufacturer'),
            'category': args.get('category'),
            'tags': args.get('tags'),
            'price_min': args.get('price_min'),
            'price_max': args.get('price_max'),
            'discount_price_min': args.get('discount_price_min'),
            'discount_price_max': args.get('discount_price_max'),
            'currency': args.get('currency'),
            'brand': args.get('brand'),
            'expiration_date_min': args.get('expiration_date_min'),
            'expiration_date_max': args.get('expiration_date_max'),
            'production_date_min': args.get('production_date_min'),
            'production_date_max': args.get('production_date_max'),
            'keyword': args.get('keyword'),
            'goods_codes': args.get('goods_codes'),
            'company_id': args.get('company_id'),
        }

        filters = add_warehouse_filter(filters)
        query = GoodsService.list_goods(filters)
        return paginate(query, page, per_page), 200
    
    @permission_required(["all_access","company_all_access","goods_edit"])
    @api_ns.expect(goods_input_model)
    @api_ns.marshal_with(goods_model)    
    def post(self):
        """Create a new goods"""
        data = api_ns.payload
        created_by = g.current_user.id
        print(data)
        print("---------------------------------")
        return GoodsService.create_goods(data,created_by), 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:goods_id>')
class GoodsDetail(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @warehouse_required()
    @api_ns.marshal_with(goods_model)
    def get(self, goods_id):
        """Get goods details"""
        user = g.current_user

        goods = GoodsService.get_goods(goods_id)

        if user.type == 'staff':
            # 验证商品所属公司
            if goods.company_id != user.company_id:
                raise NotFoundException("Goods not found in the specified or accessible warehouse",13003)
                # 检查公司级全局权限
            if not user.has_role('company_admin'):            
                if not check_goods_access(goods_id):
                    raise NotFoundException("Goods not found in the specified or accessible warehouse",13003)
            
        # 过滤 storage_records，只保留满足仓库过滤要求的记录
        goods.storage_records = [
            record for record in goods.storage_records if check_warehouse_access(record.location.warehouse_id)
        ]
                
        return goods, 200

    @permission_required(["all_access","company_all_access","goods_edit"])
    @api_ns.expect(goods_input_model)
    @api_ns.marshal_with(goods_model)
    def put(self, goods_id):
        """Update goods details"""
        data = api_ns.payload
        return GoodsService.update_goods(goods_id, data)

    @permission_required(["all_access","company_all_access","goods_delete"])
    def delete(self, goods_id):
        """Delete goods"""
        GoodsService.delete_goods(goods_id)
        return {"message": "Goods deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/locations/')
class GoodsLocationList(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @warehouse_required()
    @api_ns.expect(goods_location_pagination_parser)
    @api_ns.marshal_with(goods_location_pagination_model)
    def get(self):
        """Get a paginated list of goods locations"""
        args = goods_location_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'goods_id': args.get('goods_id'),
            'location_id': args.get('location_id'),
            'goods_code': args.get('goods_code'),
            'goods_name': args.get('goods_name'),
            'location_code': args.get('location_code'),
            'quantity_min': args.get('quantity_min'),
            'quantity_max': args.get('quantity_max'),
            'warehouse_id': args.get('warehouse_id'),
            'keyword': args.get('keyword'),
        }

        filters = add_warehouse_filter(filters)

        query = GoodsLocationService.list_goods_locations(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","goods_edit"])
    @api_ns.expect(goods_location_input_model)
    @api_ns.marshal_with(goods_location_model)
    def post(self):
        """Create a new goods location"""
        data = api_ns.payload
        return GoodsLocationService.create_goods_location(data), 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/locations/<int:goods_location_id>')
class GoodsLocationDetail(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @api_ns.marshal_with(goods_location_model)
    def get(self, goods_location_id):
        """Get goods location details"""
        return GoodsLocationService.get_goods_location(goods_location_id)

    @permission_required(["all_access","company_all_access","goods_edit"])
    @api_ns.expect(goods_location_input_model)
    @api_ns.marshal_with(goods_location_model)
    def put(self, goods_location_id):
        """Update goods location details"""
        
        data = api_ns.payload
        return GoodsLocationService.update_goods_location(goods_location_id, data)

    @permission_required(["all_access","company_all_access","goods_delete"])
    def delete(self, goods_location_id):
        """Delete goods location"""
        GoodsLocationService.delete_goods_location(goods_location_id)
        return  {"message": "GoodsLocation deleted successfully"}, 200



@api_ns.route('/image/upload')
class GoodsUpload(Resource):
    @permission_required(["all_access","company_all_access","goods_add","goods_edit"])
    @api_ns.expect(upload_parser)
    def post(self):
        """Upload an image"""
        args = upload_parser.parse_args()
        uploaded_file = args['file']
        file_url = oss.upload_file(uploaded_file, "goods/images/")
        return {"file_url": file_url}, 200

@api_ns.route('/bulk_upload')
class GoodsBulkUpload(Resource):
    @permission_required(["all_access","company_all_access","goods_add","goods_edit"])
    @api_ns.expect(goods_bulk_upload_parser)
    def post(self):
        """Upload a bulk goods file based on CSV headers"""
        args = goods_bulk_upload_parser.parse_args()
        uploaded_file = args['file']

        overwrite_mode = args.get('overwrite', 'skip').lower()
        company_id = args.get('company_id')


        # 权限检查
        if g.current_user.type == 'staff':
            if company_id != g.current_user.company_id:
                raise ForbiddenException("You do not have permission to operate on this company.", 12001)
            
        company = CompanyService.get_company(company_id)
        default_currency = company.default_currency if company else 'JPY'  # 默认货币为JPY

        # 文件格式检查
        if not uploaded_file.filename.lower().endswith('.csv'):
            raise BadRequestException("Only CSV files are supported.", 10004)

        
        # 读取并解析CSV内容
        file_content = uploaded_file.read().decode('utf-8-sig')
        reader = csv.DictReader(file_content.splitlines())

        # 检查必须的列
        required_columns = ['code', 'name']
        print("reader.fieldnames",reader.fieldnames)
        missing_columns = [col for col in required_columns if col not in reader.fieldnames]
        if missing_columns:
            raise BadRequestException(f"Missing required columns: {', '.join(missing_columns)}", 10005)

        goods_data = []
        for row in reader:
            # 检查必填字段的值
            missing_values = [col for col in required_columns if not row.get(col)]
            if missing_values:
                raise BadRequestException(f"Row {reader.line_num}: Missing values for {', '.join(missing_values)}", 10006)

            # 转换价格字段
            price = float(row['price'])

            # 构建商品数据
            goods_data.append({
                # 基础信息
                'code': row['code'],
                'name': row['name'],
                
                # 计量维度
                'unit': row.get('unit', 'pcs'),  # 默认单位为件
                'weight': float(row['weight']) if row.get('weight') else None,
                'length': float(row['length']) if row.get('length') else None,
                'width': float(row['width']) if row.get('width') else None,
                'height': float(row['height']) if row.get('height') else None,
                
                # 品牌信息
                'manufacturer': row.get('manufacturer'),
                'brand': row.get('brand'),
                
                # 多媒体
                'image_url': row.get('image_url'),
                'thumbnail_url': row.get('thumbnail_url') or row.get('image_url'),  # 自动回退
                
                # 分类标签
                'category': row.get('category'),
                'tags': row.get('tags'),
                
                # 价格相关
                'price': price,
                'discount_price': float(row['discount_price']) if row.get('discount_price') else None,
                'currency': row.get('currency', default_currency),  
                
                # 日期信息
                'expiration_date': parse_date(row.get('expiration_date')),
                'production_date': parse_date(row.get('production_date')),
                
                # 描述信息
                'description': row.get('description'),
                
                # 系统字段
                'company_id': company_id
            })

        # 批量创建商品
        created_by = g.current_user.id
        new_goods_list = GoodsService.bulk_create_goods(goods_data, created_by, override_mode=overwrite_mode)
        
        return {
            "message": f"Bulk upload successful. Processed {len(new_goods_list)} goods."
        }, 200

    