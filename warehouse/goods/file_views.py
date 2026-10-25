"""商品导出任务（/goods/exports）与导入记录（/goods/imports）的接口。实现见 exporter.py / importer.py"""
from flask import g, make_response, request
from flask_restx import Resource

from extensions import db
from extensions.error import ConflictException, NotFoundException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import warehouse_required
from . import exporter, tabular
from .columns import columns_catalog
from .models import GoodsExport, GoodsImport
from .schemas import api_ns, goods_file_list_parser

_READ = ["all_access", "company_all_access", "goods_read"]
_IMPORT = ["all_access", "company_all_access", "goods_add", "goods_edit"]
IMPORT_FILE_GONE = 16142        # 409 文件已删除（与导出文件不可下载同一个码）：details {status: expired}


def _file_response(content, file_name, content_type):
    response = make_response(content)
    response.headers['Content-Type'] = content_type
    response.headers['Content-Disposition'] = tabular.attachment_header(file_name)
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def _page(query, args):
    result = paginate(query, args.get('page'), args.get('per_page'))
    result['items'] = [item.to_dict() for item in result['items']]
    return result


def _actor_user_id():
    user = g.get('current_user')
    return user.id if user is not None else None


# ------------------------------------------------------------------
# 导出
# ------------------------------------------------------------------

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/columns', '/exports/columns/')
class GoodsExportColumns(Resource):

    @permission_required(_READ)
    def get(self):
        """Columns that can be exported: {groups [{key, labels}], columns [{key, group, labels {zh-CN, en-US, ja-JP},
        importable, required}], presets {import_template: [...20 keys, the import template], all: [...]}, langs}.
        Stock columns (stock_on_hand = total_stock, stock_available = available_stock_for_sale, stock_pending_putaway =
        sorted_stock, stock_damaged = damage_stock) are summed over the stock scope; stock and system columns are
        ignored by the import. Lot-management columns do not exist yet."""
        return columns_catalog(), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/preview', '/exports/preview/')
