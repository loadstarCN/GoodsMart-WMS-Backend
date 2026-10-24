"""文件方式接口（导出给承运商软件的 CSV / 导回运单号 / 作废导出 / 批次，warehouse/dn/carrier_file_services.py）。

权限：导出 / 导入 / 作废导出 = packing_edit 或 delivery_edit（与建运单相同）；批次 = dn_read / packing_read / delivery_read。
仓库：请求头 X-WAREHOUSE-ID（或查询参数 warehouse_id）为当前仓库；请求体 / 表单里的 warehouse_id 可带，须是当前仓库；
都没有时导出按第一张 DN 的仓库（所有 DN 须同一仓库），导入按调用方能访问的仓库找 DN。
"""
from flask import Response, g, request
from flask_restx import Resource, fields

from system.common import permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import get_warehouse_owned, require_actor_user_id, warehouse_required
from warehouse.common.ownership import require_warehouse_scope

from .carrier_file_services import CarrierFileService, _unavailable, get_batch_carrier, parse_id
from .carrier_services import CarrierShipmentService
from .carrier_views import _READ, _SHIP_EDIT
from .models import DN
from .schemas import api_ns

carrier_file_export_model = api_ns.model('CarrierFileExport', {
    'carrier_id': fields.Integer(required=True, description='Carrier whose waybill_method is file'),
    'dn_ids': fields.List(fields.Integer, required=True, description='DNs to export (1 - 500, same warehouse)'),
    'warehouse_id': fields.Integer(description='Optional; must be the current warehouse (X-WAREHOUSE-ID)'),
})


def _warehouse_id(value):
    """当前仓库：请求头 / 查询参数的；请求体 / 表单里的须与之相同（不合法 400 16138）；都没有为 None"""
    current = getattr(g, 'warehouse_id', None)
    given = parse_id(value, 'warehouse_id') if value not in (None, '') else None
    if current is not None and given is not None and given != current:
        raise _unavailable("warehouse_id must be the current warehouse (X-WAREHOUSE-ID)", 'warehouse_id', status=400)
    warehouse_id = current or given
    if warehouse_id is not None:
        require_warehouse_scope(warehouse_id, 'warehouse')
    return warehouse_id


def _bool_arg(value, field):
    text = str(value if value is not None else 'true').strip().lower()
    if text in ('true', '1', 'yes', 'on'):
        return True
    if text in ('false', '0', 'no', 'off'):
        return False
    raise _unavailable(f"{field} must be true or false", field, status=400)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-files/export', '/carrier-files/export/')
