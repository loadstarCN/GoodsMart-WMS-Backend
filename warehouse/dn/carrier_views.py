"""DN 承运商运单接口（FedEx Ship API 自动建单 / 取消）。

权限：读 = dn_read 或 packing_read；建单 / 取消 = packing_edit 或 delivery_edit。
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
        {enabled, carrier, can_create, blockers[{code, message}], etd_enabled, default_label_format,
         label_formats{A4|THERMAL: {image_type, stock_type}}, declared_value_carriage, delivery_task_id,
         shipment: null | {tracking_number, status, service_type, net_charge, currency, label_format, image_type,
         label_document_id, label_download_path, label_file_name, label_content_type, label_parts,
         created_at, created_by, cancelled_at, ...}}
        """
        return CarrierShipmentService.status(_owned_dn(dn_id)), 200

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(carrier_shipment_create_model)
    def post(self, dn_id):
        """
        Create the shipment in FedEx (Ship API) and save the tracking number / label.
        Body (optional): {"label_format": "A4" | "THERMAL"}; invalid → 400 16077
        - 201: created (same body as GET plus `alerts` from FedEx)
        - 409 16072: preconditions not met (details.blockers)
        - 502 16073: FedEx returned an error (details.errors / transaction_id); nothing is saved
        - 504 16074: FedEx timed out (details.maybe_processed); nothing is saved
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
        - 409 16075: no active shipment; 16065: already shipped; 16072: FedEx not configured
        - 502 16073: FedEx refused to cancel (reason in message / details)
        """
        dn = _owned_dn(dn_id)
        return CarrierShipmentService.cancel_shipment(dn, require_actor_user_id()), 200
