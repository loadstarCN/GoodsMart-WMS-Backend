"""库存对账与校正（doc/库存数值系统说明.md 第九节「对账」）。

对账（audit_stock）核对：
① Inventory 缓存 = 库位库存（goods_locations，按库位类型）与不在库位上的库存（inventory_lot_stock）的合计，
   total_stock = 公式（InventoryService._calculate_total_stock）
② 每个桶的流水合计 = 当前结存；③ 每个桶最后一条流水的 balance_after = 当前结存；
   另核对流水链：每行 balance_after = 上一行 balance_after + quantity（按 (posted_at, id)）
④ 没有负数；⑤ 破损 ≤ 待上架；⑥ 批次属于该商品
桶的键：(warehouse_id, goods_id, lot_id, location_id, stock_type)。

校正（reconcile）：
- 缓存 ← 行表（不写流水：缓存不是桶）
- （仅一期首次部署）inventory_lot_stock ← Inventory 计数器：部署窗口里旧进程只改 Inventory，不改新表
- 流水 ← 当前结存：差额写成 reconcile 行（期初 init-ledger 同理写 opening 行）
所有写入都在该商品的 Inventory 行锁下进行（与 StockService 的加锁顺序一致）。
"""
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import lazyload

from extensions.db import db
from . import guard
from .models import (
    GoodsLot, InventoryLotStock, LOCATION_STOCK_TYPES, STOCK_IN_TRANSIT, STOCK_PACKED, STOCK_PENDING,
    STOCK_PENDING_DAMAGED, STOCK_PICKED, StockLedger,
)
from .service import CACHE_COLUMNS, bucket_balance, current_api_key_id, current_operator_id, current_source, trace_id

LOT_STOCK_TYPES = (STOCK_PENDING, STOCK_PENDING_DAMAGED, STOCK_PICKED, STOCK_PACKED, STOCK_IN_TRANSIT)
CACHE_FIELDS = ('onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock', 'sorted_damage_stock',
                'picked_stock', 'packed_stock', 'delivered_stock', 'total_stock')


def _scope(query, warehouse_column, goods_column, warehouse_id=None, goods_ids=None):
    if warehouse_id is not None:
        query = query.filter(warehouse_column == warehouse_id)
    if goods_ids is not None:
        query = query.filter(goods_column.in_(list(goods_ids)))
    return query


def bucket_balances(warehouse_id=None, goods_ids=None) -> dict:
    """当前结存：{(warehouse_id, goods_id, lot_id, location_id, stock_type): 数量}（只含非零桶）"""
    from warehouse.goods.models import GoodsLocation
    from warehouse.location.models import Location
    balances = {}
    rows = _scope(
        db.session.query(Location.warehouse_id, GoodsLocation.goods_id, GoodsLocation.lot_id, GoodsLocation.location_id,
                         Location.location_type, GoodsLocation.quantity)
        .join(Location, Location.id == GoodsLocation.location_id),
        Location.warehouse_id, GoodsLocation.goods_id, warehouse_id, goods_ids).all()
    for warehouse, goods, lot, location, location_type, quantity in rows:
        if quantity:
            key = (warehouse, goods, lot, location, LOCATION_STOCK_TYPES[location_type])
            balances[key] = balances.get(key, 0) + int(quantity)
    for row in _scope(InventoryLotStock.query.options(lazyload('*')), InventoryLotStock.warehouse_id,
                      InventoryLotStock.goods_id, warehouse_id, goods_ids).all():
        for stock_type in LOT_STOCK_TYPES:
            quantity = bucket_balance(stock_type, row)
            if quantity:
                balances[(row.warehouse_id, row.goods_id, row.lot_id, None, stock_type)] = quantity
    return balances


def ledger_rows(warehouse_id=None, goods_ids=None):
    """流水的 (键, quantity, balance_after)，按桶内的记账顺序 (posted_at, id)"""
    query = _scope(
        db.session.query(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id,
                         StockLedger.stock_type, StockLedger.quantity, StockLedger.balance_after, StockLedger.id),
        StockLedger.warehouse_id, StockLedger.goods_id, warehouse_id, goods_ids)
    return query.order_by(StockLedger.posted_at, StockLedger.id).all()