class CarrierFileExport(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_file_export_model)
    @api_ns.produces(['text/csv'])
    def post(self):
        """
        Export DNs as a CSV file in the carrier software's import format (waybill_method file, doc/shipping-flow.md
        §6.1). Body: {carrier_id, dn_ids: [...], warehouse_id?}.
        - 200: the file (Content-Disposition attachment; filename <carrier-code>_<YYYYmmdd-HHMMSS>.csv; bytes in the
          template encoding, e.g. Shift_JIS). Headers X-Batch-Id / X-Row-Count. Each DN gets a file waybill record
          (source file, status exported, file_batch_id); exporting an exported DN again keeps the record and updates
          the batch.
        - 409 16137: some rows have problems — nothing is exported and no record is written:
          details {rows: [{dn_id, order_number, problems: [{column (header text, options.<key> or null), column_index?,
          code, message, ...}]}], total}. Codes: DN_NOT_FOUND, OTHER_WAREHOUSE, NOT_PACKED, DN_SHIPPED, DN_CLOSED,
          NO_OPEN_TASK, CARRIER_NOT_SET, OTHER_CARRIER, WAYBILL_EXISTS, TRACKING_NUMBER_EXISTS, OPTION_INVALID,
          REQUIRED, TOO_LONG, UNENCODABLE, CHARACTER_NOT_ALLOWED
        - 400 16138: invalid carrier_id / dn_ids / warehouse_id (field); 409 16138: the carrier is not in file mode,
          has no usable template, or required account fields are missing (field carrier_id / file_account.<key>)
        """
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise _unavailable("The request body must be a JSON object", None, status=400)
        result = CarrierFileService.export(body.get('carrier_id'), body.get('dn_ids'),
                                           _warehouse_id(body.get('warehouse_id')), require_actor_user_id())
        return Response(
            result['content'],
            content_type=result['content_type'],
            headers={
                'Content-Disposition': f'attachment; filename="{result["file_name"]}"',
                'X-Batch-Id': str(result['batch']['id']),
                'X-Row-Count': str(result['rows']),
                'Access-Control-Expose-Headers': 'Content-Disposition, X-Batch-Id, X-Row-Count',
                'Cache-Control': 'private, no-store',
            },
        )


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-files/import', '/carrier-files/import/')
class CarrierFileImport(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    def post(self):
        """
        Import the tracking numbers file exported by the carrier software (multipart/form-data: file, carrier_id,
        dry_run = true | false (default true), warehouse_id?). Rows are matched by the reference column
        (DN{dn.id}); several rows of one DN = several tracking numbers (the first is the main one, the others go to
        package_tracking_numbers).
        → 200 {dry_run, file_name, encoding, rows: [{line, reference, dn_id | null, order_number | null,
          tracking_numbers: [...], result: matched | not_found | already_has_number | same_number | invalid,
          message | null, primary?}], summary: {matched, not_found, already_has_number, same_number, invalid,
          skipped, dns: {<result>: count of DNs}}, batch_id | null}
        - dry_run true: preview only. false: the matched DNs are written in one transaction (each DN re-checked under
          its lock): an exported file waybill becomes active (a DN that was never exported gets a new file waybill),
          the delivery task gets the tracking number; an import batch is recorded (batch_id).
        - 400 16138: no / empty / too large (5 MB) / unreadable file (field file), invalid carrier_id / dry_run /
          warehouse_id; 409 16138: the carrier is not in file mode or its template cannot import (field carrier_id)
        """
        upload = request.files.get('file')
        if upload is None:
            raise _unavailable("file is required (multipart/form-data)", 'file', status=400)
        content = upload.read(5 * 1024 * 1024 + 1)
        dry_run = _bool_arg(request.form.get('dry_run'), 'dry_run')
        warehouse_id = _warehouse_id(request.form.get('warehouse_id'))
        carrier_id = request.form.get('carrier_id')
        file_name = (upload.filename or '').strip()[:255] or None
        if dry_run:
            return CarrierFileService.preview(carrier_id, content, file_name, warehouse_id), 200
        return CarrierFileService.apply(carrier_id, content, file_name, warehouse_id, require_actor_user_id()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-files/batches', '/carrier-files/batches/')
class CarrierFileBatches(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self):
        """
        Recent export / import batches (newest first, ?carrier_id=&warehouse_id=&limit= (default 50, max 200)):
        [{id, carrier_id, warehouse_id, kind: export | import, file_name, row_count, summary, created_at, created_by,
          created_by_user: {id, user_name}}]
        """
        carrier = get_batch_carrier(request.args.get('carrier_id'))
        limit = request.args.get('limit')
        limit = min(parse_id(limit, 'limit'), 200) if limit not in (None, '') else 50
        return CarrierFileService.batches(
            carrier_id=carrier.id if carrier is not None else None,
            warehouse_id=getattr(g, 'warehouse_id', None),
            company_id=get_actor_company_id(), limit=limit), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/carrier-shipment/discard-export', '/<int:dn_id>/carrier-shipment/discard-export/')
class DNCarrierShipmentDiscardExport(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    def post(self, dn_id):
        """
        Discard the exported file waybill of a DN (status exported → cancelled, reason discarded; only WMS is
        changed — delete the shipment in the carrier software too if it was printed). Afterwards the DN can be
        exported again or get a tracking number by hand.
        - 200: same body as GET /carrier-shipment plus `waybill` (the current waybill of any source, or null)
        - 409 16075: the DN has no exported file waybill
        """
        dn = get_warehouse_owned(DN, dn_id, what='DN')
        dn = CarrierFileService.discard_export(dn, require_actor_user_id())
        from warehouse.shipping.waybills import current_waybill
        payload = CarrierShipmentService.status(dn)
        payload['waybill'] = CarrierShipmentService.shipment_payload(current_waybill(dn))
        return payload, 200
