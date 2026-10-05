"""DN 承运商运单接口（FedEx Ship API 自动建单 / 取消 / 确认作废结果不明的记录）。

权限：读 = dn_read 或 packing_read；建单 / 取消 / 确认作废 = packing_edit 或 delivery_edit。
取消 / 确认作废可带目标标识（tracking_number / unresolved_id），与当前记录不符 → 409 16093。
面单 PDF 用单证下载接口 GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file。
"""
from flask import request
from flask_restx import Resource, fields

from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned, require_actor_user_id

from .carrier_services import CarrierShipmentService
from .models import DN
from .schemas import api_ns

carrier_shipment_create_model = api_ns.model('DNCarrierShipmentCreate', {
    'label_format': fields.String(
        enum=['A4', 'THERMAL'],
        description='A4 = laser printer (PDF on plain paper); THERMAL = 4x6 in label printer. '
                    'Omitted: FEDEX_DEFAULT_LABEL_FORMAT'),
})

carrier_shipment_dismiss_model = api_ns.model('DNCarrierShipmentDismiss', {
    'confirm': fields.Boolean(
        required=True,
        description='Must be true: the operator checked FedEx Ship Manager and the shipment does not exist '
                    '(or has been cancelled there)'),
    'unresolved_id': fields.Integer(
        description='Optional: id of the unresolved record the operator checked (GET unresolved.id). '
                    'If it is no longer the current unresolved record → 409 16093 {expected, actual}'),
})

carrier_shipment_cancel_model = api_ns.model('DNCarrierShipmentCancel', {
    'tracking_number': fields.String(
        description='Optional: tracking number the operator wants to cancel (GET shipment.tracking_number / '
                    'unresolved.tracking_number). Compared ignoring spaces / hyphens; if it is not the current '
                    'shipment → 409 16093 {expected, actual}'),
})

_READ = ["all_access", "company_all_access", "dn_read", "packing_read"]
_SHIP_EDIT = ["all_access", "company_all_access", "packing_edit", "delivery_edit"]


def _owned_dn(dn_id: int) -> DN:
    return get_warehouse_owned(DN, dn_id, what='DN')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/carrier-shipment', '/<int:dn_id>/carrier-shipment/')
class DNCarrierShipmentResource(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, dn_id):
        """
        Carrier (FedEx) shipment of an export DN:
        {enabled, carrier, can_create, blockers[{code, message, goods_code?, field?}],
         unresolved: null | {id, status: pending|unknown|cancelling, reason, tracking_number, transaction_id,
         created_at, updated_at}, can_dismiss, can_cancel, etd_enabled, default_label_format,
         label_formats{A4|THERMAL: {image_type, stock_type}}, declared_value_carriage, delivery_task_id,
         shipment: null | {id, tracking_number, status: active|cancelling|cancelled, reason, service_type,
         net_charge, currency, label_format, image_type, label_document_id, label_download_path, label_file_name,
         label_content_type, label_parts, sender_country, created_at, created_by, updated_at, cancelled_at, ...},
         warnings[]}
        - enabled: FedEx configured and the DN's company is in FEDEX_ALLOWED_COMPANY_IDS
        - can_create: no blockers and no unresolved record
        - unresolved: the latest record whose result is not settled - pending = request in progress;
          unknown = the shipment may exist on FedEx (reason timeout / connection_error / server_error /
          bad_response / compensation_failed / save_unconfirmed / interrupted, or stale = pending for longer than
          FEDEX_PENDING_STALE_MINUTES); cancelling = the shipment is being cancelled (reason cancel_in_progress)
          or the result of cancelling it is unclear (reason timeout / server_error / bad_response /
          connection_error / save_failed / interrupted / stale). While it exists the DN cannot be shipped (16079).
        - can_dismiss: unresolved.status == unknown, or cancelling whose cancellation is not in progress
        - can_cancel: an active shipment, a cancelling one whose cancellation is not in progress, or an unknown
          record with a tracking number (POST .../cancel); false after the DN is shipped
        - shipment: the active / cancelling shipment, otherwise the latest cancelled one (same id as unresolved.id
          while cancelling)
        """
        return CarrierShipmentService.status(_owned_dn(dn_id)), 200

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_shipment_create_model)
    def post(self, dn_id):
        """
        Create the shipment in FedEx (Ship API) and save the tracking number / label.
        Body (optional): {"label_format": "A4" | "THERMAL"}; invalid → 400 16077
        A `pending` record is committed before FedEx is called (no DB lock is held while waiting for FedEx);
        the whole request is limited to FEDEX_CREATE_BUDGET_SECONDS.
        - 201: created (same body as GET plus `alerts` from FedEx)
        - 409 16079: a previous request is still pending or its result is unclear (details.unresolved);
          check FedEx Ship Manager, then POST .../carrier-shipment/dismiss
        - 409 16072: preconditions not met (details.blockers)
        - 502 16073: FedEx returned an error (details.errors / transaction_id / maybe_processed / unresolved).
          4xx → record failed, no shipment; 5xx / broken or non-JSON response / connection lost → record unknown
          (details.unresolved), the shipment may exist
        - 504 16074: FedEx timed out (details.maybe_processed / unresolved) or the time budget ran out before
          the request was sent (details.budget_exhausted)
        - 502 16095: FedEx created the shipment but saving it in WMS failed (including business errors such as
          the DN being shipped meanwhile): details {tracking_number, transaction_id, error {code, message},
          compensation {attempted, cancelled, cancel_transaction_id, error}, unresolved, maybe_processed}.
          WMS re-reads the record first - if the save was in fact committed the shipment is kept (201);
          otherwise it is cancelled in FedEx (record cancelled, reason compensated, unresolved null) or, if
          cancelling failed / the save could not be verified, the record is unknown (reason compensation_failed /
          save_unconfirmed) with the tracking number - cancel it again or dismiss it
        """
        dn = _owned_dn(dn_id)
        body = request.get_json(silent=True)
        label_format = body.get('label_format') if isinstance(body, dict) else None
        return CarrierShipmentService.create_shipment(dn, require_actor_user_id(), label_format), 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/carrier-shipment/cancel', '/<int:dn_id>/carrier-shipment/cancel/')