def ledger_sums(warehouse_id=None, goods_ids=None) -> dict:
    """{键: 流水合计}"""
    query = _scope(
        db.session.query(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id,
                         StockLedger.stock_type, func.sum(StockLedger.quantity)),
        StockLedger.warehouse_id, StockLedger.goods_id, warehouse_id, goods_ids)
    rows = query.group_by(StockLedger.warehouse_id, StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id,
                          StockLedger.stock_type).all()
    return {tuple(row[:5]): int(row[5] or 0) for row in rows}


def expected_cache(balances: dict) -> dict:
    """由行表结存算出的 Inventory 缓存：{(goods_id, warehouse_id): {列: 值}}（不含 total_stock）"""
    expected = defaultdict(lambda: {field: 0 for field in CACHE_FIELDS if field != 'total_stock'})
    for (warehouse, goods, _lot, _location, stock_type), quantity in balances.items():
        values = expected[(goods, warehouse)]
        for column in CACHE_COLUMNS[stock_type]:
            values[column] += quantity
    return expected


def _total(values) -> int:
    return (values['onhand_stock'] + values['damage_stock'] + values['return_stock'] + values['sorted_stock']
            + values['picked_stock'] + values['packed_stock'])


def audit_stock(warehouse_id=None, goods_ids=None, check_ledger=True, check_chain=True) -> list:
    """对账，返回发现的问题（空列表 = 一致）。每项 {type, warehouse_id, goods_id, ...}"""
    from warehouse.inventory.models import Inventory
    problems = []
    balances = bucket_balances(warehouse_id, goods_ids)
    expected = expected_cache(balances)

    # ① 缓存 = 行表；④ 不为负；⑤ 破损 ≤ 待上架
    inventories = _scope(Inventory.query.options(lazyload('*')), Inventory.warehouse_id, Inventory.goods_id,
                         warehouse_id, goods_ids).all()
    seen = set()
    for inventory in inventories:
        key = (inventory.goods_id, inventory.warehouse_id)
        seen.add(key)
        values = dict(expected.get(key) or {field: 0 for field in CACHE_FIELDS if field != 'total_stock'})
        values['total_stock'] = _total(values)
        diff = {field: {'cache': int(getattr(inventory, field) or 0), 'rows': values[field]}
                for field in CACHE_FIELDS if int(getattr(inventory, field) or 0) != values[field]}
        actual_total = _formula_total(inventory)
        if int(inventory.total_stock or 0) != actual_total and 'total_stock' not in diff:
            diff['total_stock'] = {'cache': int(inventory.total_stock or 0), 'formula': actual_total}
        if diff:
            problems.append({'type': 'cache_mismatch', 'warehouse_id': inventory.warehouse_id,
                             'goods_id': inventory.goods_id, 'fields': diff})
        negative = [field for field in CACHE_FIELDS if int(getattr(inventory, field) or 0) < 0]
        if negative:
            problems.append({'type': 'negative', 'warehouse_id': inventory.warehouse_id,
                             'goods_id': inventory.goods_id, 'fields': negative})
        if int(inventory.sorted_damage_stock or 0) > int(inventory.sorted_stock or 0):
            problems.append({'type': 'damaged_exceeds_sorted', 'warehouse_id': inventory.warehouse_id,
                             'goods_id': inventory.goods_id})
    for goods, warehouse in expected:
        if (goods, warehouse) not in seen:
            problems.append({'type': 'missing_inventory', 'warehouse_id': warehouse, 'goods_id': goods})

    # ⑥ 批次属于该商品
    from warehouse.goods.models import GoodsLocation
    for table, model in (('goods_locations', GoodsLocation), ('inventory_lot_stock', InventoryLotStock)):
        wrong = (db.session.query(model.id).join(GoodsLot, GoodsLot.id == model.lot_id)
                 .filter(GoodsLot.goods_id != model.goods_id).limit(20).all())
        for (row_id,) in wrong:
            problems.append({'type': 'lot_goods_mismatch', 'table': table, 'id': row_id})

    if not check_ledger:
        return problems

    # ② 流水合计 = 结存；③ 最后一条 balance_after = 结存；流水链连续
    sums = defaultdict(int)
    last = {}
    broken = {}
    for row in ledger_rows(warehouse_id, goods_ids):
        key = tuple(row[:5])
        quantity, balance_after = int(row[5]), int(row[6])
        if check_chain and key not in broken and balance_after != sums[key] + quantity:
            broken[key] = {'ledger_id': row[7], 'expected_balance_after': sums[key] + quantity,
                           'balance_after': balance_after}
        sums[key] += quantity
        last[key] = balance_after
    for key in set(sums) | set(balances):
        balance = balances.get(key, 0)
        warehouse, goods, lot, location, stock_type = key
        base = {'warehouse_id': warehouse, 'goods_id': goods, 'lot_id': lot, 'location_id': location,
                'stock_type': stock_type, 'balance': balance}
        if sums.get(key, 0) != balance:
            problems.append({'type': 'ledger_sum_mismatch', **base, 'ledger_sum': sums.get(key, 0)})
        if key in last and last[key] != balance:
            problems.append({'type': 'ledger_last_balance_mismatch', **base, 'last_balance_after': last[key]})
        if key in broken:
            problems.append({'type': 'ledger_chain_broken', **base, **broken[key]})
    return problems


