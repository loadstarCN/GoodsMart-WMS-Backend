"""上门揽收预约接口（承运商对接插件 supports_pickup，内置 FedEx）。仓库级：一律按请求头 X-WAREHOUSE-ID 的当前仓库；
承运商按 carrier_id（查询参数 / 请求体），不带用本公司第一个能预约揽收的承运商。

权限：读 = dn_read / packing_read / delivery_read；预约 / 取消 / 确认作废 = packing_edit 或 delivery_edit
（与自动运单相同）。规则见 pickup_services 模块说明。
"""
from flask import g, request
from flask_restx import Resource, fields

from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned, require_actor_user_id
from warehouse.common.ownership import require_warehouse_scope

from .carrier_views import _READ, _SHIP_EDIT
from .models import CarrierPickup
from .pickup_services import CarrierPickupService
from .schemas import api_ns

carrier_pickup_create_model = api_ns.model('CarrierPickupCreate', {
    'carrier_id': fields.Integer(description='Carrier (GET .carriers[].id); default: the first one that can book pickups'),
    'shipment_ids': fields.List(fields.Integer, required=True,
                                description='Carrier shipment ids (GET .shipments[].shipment_id with can_schedule)'),
    'pickup_date': fields.String(required=True, description='Pickup date YYYY-MM-DD (warehouse local date)'),
    'ready_time': fields.String(required=True, description='Packages ready from HH:MM (local time)'),
    'close_time': fields.String(required=True, description='Latest pickup time HH:MM (local time)'),
    'remarks': fields.String(description='Message for the courier (GET .max_remarks characters at most)'),
    'notification_email': fields.String(description='The carrier sends the pickup confirmation to this address'),
})

carrier_pickup_dismiss_model = api_ns.model('CarrierPickupDismiss', {
    'confirm': fields.Boolean(required=True,
                              description='Must be true: the operator checked with the carrier whether the pickup '
                                          'exists'),
})


def _current_warehouse():
    require_warehouse_scope(g.warehouse_id, 'warehouse')
    return g.warehouse


def _carrier_id_arg():
    return request.args.get('carrier_id') or None


def _owned_pickup(pickup_id: int) -> CarrierPickup:
    return get_warehouse_owned(CarrierPickup, pickup_id, what='pickup')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-pickups', '/carrier-pickups/')
class CarrierPickupList(Resource):

    @permission_required(_READ)
    @warehouse_required(force_warehouse=True)
    def get(self):
        """
        Courier pickups of the current warehouse for one carrier (?carrier_id=, default: the first carrier of the
        company whose integration can book pickups):
        {enabled, carrier: null | {id, name, code, adapter, enabled}, carriers: [same], integration: null | {code, name,
         portal_name, portal_url}, warehouse_id, today, timezone, can_create, blockers[{code, message, field?}],
         pickup_location: null | {contact, address}, max_remarks,
         shipments: [{shipment_id, dn_id, order_number, delivery_task_id, tracking_number, service_type,
                      service_name, ship_date,
                      package_count, weight_kg, created_at, pickup: null | {id, status, reason, pickup_date,
                      ready_time, close_time, confirmation_code, past, holds_shipments}, can_schedule}],
         pickups: [{id, status, reason, pickup_date, ready_time, close_time, package_count, total_weight_kg,
                    shipments[], confirmation_code, location_code, remarks, notification_email, error_message,
                    past, holds_shipments, can_cancel, can_dismiss, created_at, created_by_user, ...}]}
        - shipments: active automatic shipments of the carrier whose DN is not shipped; can_schedule = not held by a
          pending / unknown pickup or a scheduled one whose date has not passed
        - pickups: pickups of the carrier dated within the last 14 days (and every unresolved one), latest date first
        - blockers: PICKUP_CARRIER_MISSING / CARRIER_NOT_INTEGRATED / PICKUP_NOT_SUPPORTED / CARRIER_INTEGRATION_* /
          CARRIER_NOT_CONFIGURED / CARRIER_CONFIG_INVALID / PICKUP_ADDRESS_MISSING / PICKUP_ADDRESS_INVALID /
          PICKUP_PHONE_INVALID
        - 400 16131 field carrier_id: not a carrier of this company that can book pickups
        """
        return CarrierPickupService.overview(_current_warehouse(), _carrier_id_arg()), 200

    @permission_required(_SHIP_EDIT)
    @warehouse_required(force_warehouse=True)
    @api_ns.expect(carrier_pickup_create_model)
    def post(self):
        """
        Schedule a courier pickup for the selected shipments of one carrier (package count / weight = their packages).
        A `pending` record is committed before the carrier is called; the request is limited to
        CARRIER_CREATE_BUDGET_SECONDS.
        - 201 {pickup, alerts, message}: pickup.status scheduled with confirmation_code / location_code
        - 400 16131: invalid field (field = carrier_id / shipment_ids / pickup_date / ready_time / close_time / remarks /
          notification_email)
        - 409 16130: blockers (details.blockers); 16132: some shipments cannot be included
          (details.shipments[{shipment_id, tracking_number, reason: NOT_FOUND | OTHER_CARRIER | NOT_ACTIVE |
          DN_SHIPPED | ALREADY_SCHEDULED, ...}])
        - 502 16073 / 504 16074 (details.action 'pickup', errors, transaction_id, maybe_processed, pickup):
          refused / not sent → pickup failed, shipments released; unclear → pickup unknown (shipments stay held;
          check with the carrier, then POST .../dismiss)
        """
        return CarrierPickupService.create(_current_warehouse(), require_actor_user_id(),
                                           request.get_json(silent=True)), 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-pickups/availability', '/carrier-pickups/availability/')
