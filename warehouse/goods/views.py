from datetime import date
from decimal import Decimal

from flask import g, make_response
from flask_restx import Resource,abort

from extensions import oss
from extensions.cache import cache
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import paginate,permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import (
    require_actor_user_id,
    warehouse_required, add_warehouse_filter, check_goods_access, check_warehouse_access,
    get_company_owned, get_warehouse_owned, require_fields,
)
from warehouse.company.services import CompanyService
from . import importer, tabular
from .columns import COLUMNS_BY_KEY, GOODS_IMPORT_COLUMNS, GOODS_IMPORT_SAMPLE_ROW, SHEET_TITLES, header_label
from .filters import stock_scope
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
    goods_template_parser,
)

from .services import GoodsService, GoodsLocationService, resolve_spec_source

# 商品导入的列与业务码见 warehouse/goods/columns.py、importer.py（GOODS_IMPORT_COLUMNS 仍可从这里导入）


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
            'created_from': args.get('created_from'),
            'created_to': args.get('created_to'),
            'updated_from': args.get('updated_from'),
            'updated_to': args.get('updated_to'),
            # 有 / 无库存：按当前仓库（没有则调用方可访问的仓库）
            'has_stock': args.get('has_stock'),
            'stock_warehouse_ids': stock_scope(),
            'import_id': args.get('import_id'),
            'import_action': args.get('import_action'),
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


_IMPORT_PERMISSIONS = ["all_access", "company_all_access", "goods_add", "goods_edit"]


def _file_response(content, file_name, content_type):
    response = make_response(content)
    response.headers['Content-Type'] = content_type
    response.headers['Content-Disposition'] = tabular.attachment_header(file_name)
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def _template_cell(key, value):
    """Excel 模板的示例行：数值 / 日期按类型写（打开后能直接改）"""
    if value in (None, ''):
        return None
    kind = COLUMNS_BY_KEY[key].kind
    if kind == 'number':
        return Decimal(value)
    if kind == 'integer':
        return int(value)
    if kind == 'date':
        return date.fromisoformat(value)
    return value


@api_ns.route('/bulk_upload/template')
class GoodsBulkUploadTemplate(Resource):
    @permission_required(_IMPORT_PERMISSIONS)
    @api_ns.expect(goods_template_parser)
    def get(self):
        """Download the goods import template: the header lists every column the import reads, then one sample row

        `format`: csv (default; UTF-8 with BOM) / xlsx (sheet "Goods" / "商品"; the code column is formatted as text).
        `lang`: zh-CN / en-US / ja-JP headers (e.g. 编码 / Code / コード); without it the headers are the column keys
        (code, name, weight, …). The import recognises all of them. Invalid lang → 400 10019.
        """
        args = goods_template_parser.parse_args()
        lang = importer.resolve_lang(args.get('lang'))
        header = [header_label(key, lang) for key in GOODS_IMPORT_COLUMNS]
        if args.get('format') == 'xlsx':
            kinds = ['text' if COLUMNS_BY_KEY[key].kind in ('text', 'country') else None
                     for key in GOODS_IMPORT_COLUMNS]
            sample = [_template_cell(key, value) for key, value in zip(GOODS_IMPORT_COLUMNS, GOODS_IMPORT_SAMPLE_ROW)]
            content = tabular.write_xlsx(SHEET_TITLES.get(lang, 'Goods'), header, [sample], kinds)
            return _file_response(content, 'Goods_template.xlsx', tabular.content_type('xlsx'))
        content = tabular.write_csv(header, [GOODS_IMPORT_SAMPLE_ROW], 'utf-8-sig')
        return _file_response(content, 'Goods_template.csv', tabular.content_type('csv'))