def _formula_total(inventory) -> int:
    from warehouse.inventory.services import InventoryService
    return InventoryService._calculate_total_stock(inventory)


# ---------------------------------------------------------------------------
# 校正（在该商品的 Inventory 行锁下）
# ---------------------------------------------------------------------------
def _lock_inventory(goods_id, warehouse_id):
    from warehouse.common.locks import lock_inventory_pairs
    return lock_inventory_pairs([(goods_id, warehouse_id)]).get((goods_id, warehouse_id))


def recompute_cache(goods_id, warehouse_id) -> dict:
    """Inventory 缓存 ← 行表（原 update_and_calculate_stock 的扩展：库位库存 + 不在库位上的库存）。
    返回改动 {列: (旧, 新)}；没有库存行返回 {}"""
    inventory = _lock_inventory(goods_id, warehouse_id)
    if inventory is None:
        return {}
    balances = bucket_balances(warehouse_id, [goods_id])
    values = expected_cache(balances).get((goods_id, warehouse_id)) or {
        field: 0 for field in CACHE_FIELDS if field != 'total_stock'}
    values = dict(values)
    values['total_stock'] = _total(values)
    changes = {}
    for field, value in values.items():
        old = int(getattr(inventory, field) or 0)
        if old != value:
            changes[field] = (old, value)
            setattr(inventory, field, value)
    if changes:
        guard.authorize(inventory)
    return changes


def sync_lot_stock_from_counters(goods_id, warehouse_id) -> dict:
    """（仅一期首次部署）无批次的 inventory_lot_stock ← Inventory 计数器（sorted / sorted_damage / picked / packed /
    delivered 减去带批次的行）。部署窗口里旧进程只改 Inventory，不改新表。返回改动 {列: (旧, 新)}"""
    from .service import lock_or_create_lot_stock
    inventory = _lock_inventory(goods_id, warehouse_id)
    if inventory is None:
        return {}
    lot_rows = (InventoryLotStock.query.options(lazyload('*'))
                .filter(InventoryLotStock.warehouse_id == warehouse_id, InventoryLotStock.goods_id == goods_id,
                        InventoryLotStock.lot_id.isnot(None)).all())
    target = {}
    for column in InventoryLotStock.STOCK_COLUMNS:
        value = int(getattr(inventory, column) or 0) - sum(int(getattr(row, column) or 0) for row in lot_rows)
        target[column] = max(0, value)
    target['sorted_damage_stock'] = min(target['sorted_damage_stock'], target['sorted_stock'])
    row = lock_or_create_lot_stock(warehouse_id, goods_id, None)
    changes = {}
    for column, value in target.items():
        old = int(getattr(row, column) or 0)
        if old != value:
            changes[column] = (old, value)
            setattr(row, column, value)
    if row.is_empty():
        db.session.delete(row)
    guard.authorize(row)
    return changes