class CarrierPickupAvailability(Resource):

    @permission_required(_READ)
    @warehouse_required(force_warehouse=True)
    def get(self):
        """
        Pickup dates / times the carrier (?carrier_id=) offers for the warehouse address (query only, nothing is
        scheduled):
        {today, timezone, request_timestamp, transaction_id, alerts,
         options: [{pickup_date, schedule_day: SAME_DAY | FUTURE_DAY, available, cutoff_time, access_minutes,
                    ready_times[HH:MM], latest_times[HH:MM], default_ready_time, default_latest_time}]}
        - ready time must not be later than cutoff_time; latest time must be at least access_minutes after it
        - 409 16130 blockers; 502 16073 / 504 16074 carrier errors (details.action 'pickup_availability')
        """
        return CarrierPickupService.availability(_current_warehouse(), _carrier_id_arg()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-pickups/<int:pickup_id>/cancel', '/carrier-pickups/<int:pickup_id>/cancel/')
class CarrierPickupCancel(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required(force_warehouse=True)
    def post(self, pickup_id):
        """
        Cancel a scheduled pickup whose date has not passed.
        - 200 {pickup, alerts, message}: cancelled (the carrier answering already cancelled also counts,
          pickup.reason already_cancelled); its shipments can be scheduled again
        - 409 16133: not scheduled / date passed (details {pickup_id, status, reason, pickup_date});
          16130: the integration of the carrier that booked it is gone (details.blockers)
        - 502 16073 / 504 16074 (details.action 'pickup_cancel', pickup): refused → stays scheduled
          (reason cancel_refused); unclear → stays scheduled (reason cancel_unclear), retry
        """
        return CarrierPickupService.cancel(_owned_pickup(pickup_id), require_actor_user_id()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/carrier-pickups/<int:pickup_id>/dismiss', '/carrier-pickups/<int:pickup_id>/dismiss/')
class CarrierPickupDismiss(Resource):

    @permission_required(_SHIP_EDIT)
    @warehouse_required(force_warehouse=True)
    @api_ns.expect(carrier_pickup_dismiss_model)
    def post(self, pickup_id):
        """
        Dismiss a pickup request whose result is unclear (unknown, or pending for too long) after checking with
        the carrier; its shipments can be scheduled again. Body {"confirm": true}.
        - 200 {pickup}; 400 16131 confirm missing; 409 16133 not unclear
        """
        body = request.get_json(silent=True)
        confirm = body.get('confirm') if isinstance(body, dict) else None
        return CarrierPickupService.dismiss(_owned_pickup(pickup_id), require_actor_user_id(), confirm), 200
