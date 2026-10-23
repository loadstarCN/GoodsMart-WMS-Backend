"""按承运商跟踪自动完成发货 / 签收（承运商对接插件 supports_tracking，内置 FedEx Track API）。

运单是 WMS 建的，WMS 最先知道运单号，所以由 WMS 查跟踪，来源系统只收结果（dn.delivered / dn.tracking_updated /
dn.completed）。本模块与具体承运商无关，查询一律经插件（CarrierAdapter.track）。
- 要跟的运单：状态 active 的自动运单、有运单号、DN 是 packed / delivered、建单不超过 CARRIER_TRACKING_MAX_DAYS 天
  （默认 60）。定时任务每 CARRIER_TRACKING_INTERVAL_MINUTES 分钟（默认 30）查一遍；配送页可以手动刷新一张
  （POST /dn/<id>/carrier-shipment/track，不看天数，DN 已完成也能刷新显示）。
- 承运商已经经手（picked_up_at 有值，或状态是在途 / 派送中 / 已送达）而 DN 还是 packed：以建运单的人的名义完成发货
  （同手动「完成发货」：pending 的发货任务先开始，单证 / 运单 / 计划发货日等校验照旧，推 dn.delivered）。
- 已送达而 DN 是 delivered：签收发货任务（签收时间 = 承运商的送达时刻，换成服务器本地时间——与 WMS 其他时间列一样是
  datetime.now() 的口径），DN 完成，推 dn.completed。
- 自动发货 / 签收做不成（单证没出 / 过期、计划发货日在后……）把原因记在运单的 tracking_problem（配送页显示），
  人处理后下一轮再试；做成了、或这一轮没有要做的就清空。手动完成发货 / 签收照常可用，谁先到算谁。
- 跟踪结果存到运单记录（tracking_*）；状态 / 扫描记录有变化时推 dn.tracking_updated（只推给创建 DN 的 API Key）。
  先自动发货 / 签收、后存跟踪推事件：来源系统收到跟踪更新时，出库单已经是已发货。
- 调承运商时不持有行锁、不开事务；结果逐张在各自的事务里写（先锁 DN），一张失败不影响别的。
  查询失败（超时、凭证不对……）只在运单上记 error，保留上一次的跟踪结果。
"""
import logging
from datetime import datetime, timedelta, timezone

from extensions.db import db
from extensions.error import APIException
from extensions.transaction import transactional
from system.webhook.services import emit as webhook_emit
from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.integrations.base import (
    TRACK_DELIVERED, TRACK_IN_TRANSIT, TRACK_OUT_FOR_DELIVERY, CarrierError, clock,
)
from warehouse.carrier.models import Carrier
from warehouse.common.locks import lock_row
from .carrier_services import CarrierShipmentService, _config_number
from .customs_services import CustomsService
from .models import DN, DNCarrierShipment

logger = logging.getLogger(__name__)

DEFAULT_MAX_DAYS = 60
DEFAULT_BUDGET_SECONDS = 120
PROBLEM_MAX = 500
ERROR_MAX = 300
# 定时任务只跟这两种状态的 DN（packed 等自动发货、delivered 等自动签收）；手动刷新另外允许已完成的
AUTO_DN_STATUSES = ('packed', 'delivered')
REFRESH_DN_STATUSES = ('packed', 'delivered', 'completed')
# 说明货已经到了承运商手上的状态
HANDLED_STATUSES = (TRACK_IN_TRANSIT, TRACK_OUT_FOR_DELIVERY, TRACK_DELIVERED)
NO_RESULT = 'NOT.FOUND: the carrier returned no result for this tracking number'
KEEP = object()     # _save 的 problem：不动 tracking_problem（这一轮没有试自动发货 / 签收）


def max_days() -> float:
    return _config_number('CARRIER_TRACKING_MAX_DAYS', DEFAULT_MAX_DAYS, 1)


def budget_seconds() -> float:
    """一轮查询（所有承运商、所有批次）的总时限，CARRIER_TRACKING_BUDGET_SECONDS（默认 120 秒）"""
    return _config_number('CARRIER_TRACKING_BUDGET_SECONDS', DEFAULT_BUDGET_SECONDS, 10)


def picked_up(result: dict) -> bool:
    return bool(result.get('picked_up_at')) or result.get('status') in HANDLED_STATUSES


def local_naive(value):
    """承运商的 UTC ISO 时刻 → 服务器本地时间（naive，与 WMS 其他时间列的 datetime.now() 同一口径）；
    空 / 纯日期 / 解析不了 → None"""
    if not value or len(str(value).strip()) <= 10:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().replace(tzinfo=None)


class AutoActionError(Exception):
    """自动发货 / 签收的前提不满足（不是接口校验错误），message 记进 tracking_problem"""


def _fingerprint(status, code, detail: dict) -> tuple:
    events = detail.get('events') or []
    return (status, code, detail.get('estimated_delivery'), detail.get('delivered_at'), detail.get('picked_up_at'),
            len(events), events[0].get('time') if events else None)


