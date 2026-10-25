import csv
import io
from decimal import Decimal, InvalidOperation
from flask import g, make_response
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
    goods_origin_country_input_model,
    goods_origin_country_model,
    goods_pagination_parser,
    goods_location_pagination_parser,
    goods_location_pagination_model,
    goods_pagination_model,
    upload_parser,
    goods_bulk_upload_parser,
)

from .services import (
    GoodsService, GoodsLocationService, clean_origin_country, clean_goods_numbers, goods_number_error,
    resolve_spec_source,
)


# 商品导入（CSV）的列：模板（GET /goods/bulk_upload/template）与导入读的是同一份清单，不会再出现
# 「后端读 origin_country、模板里却没有这一列」。重量 kg，长宽高 mm（正整数），日期 YYYY-MM-DD
GOODS_IMPORT_COLUMNS = (
    'code', 'name', 'unit', 'weight', 'length', 'width', 'height', 'origin_country', 'manufacturer', 'brand',
    'image_url', 'thumbnail_url', 'category', 'tags', 'price', 'discount_price', 'currency', 'expiration_date',
    'production_date', 'description',
)
GOODS_IMPORT_REQUIRED_COLUMNS = ('code', 'name')
GOODS_IMPORT_SAMPLE_ROW = (
    'SAMPLE-001', 'Sample goods', 'pcs', '0.328', '140', '130', '13', 'JP', 'Sample Maker', 'Sample Brand',
    'https://example.com/sample-001.jpg', '', 'Sample category', 'tag1,tag2', '1760', '1500', 'JPY', '', '2025-07-01',
    'Sample description',
)
# 商品导入的业务码（失败都带 details，界面按 row / rows / code / columns 显示行号与列，按业务码翻译）：
# 10004 不是 .csv；10005 缺必填列（details {columns}）；10006 某行必填值为空（{row, columns}）；
# 10012 数值 / 价格不合法（{row, columns, value}）；10014 原产国不合法（{row, columns, value}）；
# 16100 数值超出范围（{row, columns, field}）；16101 文件内商品编码重复（{code, rows}）；10016 文件读不了（不是 UTF-8 文本）
GOODS_IMPORT_NOT_READABLE = 10016


def _import_error(message, code, row=None, columns=None, **details):
    """商品导入的错误：details 带 row（文件行号，表头是第 1 行）/ columns（相关列）等"""
    payload = {}
    if row is not None:
        payload['row'] = row
    if columns:
        payload['columns'] = list(columns)
    payload.update({k: v for k, v in details.items() if v is not None})
    return BadRequestException(message, code, columns[0] if columns and len(columns) == 1 else None,
                               details=payload)


def _parse_csv_number(row, column, line_num):
    """CSV 里的数值列：空值 → None；非数字 → 400 10012；NaN / Infinity → 400 16100（带行号）"""
    raw = (row.get(column) or '').strip()
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise _import_error(f"Row {line_num}: invalid number for '{column}': {raw}", 10012, line_num, [column],
                            value=raw)
    if not number.is_finite():
        raise goods_number_error(column, line_num)
    return number


class _GoodsOutput:
    """序列化用的只读视图：storage_records 换成按仓库范围过滤后的列表，其余字段取自商品本身。

    不能把过滤结果赋给 goods.storage_records：它是 lazy='dynamic' + delete-orphan 的关系，
    赋值后 flush（PUT 请求的审计日志会 commit）就会 DELETE 被滤掉的其他仓库库位记录，
    GET 虽然最后回滚，也会在请求期间锁住这些行（B-101）。
    """

    def __init__(self, goods, storage_records):
        self._goods = goods
        self.storage_records = storage_records

    def __getattr__(self, name):
        return getattr(self._goods, name)


def _with_accessible_storage(goods):
    """商品 + 调用方仓库范围内的库位记录（须在 @warehouse_required() 之后调用）"""
    records = [
        record for record in goods.storage_records if check_warehouse_access(record.location.warehouse_id)
    ]
    return _GoodsOutput(goods, records)


def _require_goods_visible(goods_id):
    """非公司管理员的员工：商品还必须在其可访问仓库里有库存记录，否则 404（与 GET 一致）"""
    user = g.get('current_user')
    if user is not None and user.type == 'staff' and not user.has_role('company_admin'):
        if not check_goods_access(goods_id):
            raise NotFoundException("Goods not found in the specified or accessible warehouse", 13003)


