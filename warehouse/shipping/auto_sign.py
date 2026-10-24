"""自动视为签收（规则 B，doc/shipping-flow.md §2）：carriers.auto_sign_days（1–90，空 = 关）。

没有 API 跟踪的运单（手填运单号、承运商没有跟踪接口、跟踪方式选了 link / none）不会自动签收；设了 auto_sign_days 的承运商，
交承运商后 N 天还没签收的，定时任务（scheduler.py，每 CARRIER_AUTO_SIGN_INTERVAL_MINUTES 分钟，默认 60，0 = 不跑）
按「交承运商时刻 + N 天」签收：
- 对象：DN 是 delivered、当前发货任务是 completed（有完成时刻）、承运商（当前发货任务的，没有用 DN 的）设了 auto_sign_days、
  这张 DN 的有效运单没有经 API 跟踪（warehouse/dn/tracking_services.py 的口径：承运商 tracking_method = api、插件
  supports_tracking、插件建的或插件 tracks_any_number），且完成时刻 + N 天 ≤ 现在。
- 签收时刻 signed_at = 完成时刻 + N 天（不是跑任务的时刻）；signed_source = rule；操作人 = 运单的建单人，没有用 DN 的创建人。
- 走手工签收同一个逻辑（DeliveryTaskService.sign_task：DN completed、推 dn.completed，signed_source = rule）。
- 每张自己一个事务，一张失败不影响别的；任何时候都可以手工确认签收（填实际时刻）。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from extensions.db import db
from extensions.transaction import transactional
from warehouse.carrier.models import Carrier
from warehouse.common.locks import lock_row
from warehouse.delivery.models import DeliveryTask
from warehouse.dn.models import DN

from .common import current_delivery_task
from .waybills import active_waybill

logger = logging.getLogger(__name__)

SOURCE_RULE = 'rule'
BATCH_LIMIT = 500           # 一轮最多处理这么多张（其余下一轮）


def auto_sign_at(task, days):
    """规则签收的时刻：完成发货时刻 + days 天；没有完成时刻 / 没设天数为 None"""
    if task is None or task.completed_at is None or not days:
        return None
    return task.completed_at + timedelta(days=int(days))


def tracked_by_api(dn) -> bool:
    """DN 的有效运单经 API 跟踪（承运商跟踪会自动签收，规则 B 不管）"""
    from warehouse.dn.tracking_services import CarrierTrackingService
    record = active_waybill(dn)
    return bool(record is not None and record.tracking_number and CarrierTrackingService.supports_tracking(record))


def carrier_of(dn, task):
    """规则用的承运商：当前发货任务的，没有用 DN 的（与建运单同一口径）"""
    return (task.carrier if task is not None and task.carrier is not None else None) or dn.carrier


class AutoSignService:

    @staticmethod
    def _candidates(now) -> list:
        """可能到期的 (DN id, 发货任务 id)：DN delivered、任务 completed 有完成时刻、承运商设了 auto_sign_days（SQL 预筛，
        到期 / 跟踪在 Python 里再判）"""
        carrier_id = func.coalesce(DeliveryTask.carrier_id, DN.carrier_id)
        rows = (
            db.session.query(DN.id, DeliveryTask.id, DeliveryTask.completed_at, Carrier.auto_sign_days)
            .join(DeliveryTask, DeliveryTask.dn_id == DN.id)
            .join(Carrier, Carrier.id == carrier_id)
            .filter(DN.status == 'delivered', DeliveryTask.is_active.is_(True), DeliveryTask.status == 'completed',
                    DeliveryTask.completed_at.isnot(None), Carrier.auto_sign_days.isnot(None))
            .order_by(DeliveryTask.completed_at, DeliveryTask.id)
            .all()
        )
        due = [(dn_id, task_id) for dn_id, task_id, completed_at, days in rows
               if completed_at + timedelta(days=days) <= now]
        return due[:BATCH_LIMIT]

    @staticmethod
    def sync(now=None) -> dict:
        """一轮：到期的签收 → {signed, skipped, errors}（skipped = 有 API 跟踪 / 已不满足条件）"""
        now = now or datetime.now()
        stats = {'signed': 0, 'skipped': 0, 'errors': 0}
        candidates = AutoSignService._candidates(now)
        db.session.rollback()
        for dn_id, task_id in candidates:
            try:
                if AutoSignService._sign(dn_id, task_id, now):
                    stats['signed'] += 1
                else:
                    stats['skipped'] += 1
            except Exception:   # 一张出错不影响别的
                logger.exception("auto sign: DN %s / delivery task %s failed", dn_id, task_id)
                db.session.rollback()
                stats['errors'] += 1
        return stats

    @staticmethod
    @transactional
    def _sign(dn_id, task_id, now) -> bool:
        """锁 DN 后重新判断，满足条件就按规则签收 → 签了没有"""
        from warehouse.delivery.services import DeliveryTaskService
        dn = lock_row(db.session.get(DN, dn_id))
        if dn is None or dn.status != 'delivered':
            return False
        task = current_delivery_task(dn)
        if task is None or task.id != task_id or task.status != 'completed':
            return False
        carrier = carrier_of(dn, task)
        signed_at = auto_sign_at(task, getattr(carrier, 'auto_sign_days', None))
        if signed_at is None or signed_at > now or tracked_by_api(dn):
            return False
        record = active_waybill(dn)
        operator_id = (record.created_by if record is not None else None) or dn.created_by
        DeliveryTaskService.sign_task(task, {'signed_at': signed_at}, operator_id, source=SOURCE_RULE)
        logger.info("DN %s signed by rule: %s days after handover (carrier %s)", dn.id, carrier.auto_sign_days,
                    carrier.id)
        return True
