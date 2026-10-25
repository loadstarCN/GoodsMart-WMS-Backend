"""库存写入守卫：发现绕过 StockService 直接改库存数量的代码（doc/库存数值系统说明.md 第九节）。

挂在 SQLAlchemy 的 before_flush 上，检查本次 flush 里的：
- Inventory：受保护列（onhand / damage / return / sorted / sorted_damage / picked / packed / delivered / total）有改动；
  新建时受保护列不全为 0；删除时受保护列不全为 0
- GoodsLocation：数量 / 键（goods_id、location_id、lot_id）有改动；新建数量不为 0；删除数量不为 0
- InventoryLotStock：任何库存列 / 键有改动；新建不全为 0；删除不全为 0
- StockLedger：任何修改或删除（流水只增不改），以及不是 StockService 写的新行
这些对象必须由 StockService 在记账时登记（authorize），且值与登记时一致；否则按 STOCK_GUARD 处理：
- raise：抛 StockGuardViolation（测试 / 开发用）
- log：只记 warning 日志（logger stock.guard），照常写库（生产上线初期）
- off：不检查
另外拦截对这几张表的批量 UPDATE / DELETE（Query.update / delete、update() / delete() 语句）。
数据修复 / 测试造数据可以用 suspended() 临时关掉（会话级，可嵌套）。
"""
import logging
import traceback
from contextlib import contextmanager

from flask import current_app, has_app_context
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger('stock.guard')

INVENTORY_PROTECTED_COLUMNS = (
    'onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock', 'sorted_damage_stock',
    'picked_stock', 'packed_stock', 'delivered_stock', 'total_stock',
)
GOODS_LOCATION_PROTECTED_COLUMNS = ('quantity', 'goods_id', 'location_id', 'lot_id')
LOT_STOCK_PROTECTED_COLUMNS = ('sorted_stock', 'sorted_damage_stock', 'picked_stock', 'packed_stock',
                               'delivered_stock', 'warehouse_id', 'goods_id', 'lot_id')
# 新建 / 删除时必须为 0 的列（否则算改了库存）
_QUANTITY_COLUMNS = {
    'Inventory': INVENTORY_PROTECTED_COLUMNS,
    'GoodsLocation': ('quantity',),
    'InventoryLotStock': ('sorted_stock', 'sorted_damage_stock', 'picked_stock', 'packed_stock', 'delivered_stock'),
}

MODES = ('raise', 'log', 'off')
_AUTH_KEY = 'stock_authorized'
_SUSPEND_KEY = 'stock_guard_suspended'


class StockGuardViolation(RuntimeError):
    """绕过 StockService 改了库存（STOCK_GUARD = raise 时抛出）"""


def _models():
    from warehouse.goods.models import GoodsLocation
    from warehouse.inventory.models import Inventory
    from .models import InventoryLotStock, StockLedger
    return {
        Inventory: INVENTORY_PROTECTED_COLUMNS,
        GoodsLocation: GOODS_LOCATION_PROTECTED_COLUMNS,
        InventoryLotStock: LOT_STOCK_PROTECTED_COLUMNS,
        StockLedger: None,
    }


def mode() -> str:
    """STOCK_GUARD 配置（config.py；默认 log）"""
    value = current_app.config.get('STOCK_GUARD', 'log') if has_app_context() else 'log'
    value = (value or 'log').strip().lower()
    return value if value in MODES else 'log'


def _snapshot(obj):
    columns = _models().get(type(obj))
    if columns is None:
        return None
    return {column: getattr(obj, column) for column in columns}


def authorize(obj, session=None):
    """StockService 登记：obj 的当前值是经过记账的（下一次 flush 核对；flush 后清空）"""
    from extensions.db import db
    session = session or db.session
    session.info.setdefault(_AUTH_KEY, {})[id(obj)] = (obj, _snapshot(obj))


@contextmanager
def suspended(session=None):
    """临时关掉守卫（测试造数据、维护脚本）。会话级、可嵌套"""
    from extensions.db import db
    session = session or db.session
    session.info[_SUSPEND_KEY] = session.info.get(_SUSPEND_KEY, 0) + 1
    try:
        yield
    finally:
        session.info[_SUSPEND_KEY] -= 1
        if not session.info[_SUSPEND_KEY]:
            session.info.pop(_SUSPEND_KEY, None)