def _parse_csv_origin_country(row, line_num):
    """CSV 的 origin_country 列：空 → None；不是有效 ISO 3166-1 alpha-2 → 400（10014）"""
    raw = (row.get('origin_country') or '').strip()
    try:
        return clean_origin_country(raw)
    except BadRequestException:
        raise _import_error(f"Row {line_num}: invalid origin_country: {raw}", 10014, line_num, ['origin_country'],
                            value=raw)


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
            'image_missing': args.get('image_missing'),
            'spec_missing': args.get('spec_missing'),
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
        _require_goods_visible(goods_id)

        # storage_records 只输出调用方仓库范围内的记录（局部过滤，不改 ORM 关系）
        return _with_accessible_storage(goods), 200

    @permission_required(["all_access","company_all_access","goods_edit"])
    @warehouse_required()
    @api_ns.expect(goods_input_model)
    @api_ns.marshal_with(goods_model)
    def put(self, goods_id):
        """Update goods details"""
        data = api_ns.payload
        get_company_owned(Goods, goods_id)
        # 归属公司不允许通过更新接口迁移
        data.pop('company_id', None)
        spec_source = resolve_spec_source(data.pop('spec_source', None))
        goods = GoodsService.update_goods(goods_id, data, spec_source=spec_source)
        # 响应里的库位记录同 GET：只给调用方仓库范围内的
        return _with_accessible_storage(goods)

    @permission_required(["all_access","company_all_access","goods_delete"])
    def delete(self, goods_id):
        """Delete goods"""
        get_company_owned(Goods, goods_id)
        GoodsService.delete_goods(goods_id)
        return {"message": "Goods deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:goods_id>/origin-country')
class GoodsOriginCountry(Resource):

    # 最小权限：仓库作业账号（入库分拣 / 打包）看包装「MADE IN」录入原产国，
    # 不需要整条商品编辑权限（goods_edit）
    @permission_required(["all_access","company_all_access","goods_edit","sorting_edit","packing_edit"])
    @warehouse_required()
    @api_ns.expect(goods_origin_country_input_model)
    @api_ns.marshal_with(goods_origin_country_model)
    def put(self, goods_id):
        """Update only the country of origin of a goods (response: id, code, origin_country)"""
        # 归属校验：公司同商品修改接口；非公司管理员的员工还要求商品在其可访问仓库里有库存（同 GET，否则 404）。
        # 响应只给 id / code / origin_country，不再带出其他仓库的库位库存
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        data = api_ns.payload
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 10014)
        if 'origin_country' not in data:
            raise BadRequestException("origin_country is required (use \"\" or null to clear it)",
                                      10014, 'origin_country')
        spec_source = resolve_spec_source(data.get('spec_source'))
        return GoodsService.update_origin_country(goods_id, data['origin_country'], spec_source=spec_source)


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
            'location_type': args.get('location_type'),
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

