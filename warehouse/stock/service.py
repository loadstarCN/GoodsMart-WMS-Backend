"""统一库存变动服务 StockService（库存底座一期，doc/库存数值系统说明.md 第九节）。

所有改实物库存的地方都经过这里：库位库存（goods_locations 的数量、行的新增 / 删除）、不在库位上的仓内库存
（inventory_lot_stock），以及它们在 Inventory 上的缓存（onhand / damage / return / sorted / sorted_damage / picked /
packed / delivered / total）。每次变动按桶写库存流水（stock_ledger）。绕过这里直接改数量会被 stock/guard.py 发现。

用法：
    op = StockOp(location.warehouse_id, 'putaway', doc_type='putaway', doc_id=record.id, operator_id=user_id)
    op.move(goods_id, quantity, src=Leg.pending_for(location.location_type), dst=Leg.at(location.id))
    lines = op.commit()                    # = StockService.apply(op)

一侧的桶（Leg）：
- Leg.at(location_id)：库位，类别按库位类型（standard → onhand、damaged → damaged、return → return）
- Leg.pending(damaged=False)：待上架良品 / 破损
- Leg.pending_for(location_type)：上架的来源。破损库位先扣破损、其余扣良品；standard / return 库位只能用良品
  （超出 → 400 15016）；待上架合计不够 → 15007。实际按扣的部分拆成两笔记账
- Leg.picked() / Leg.packed() / Leg.in_transit()
- src=None：从仓外进（分拣入库、盘盈、期初）；dst=None：出到仓外（签收、盘亏）

apply 的步骤：
1. 校验：数量为正整数；库位属于本仓库；进货的库位必须启用（16085，期初 / 校正除外）；一期 lot 一律为空
2. 加锁，按全局顺序（B-103，common/locks.py）一次锁齐：Inventory(goods, warehouse) → GoodsLocation(goods, location, lot)
   → InventoryLotStock(warehouse, goods, lot)，各自按键升序；不存在的行在 savepoint 里新建（B-107）。
   InventoryLotStock / GoodsLocation 只在持有同一商品 Inventory 行锁时才写，所以新加的这张表不会引入新的死锁顺序
3. 逐笔记账：来源不足报错（沿用原错误码：库位 15001、待上架 15007 / 15016、已拣 15009、已打包 15010、已发 15011，
   调用方可用 errors 覆盖）；归零的行删掉
4. 增量更新 Inventory 缓存，重算 total_stock（InventoryService._calculate_total_stock）
5. 写流水：每个桶一行；同一事务的所有行共用 trace_id；operator / source / api_key 取自请求上下文（可由调用方指定）
"""
import re
import uuid

from flask import g, has_request_context, request
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, lazyload

from extensions.db import db
from extensions.error import BadRequestException, NotFoundException
from . import guard
from .models import (
    InventoryLotStock, LOCATION_STOCK_TYPES, MOVEMENT_TYPES, STOCK_DAMAGED, STOCK_IN_TRANSIT, STOCK_ONHAND,
    STOCK_PACKED, STOCK_PENDING, STOCK_PENDING_DAMAGED, STOCK_PICKED, STOCK_RETURN, StockLedger,
)