def _active(session) -> bool:
    return not session.info.get(_SUSPEND_KEY) and mode() != 'off'


def _authorized(session, obj) -> bool:
    entry = session.info.get(_AUTH_KEY, {}).get(id(obj))
    if entry is None or entry[0] is not obj:
        return False
    snapshot = entry[1]
    return snapshot is None or snapshot == _snapshot(obj)


def _all_zero(obj) -> bool:
    return all(not (getattr(obj, column) or 0) for column in _QUANTITY_COLUMNS[type(obj).__name__])


def _identity(obj):
    state = inspect(obj)
    key = state.identity if state.identity is not None else None
    return f"{type(obj).__name__}{tuple(key) if key else '(new)'}"


def _changes(obj, columns):
    state = inspect(obj)
    changed = {}
    for column in columns:
        history = state.attrs[column].history
        if history.has_changes():
            before = history.deleted[0] if history.deleted else None
            after = history.added[0] if history.added else getattr(obj, column)
            changed[column] = (before, after)
    return changed


def _report(problems):
    """按模式处理发现的问题（problems：文字说明的列表）"""
    stack = ''.join(traceback.format_list([frame for frame in traceback.extract_stack()[:-3]
                                           if 'site-packages' not in frame.filename][-8:]))
    message = "Stock written outside StockService: " + '; '.join(problems)
    if mode() == 'raise':
        raise StockGuardViolation(message)
    logger.warning("[StockGuard] %s\n%s", message, stack)


@event.listens_for(Session, 'before_flush')
def _before_flush(session, flush_context, instances):
    if not _active(session):
        return
    models = _models()
    from .models import StockLedger
    problems = []
    for obj in session.new:
        columns = models.get(type(obj), ())
        if columns == ():
            continue
        if type(obj) is StockLedger:
            if not _authorized(session, obj):
                problems.append(f"new {type(obj).__name__} not written by StockService")
        elif not _all_zero(obj) and not _authorized(session, obj):
            problems.append(f"new {type(obj).__name__} with stock "
                            f"{ {c: getattr(obj, c) for c in _QUANTITY_COLUMNS[type(obj).__name__]} }")
    for obj in session.dirty:
        columns = models.get(type(obj), ())
        if columns == ():
            continue
        if type(obj) is StockLedger:
            if session.is_modified(obj, include_collections=False):
                problems.append(f"{_identity(obj)} modified (the ledger is append-only)")
            continue
        changed = _changes(obj, columns)
        if changed and not _authorized(session, obj):
            problems.append(f"{_identity(obj)} changed {changed}")
    for obj in session.deleted:
        columns = models.get(type(obj), ())
        if columns == ():
            continue
        if type(obj) is StockLedger:
            problems.append(f"{_identity(obj)} deleted (the ledger is append-only)")
        elif not _all_zero(obj) and not _authorized(session, obj):
            problems.append(f"{_identity(obj)} deleted with stock")
    if problems:
        _report(problems)


@event.listens_for(Session, 'after_flush_postexec')
def _after_flush(session, flush_context):
    session.info.pop(_AUTH_KEY, None)


@event.listens_for(Session, 'after_transaction_end')
def _after_transaction(session, transaction):
    if transaction.parent is None:
        session.info.pop(_AUTH_KEY, None)


@event.listens_for(Session, 'do_orm_execute')
def _bulk_statement(orm_execute_state):
    """批量 UPDATE / DELETE（不经过 flush，before_flush 看不到）"""
    if not (orm_execute_state.is_update or orm_execute_state.is_delete):
        return
    session = orm_execute_state.session
    if not _active(session):
        return
    mapper = orm_execute_state.bind_mapper
    if mapper is None or mapper.class_ not in _models():
        return
    if mapper.class_.__name__ == 'Inventory' and orm_execute_state.is_update:
        values = getattr(orm_execute_state.statement, '_values', None) or {}
        names = {getattr(key, 'key', key) for key in values}
        if not names & set(INVENTORY_PROTECTED_COLUMNS):
            return
    _report([f"bulk {'UPDATE' if orm_execute_state.is_update else 'DELETE'} on {mapper.class_.__name__}"])
