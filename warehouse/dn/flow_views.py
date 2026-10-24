"""出库单流程汇总接口（warehouse/shipping/flow.py，doc/shipping-flow.md §5）。

权限：读 = dn_read / packing_read / delivery_read（与报关 / 运单的读接口相同：PDA 的打包、配送页也用它）。
DN 归属校验与其它 DN 接口一致（跨仓库 403、不存在 404）。
"""
from flask import request
from flask_restx import Resource, fields

from extensions.error import BadRequestException
from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned
from warehouse.shipping import options as shipping_options
from warehouse.shipping.flow import ShippingFlowService

from .carrier_views import _READ, _SHIP_EDIT
from .models import DN
from .schemas import api_ns

ship_to_input_model = api_ns.model('DNShipToInput', {
    'ship_to': fields.Raw(required=True, description='Structured ship-to address {country, postal_code, admin_area, '
                                                     'locality, dependent_locality, address_line1, address_line2, '
                                                     'organization, name, phone, email, latin}; null = use the '
                                                     "recipient's structured address"),
})

shipping_options_input_model = api_ns.model('DNShippingOptionsInput', {
    'options': fields.Raw(required=True, description='{key: value} for the keys the carrier declares '
                                                     '(service_options); null / "" removes a key'),
})


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/shipping-flow', '/<int:dn_id>/shipping-flow/')
class DNShippingFlow(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, dn_id):
        """
        Shipping flow of a DN (Web and PDA render it as is, without deriving states themselves):
        {dn_id, dn_status, order_number, warehouse_id,
         route: {origin_country, destination_country, international, requires_customs},
         carrier: null | {id, name, code, integration, methods: {waybill, pickup, tracking}, auto_sign_days,
                          tracking_url},
         stage: preparing | awaiting_waybill | awaiting_number | awaiting_pickup | awaiting_handover | in_transit |
                exception |
                delivered | closed (same definition as the DN list's shipping_stage),
         steps: [{key, state, at, source, summary, actions: [{key, enabled, reason?, reason_code?, ...}]}],
         problems: [{code, level: error | warning, message, step, ...}],
         tasks: {picking: [ids], packing: [ids], delivery: id | null}}
        - steps[].key in order: ordered, picking, packing, documents, waybill, pickup, handover, transit, delivered
        - steps[].state: done / current / todo / warning (needs attention) / blocked / skipped (not applicable)
        - steps[].at: when it actually happened (ISO 8601 with offset); source: warehouse / carrier / rule / api / system
        - actions[].key: create_waybill, enter_tracking, cancel_waybill, dismiss_waybill, print_label, export_file,
          import_numbers, discard_export, book_pickup, record_pickup, cancel_pickup, complete_shipping,
          refresh_tracking, open_tracking, sign, issue_documents, edit_packages (map them to the existing endpoints);
          a disabled action has reason (English) and reason_code (REASON_CODES in warehouse/shipping/flow.py, or a
          blocker / customs problem code) for translation
        - waybill summary: also options {values, schema, defaults, locked, locked_reason, locked_message},
          service_name and export ({exported_at, batch_id, file_name, export_count, exported_by} while a file waybill
          is exported and waiting for its number, else null); file waybills carry shipment.file {preset, exported_at,
          export_count, import_batch_id, imported_at}
        """
        return ShippingFlowService.build(get_warehouse_owned(DN, dn_id, what='DN')), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/shipping-options', '/<int:dn_id>/shipping-options/')
class DNShippingOptions(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, dn_id):
        """Shipping service options of a DN (doc/shipping-flow.md §6.4): {options, values, schema, defaults, locked,
        locked_reason, locked_message}; schema = the service_options declared by the DN's carrier (the file template
        for waybill_method file, else the integration plugin)"""
        dn = get_warehouse_owned(DN, dn_id, what='DN')
        payload = shipping_options.payload(dn)
        return {'options': payload['values'], **payload}, 200

    @permission_required(_SHIP_EDIT)
    @warehouse_required()
    @api_ns.expect(shipping_options_input_model)
    def put(self, dn_id):
        """Replace the shipping service options: {options: {...}} → 200 {options, schema, locked, ...}.
        - 400 16139: unknown key / select value not in the list / invalid date (YYYY-MM-DD) / money / text
          (field options.<key>, or options when it is not an object)
        - 409 16076 (details.reason SHIPPING_OPTIONS_LOCKED, carrier_shipment): a waybill has been created through
          the integration or exported / imported as a file — cancel it (or discard the export) first; 409 16065:
          the DN has been shipped"""
        dn = get_warehouse_owned(DN, dn_id, what='DN')
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or 'options' not in body:
            raise BadRequestException('The body must be {"options": {...}}', shipping_options.OPTIONS_INVALID,
                                      field='options')
        return shipping_options.update(dn, body['options']), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/ship-to', '/<int:dn_id>/ship-to/')
class DNShipTo(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, dn_id):
        """Ship-to address of a DN (doc/shipping-flow.md §6.2 / §6.6): {ship_to (snapshot or null), ship_to_effective
        (what labels / carrier files use), shipping_address (one line), locked, locked_reason (DN_CLOSED / DN_SHIPPED /
        WAYBILL_EXISTS), locked_message}"""
        from .services import DNService
        return DNService.ship_to_payload(get_warehouse_owned(DN, dn_id, what='DN')), 200

    @permission_required(_SHIP_EDIT + ['dn_edit'])
    @warehouse_required()
    @api_ns.expect(ship_to_input_model)
    def put(self, dn_id):
        """Replace the ship-to address snapshot, also after packing (e.g. shorten an address the carrier file rejected
        as too long): {ship_to: {...} | null} → 200 same body as GET; shipping_address is regenerated from it.
        The customs snapshot (consignee) of export shipments is not changed.
        - 400 14019 / 14020: invalid field / country (field ship_to.<key>); 400 16001: the DN is closed
        - 409 16076 (details.reason SHIP_TO_LOCKED, carrier_shipment): a waybill has been created through the
          integration or exported / imported as a file — cancel it (or discard the export) first; 409 16065: shipped"""
        from .services import DNService
        dn = get_warehouse_owned(DN, dn_id, what='DN')
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or 'ship_to' not in body:
            raise BadRequestException('The body must be {"ship_to": {...}}', 14019, field='ship_to')
        return DNService.update_ship_to(dn, body['ship_to']), 200
