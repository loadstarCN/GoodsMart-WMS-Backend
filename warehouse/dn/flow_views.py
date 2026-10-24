"""出库单流程汇总接口（warehouse/shipping/flow.py，doc/shipping-flow.md §5）。

权限：读 = dn_read / packing_read / delivery_read（与报关 / 运单的读接口相同：PDA 的打包、配送页也用它）。
DN 归属校验与其它 DN 接口一致（跨仓库 403、不存在 404）。
"""
from flask_restx import Resource

from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned
from warehouse.shipping.flow import ShippingFlowService

from .carrier_views import _READ
from .models import DN
from .schemas import api_ns


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
         stage: preparing | awaiting_waybill | awaiting_pickup | awaiting_handover | in_transit | exception |
                delivered | closed (same definition as the DN list's shipping_stage),
         steps: [{key, state, at, source, summary, actions: [{key, enabled, reason?, reason_code?, ...}]}],
         problems: [{code, level: error | warning, message, step, ...}],
         tasks: {picking: [ids], packing: [ids], delivery: id | null}}
        - steps[].key in order: ordered, picking, packing, documents, waybill, pickup, handover, transit, delivered
        - steps[].state: done / current / todo / warning (needs attention) / blocked / skipped (not applicable)
        - steps[].at: when it actually happened (ISO 8601 with offset); source: warehouse / carrier / rule / api / system
        - actions[].key: create_waybill, enter_tracking, cancel_waybill, dismiss_waybill, print_label, book_pickup,
          record_pickup, cancel_pickup, complete_shipping, refresh_tracking, open_tracking, sign, issue_documents,
          edit_packages (map them to the existing endpoints); a disabled action has reason (English) and reason_code
          (REASON_CODES in warehouse/shipping/flow.py, or a blocker / customs problem code) for translation
        """
        return ShippingFlowService.build(get_warehouse_owned(DN, dn_id, what='DN')), 200
