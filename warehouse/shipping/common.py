"""发货共用的基础：DN 加锁、当前发货任务、发货后锁定、业务日期时区。

原来散在 CustomsService 里（_lock / _delivery_task / is_locked / LOCKED_DN_STATUSES / _document_timezone），
报关、运单、揽收、跟踪、流程汇总都引用这里；CustomsService 上保留同名的薄包装（测试与旧调用方照用）。
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import current_app

from extensions.error import ConflictException
from warehouse.common.locks import lock_row

LOCKED_DN_STATUSES = ('delivered', 'completed')       # 发货后锁定（报关快照 / 箱子 / 单证 / 运单）


def lock_dn(dn):
    """对 DN 行加锁（只查主键，避免 joined eager load 撞 PostgreSQL 的 FOR UPDATE 限制）并刷新状态。
    与 DN / 出库任务状态转换共用 lock_row：同一把 DN 行锁，先 DN 后任务。"""
    return lock_row(dn)


def is_locked(dn) -> bool:
    """DN 已发货（delivered / completed）"""
    return dn.status in LOCKED_DN_STATUSES


def assert_not_locked(dn):
    """DN 已发货 → 409 16065（报关数据、箱子、单证、运单一律锁定）"""
    if is_locked(dn):
        raise ConflictException(
            f"DN {dn.id} has been shipped; customs data, packages and documents are locked.", 16065
        )


def current_delivery_task(dn):
    """DN 的当前发货任务：最新的有效（is_active）任务；没有返回 None。
    单证过期判定、运单号锁定、建运单 / 揽收 / 跟踪都只认它。"""
    from warehouse.delivery.models import DeliveryTask
    return (
        dn.delivery_tasks
        .filter(DeliveryTask.is_active.is_(True))
        .order_by(DeliveryTask.id.desc())
        .first()
    )


def document_timezone():
    """业务日期（单证日期、实际发货日、揽收日）的时区：DOCUMENT_TIMEZONE（默认 Asia/Tokyo）；配错按 UTC。"""
    tz_name = current_app.config.get('DOCUMENT_TIMEZONE') or 'Asia/Tokyo'
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — 配错时区不能让出单证 500
        current_app.logger.warning("Invalid DOCUMENT_TIMEZONE %r, falling back to UTC", tz_name)
        return ZoneInfo('UTC')


def local_today() -> date:
    """仓库当地（DOCUMENT_TIMEZONE）的今天"""
    return datetime.now(document_timezone()).date()