# 不再需要「启用库位」校验的变动（库位上本来就有的货：期初、系统校正）
_NO_ACTIVE_CHECK = frozenset({'opening', 'reconcile'})
# 来源为空（从仓外进货）时，商品在该仓库还没有库存行就新建（全 0 行，ensure_inventory）
_LOCATION_ERRORS = (15001, "Stock is not enough")
_DEFAULT_ERRORS = {
    STOCK_ONHAND: _LOCATION_ERRORS,
    STOCK_DAMAGED: _LOCATION_ERRORS,
    STOCK_RETURN: _LOCATION_ERRORS,
    STOCK_PENDING: (15007, "Not enough sort stock."),
    STOCK_PENDING_DAMAGED: (15007, "Not enough sort stock."),
    STOCK_PICKED: (15009, "Not enough pick stock."),
    STOCK_PACKED: (15010, "Not enough packed stock."),
    STOCK_IN_TRANSIT: (15011, "Not enough delivered stock."),
}
# 库存类别 → Inventory 缓存上要跟着变的列（待上架·破损既是 sorted_stock 的一部分，也记在 sorted_damage_stock）
CACHE_COLUMNS = {
    STOCK_ONHAND: ('onhand_stock',),
    STOCK_DAMAGED: ('damage_stock',),
    STOCK_RETURN: ('return_stock',),
    STOCK_PENDING: ('sorted_stock',),
    STOCK_PENDING_DAMAGED: ('sorted_stock', 'sorted_damage_stock'),
    STOCK_PICKED: ('picked_stock',),
    STOCK_PACKED: ('packed_stock',),
    STOCK_IN_TRANSIT: ('delivered_stock',),
}
# 库存类别 → InventoryLotStock 上要跟着变的列
LOT_STOCK_COLUMNS = {
    STOCK_PENDING: ('sorted_stock',),
    STOCK_PENDING_DAMAGED: ('sorted_stock', 'sorted_damage_stock'),
    STOCK_PICKED: ('picked_stock',),
    STOCK_PACKED: ('packed_stock',),
    STOCK_IN_TRANSIT: ('delivered_stock',),
}

# ---------------------------------------------------------------------------
# 请求上下文：trace_id / 操作人 / 来源
# ---------------------------------------------------------------------------
_TRACE_KEY = 'stock_trace_id'
_REQUEST_ID = re.compile(r'^[A-Za-z0-9-]{8,64}$')
AUTO = object()     # operator_id 缺省：取请求的当前用户（定时任务、承运商回调为空）


def trace_id() -> str:
    """同一事务的所有流水行共用一个 trace_id：每个事务懒生成一个，存在 session.info，事务结束时清掉。
    请求头带了合法的 X-Request-ID（[A-Za-z0-9-]{8,64}）时直接用它，便于和日志关联"""
    info = db.session.info
    value = info.get(_TRACE_KEY)
    if value is None:
        header = request.headers.get('X-Request-ID') if has_request_context() else None
        value = header if header and _REQUEST_ID.match(header) else uuid.uuid4().hex
        info[_TRACE_KEY] = value
    return value


@event.listens_for(Session, 'after_transaction_end')
def _clear_trace(session, transaction):
    if transaction.parent is None:
        session.info.pop(_TRACE_KEY, None)


def current_operator_id():
    """请求的当前用户（API Key 绑定的用户也算）；没有请求上下文（定时任务 / CLI）为 None"""
    if not has_request_context():
        return None
    user = g.get('current_user')
    return getattr(user, 'id', None)


def current_api_key_id():
    if not has_request_context():
        return None
    system = g.get('current_system') or {}
    key = system.get('api_key') if isinstance(system, dict) else None
    return getattr(key, 'id', None)


def current_source() -> str:
    """API Key 调用 → api；没有请求上下文 → system；否则看请求头 X-Client-App（pda/<版本> → pda），其余为 web"""
    if not has_request_context():
        return 'system'
    if current_api_key_id() is not None:
        return 'api'
    client = (request.headers.get('X-Client-App') or '').strip().lower()
    return 'pda' if client.startswith('pda') else 'web'


# ---------------------------------------------------------------------------
# 桶与变动
# ---------------------------------------------------------------------------
class Leg:
    """一笔变动的一侧（来源或去向）的桶。lot_id 一期一律为空"""
    LOCATION = 'location'
    PENDING_FOR = 'pending_for'
    __slots__ = ('kind', 'location_id', 'location_type', 'lot_id')

    def __init__(self, kind, location_id=None, lot_id=None, location_type=None):
        self.kind = kind
        self.location_id = location_id
        self.lot_id = lot_id
        self.location_type = location_type

    @classmethod
    def at(cls, location_id, lot_id=None):
        return cls(cls.LOCATION, location_id=location_id, lot_id=lot_id)

    @classmethod
    def pending(cls, lot_id=None, damaged=False):
        return cls(STOCK_PENDING_DAMAGED if damaged else STOCK_PENDING, lot_id=lot_id)

    @classmethod
    def pending_for(cls, location_type, lot_id=None):
        """上架的来源：上到 location_type 类型的库位（damaged 先扣破损，其余扣良品；standard / return 只能用良品）"""
        return cls(cls.PENDING_FOR, lot_id=lot_id, location_type=location_type)

    @classmethod
    def picked(cls, lot_id=None):
        return cls(STOCK_PICKED, lot_id=lot_id)

    @classmethod
    def packed(cls, lot_id=None):
        return cls(STOCK_PACKED, lot_id=lot_id)

    @classmethod
    def in_transit(cls, lot_id=None):
        return cls(STOCK_IN_TRANSIT, lot_id=lot_id)

    @classmethod
    def of(cls, stock_type, location_id=None, lot_id=None):
        """按库存类别取桶（期初 / 校正用）：库位类别要给 location_id"""
        if stock_type in LOCATION_STOCK_TYPES.values():
            return cls.at(location_id, lot_id)
        return cls(stock_type, lot_id=lot_id)

    def __repr__(self):
        return f"Leg({self.kind}, location={self.location_id}, lot={self.lot_id})"