@api_ns.route('/bulk_upload/template')
class GoodsBulkUploadTemplate(Resource):
    @permission_required(["all_access","company_all_access","goods_add","goods_edit"])
    def get(self):
        """Download the goods import template (CSV, UTF-8 with BOM): the header lists every column the import reads
        (including origin_country), followed by one sample row"""
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(GOODS_IMPORT_COLUMNS)
        writer.writerow(GOODS_IMPORT_SAMPLE_ROW)
        response = make_response('﻿' + buffer.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename="Goods_template.csv"'
        return response


@api_ns.route('/bulk_upload')
class GoodsBulkUpload(Resource):
    @permission_required(["all_access","company_all_access","goods_add","goods_edit"])
    @api_ns.expect(goods_bulk_upload_parser)
    def post(self):
        """Upload a bulk goods file based on CSV headers

        Columns: see GET /goods/bulk_upload/template (code and name are required). Duplicate handling (`overwrite`):
        skip / active / append (fill blank fields of existing goods) / override (non-empty values replace the
        existing ones; blank weight, dimensions, origin_country, category, tags and discount_price never clear them).

        Response: `{message, mode, processed, created, updated, skipped, columns, ignored_columns}` (the counts are for
        the front end to build a translated message). Errors are 400 with a business code and `details`
        (`row` = line number in the file, header is line 1; `rows`; `code`; `columns`; `value`): 10004 not a .csv,
        10005 required columns missing, 10006 required value empty, 10012 invalid number / price, 10014 invalid
        origin_country, 16100 number out of range, 16101 duplicate code in the file, 10016 the file is not UTF-8 text.
        """
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
            raise _import_error("Only CSV files are supported.", 10004, filename=uploaded_file.filename)

        # 读取并解析CSV内容（UTF-8，带不带 BOM 都行）
        try:
            file_content = uploaded_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            raise _import_error("The CSV file must be UTF-8 encoded text.", GOODS_IMPORT_NOT_READABLE,
                                reason='NOT_UTF8')
        reader = csv.DictReader(file_content.splitlines())

        # 检查必须的列
        header = [name for name in (reader.fieldnames or []) if name is not None]
        missing_columns = [col for col in GOODS_IMPORT_REQUIRED_COLUMNS if col not in header]
        if missing_columns:
            raise _import_error(f"Missing required columns: {', '.join(missing_columns)}", 10005,
                                columns=missing_columns)

        goods_data = []
        first_rows = {}  # code → 首次出现的行号（同一文件里同一编码出现两次 → 400 16101，而不是撞唯一键 500）
        for row in reader:
            line_num = reader.line_num
            # 检查必填字段的值
            missing_values = [col for col in GOODS_IMPORT_REQUIRED_COLUMNS if not (row.get(col) or '').strip()]
            if missing_values:
                raise _import_error(f"Row {line_num}: Missing values for {', '.join(missing_values)}", 10006,
                                    line_num, missing_values)

            code = row['code']
            if code in first_rows:
                raise BadRequestException(
                    f"Row {line_num}: duplicate code '{code}' (first seen in row {first_rows[code]})", 16101,
                    'code', details={'code': code, 'rows': [first_rows[code], line_num], 'columns': ['code']},
                )
            first_rows[code] = line_num

            # 数值列可缺省；非数字 → 400 10012；NaN / Infinity / 超出范围 → 400 16100（带行号）
            numbers = {field: _parse_csv_number(row, field, line_num)
                       for field in ('weight', 'length', 'width', 'height', 'price', 'discount_price')}
            price = numbers['price']
            discount_price = numbers['discount_price']
            if price is not None and price < 0:
                raise _import_error(f"Row {line_num}: price must be >= 0", 10012, line_num, ['price'],
                                    value=str(price))
            if discount_price is not None and (price is None or discount_price > price):
                raise _import_error(f"Row {line_num}: discount_price must be <= price", 10012, line_num,
                                    ['discount_price', 'price'], value=str(discount_price))
            clean_goods_numbers(numbers, row=line_num)
            price = numbers['price']
            discount_price = numbers['discount_price']

            # 构建商品数据
            goods_data.append({
                # 基础信息
                'code': row['code'],
                'name': row['name'],

                # 计量维度
                'unit': row.get('unit') or 'pcs',  # 默认单位为件
                'weight': numbers['weight'],
                'length': numbers['length'],
                'width': numbers['width'],
                'height': numbers['height'],
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

                # 描述信息（入库前在 service 里按白名单清洗）
                'description': row.get('description'),

                # 系统字段
                'company_id': company_id,
                # 文件行号（与已有商品合并后价格校验失败时报给界面）
                '_row': line_num,
            })

        # 批量创建商品
        created_by = require_actor_user_id()
        stats = {}
        new_goods_list = GoodsService.bulk_create_goods(goods_data, created_by, override_mode=overwrite_mode,
                                                        spec_source='import', stats=stats)

        known = set(GOODS_IMPORT_COLUMNS)
        return {
            "message": f"Bulk upload successful. Processed {len(new_goods_list)} goods.",
            "mode": overwrite_mode,
            "processed": len(new_goods_list),
            "created": stats.get('created', 0),
            "updated": stats.get('updated', 0),
            "skipped": stats.get('skipped', 0),
            "columns": [name for name in header if name in known],
            # 文件里认不出的列（拼错的列名等），导入时忽略
            "ignored_columns": [name for name in header if name not in known],
        }, 200


