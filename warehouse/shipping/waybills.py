"""运单记录（dn_carrier_shipments，与来源无关）与手填运单号的 manual 记录（doc/shipping-flow.md §4）。

- 一张 DN 同一时刻至多一条未结束的运单记录（pending / active / unknown / cancelling，部分唯一索引兜底，不分来源）。
- api 运单（插件建的）的状态机、锁定规则（16078 / 16079、换号先取消）在 warehouse/dn/carrier_services.py，只对 api 生效。
- manual 运单：发货任务上的运单号（保存运单号 / 修改发货任务 / 完成发货时填的）同时写一条 source = manual 的记录，
  只有 active / cancelled 两种状态，不调承运商：
  - 号码或承运商变了 → 旧记录 cancelled（reason replaced）+ 新号码一条 active；
  - 清空 → 当前记录 cancelled（reason cleared）；DN 取消 → cancelled（reason dn_cancelled）。
  - 有未结束的 api 运单时不写（运单号由 api 运单定，保存时已按它校验）。
  记录的 carrier_id = 发货任务的承运商，carrier = 该承运商在线对接的插件代码（没有对接为空），created_by = 操作人。
  有了记录，揽收（manual / api 预约）可以带上它，插件 tracks_any_number 时照常跟踪。
调用方应先锁 DN 行（与保存运单号、建运单同一把锁）。
"""
import logging
from datetime import datetime

from extensions.db import db
from warehouse.dn.models import DN, DNCarrierShipment

logger = logging.getLogger(__name__)

OPEN_STATUSES = DNCarrierShipment.OPEN_STATUSES
SOURCE_API = 'api'
SOURCE_MANUAL = 'manual'
SOURCE_FILE = 'file'


def _normalize(value) -> str:
    """运单号比对用的规范化写法（去空白、连字符；与 carrier_services.normalize_tracking 同一口径）"""
    return ''.join(str(value or '').split()).replace('-', '')


def current_waybill(dn: DN):
    """未结束的运单记录（任一来源：pending / active / unknown / cancelling，同一 DN 至多一条）；没有为 None"""
    return (
        DNCarrierShipment.query
        .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.status.in_(OPEN_STATUSES))
        .order_by(DNCarrierShipment.id.desc())
        .first()
    )


def active_waybill(dn: DN):
    """有效运单（任一来源，status = active）；没有为 None"""
    return (
        DNCarrierShipment.query
        .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.status == 'active')
        .order_by(DNCarrierShipment.id.desc())
        .first()
    )


def has_waybill_records(dn: DN) -> bool:
    """DN 有过运单记录（任一来源、任一状态）"""
    return db.session.query(
        DNCarrierShipment.query.filter(DNCarrierShipment.dn_id == dn.id).exists()
    ).scalar()


def _adapter_code(carrier):
    integration = getattr(carrier, 'integration', None) if carrier is not None else None
    return integration.adapter if integration is not None else None


def _close(record: DNCarrierShipment, reason: str, user_id, now):
    record.status = 'cancelled'
    record.reason = reason
    record.cancelled_at = now
    record.cancelled_by = user_id
    record.updated_at = now


def sync_manual(task, user_id):
    """发货任务（当前任务）的运单号 / 承运商变了之后，让 manual 运单记录跟上（见模块说明）。DN 应已加锁。
    → 现在的 manual 记录（没有为 None）"""
    dn = task.dn
    record = current_waybill(dn)
    if record is not None and record.source != SOURCE_MANUAL:
        # api（或第二期 file）运单：运单号由它定，不另写 manual 记录
        return None
    number = (task.tracking_number or '').strip() or None
    now = datetime.now()
    if record is not None:
        if number is not None and _normalize(record.tracking_number) == _normalize(number) \
                and record.carrier_id == task.carrier_id:
            if record.tracking_number != number:
                record.tracking_number = number         # 同一个号码换了写法（空格 / 连字符）：跟任务上的一致
                record.updated_at = now
                db.session.flush()
            return record
        _close(record, 'replaced' if number is not None else 'cleared', user_id, now)
        db.session.flush()
        logger.info("DN %s: manual waybill %s (record %s) %s by user %s", dn.id, record.tracking_number, record.id,
                    record.reason, user_id)
    if number is None:
        return None
    # 不用 task.carrier：同一事务里刚改过 carrier_id 时关系属性还是旧对象
    from warehouse.carrier.models import Carrier
    carrier = db.session.get(Carrier, task.carrier_id) if task.carrier_id else None
    created = DNCarrierShipment(
        dn_id=dn.id,
        source=SOURCE_MANUAL,
        carrier=_adapter_code(carrier),
        carrier_id=task.carrier_id,
        tracking_number=number[:100],
        status='active',
        package_count=len(dn.packages) or None,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(created)
    db.session.flush()
    db.session.expire(dn, ['carrier_shipments'])
    return created


def close_manual(dn: DN, reason: str, user_id=None):
    """未结束的 manual / file 运单记录 cancelled（不调承运商）：DN 取消（reason dn_cancelled）、
    带运单号的发货任务被删（cleared）"""
    now = datetime.now()
    records = (
        DNCarrierShipment.query
        .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.status.in_(OPEN_STATUSES),
                DNCarrierShipment.source != SOURCE_API)
        .all()
    )
    for record in records:
        _close(record, reason, user_id, now)
    if records:
        db.session.flush()
    return records