class _Move:
    __slots__ = ('goods_id', 'quantity', 'src', 'dst', 'movement_type', 'doc_line_id', 'reason', 'remark', 'errors')

    def __init__(self, goods_id, quantity, src, dst, movement_type, doc_line_id, reason, remark, errors):
        self.goods_id = goods_id
        self.quantity = quantity
        self.src = src
        self.dst = dst
        self.movement_type = movement_type
        self.doc_line_id = doc_line_id
        self.reason = reason
        self.remark = remark
        self.errors = errors or {}


class StockOp:
    """一次库存变动（同一仓库、同一单据）：若干笔 move，commit 时一起记账、写流水"""

    def __init__(self, warehouse_id, movement_type, *, doc_type=None, doc_id=None, doc_no=None, operator_id=AUTO,
                 occurred_at=None, reason=None, remark=None, source=None):
        if movement_type not in MOVEMENT_TYPES:
            raise ValueError(f"unknown movement_type {movement_type!r}")
        self.warehouse_id = warehouse_id
        self.movement_type = movement_type
        self.doc_type = doc_type
        self.doc_id = doc_id
        self.doc_no = None if doc_no is None else str(doc_no)[:100]
        self.operator_id = operator_id
        self.occurred_at = occurred_at
        self.reason = reason
        self.remark = remark
        self.source = source
        self.moves = []
        self.committed = False

    def move(self, goods_id, quantity, src=None, dst=None, *, movement_type=None, doc_line_id=None, reason=None,
             remark=None, errors=None):
        """记一笔：从 src 桶搬 quantity 件到 dst 桶（src / dst 为空 = 仓外）。quantity 为 0 的不记。
        errors：覆盖来源不足时的报错，{'insufficient': 业务码 | (业务码, 文案) | callable(have) -> 异常}"""
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValueError(f"quantity must be a non-negative int, got {quantity!r}")
        if src is None and dst is None:
            raise ValueError("a move needs a source or a destination")
        if movement_type is not None and movement_type not in MOVEMENT_TYPES:
            raise ValueError(f"unknown movement_type {movement_type!r}")
        if quantity:
            self.moves.append(_Move(goods_id, quantity, src, dst, movement_type or self.movement_type, doc_line_id,
                                    reason, remark, errors))
        return self

    def commit(self):
        """记账并写流水，返回写入的流水行（没有 move 时返回 []）"""
        return StockService.apply(self)


# ---------------------------------------------------------------------------
# 加锁读取 / 新建行（查询一律 lazyload('*')：FOR UPDATE 不能用在 outer join 的可空一侧）
# ---------------------------------------------------------------------------
def _filter_lot(query, column, lot_id):
    return query.filter(column.is_(None) if lot_id is None else column == lot_id)


def lock_goods_location(goods_id, location_id, lot_id=None):
    """按 (goods_id, location_id, lot) 加锁读取库位库存行；不存在返回 None"""
    from warehouse.goods.models import GoodsLocation
    query = (GoodsLocation.query.options(lazyload('*'))
             .filter(GoodsLocation.goods_id == goods_id, GoodsLocation.location_id == location_id))
    return _filter_lot(query, GoodsLocation.lot_id, lot_id).with_for_update().first()


