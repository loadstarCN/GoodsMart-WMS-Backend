"""入库单流程汇总接口（warehouse/asn/flow.py，doc/shipping-flow.md §7.1）。

权限：读 = asn_read；刷新跟踪 = asn_receive 或 asn_edit（会写入库单上的跟踪结果、调承运商接口；作业员收货前查到货）。
归属校验与其它 ASN 接口一致（跨仓库 / 跨公司 403、不存在 404），同样接受 warehouse_id / X-WAREHOUSE-ID。
"""
from flask_restx import Resource

from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned

from .flow import InboundFlowService
from .models import ASN
from .schemas import api_ns
from .tracking import ASNTrackingService

_READ = ["all_access", "company_all_access", "asn_read"]
_TRACK = ["all_access", "company_all_access", "asn_receive", "asn_edit"]


def _owned_asn(asn_id: int) -> ASN:
    return get_warehouse_owned(ASN, asn_id, what='ASN')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/flow', '/<int:asn_id>/flow/')
class ASNFlow(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, asn_id):
        """
        Inbound flow of an ASN (the Web renders it as is, without deriving states itself):
        {asn_id, asn_status, asn_type, order_number, warehouse_id, expected_arrival_date, closed_at,
         supplier: null | {id, name},
         carrier: null | {id, name, code, integration, tracking_method, tracking_url},
         tracking_number,
         stage: expected | in_transit | receiving | sorting | putaway | completed | closed | exception
                (same definition as the ASN list's inbound_stage),
         steps: [{key, state, at, source, summary, actions: [{key, enabled, reason?, reason_code?, ...}]}],
         problems: [{code, level: error | warning, message, step, ...}],
         tasks: {sorting: [ids]}}
        - steps[].key in order: created, in_transit, received, sorting, putaway, completed
        - steps[].state: done / current / todo / warning (needs attention) / blocked / skipped (not applicable)
        - steps[].at: when it actually happened (ISO 8601 with offset); source: warehouse / carrier / api / system
        - actions[].key: refresh_tracking (POST /asn/<id>/track), open_tracking (url), receive (PUT /asn/<id>/receive/);
          a disabled action has reason (English) and reason_code (ASN_NOT_PENDING / NO_DETAILS) for translation
        - in_transit summary: {tracking_number, tracking: null | {status, code, status_text, picked_up_at,
          delivered_at, estimated_delivery, events[], error, synced_at}, tracking_url, tracked_by_api, tracking_method};
          skipped without a tracking number. Tracking never receives the ASN automatically.
        - putaway is estimated from the warehouse's stock waiting for putaway (putaway records carry no ASN):
          summary {quantity, pending_quantity, put_away_quantity, goods[{goods_id, code, name, quantity, pending}],
          last_putaway_at, estimated: true}
        - sorting summary.spec_capture: {fields, mode, enforcement: warn | block, pending_count, sorted_pending:
          [{asn_detail_id, goods_id, goods_code, goods_name, spec_pending}]} (sorted lines still to capture: what
          completing sorting warns about / is blocked by)
        """
        return InboundFlowService.build(_owned_asn(asn_id)), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:asn_id>/track', '/<int:asn_id>/track/')
class ASNTrack(Resource):

    @permission_required(_TRACK)
    @warehouse_required()
    def post(self, asn_id):
        """
        Refresh the carrier tracking of the ASN's tracking number now (only when the carrier's tracking_method is api
        and its integration can track numbers it did not create) and store the result on the ASN. It never receives
        the ASN; the lookup is limited to about 40 seconds.
        Returns the flow (same body as GET /asn/<id>/flow) plus `tracking_sync` {tracked, updated, error}.
        - 409 16140: no tracking number, or the carrier cannot be tracked through its integration
        A failed lookup is not an HTTP error: it is in tracking_sync.error and in_transit.summary.tracking.error
        (the last result is kept).
        """
        stats = ASNTrackingService.refresh(_owned_asn(asn_id))
        return {**InboundFlowService.build(_owned_asn(asn_id)), 'tracking_sync': stats}, 200
