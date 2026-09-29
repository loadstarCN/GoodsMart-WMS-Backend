import csv
from decimal import Decimal, InvalidOperation
from flask import g
from flask_restx import Resource,abort

from extensions import oss
from extensions.cache import cache
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import paginate,permission_required,parse_date
from system.common.permissions import get_actor_company_id
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter, check_goods_access, check_warehouse_access,
    get_company_owned, get_warehouse_owned, require_fields,
)
from warehouse.company.services import CompanyService
from .models import Goods, GoodsLocation
from .schemas import (
    api_ns,
    goods_model,
    goods_location_model,
    goods_input_model,
    goods_pagination_parser,
    goods_location_pagination_parser,
    goods_location_pagination_model,
    goods_pagination_model,
    upload_parser,
    goods_bulk_upload_parser,
)

from .services import GoodsService, GoodsLocationService, clean_origin_country, resolve_spec_source


def _parse_csv_number(row, column, line_num):
    """CSV 里的数值列：空值 → None；非数字 → 400（而不是 ValueError 变 500）"""
    raw = (row.get(column) or '').strip()
    if not raw:
        return None
    try:
        return float(Decimal(raw))
    except (InvalidOperation, ValueError):
        raise BadRequestException(f"Row {line_num}: invalid number for '{column}': {raw}", 10012)


def _parse_csv_origin_country(row, line_num):
    """CSV 的 origin_country 列：空 → None；不是有效 ISO 3166-1 alpha-2 → 400（10014）"""
    raw = (row.get('origin_country') or '').strip()
    try:
        return clean_origin_country(raw)
    except BadRequestException:
        raise BadRequestException(f"Row {line_num}: invalid origin_country: {raw}", 10014, 'origin_country')


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
            'origin_missing': args.get('origin_missing'),
            # 商品是公司级主数据：员工 / 公司级 API Key 强制只看本公司
            'company_id': get_actor_company_id() or args.get('company_id'),
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
        # 员工 / 公司级 API Key 一律落在自己公司（防止跨公司建档）
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            data['company_id'] = actor_company_id
        require_fields(data, 'company_id', 'code', 'name')
        created_by = require_actor_user_id()
        spec_source = resolve_spec_source(data.pop('spec_source', None))
        return GoodsService.create_goods(data, created_by, spec_source=spec_source), 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:goods_id>')
class GoodsDetail(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @warehouse_required()
    @api_ns.marshal_with(goods_model)
    def get(self, goods_id):
        """Get goods details"""
        goods = get_company_owned(Goods, goods_id)

        # 非公司管理员的员工：商品还必须在其可访问仓库里有库存记录
        user = g.get('current_user')
        if user is not None and user.type == 'staff' and not user.has_role('company_admin'):
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
        get_company_owned(Goods, goods_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)
        spec_source = resolve_spec_source(data.pop('spec_source', None))
        return GoodsService.update_goods(goods_id, data, spec_source=spec_source)

    @permission_required(["all_access","company_all_access","goods_delete"])
    def delete(self, goods_id):
        """Delete goods"""
        get_company_owned(Goods, goods_id)
        GoodsService.delete_goods(goods_id)
        return {"message": "Goods deleted successfully"}, 200


# 库位库存（GoodsLocation）只读：数量只能由上架 / 移库 / 下架 / 调整等单据流程变更，
# 不再提供直写端点（否则会绕过 Inventory 汇总）。
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
            'goods_ids': args.get('goods_ids'),
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


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/locations/<int:goods_location_id>')
class GoodsLocationDetail(Resource):

    @permission_required(["all_access","company_all_access","goods_read"])
    @warehouse_required()
    @api_ns.marshal_with(goods_location_model)
    def get(self, goods_location_id):
        """Get goods location details"""
        return get_warehouse_owned(GoodsLocation, goods_location_id, 'location.warehouse_id')



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


        # 权限检查：员工 / 公司级 API Key 只能导入到自己公司
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None and company_id != actor_company_id:
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
        missing_columns = [col for col in required_columns if col not in (reader.fieldnames or [])]
        if missing_columns:
            raise BadRequestException(f"Missing required columns: {', '.join(missing_columns)}", 10005)

        goods_data = []
        for row in reader:
            # 检查必填字段的值
            missing_values = [col for col in required_columns if not row.get(col)]
            if missing_values:
                raise BadRequestException(f"Row {reader.line_num}: Missing values for {', '.join(missing_values)}", 10006)

            # 数值列可缺省；非数字 → 400
            line_num = reader.line_num
            price = _parse_csv_number(row, 'price', line_num)
            discount_price = _parse_csv_number(row, 'discount_price', line_num)
            if price is not None and price < 0:
                raise BadRequestException(f"Row {line_num}: price must be >= 0", 10012)
            if discount_price is not None and (price is None or discount_price > price):
                raise BadRequestException(f"Row {line_num}: discount_price must be <= price", 10012)

            # 构建商品数据
            goods_data.append({
                # 基础信息
                'code': row['code'],
                'name': row['name'],

                # 计量维度
                'unit': row.get('unit') or 'pcs',  # 默认单位为件
                'weight': _parse_csv_number(row, 'weight', line_num),
                'length': _parse_csv_number(row, 'length', line_num),
                'width': _parse_csv_number(row, 'width', line_num),
                'height': _parse_csv_number(row, 'height', line_num),
                'origin_country': _parse_csv_origin_country(row, line_num),

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
                'discount_price': discount_price,
                'currency': row.get('currency') or default_currency,

                # 日期信息
                'expiration_date': parse_date(row.get('expiration_date')),
                'production_date': parse_date(row.get('production_date')),

                # 描述信息
                'description': row.get('description'),

                # 系统字段
                'company_id': company_id
            })

        # 批量创建商品
        created_by = require_actor_user_id()
        new_goods_list = GoodsService.bulk_create_goods(goods_data, created_by, override_mode=overwrite_mode,
                                                        spec_source='import')

        return {
            "message": f"Bulk upload successful. Processed {len(new_goods_list)} goods."
        }, 200