def _import_request():
    """multipart 参数 → importer.ImportRequest（公司归属、编码、语言校验）"""
    args = goods_bulk_upload_parser.parse_args()
    uploaded_file = args['file']
    company_id = args.get('company_id')
    # 权限检查：员工 / 公司级 API Key 只能导入到自己公司
    actor_company_id = get_actor_company_id()
    if actor_company_id is not None and company_id != actor_company_id:
        raise ForbiddenException("You do not have permission to operate on this company.", 12001)
    company = CompanyService.get_company(company_id)
    encoding = tabular.normalize_encoding(args.get('encoding'))
    if encoding is None:
        raise BadRequestException("encoding must be auto, utf-8 or shift_jis", importer.PARAM_INVALID, 'encoding')
    return args, importer.ImportRequest(
        company_id=company_id, company=company, mode=(args.get('overwrite') or 'skip').lower(), encoding=encoding,
        sheet=(args.get('sheet') or '').strip() or None, lang=importer.resolve_lang(args.get('lang')),
        file_name=(uploaded_file.filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip(),
        content=uploaded_file.read(),
    )


@api_ns.route('/bulk_upload')
class GoodsBulkUpload(Resource):
    @permission_required(_IMPORT_PERMISSIONS)
    @api_ns.expect(goods_bulk_upload_parser)
    def post(self):
        """Import goods from a CSV / Excel (.xlsx) file (multipart/form-data)

        Parameters: `file`, `company_id`, `overwrite` (codes that already exist: skip = add new only / append = fill
        blank fields / override = non-empty cells replace the values, **empty cells keep them** / active = legacy,
        reactivate only; append and override also reactivate inactive goods), `dry_run`, `import_token`, `encoding`
        (CSV: auto (default; BOM or valid UTF-8 → UTF-8, else Shift_JIS) / utf-8 / shift_jis), `sheet` (Excel; default
        the first sheet), `lang` (language of the error file, default the language of the headers).
        Headers: column keys, zh-CN / en-US / ja-JP labels and common aliases (see GET /goods/bulk_upload/template);
        code and name are required. At most 10,000 data rows.

        - `dry_run=true`: validates everything, writes nothing → `{dry_run, import_token, mode, file {name, format,
          encoding, encoding_detected, sheet, sheets, rows, columns}, header_lang, columns, column_map [{index, header,
          field}], ignored_columns, read_only_columns, summary {rows, create, update, unchanged, skip, error},
          rows [{row, action: create | update | unchanged | skip | error, code, name, goods_id, values (create),
          changes [{field, from, to}] (update), spec_missing, errors [{field, column, code, reason, value, message, …}]
          (error)}]}`. `row` is the line in the file (header = line 1).
        - with `import_token`: imports the rows without errors and skips the error rows; 409 16141 when the file /
          options (FILE_CHANGED) or the result of a row (DATA_CHANGED) differ from the dry run — validate again.
        - without `import_token`: any error → nothing imported, 400 with the first error (as before).
        Response: `{message, import_id, status: completed | partial | failed, mode, processed, created, updated,
        unchanged, skipped, errors, has_error_file, file, columns, ignored_columns, read_only_columns}`; every run is
        recorded (GET /goods/imports).
        Errors (400, `details` with `row` / `rows` / `code` / `columns` / `value`): 10004 not .csv / .xlsx, 10005
        required columns missing, 10006 required value empty, 10012 invalid number / negative price / discount above
        price, 10014 invalid origin_country, 10016 unreadable file (`details.reason` UNKNOWN_ENCODING / NOT_UTF8 /
        NOT_SHIFT_JIS / NOT_XLSX / NOT_CSV), 10017 invalid date, 10018 more than 10,000 rows / Excel too large, 10019
        invalid parameter (`field` encoding / sheet / lang / import_token), 10020 text too long, 16100 number out of
        range, 16101 duplicate code in the file.
        """
        args, import_request = _import_request()
        if args.get('dry_run'):
            return importer.preview(import_request), 200
        return importer.run(import_request, require_actor_user_id(), args.get('import_token')), 200


@api_ns.route('/bulk_upload/errors')
class GoodsBulkUploadErrors(Resource):
    @permission_required(_IMPORT_PERMISSIONS)
    @api_ns.expect(goods_bulk_upload_parser)
    def post(self):
        """Error rows of a file before importing it (same multipart parameters as POST /goods/bulk_upload)

        → 200 the file in its own format (CSV in its encoding / Excel) with the header and only the error rows, plus a
        last column 错误 / Error / エラー (by `lang`, default the language of the headers); 204 when no row has errors.
        File-level errors as for the import (400).
        """
        _args, import_request = _import_request()
        result = importer.error_file_for(import_request)
        if result is None:
            return '', 204
        return _file_response(*result)

