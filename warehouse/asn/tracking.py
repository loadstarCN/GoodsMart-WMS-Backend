"""入库单运单号的承运商跟踪（doc/shipping-flow.md §7.1）：只在入库流程里手动刷新（POST /asn/<id>/track），不定时查。

- 能查的条件：入库单有运单号、有承运商，承运商的跟踪方式是 api（carriers.tracking_method），承运商在线对接的插件
  supports_tracking 且 tracks_any_number（入库单的运单号是供应商建的，不是本插件建的），对接设置齐全（停用了照样查，
  同出库的跟踪）。否则只给承运商的公开查询页（open_tracking）。
- 查询经插件（CarrierAdapter.track），结果用插件归好的那一套（status = TRACK_*、code、status_text、picked_up_at、
  delivered_at、estimated_delivery、events），存到入库单的 tracking_status / tracking_detail / tracking_synced_at。
- **不按跟踪自动收货**：收货一直是仓库的操作；跟踪只用来显示（承运商说已送达 → 流程提示「承运商 … 送达」、阶段 receiving）。
- 查询失败（超时、凭证不对、查无此号……）只在 tracking_detail.error 记原因，保留上一次的结果；不是 HTTP 错误。
- 防误判：揽收 / 送达时刻比入库单创建早 OLD_SHIPMENT_MARGIN 以上 = 承运商回收再用的运单号查到了旧包裹，不用，记成 error。
  （供应商常在发货后才建入库单，所以余量比出库的 12 小时宽。）
- 调承运商时不持有行锁、不开事务；写结果时锁入库单行，运单号已经改了、或别人已经存了更新的结果时不写。
"""
import logging
from datetime import datetime, timedelta

from extensions.db import db
from extensions.error import ConflictException
from extensions.transaction import transactional
from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.integrations.base import CarrierError, clock
from warehouse.carrier.methods import methods_of
from warehouse.common.locks import lock_row
from warehouse.dn.tracking_services import ERROR_MAX, MANUAL_BUDGET_SECONDS, NO_RESULT, _aware

from .models import ASN

logger = logging.getLogger(__name__)

NOT_TRACKABLE = 16140        # 409：入库单没有运单号，或承运商不能经 API 查
OLD_SHIPMENT_MARGIN = timedelta(days=30)
OLD_SHIPMENT = ('TRACKING.OLD_SHIPMENT: the carrier returned scans from long before this ASN was created '
                '(a reused tracking number); ignored')


def tracking_number_of(asn) -> str | None:
    number = str(asn.tracking_number or '').strip() if asn is not None else ''
    return number or None


def from_older_shipment(result: dict, created_at) -> bool:
    """揽收 / 送达时刻比入库单创建早很多：运单号被承运商回收再用，查到的是旧包裹"""
    if created_at is None:
        return False
    cutoff = created_at.astimezone() - OLD_SHIPMENT_MARGIN       # naive = 服务器本地时间
    for key in ('picked_up_at', 'delivered_at'):
        moment = _aware(result.get(key))
        if moment is not None and moment < cutoff:
            return True
    return False


class ASNTrackingService:

    @staticmethod
    def adapter_of(asn):
        """→ (对接设置快照, 适配器)；适配器为 None = 不能经 API 查（跟踪方式不是 api、插件不支持跟踪或不能查非本插件建的
        单号、对接没装 / 设置不全）"""
        carrier = asn.carrier if asn is not None else None
        if carrier is None or methods_of(carrier)['tracking'] != 'api':
            return None, None
        snapshot = CarrierIntegrationService.for_carrier(carrier)
        adapter = CarrierIntegrationService.build(snapshot)
        if snapshot is None or adapter is None or not adapter.supports_tracking or not adapter.tracks_any_number:
            return snapshot, None
        if CarrierIntegrationService.problems(snapshot, adapter, for_create=False):
            return snapshot, None
        return snapshot, adapter

    @staticmethod
    def trackable(asn) -> bool:
        """有运单号，且承运商能经 API 查（流程的 refresh_tracking）"""
        return tracking_number_of(asn) is not None and ASNTrackingService.adapter_of(asn)[1] is not None

    @staticmethod
    def refresh(asn) -> dict:
        """查一次跟踪并存到入库单 → {tracked: 查到了结果, updated: 结果有变化, error: 查询失败的原因 | None}。
        不能查 → 409 16140"""
        number = tracking_number_of(asn)
        snapshot, adapter = ASNTrackingService.adapter_of(asn) if number else (None, None)
        if adapter is None:
            raise ConflictException("This ASN has no tracking number that can be tracked through the carrier's "
                                    "integration.", NOT_TRACKABLE)
        asn_id, created_at = asn.id, asn.created_at
        # 结束只读事务：调承运商期间不占着数据库事务
        db.session.rollback()
        started = datetime.now()
        try:
            results = adapter.track([number], deadline=clock() + MANUAL_BUDGET_SECONDS)
            result = results.get(number) or {'error': NO_RESULT}
        except CarrierError as exc:
            logger.warning("carrier %s: tracking ASN %s (%s) failed: %s", snapshot['carrier_id'], asn_id, number,
                           exc.message)
            result = {'error': exc.message}
        if not result.get('error') and from_older_shipment(result, created_at):
            logger.warning("carrier tracking: ASN %s tracking number %s returned old scans; ignored", asn_id, number)
            result = {'error': OLD_SHIPMENT}
        updated = ASNTrackingService._save(asn_id, number, result, started)
        error = str(result['error'])[:ERROR_MAX] if result.get('error') else None
        return {'tracked': error is None, 'updated': bool(updated), 'error': error}

    @staticmethod
    @transactional
    def _save(asn_id, number, result: dict, started) -> bool:
        """存跟踪结果 → 有没有变化。不写：运单号已经改了、或这次开始查之后别人已经存了更新的结果。
        查询出错（result.error）保留上次的结果，只记 error"""
        asn = db.session.get(ASN, asn_id)
        if asn is None:
            return False
        asn = lock_row(asn)
        if tracking_number_of(asn) != number:
            return False
        if asn.tracking_synced_at is not None and asn.tracking_synced_at > started:
            return False
        asn.tracking_synced_at = datetime.now()
        if result.get('error'):
            detail = dict(asn.tracking_detail) if isinstance(asn.tracking_detail, dict) else {}
            detail['error'] = str(result['error'])[:ERROR_MAX]
            asn.tracking_detail = detail
            return False
        before = (asn.tracking_status, dict(asn.tracking_detail) if isinstance(asn.tracking_detail, dict) else None)
        detail = {
            'code': str(result['code'])[:10] if result.get('code') else None,
            'status_text': result.get('status_text'),
            'picked_up_at': result.get('picked_up_at'),
            'delivered_at': result.get('delivered_at'),
            'estimated_delivery': result.get('estimated_delivery'),
            'events': result.get('events') or [],
            'error': None,
        }
        asn.tracking_status = result.get('status')
        asn.tracking_detail = detail
        return before != (asn.tracking_status, detail)
