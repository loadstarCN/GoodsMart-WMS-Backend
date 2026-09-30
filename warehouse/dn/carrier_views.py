"""DN 承运商运单接口（FedEx Ship API 自动建单 / 取消 / 确认作废结果不明的记录）。

权限：读 = dn_read 或 packing_read；建单 / 取消 / 确认作废 = packing_edit 或 delivery_edit。
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
         unresolved: null | {id, status: pending|unknown, reason, tracking_number, transaction_id, created_at,
         updated_at}, can_dismiss, etd_enabled, default_label_format,
         label_formats{A4|THERMAL: {image_type, stock_type}}, declared_value_carriage, delivery_task_id,
         shipment: null | {tracking_number, status: active|cancelled, reason, service_type, net_charge, currency,
         label_format, image_type, label_document_id, label_download_path, label_file_name, label_content_type,
         label_parts, sender_country, created_at, created_by, updated_at, cancelled_at, ...}, warnings[]}
        - enabled: FedEx configured and the DN's company is in FEDEX_ALLOWED_COMPANY_IDS
        - can_create: no blockers and no unresolved record
        - unresolved: the latest request whose result is not settled - pending = request in progress;
          unknown = the shipment may exist on FedEx (reason timeout / connection_error / server_error /
          bad_response / compensation_failed / interrupted, or stale = pending for longer than
          FEDEX_PENDING_STALE_MINUTES)
        - can_dismiss: unresolved.status == unknown
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
        - 500: saving failed after FedEx created the shipment; WMS cancelled it (record cancelled, reason
          compensated) or, if cancelling failed, the record is unknown (reason compensation_failed) with the
          tracking number
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
    def post(self, dn_id):
        """
        Cancel the active carrier shipment (DN not shipped yet).
        Clears the tracking number on the delivery task, voids the label and re-issues CI / PL without the AWB.
        Uses the ship-from country stored when the shipment was created. If FedEx answers that the shipment is
        already cancelled / not found, it is marked cancelled (shipment.reason = already_cancelled).
        - 409 16075: no active shipment; 16065: already shipped; 16072: FedEx not configured
        - 502 16073: FedEx refused to cancel (reason in message / details)
        """
        dn = _owned_dn(dn_id)
        return CarrierShipmentService.cancel_shipment(dn, require_actor_user_id()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/carrier-shipment/dismiss', '/<int:dn_id>/carrier-shipment/dismiss/')
class DNCarrierShipmentDismiss(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_shipment_dismiss_model)
    def post(self, dn_id):
        """
        Dismiss the carrier shipment request whose result is unclear (unresolved.status == unknown, including a
        pending request older than FEDEX_PENDING_STALE_MINUTES) after checking FedEx Ship Manager: the shipment
        does not exist there, or it has been cancelled there. FedEx is not called. Afterwards a new shipment can
        be created (or a tracking number saved by hand).
        Body: {"confirm": true} (anything else → 400 40000, field confirm)
        - 200: same body as GET (the record becomes status dismissed with dismissed_at / dismissed_by)
        - 409 16075: nothing to dismiss, or the request is still in progress (details.unresolved)
        """
        dn = _owned_dn(dn_id)
        body = request.get_json(silent=True)
        confirm = body.get('confirm') if isinstance(body, dict) else None
        return CarrierShipmentService.dismiss(dn, require_actor_user_id(), confirm), 200