class GoodsExportPreview(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def post(self):
        """Check an export before creating it (same body as POST /goods/exports, nothing is written)

        → {total, background (total > sync_limit), sync_limit: 1000, max_rows: 100000, too_large, columns [{key,
        header}], preset, lang, file_format, encoding, warehouse_id, unencodable}. `unencodable` (CSV + Shift_JIS only,
        else null): {count = goods with characters Shift_JIS cannot hold (written as "?"), characters (up to 50),
        items [{id, code, name, columns, characters}] (up to 100)}; 〜 ‖ − ¢ £ ¬ — are mapped to their CP932
        counterparts first and do not count. Errors as for POST /goods/exports (400 10021).
        """
        spec = exporter.normalize_spec(request.get_json(silent=True))
        return exporter.preview(spec, exporter.resolve_scope(spec)), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports', '/exports/')
class GoodsExportList(Resource):

    @permission_required(_READ)
    @warehouse_required()
    @api_ns.expect(goods_file_list_parser)
    def get(self):
        """Export records, newest first (page / per_page; status = pending / running / completed / failed / expired;
        company_id for platform users). Staff limited to some warehouses only see exports without stock data or with
        the stock of the current warehouse. Items: see POST /goods/exports."""
        args = goods_file_list_parser.parse_args()
        query = exporter.visible_query(args.get('company_id'))
        if args.get('status'):
            query = query.filter(GoodsExport.status == args['status'])
        return _page(query.order_by(GoodsExport.id.desc()), args), 200

    @permission_required(_READ)
    @warehouse_required()
    def post(self):
        """Create a goods export (CSV / Excel)

        Body: {filters: {…the goods list parameters: code, name, keyword, category, brand, manufacturer, tags, currency,
        is_active (true / false / all; default active only), goods_codes [..] or "a,b", origin_missing, image_missing,
        spec_missing [weight, dimensions, origin_country], price_min/max, discount_price_min/max,
        expiration_date_min/max, production_date_min/max, created_from / created_to / updated_from / updated_to
        (YYYY-MM-DD in the default time zone, end day included; or ISO 8601 with offset), has_stock (true / false),
        warehouse_id (stock scope; default the current warehouse X-WAREHOUSE-ID, else every accessible warehouse),
        import_id / import_action, company_id (platform users)}, columns: [keys] (file order; must include code) or
        preset: import_template | all, file_format: csv (default) | xlsx, encoding (CSV): utf-8 (with BOM, default) |
        shift_jis, lang: zh-CN | en-US | ja-JP (headers; default Accept-Language, else en-US)}.

        - ≤ 1,000 goods: generated now → 201 with status completed (or failed); download with GET …/download
        - more: → 202 with status pending; generated in the background (poll GET /goods/exports/<id>: processed /
          total / progress 0-100 → completed / failed)
        Item: {id, status, background, filters, columns, column_count, preset, lang, file_format, encoding,
        warehouse_id, total, processed, progress, row_count, unencodable_count, file_name, file_size, downloadable,
        error, attempts, source_export_id, created_by, created_by_user {id, user_name}, created_at, started_at,
        finished_at, expires_at (files are kept 7 days, then status expired)}.
        Errors: 400 10021 invalid body (field filters.<key> / columns / preset / file_format / encoding / lang), 400
        10022 more than 100,000 goods (details {total, max_rows}), 403 12001 warehouse not accessible.
        """
        spec = exporter.normalize_spec(request.get_json(silent=True))
        export = exporter.create(spec, exporter.resolve_scope(spec), _actor_user_id())
        return export.to_dict(), 202 if export.status in ('pending', 'running') else 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/<int:export_id>', '/exports/<int:export_id>/')
class GoodsExportDetail(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, export_id):
        """One export record (progress while running); 404 13001 when not visible"""
        return exporter.get_visible(export_id).to_dict(), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/<int:export_id>/download', '/exports/<int:export_id>/download/')
class GoodsExportDownload(Resource):

    @permission_required(_READ)
    @warehouse_required()
    @api_ns.produces(['text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
    def get(self, export_id):
        """Download the file (Content-Disposition attachment; goods_<YYYYmmdd-HHMMSS>.csv / .xlsx); 409 16142 when it
        is not completed (details {status}: pending / running / failed / expired)"""
        return _file_response(*exporter.download(exporter.get_visible(export_id)))


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/<int:export_id>/retry', '/exports/<int:export_id>/retry/')
class GoodsExportRetry(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def post(self, export_id):
        """Generate a failed export again (same record) → 201 / 202 like POST /goods/exports; 409 16143 unless failed"""
        export = exporter.retry(exporter.get_visible(export_id))
        return export.to_dict(), 202 if export.status in ('pending', 'running') else 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/exports/<int:export_id>/rerun', '/exports/<int:export_id>/rerun/')
class GoodsExportRerun(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def post(self, export_id):
        """Export again with the conditions of this record (e.g. after the file expired): a new record
        (source_export_id), 201 / 202 like POST /goods/exports; the data is the current one"""
        source = exporter.get_visible(export_id)
        spec = exporter.rerun_spec(source)
        export = exporter.create(spec, exporter.resolve_scope(spec), _actor_user_id(), source_export_id=source.id)
        return export.to_dict(), 202 if export.status in ('pending', 'running') else 201


# ------------------------------------------------------------------
# 导入记录
# ------------------------------------------------------------------

def _imports_query(company_id=None):
    query = GoodsImport.query
    actor_company_id = get_actor_company_id()
    if actor_company_id is not None:
        query = query.filter(GoodsImport.company_id == actor_company_id)
    elif company_id is not None:
        query = query.filter(GoodsImport.company_id == company_id)
    return query


def _get_import(import_id) -> GoodsImport:
    record = _imports_query().filter(GoodsImport.id == import_id).first()
    if record is None:
        raise NotFoundException(f"GoodsImport with id {import_id} not found", 13001)
    return record


def _import_file(record, column, file_name):
    if record.files_purged_at is not None:
        raise ConflictException("The file was deleted (files are kept 30 days)", IMPORT_FILE_GONE,
                                details={'status': 'expired'})
    content = db.session.query(getattr(GoodsImport, column)).filter(GoodsImport.id == record.id).scalar()
    if content is None:
        raise NotFoundException("This import has no such file", 13001)
    # 原文件与错误行文件同格式；CSV 的错误行文件与原文件同编码（UTF-8 一律带 BOM）
    fmt = tabular.file_format(file_name) or record.file_format
    content_type = (tabular.content_type(fmt, 'cp932' if record.encoding == 'cp932' else None) if fmt
                    else 'application/octet-stream')
    return _file_response(content, file_name, content_type)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/imports', '/imports/')
class GoodsImportList(Resource):

    @permission_required(_IMPORT)
    @api_ns.expect(goods_file_list_parser)
    def get(self):
        """Import records, newest first (page / per_page; status = completed / partial / failed; company_id for
        platform users): [{id, file_name, file_format, encoding, encoding_detected, sheet_name, mode, status, row_count,
        created, updated, unchanged, skipped, errors, columns, ignored_columns, failure {code, message, details} (the
        whole file was rejected / not imported), has_original_file, has_error_file, error_file_name, files_expire_at
        (original and error files are kept 30 days), files_purged_at, created_by, created_by_user, created_at}].
        The goods of an import: GET /goods/?import_id=<id>[&import_action=create|update]."""
        args = goods_file_list_parser.parse_args()
        query = _imports_query(args.get('company_id'))
        if args.get('status'):
            query = query.filter(GoodsImport.status == args['status'])
        return _page(query.order_by(GoodsImport.id.desc()), args), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/imports/<int:import_id>', '/imports/<int:import_id>/')
class GoodsImportDetail(Resource):

    @permission_required(_IMPORT)
    def get(self, import_id):
        """One import record, plus error_rows: the first 200 error rows [{row, code, name, errors [...]}]
        (the full list is in the error file)"""
        record = _get_import(import_id)
        return {**record.to_dict(), 'error_rows': record.errors or []}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/imports/<int:import_id>/file', '/imports/<int:import_id>/file/')
class GoodsImportOriginalFile(Resource):

    @permission_required(_IMPORT)
    def get(self, import_id):
        """Download the uploaded file; 409 16142 after 30 days (details {status: expired})"""
        record = _get_import(import_id)
        return _import_file(record, 'original_file', record.file_name or f'goods_import_{record.id}')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/imports/<int:import_id>/errors', '/imports/<int:import_id>/errors/')
class GoodsImportErrorFile(Resource):

    @permission_required(_IMPORT)
    def get(self, import_id):
        """Download the error rows (the original format, header + error rows + a last column 错误 / Error / エラー);
        404 13001 when the import had no error rows; 409 16142 after 30 days"""
        record = _get_import(import_id)
        if record.error_file_name is None:
            raise NotFoundException("This import has no error rows", 13001)
        return _import_file(record, 'error_file', record.error_file_name)
