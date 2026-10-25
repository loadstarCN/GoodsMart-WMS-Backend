"""历史库存：日终结存 stock_daily_balances（doc/库存数值系统说明.md 第十节）。

- 日终的口径：仓库当地日期 D 的 24:00。算法 = D 结束（当地次日 00:00）之前记账（posted_at）的流水合计；
  已过去的日子查出来不会再变（补录的发货时刻早于记账时刻时，算在记账那一天）
- 生成：调度器每小时检查一次，对「当地已经过了 0 点、但前一天还没生成」的仓库补生成（缺了好几天一起补）；
  有前一天的结存（由流水算出 / 回放）时增量算：前一天 + 当天的流水，否则从头合计
- 流水覆盖之前的日子：期初（init-ledger）时把旧快照 inventory_snapshot 转成 legacy_snapshot 行（只有库位库存）；
  历史回放（flask stock replay --apply）之后由回放的流水重建（derived）
- 查询：D ≥ 当地今天 → 实时库存；已生成 → 查表；流水覆盖内但还没生成 → 现算；更早 → 旧快照转换的行
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func

from extensions.db import as_utc, db
from warehouse.common import clock
from .models import (
    IN_WAREHOUSE_STOCK_TYPES, LOCATION_STOCK_TYPES, STOCK_TYPES, StockDailyBalance, StockLedger,
)

# 回放起点（线上第一条上架记录的日期）：历史库存可查的最早日期
HISTORY_START = date(2025, 9, 21)
LEDGER_SOURCES = ('ledger', 'derived')


def ledger_start(warehouse_id):
    """该仓库第一条流水的记账时刻（流水覆盖的起点）；没有流水为 None"""
    value = db.session.query(func.min(StockLedger.posted_at)).filter(StockLedger.warehouse_id == warehouse_id).scalar()
    return as_utc(value) if value is not None else None


def ledger_start_date(warehouse_id):
    start = ledger_start(warehouse_id)
    return clock.local_date(start, warehouse_id) if start is not None else None


def _key(row):
    return (row.goods_id, row.lot_id, row.location_id, row.stock_type)


def balances_before(warehouse_id, moment, goods_ids=None) -> dict:
    """moment 之前记账的流水合计：{(goods_id, lot_id, location_id, stock_type): 数量}（只含非零）"""
    query = (db.session.query(StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id, StockLedger.stock_type,
                              func.sum(StockLedger.quantity))
             .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.posted_at < moment))
    if goods_ids is not None:
        query = query.filter(StockLedger.goods_id.in_(list(goods_ids)))
    rows = query.group_by(StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id,
                          StockLedger.stock_type).all()
    return {tuple(row[:4]): int(row[4]) for row in rows if row[4]}


def _flows_between(warehouse_id, start, end) -> dict:
    rows = (db.session.query(StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id, StockLedger.stock_type,
                             func.sum(StockLedger.quantity))
            .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.posted_at >= start,
                    StockLedger.posted_at < end)
            .group_by(StockLedger.goods_id, StockLedger.lot_id, StockLedger.location_id, StockLedger.stock_type).all())
    return {tuple(row[:4]): int(row[4]) for row in rows if row[4]}


def _stored(warehouse_id, day, goods_ids=None, sources=None):
    query = StockDailyBalance.query.filter(StockDailyBalance.warehouse_id == warehouse_id,
                                           StockDailyBalance.balance_date == day)
    if goods_ids is not None:
        query = query.filter(StockDailyBalance.goods_id.in_(list(goods_ids)))
    if sources is not None:
        query = query.filter(StockDailyBalance.source.in_(sources))
    return query.all()


def last_generated_date(warehouse_id):
    """已生成（由流水 / 回放算出）的最近一天"""
    return (db.session.query(func.max(StockDailyBalance.balance_date))
            .filter(StockDailyBalance.warehouse_id == warehouse_id,
                    StockDailyBalance.source.in_(LEDGER_SOURCES)).scalar())


def compute_day(warehouse_id, day) -> dict:
    """算出 day 日终的结存：有前一天（由流水算出）的结存就增量算，否则从头合计"""
    end = clock.local_midnight(day + timedelta(days=1), warehouse_id)
    previous = _stored(warehouse_id, day - timedelta(days=1), sources=LEDGER_SOURCES)
    if previous:
        balances = defaultdict(int)
        for row in previous:
            balances[_key(row)] += int(row.quantity)
        start = clock.local_midnight(day, warehouse_id)
        for key, quantity in _flows_between(warehouse_id, start, end).items():
            balances[key] += quantity
        return {key: quantity for key, quantity in balances.items() if quantity}
    return balances_before(warehouse_id, end)


def generate_day(warehouse_id, day, source='ledger') -> int:
    """生成（或重建）day 的日终结存，返回写入的行数。调用方提交"""
    balances = compute_day(warehouse_id, day)
    StockDailyBalance.query.filter(StockDailyBalance.warehouse_id == warehouse_id,
                                   StockDailyBalance.balance_date == day).delete(synchronize_session=False)
    rows = [StockDailyBalance(balance_date=day, warehouse_id=warehouse_id, goods_id=goods_id, lot_id=lot_id,
                              location_id=location_id, stock_type=stock_type, quantity=quantity, source=source)
            for (goods_id, lot_id, location_id, stock_type), quantity in balances.items() if quantity > 0]
    db.session.add_all(rows)
    db.session.flush()
    return len(rows)


def generate_missing(warehouse_id=None, until=None) -> dict:
    """补生成各仓库「当地已经过完、还没生成」的日终结存（从上次生成的次日，或流水覆盖的第一天，到当地昨天）。
    返回 {warehouse_id: [生成的日期]}。每天提交一次"""
    from warehouse.warehouse.models import Warehouse
    query = Warehouse.query.filter(Warehouse.is_active == True)
    if warehouse_id is not None:
        query = query.filter(Warehouse.id == warehouse_id)
    done = {}
    for warehouse in query.order_by(Warehouse.id).all():
        first = ledger_start_date(warehouse.id)
        if first is None:
            continue
        yesterday = (until or clock.local_today(warehouse)) - timedelta(days=1)
        last = last_generated_date(warehouse.id)
        day = max(first, last + timedelta(days=1)) if last else first
        while day <= yesterday:
            generate_day(warehouse.id, day)
            db.session.commit()
            done.setdefault(warehouse.id, []).append(day)
            day += timedelta(days=1)
    return done


def rebuild(warehouse_id, date_from, date_to, source='ledger') -> int:
    """重建 [date_from, date_to] 的日终结存（流水覆盖之前的日子跳过，保留旧快照转换的行）。返回天数"""
    first = ledger_start_date(warehouse_id)
    if first is None:
        return 0
    day = max(date_from, first)
    count = 0
    while day <= date_to:
        generate_day(warehouse_id, day, source=source)
        db.session.commit()
        count += 1
        day += timedelta(days=1)
    return count


def import_legacy_snapshots(warehouse_id=None, before=None) -> dict:
    """旧快照 inventory_snapshot（只拍了库位库存，第二天凌晨拍）→ 前一天的日终结存（source = legacy_snapshot）。
    同一天有几批快照取当地日期最早的一批；只转换 before（默认流水覆盖的第一天）之前、还没有结存的日子。
    返回 {warehouse_id: 天数}"""
    from warehouse.inventory_snapshot.models import InventorySnapshot
    from warehouse.location.models import Location
    from warehouse.warehouse.models import Warehouse
    query = Warehouse.query
    if warehouse_id is not None:
        query = query.filter(Warehouse.id == warehouse_id)
    result = {}
    for warehouse in query.order_by(Warehouse.id).all():
        limit = before or ledger_start_date(warehouse.id) or clock.local_today(warehouse)
        times = [as_utc(t) for (t,) in db.session.query(InventorySnapshot.snapshot_time)
                 .filter(InventorySnapshot.warehouse_id == warehouse.id).distinct().all()]
        batches = {}
        for moment in sorted(times):
            day = clock.local_date(moment, warehouse) - timedelta(days=1)
            if day < limit and day not in batches:
                batches[day] = moment
        existing = {d for (d,) in db.session.query(StockDailyBalance.balance_date)
                    .filter(StockDailyBalance.warehouse_id == warehouse.id).distinct().all()}
        count = 0
        for day, moment in sorted(batches.items()):
            if day in existing:
                continue
            rows = (db.session.query(InventorySnapshot.goods_id, InventorySnapshot.location_id, Location.location_type,
                                     func.sum(InventorySnapshot.quantity))
                    .join(Location, Location.id == InventorySnapshot.location_id)
                    .filter(InventorySnapshot.warehouse_id == warehouse.id, InventorySnapshot.snapshot_time == moment)
                    .group_by(InventorySnapshot.goods_id, InventorySnapshot.location_id, Location.location_type).all())
            db.session.add_all([
                StockDailyBalance(balance_date=day, warehouse_id=warehouse.id, goods_id=goods_id, location_id=location_id,
                                  stock_type=LOCATION_STOCK_TYPES.get(location_type, 'onhand'), quantity=int(quantity),
                                  source='legacy_snapshot')
                for goods_id, location_id, location_type, quantity in rows if quantity and quantity > 0])
            db.session.commit()
            count += 1
        result[warehouse.id] = count
    return result


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def realtime_balances(warehouse_id, goods_ids=None) -> dict:
    """当前结存（行表）：{(goods_id, lot_id, location_id, stock_type): 数量}"""
    from .audit import bucket_balances
    return {(goods, lot, location, stock_type): quantity
            for (_w, goods, lot, location, stock_type), quantity in bucket_balances(warehouse_id, goods_ids).items()}


def _sum_rows(rows) -> dict:
    balances = defaultdict(int)
    for row in rows:
        balances[_key(row)] += int(row.quantity)
    return dict(balances)


def balances_on(warehouse_id, day, goods_ids=None):
    """day 日终的结存：(balances, source, coverage)。
    source：realtime（今天及以后）/ ledger、derived（已生成）/ computed（流水覆盖内、还没生成，现算）/
    legacy_snapshot（流水覆盖之前，旧快照转换）/ none"""
    if day >= clock.local_today(warehouse_id):
        return realtime_balances(warehouse_id, goods_ids), 'realtime', 'full'
    first = ledger_start_date(warehouse_id)
    if first is not None and day >= first:
        last = last_generated_date(warehouse_id)
        if last is not None and day <= last:
            rows = _stored(warehouse_id, day, goods_ids, sources=LEDGER_SOURCES)
            sources = {row.source for row in rows}
            return _sum_rows(rows), (sources.pop() if len(sources) == 1 else 'ledger'), 'full'
        end = clock.local_midnight(day + timedelta(days=1), warehouse_id)
        return balances_before(warehouse_id, end, goods_ids), 'computed', 'full'
    rows = _stored(warehouse_id, day, goods_ids)
    if not rows:
        return {}, 'none', 'none'
    sources = {row.source for row in rows}
    source = sources.pop() if len(sources) == 1 else 'mixed'
    return _sum_rows(rows), source, 'on_shelf_only' if source == 'legacy_snapshot' else 'full'


def balances_at_time(warehouse_id, moment, goods_ids=None):
    """任意时刻的结存（该时刻之前记账的流水合计）；流水覆盖之前的时刻返回 coverage none"""
    start = ledger_start(warehouse_id)
    if start is None or as_utc(moment) < start:
        return {}, 'none'
    return balances_before(warehouse_id, moment, goods_ids), 'full'


def empty_row():
    return {stock_type: 0 for stock_type in STOCK_TYPES}


def finish_row(values: dict) -> dict:
    """补上 total（仓内实物，不含在途）"""
    values['total'] = sum(values[t] for t in IN_WAREHOUSE_STOCK_TYPES)
    return values


def group_balances(balances: dict, group_by='goods') -> dict:
    """按 goods / goods_location / location / goods_lot 合并：{分组键: {各类别: 数量, total}}"""
    grouped = defaultdict(empty_row)
    for (goods_id, lot_id, location_id, stock_type), quantity in balances.items():
        if group_by == 'goods_location':
            key = (goods_id, location_id)
        elif group_by == 'location':
            if location_id is None:
                continue
            key = (location_id,)
        elif group_by == 'goods_lot':
            key = (goods_id, lot_id)
        else:
            key = (goods_id,)
        grouped[key][stock_type] += quantity
    return {key: finish_row(values) for key, values in grouped.items()}


def daily_totals(warehouse_id, date_from, date_to, goods_ids=None, stock_types=None) -> dict:
    """[date_from, date_to] 每天日终的合计（goods_ids 为空 = 全仓库）：{日期: {goods_id: 数量}}。
    stock_types 默认仓内实物（不含在途）。已生成的日子查表，其余现算 / 实时"""
    stock_types = tuple(stock_types or IN_WAREHOUSE_STOCK_TYPES)
    result = {}
    last = last_generated_date(warehouse_id)
    if last is not None:
        query = (db.session.query(StockDailyBalance.balance_date, StockDailyBalance.goods_id,
                                  func.sum(StockDailyBalance.quantity))
                 .filter(StockDailyBalance.warehouse_id == warehouse_id,
                         StockDailyBalance.balance_date >= date_from,
                         StockDailyBalance.balance_date <= min(date_to, last),
                         StockDailyBalance.stock_type.in_(stock_types)))
        if goods_ids is not None:
            query = query.filter(StockDailyBalance.goods_id.in_(list(goods_ids)))
        for day, goods_id, quantity in query.group_by(StockDailyBalance.balance_date, StockDailyBalance.goods_id).all():
            result.setdefault(day, {})[goods_id] = int(quantity or 0)
    day = date_from
    while day <= date_to:
        if last is None or day > last:
            balances, _source, coverage = balances_on(warehouse_id, day, goods_ids)
            if coverage != 'none':
                totals = defaultdict(int)
                for (goods_id, _lot, _location, stock_type), quantity in balances.items():
                    if stock_type in stock_types:
                        totals[goods_id] += quantity
                result[day] = dict(totals)
        else:
            result.setdefault(day, {})
        day += timedelta(days=1)
    return result