def lock_lot_stock(warehouse_id, goods_id, lot_id=None):
    """按 (warehouse_id, goods_id, lot) 加锁读取 inventory_lot_stock 行；不存在返回 None"""
    query = (InventoryLotStock.query.options(lazyload('*'))
             .filter(InventoryLotStock.warehouse_id == warehouse_id, InventoryLotStock.goods_id == goods_id))
    return _filter_lot(query, InventoryLotStock.lot_id, lot_id).with_for_update().first()


def _lock_or_create(lock, make):
    """加锁读取；不存在就在 savepoint 里新建（数量 0）再返回（B-107：并发首次新建撞唯一索引 → 回滚 savepoint 后重读）"""
    record = lock()
    if record is not None:
        return record
    try:
        with db.session.begin_nested():
            record = make()
            db.session.add(record)
            db.session.flush()
    except IntegrityError:
        record = lock()
        if record is None:
            raise
    return record


def lock_or_create_goods_location(goods_id, location_id, lot_id=None):
    from warehouse.goods.models import GoodsLocation
    return _lock_or_create(
        lambda: lock_goods_location(goods_id, location_id, lot_id),
        lambda: GoodsLocation(goods_id=goods_id, location_id=location_id, lot_id=lot_id, quantity=0),
    )


def lock_or_create_lot_stock(warehouse_id, goods_id, lot_id=None):
    return _lock_or_create(
        lambda: lock_lot_stock(warehouse_id, goods_id, lot_id),
        lambda: InventoryLotStock(warehouse_id=warehouse_id, goods_id=goods_id, lot_id=lot_id),
    )


def bucket_balance(stock_type, row) -> int:
    """行上某个库存类别的结存（待上架良品 = sorted_stock − sorted_damage_stock）"""
    if row is None:
        return 0
    if stock_type in LOCATION_STOCK_TYPES.values():
        return int(row.quantity or 0)
    if stock_type == STOCK_PENDING:
        return int(row.sorted_stock or 0) - int(row.sorted_damage_stock or 0)
    return int(getattr(row, LOT_STOCK_COLUMNS[stock_type][-1]) or 0)


def _raise_insufficient(move, stock_type, have, need):
    custom = move.errors.get('insufficient')
    if callable(custom):
        raise custom(have)
    if isinstance(custom, tuple):
        code, message = custom
    elif isinstance(custom, int):
        code, message = custom, _DEFAULT_ERRORS[stock_type][1]
    else:
        code, message = _DEFAULT_ERRORS[stock_type]
    raise BadRequestException(message, code)


class _Bucket:
    __slots__ = ('stock_type', 'row', 'location_id', 'lot_id')

    def __init__(self, stock_type, row, location_id=None, lot_id=None):
        self.stock_type = stock_type
        self.row = row
        self.location_id = location_id
        self.lot_id = lot_id