def post_ledger_to_balances(goods_id, warehouse_id, movement_type='reconcile', remark=None) -> list:
    """流水 ← 当前结存：每个桶「当前结存 − 流水合计」的差额写一行（期初为 opening，其余 reconcile），不改行表。
    返回写入的流水行"""
    from warehouse.common import clock
    from warehouse.warehouse.models import Warehouse
    inventory = _lock_inventory(goods_id, warehouse_id)
    balances = bucket_balances(warehouse_id, [goods_id])
    sums = ledger_sums(warehouse_id, [goods_id])
    warehouse = db.session.get(Warehouse, warehouse_id)
    now = clock.now()
    total = int(inventory.total_stock or 0) if inventory is not None else 0
    lines = []
    for key in sorted(set(balances) | set(sums), key=lambda k: tuple('' if v is None else str(v) for v in k)):
        diff = balances.get(key, 0) - sums.get(key, 0)
        if not diff:
            continue
        _warehouse, _goods, lot, location, stock_type = key
        line = StockLedger(
            posted_at=now, occurred_at=now, company_id=warehouse.company_id, warehouse_id=warehouse_id,
            goods_id=goods_id, lot_id=lot, location_id=location, stock_type=stock_type, quantity=diff,
            balance_after=balances.get(key, 0), goods_total_after=total, counter_stock_type=None,
            movement_type=movement_type, operator_id=current_operator_id(), api_key_id=current_api_key_id(),
            source=current_source(), trace_id=trace_id(), remark=remark,
        )
        guard.authorize(line)
        lines.append(line)
    db.session.add_all(lines)
    db.session.flush()
    return lines


def stock_pairs(warehouse_id=None) -> list:
    """有库存行或流水的 (goods_id, warehouse_id)，按键升序"""
    from warehouse.goods.models import GoodsLocation
    from warehouse.inventory.models import Inventory
    from warehouse.location.models import Location
    pairs = set()
    query = db.session.query(Inventory.goods_id, Inventory.warehouse_id)
    if warehouse_id is not None:
        query = query.filter(Inventory.warehouse_id == warehouse_id)
    pairs.update(query.all())
    query = db.session.query(GoodsLocation.goods_id, Location.warehouse_id).join(
        Location, Location.id == GoodsLocation.location_id)
    if warehouse_id is not None:
        query = query.filter(Location.warehouse_id == warehouse_id)
    pairs.update(query.distinct().all())
    query = db.session.query(InventoryLotStock.goods_id, InventoryLotStock.warehouse_id)
    if warehouse_id is not None:
        query = query.filter(InventoryLotStock.warehouse_id == warehouse_id)
    pairs.update(query.all())
    query = db.session.query(StockLedger.goods_id, StockLedger.warehouse_id)
    if warehouse_id is not None:
        query = query.filter(StockLedger.warehouse_id == warehouse_id)
    pairs.update(query.distinct().all())
    return sorted(pairs)


def reconcile(warehouse_id=None, apply=False, sync_legacy_counters=False, movement_type='reconcile',
              remark='system reconcile', commit_every=200) -> dict:
    """校正：sync_legacy_counters（仅一期首次部署）→ 缓存 ← 行表 → 流水 ← 结存，逐个商品在其 Inventory 行锁下做。
    apply=False 只算出要改的（演练）：每个商品算完就回滚（不长时间持锁）。返回 {lot_stock, cache, ledger} 报告"""
    report = {'lot_stock': [], 'cache': [], 'ledger': []}
    pairs = stock_pairs(warehouse_id)
    if not apply:
        db.session.rollback()
    done = 0
    for goods_id, pair_warehouse in pairs:
        if sync_legacy_counters:
            changes = sync_lot_stock_from_counters(goods_id, pair_warehouse)
            if changes:
                report['lot_stock'].append({'goods_id': goods_id, 'warehouse_id': pair_warehouse,
                                            'changes': changes})
            db.session.flush()
        changes = recompute_cache(goods_id, pair_warehouse)
        if changes:
            report['cache'].append({'goods_id': goods_id, 'warehouse_id': pair_warehouse, 'changes': changes})
        db.session.flush()
        for line in post_ledger_to_balances(goods_id, pair_warehouse, movement_type, remark):
            report['ledger'].append({'goods_id': goods_id, 'warehouse_id': pair_warehouse,
                                     'location_id': line.location_id, 'lot_id': line.lot_id,
                                     'stock_type': line.stock_type, 'quantity': line.quantity,
                                     'balance_after': line.balance_after})
        done += 1
        if not apply:
            db.session.rollback()
        elif done % commit_every == 0:
            db.session.commit()
    if apply:
        db.session.commit()
    return report