class CarrierTrackingService:

    # ------------------------------------------------------------------
    # 选运单
    # ------------------------------------------------------------------

    @staticmethod
    def _targets(dn_id=None) -> list:
        query = (
            DNCarrierShipment.query
            .join(DN, DN.id == DNCarrierShipment.dn_id)
            .filter(
                DNCarrierShipment.status == 'active',
                DNCarrierShipment.tracking_number.isnot(None),
                DNCarrierShipment.tracking_number != '',
            )
        )
        if dn_id is None:
            query = query.filter(
                DN.status.in_(AUTO_DN_STATUSES),
                DNCarrierShipment.created_at >= datetime.now() - timedelta(days=max_days()),
            )
        else:
            query = query.filter(DNCarrierShipment.dn_id == dn_id, DN.status.in_(REFRESH_DN_STATUSES))
        return query.order_by(DNCarrierShipment.id).all()

    @staticmethod
    def adapter_of(record: DNCarrierShipment):
        """→ (对接设置快照, 适配器)：建单用的承运商（carrier_id）的对接；旧记录按 DN 当前的承运商。
        插件不支持跟踪、设置不全 / 解不开时适配器为 None（停用了照样查：已建的运单还要跟到底）"""
        carrier = db.session.get(Carrier, record.carrier_id) if record.carrier_id else None
        if carrier is not None:
            snapshot = CarrierIntegrationService.for_carrier(carrier)
            adapter = CarrierIntegrationService.build(snapshot)
        else:
            _carrier, snapshot, adapter = CarrierShipmentService.integration_of(record.dn)
        if snapshot is None or adapter is None or not adapter.supports_tracking:
            return snapshot, None
        if CarrierIntegrationService.problems(snapshot, adapter, for_create=False):
            return snapshot, None
        return snapshot, adapter

    @staticmethod
    def supports_tracking(record: DNCarrierShipment) -> bool:
        return record is not None and CarrierTrackingService.adapter_of(record)[1] is not None

    @staticmethod
    def _plan(records) -> list:
        """→ [{snapshot, records: [(record_id, tracking_number)]}]，按对接设置（承运商）分组；查不了的跳过"""
        groups = {}
        for record in records:
            snapshot, adapter = CarrierTrackingService.adapter_of(record)
            if adapter is None:
                continue
            group = groups.setdefault(snapshot['carrier_id'], {'snapshot': snapshot, 'records': []})
            group['records'].append((record.id, record.tracking_number))
        return list(groups.values())

    # ------------------------------------------------------------------
    # 一轮
    # ------------------------------------------------------------------

    @staticmethod
    def sync(dn_id=None, budget=None) -> dict:
        """查跟踪并按结果自动完成发货 / 签收 → {tracked, updated, shipped, signed, problems, errors}。
        dn_id：只刷新这张 DN（手动刷新）；不传 = 定时任务的一轮"""
        stats = {'tracked': 0, 'updated': 0, 'shipped': 0, 'signed': 0, 'problems': 0, 'errors': 0}
        plan = CarrierTrackingService._plan(CarrierTrackingService._targets(dn_id))
        # 结束只读事务：调承运商期间不占着数据库事务
        db.session.rollback()
        deadline = clock() + (budget or budget_seconds())
        for group in plan:
            adapter = CarrierIntegrationService.build(group['snapshot'])
            numbers = [number for _record_id, number in group['records']]
            try:
                results = adapter.track(numbers, deadline=deadline)
            except CarrierError as exc:
                logger.warning("carrier %s: tracking %d shipment(s) failed: %s", group['snapshot']['carrier_id'],
                               len(numbers), exc.message)
                stats['errors'] += 1
                for record_id, number in group['records']:
                    CarrierTrackingService._safely(CarrierTrackingService._save, record_id, number,
                                                   {'error': exc.message}, KEEP, stats=stats)
                continue
            for record_id, number in group['records']:
                result = results.get(number) or {'error': NO_RESULT}
                CarrierTrackingService._safely(CarrierTrackingService._handle, record_id, number, result, stats,
                                               stats=stats)
        return stats

    @staticmethod
    def _safely(func, *args, stats):
        try:
            func(*args)
        except Exception:   # 一张出错不影响别的
            logger.exception("carrier tracking: %s failed for %s", func.__name__, args[:2])
            db.session.rollback()
            stats['errors'] += 1

    @staticmethod
    def _handle(record_id, number, result: dict, stats: dict):
        if result.get('error'):
            CarrierTrackingService._save(record_id, number, result, KEEP)
            return
        problem = None
        if picked_up(result):
            done, problem = CarrierTrackingService._attempt(CarrierTrackingService._auto_ship, record_id)
            stats['shipped'] += int(bool(done))
        if problem is None and result.get('status') == TRACK_DELIVERED:
            done, problem = CarrierTrackingService._attempt(
                CarrierTrackingService._auto_sign, record_id, result.get('delivered_at'))
            stats['signed'] += int(bool(done))
        stats['problems'] += int(problem is not None)
        stats['tracked'] += 1
        if CarrierTrackingService._save(record_id, number, result, problem):
            stats['updated'] += 1

    @staticmethod
    def _attempt(action, *args):
        """→ (做成了, 没做成的原因)。action 自成事务（出错整体回滚）"""
        try:
            return action(*args), None
        except APIException as exc:
            db.session.rollback()
            return False, f"{exc.message} ({exc.biz_code})"
        except AutoActionError as exc:
            db.session.rollback()
            return False, str(exc)

    # ------------------------------------------------------------------
    # 自动发货 / 签收
    # ------------------------------------------------------------------

    @staticmethod
    def _locked(record_id):
        """锁 DN（与完成发货 / 改单证 / 建运单同一把锁）后重读运单记录"""
        record = db.session.get(DNCarrierShipment, record_id)
        if record is None:
            return None, None
        dn = lock_row(record.dn)
        db.session.refresh(record)
        return record, dn

    @staticmethod
    def _operator(record: DNCarrierShipment, dn: DN) -> int:
        operator_id = record.created_by or dn.created_by
        if not operator_id:
            raise AutoActionError("No user to record the automatic step under (the shipment creator was deleted)")
        return operator_id

    @staticmethod
    @transactional
    def _auto_ship(record_id) -> bool:
        """承运商已经经手：完成发货（DN 不是 packed、运单不再有效时什么都不做）"""
        from warehouse.delivery.services import DeliveryTaskService
        record, dn = CarrierTrackingService._locked(record_id)
        if record is None or record.status != 'active' or dn.status != 'packed':
            return False
        task = CustomsService._delivery_task(dn)
        if task is None or task.status not in ('pending', 'in_progress'):
            raise AutoActionError(f"The carrier has the parcel, but DN {dn.id} has no delivery task to complete")
        operator_id = CarrierTrackingService._operator(record, dn)
        if task.status == 'pending':
            DeliveryTaskService.process_task(task, operator_id)
        DeliveryTaskService.complete_task(task, {}, operator_id)
        logger.info("DN %s shipped automatically: carrier %s has shipment %s", dn.id, record.carrier,
                    record.tracking_number)
        return True

    @staticmethod
    @transactional
    def _auto_sign(record_id, delivered_at) -> bool:
        """承运商已送达：签收已完成的发货任务（DN 不是 delivered 时什么都不做）"""
        from warehouse.delivery.models import DeliveryTask
        from warehouse.delivery.services import DeliveryTaskService
        record, dn = CarrierTrackingService._locked(record_id)
        if record is None or dn.status != 'delivered':
            return False
        task = (
            DeliveryTask.query
            .filter(DeliveryTask.dn_id == dn.id, DeliveryTask.is_active.is_(True), DeliveryTask.status == 'completed')
            .order_by(DeliveryTask.id.desc())
            .first()
        )
        if task is None:
            raise AutoActionError(f"The carrier delivered the parcel, but DN {dn.id} has no shipped delivery task "
                                  "to sign")
        DeliveryTaskService.sign_task(task, {'signed_at': local_naive(delivered_at) or datetime.now()},
                                      CarrierTrackingService._operator(record, dn))
        logger.info("DN %s signed automatically: carrier %s delivered shipment %s", dn.id, record.carrier,
                    record.tracking_number)
        return True

    # ------------------------------------------------------------------
    # 存跟踪结果
    # ------------------------------------------------------------------

    @staticmethod
    @transactional
    def _save(record_id, number, result: dict, problem) -> bool:
        """存跟踪结果与 tracking_problem；状态 / 扫描记录有变化时推 dn.tracking_updated → 有没有变化。
        运单已经不是这张（取消了 / 换了号）不写。查询出错（result.error）保留上次的结果，只记 error"""
        record, dn = CarrierTrackingService._locked(record_id)
        if record is None or record.tracking_number != number:
            return False
        record.tracking_synced_at = datetime.now()
        if problem is not KEEP:
            record.tracking_problem = problem[:PROBLEM_MAX] if problem else None
        if result.get('error'):
            detail = dict(record.tracking_detail or {})
            detail['error'] = str(result['error'])[:ERROR_MAX]
            record.tracking_detail = detail
            return False
        before = _fingerprint(record.tracking_status, record.tracking_code, record.tracking_detail or {})
        detail = {
            'status_text': result.get('status_text'),
            'picked_up_at': result.get('picked_up_at'),
            'delivered_at': result.get('delivered_at'),
            'estimated_delivery': result.get('estimated_delivery'),
            'events': result.get('events') or [],
            'error': None,
        }
        record.tracking_status = result.get('status')
        record.tracking_code = (str(result['code'])[:10] if result.get('code') else None)
        record.tracking_detail = detail
        if _fingerprint(record.tracking_status, record.tracking_code, detail) == before:
            return False
        webhook_emit('dn.tracking_updated', {
            'dn_id': dn.id, 'order_number': dn.order_number,
            'carrier': record.carrier, 'tracking_number': record.tracking_number,
            'tracking': {
                'status': record.tracking_status, 'code': record.tracking_code,
                **{k: detail[k] for k in ('status_text', 'picked_up_at', 'delivered_at', 'estimated_delivery',
                                          'events')},
            },
        }, api_key_id=dn.api_key_id, dedupe_key=f'dn:{dn.id}')
        return True