class StockService:

    @staticmethod
    def apply(op: StockOp):
        """记账（步骤见模块说明）。调用方负责事务（transactional）；这里只 flush"""
        if op.committed:
            raise RuntimeError("StockOp already committed")
        op.committed = True
        moves = op.moves
        if not moves:
            return []
        from warehouse.inventory.services import InventoryService
        from warehouse.location.models import Location
        from warehouse.warehouse.models import Warehouse
        from warehouse.common.locks import lock_inventory_pairs, lock_order_key
        from warehouse.common.ownership import require_same_warehouse

        warehouse_id = op.warehouse_id
        warehouse = db.session.get(Warehouse, warehouse_id)
        if warehouse is None:
            raise NotFoundException(f"Warehouse with id {warehouse_id} not found", 13001)

        # 1. 校验
        locations = {}
        for move in moves:
            for leg, inbound in ((move.src, False), (move.dst, True)):
                if leg is None:
                    continue
                if leg.lot_id is not None:
                    raise ValueError("lots are not enabled yet (phase 2)")
                if leg.kind != Leg.LOCATION:
                    continue
                location = locations.get(leg.location_id)
                if location is None:
                    location = db.session.get(Location, leg.location_id)
                    if location is None:
                        raise NotFoundException(f"Location with id {leg.location_id} not found", 13001)
                    locations[leg.location_id] = location
                require_same_warehouse(warehouse_id, location.warehouse_id, 'location')
                if inbound and not location.is_active and move.movement_type not in _NO_ACTIVE_CHECK:
                    raise BadRequestException(
                        f"Location {location.code} is inactive and cannot receive stock.", 16085, field='location_id')

        # 2. 加锁：库存行 → 库位库存行 → 不在库位上的库存行，各自按键升序
        goods_ids = sorted({move.goods_id for move in moves}, key=lock_order_key)
        inventories = lock_inventory_pairs((goods_id, warehouse_id) for goods_id in goods_ids)
        inbound_goods = {move.goods_id for move in moves if move.src is None}
        for goods_id in goods_ids:
            if (goods_id, warehouse_id) in inventories:
                continue
            if goods_id not in inbound_goods:
                raise NotFoundException(
                    f"Inventory not found for goods {goods_id} in warehouse {warehouse_id}", 43001)
            InventoryService.ensure_inventory(goods_id, warehouse_id)
            inventories.update(lock_inventory_pairs([(goods_id, warehouse_id)]))

        location_keys, lot_keys = {}, {}
        for move in moves:
            for leg, inbound in ((move.src, False), (move.dst, True)):
                if leg is None:
                    continue
                if leg.kind == Leg.LOCATION:
                    key = (move.goods_id, leg.location_id, leg.lot_id)
                    location_keys[key] = location_keys.get(key, False) or inbound
                else:
                    key = (move.goods_id, leg.lot_id)
                    lot_keys[key] = lot_keys.get(key, False) or inbound
        location_rows = {}
        for key in sorted(location_keys, key=lambda k: lock_order_key(*k)):
            goods_id, location_id, lot_id = key
            location_rows[key] = (lock_or_create_goods_location(goods_id, location_id, lot_id) if location_keys[key]
                                  else lock_goods_location(goods_id, location_id, lot_id))
        lot_rows = {}
        for key in sorted(lot_keys, key=lambda k: lock_order_key(*k)):
            goods_id, lot_id = key
            lot_rows[key] = (lock_or_create_lot_stock(warehouse_id, goods_id, lot_id) if lot_keys[key]
                             else lock_lot_stock(warehouse_id, goods_id, lot_id))

        def bucket(goods_id, leg):
            if leg.kind == Leg.LOCATION:
                location = locations[leg.location_id]
                return _Bucket(LOCATION_STOCK_TYPES[location.location_type],
                               location_rows[(goods_id, leg.location_id, leg.lot_id)], leg.location_id, leg.lot_id)
            return _Bucket(leg.kind, lot_rows[(goods_id, leg.lot_id)], None, leg.lot_id)

        # 3–5. 逐笔记账（整段不 autoflush：守卫按记账结束时的值核对）
        posted_at = _now()
        context = {
            'posted_at': posted_at,
            'occurred_at': op.occurred_at or posted_at,
            'company_id': warehouse.company_id,
            'operator_id': current_operator_id() if op.operator_id is AUTO else op.operator_id,
            'api_key_id': current_api_key_id(),
            'source': op.source or current_source(),
            'trace_id': trace_id(),
        }
        lines = []
        with db.session.no_autoflush:
            for move in moves:
                inventory = inventories[(move.goods_id, warehouse_id)]
                if move.src is not None and move.src.kind == Leg.PENDING_FOR:
                    parts = StockService._split_pending_for(move, lot_rows[(move.goods_id, move.src.lot_id)])
                else:
                    parts = [(move.src, move.quantity)]
                for src, quantity in parts:
                    src_bucket = bucket(move.goods_id, src) if src is not None else None
                    dst_bucket = bucket(move.goods_id, move.dst) if move.dst is not None else None
                    if src_bucket is not None:
                        have = bucket_balance(src_bucket.stock_type, src_bucket.row)
                        if have < quantity:
                            _raise_insufficient(move, src_bucket.stock_type, have, quantity)
                        StockService._change(src_bucket, inventory, -quantity, move)
                    if dst_bucket is not None:
                        StockService._change(dst_bucket, inventory, quantity, move)
                    inventory.total_stock = InventoryService._calculate_total_stock(inventory)
                    for side, other, sign in ((src_bucket, dst_bucket, -1), (dst_bucket, src_bucket, 1)):
                        if side is None:
                            continue
                        lines.append(StockService._line(op, move, context, side, sign * quantity,
                                                        other.stock_type if other is not None else None,
                                                        inventory.total_stock))
            touched = list(inventories.values())
            for row in list(location_rows.values()) + list(lot_rows.values()):
                if row is None:
                    continue
                empty = (row.quantity or 0) == 0 if not isinstance(row, InventoryLotStock) else row.is_empty()
                if empty:
                    db.session.delete(row)
                touched.append(row)
            for obj in touched + lines:
                guard.authorize(obj)
            db.session.add_all(lines)
        db.session.flush()
        return lines

    @staticmethod
    def _split_pending_for(move, row):
        """上架的来源拆成「破损部分 + 良品部分」：damaged 库位先扣破损；standard / return 只能用良品（15016）"""
        quantity = move.quantity
        location_type = move.src.location_type
        sorted_stock = int(row.sorted_stock or 0) if row is not None else 0
        damaged = min(int(row.sorted_damage_stock or 0), sorted_stock) if row is not None else 0
        if sorted_stock < quantity:
            _raise_insufficient(move, STOCK_PENDING, sorted_stock, quantity)
        good = sorted_stock - damaged
        if location_type == 'damaged':
            take_damaged = min(quantity, damaged)
            take_good = quantity - take_damaged
        else:
            if quantity > good:
                custom = move.errors.get('damaged_only')
                if callable(custom):
                    raise custom(good, damaged)
                raise BadRequestException(
                    f"Only {good} good item(s) of goods {move.goods_id} are waiting to be put away; "
                    f"{damaged} damaged item(s) can only be put away to a damaged location.",
                    15016, field='quantity',
                    details={'goods_id': move.goods_id, 'location_type': location_type, 'quantity': quantity,
                             'good_pending': good, 'damaged_pending': damaged},
                )
            take_damaged, take_good = 0, quantity
        lot_id = move.src.lot_id
        parts = []
        if take_damaged:
            parts.append((Leg.pending(lot_id, damaged=True), take_damaged))
        if take_good:
            parts.append((Leg.pending(lot_id), take_good))
        return parts

    @staticmethod
    def _change(bucket, inventory, delta, move):
        """桶所在行与 Inventory 缓存一起加减 delta；缓存会变成负数时（缓存与行表不一致）按来源不足报错"""
        if bucket.stock_type in LOCATION_STOCK_TYPES.values():
            bucket.row.quantity = int(bucket.row.quantity or 0) + delta
        else:
            for column in LOT_STOCK_COLUMNS[bucket.stock_type]:
                setattr(bucket.row, column, int(getattr(bucket.row, column) or 0) + delta)
        for column in CACHE_COLUMNS[bucket.stock_type]:
            value = int(getattr(inventory, column) or 0) + delta
            if value < 0:
                _raise_insufficient(move, bucket.stock_type, value - delta, -delta)
            setattr(inventory, column, value)

    @staticmethod
    def _line(op, move, context, bucket, quantity, counter_stock_type, goods_total_after):
        return StockLedger(
            posted_at=context['posted_at'],
            occurred_at=context['occurred_at'],
            company_id=context['company_id'],
            warehouse_id=op.warehouse_id,
            goods_id=move.goods_id,
            lot_id=bucket.lot_id,
            location_id=bucket.location_id,
            stock_type=bucket.stock_type,
            quantity=quantity,
            balance_after=bucket_balance(bucket.stock_type, bucket.row),
            goods_total_after=goods_total_after,
            counter_stock_type=counter_stock_type,
            movement_type=move.movement_type,
            doc_type=op.doc_type,
            doc_id=op.doc_id,
            doc_no=op.doc_no,
            doc_line_id=move.doc_line_id,
            reason=_clip(move.reason if move.reason is not None else op.reason, 30),
            operator_id=context['operator_id'],
            api_key_id=context['api_key_id'],
            source=context['source'],
            trace_id=context['trace_id'],
            remark=_clip(move.remark if move.remark is not None else op.remark, 255),
        )


def _clip(value, length):
    if value is None:
        return None
    text = str(value)
    return text[:length] if text else None


def _now():
    from warehouse.common import clock
    return clock.now()