class DNCarrierShipmentCancel(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_shipment_cancel_model)
    def post(self, dn_id):
        """
        Cancel the carrier shipment (DN not shipped yet): the active shipment, a shipment whose previous
        cancellation had an unclear result (status cancelling), or an unknown record with a tracking number
        (e.g. reason compensation_failed).
        Body (optional): {"tracking_number": "..."} - the shipment the operator means; another one → 409 16093.
        Three steps like creating: the shipment is marked `cancelling` and committed, FedEx is called without
        holding the DN lock, then the result is saved. Uses the ship-from country stored at creation.
        - 200: cancelled (same body as GET): tracking number cleared on the delivery task, label voided, CI / PL
          re-issued without the AWB. FedEx answering already cancelled / not found also counts
          (shipment.reason = already_cancelled)
        - 409 16075: nothing to cancel (details.unresolved); 16093: not the shipment in the request;
          16079: a cancellation is already in progress; 16065: already shipped; 16072: FedEx not configured
        - 502 16073 / 504 16074: FedEx refused (the shipment stays active) or the result is unclear
          (details.maybe_processed true; the record stays `cancelling`, shipping is blocked with 16079 until a
          retry gets a clear answer; details.unresolved)
        """
        dn = _owned_dn(dn_id)
        body = request.get_json(silent=True)
        tracking_number = body.get('tracking_number') if isinstance(body, dict) else None
        return CarrierShipmentService.cancel_shipment(dn, require_actor_user_id(), tracking_number), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/carrier-shipment/dismiss', '/<int:dn_id>/carrier-shipment/dismiss/')
class DNCarrierShipmentDismiss(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_shipment_dismiss_model)
    def post(self, dn_id):
        """
        Dismiss the record whose result is unclear (can_dismiss: unresolved.status == unknown, including a
        pending request older than FEDEX_PENDING_STALE_MINUTES, or cancelling whose cancellation is not in
        progress) after checking FedEx Ship Manager. Afterwards a new shipment can be created (or a tracking
        number saved by hand).
        - Without a tracking number: FedEx is not called; the record becomes dismissed.
        - With a tracking number: FedEx is asked to cancel it first (without holding the DN lock); only when
          FedEx cancels it or answers already cancelled / not found is it dismissed (a cancelling shipment becomes
          cancelled, with the label voided and the tracking number cleared). Otherwise 409 16094.
        Body: {"confirm": true, "unresolved_id": <optional>} (confirm missing / not true → 400 40000, field
        confirm; unresolved_id not a positive integer → 400 40000)
        - 200: same body as GET (dismissed_at / dismissed_by, or cancelled_at / cancelled_by)
        - 409 16075: nothing to dismiss, or a request / cancellation is still in progress (details.unresolved)
        - 409 16093: unresolved_id is not the current unresolved record (details {expected, actual})
        - 409 16094: FedEx did not confirm the cancellation (details {unresolved, tracking_number, result,
          http_status, transaction_id, errors, alerts, maybe_processed})
        - 409 16072: FedEx not configured (a record with a tracking number cannot be checked)
        """
        dn = _owned_dn(dn_id)
        body = request.get_json(silent=True)
        confirm = body.get('confirm') if isinstance(body, dict) else None
        unresolved_id = body.get('unresolved_id') if isinstance(body, dict) else None
        return CarrierShipmentService.dismiss(dn, require_actor_user_id(), confirm, unresolved_id), 200
