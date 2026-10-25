"""历史流水回放（flask stock replay；doc/库存数值系统说明.md 第十节「期初与历史」）。

从现有单据把流水覆盖之前（期初 opening 的时刻 T0 之前）的库存变动回放出来，逐批和旧快照（inventory_snapshot，
只拍了库位库存）核对：
- 用到的单据与时刻：入库单完成（completed_at，旧数据没有按创建时刻；实收 actual_quantity）→ sort_in；上架记录
  （putaway_time）；下架记录（removal_time：拣货下架 reason = picking 记 pick，其余回待上架）；移库记录；调整单完成
  （completed_at，正向 adjust_gain / 负向 adjust_loss）；DN 打包（packed_at：已打包、少打包退回）、发货（delivered_at）、
  签收（completed_at）、拣货后取消（closed_at：退回待上架）
- 待上架一律按良品回放（升级 e7b4c2a9d1f3 之前的系统不分破损；差额在 T0 校正）
- 宽松记账：来源不够也照记（结存可以暂时为负），报告里给出次数
- 已经在真实流水里的单据（同一 doc_type / doc_id / 变动类型）不再回放，避免重复
默认演练：只输出差异报告（逐批快照的不一致行数、最大差额、样例；回放到 T0 的结存与期初的差额），不写库。
--apply（要求流水里有 opening 行、还没有回放过）：
  1. 写回放出的流水（source = system、trace_id = replay-…、posted_at = occurred_at = 历史时刻）
  2. 在 T0 把「期初 − 回放到 T0 的结存」写成 reconcile 行，删掉 opening 行 —— T0 的结存不变，之后的真实流水不受影响
  3. 用回放的流水重建 T0 之前各天的日终结存（source = derived，取代旧快照转换的 legacy_snapshot 行）
"""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, inspect

from extensions.db import as_utc, db
from warehouse.common import clock
from .models import IN_WAREHOUSE_STOCK_TYPES, LOCATION_STOCK_TYPES

# 同一时刻的事件按这个顺序回放
_ORDER = {'sort_in': 0, 'putaway': 1, 'removal': 2, 'transfer': 3, 'adjust_gain': 4, 'adjust_loss': 4, 'pick': 5,
          'pack': 6, 'pack_short_return': 6, 'dn_cancel_return': 7, 'ship': 8, 'sign': 9}
SAMPLE_LIMIT = 20


class _Event:
    __slots__ = ('at', 'movement', 'goods_id', 'quantity', 'src', 'dst', 'doc_type', 'doc_id', 'doc_no',
                 'doc_line_id', 'operator_id', 'reason')

    def __init__(self, at, movement, goods_id, quantity, src, dst, doc_type, doc_id, doc_no=None, doc_line_id=None,
                 operator_id=None, reason=None):
        self.at = as_utc(at)
        self.movement = movement
        self.goods_id = goods_id
        self.quantity = int(quantity)
        self.src = src            # (stock_type, location_id) 或 None（仓外）
        self.dst = dst
        self.doc_type = doc_type
        self.doc_id = doc_id
        self.doc_no = doc_no
        self.doc_line_id = doc_line_id
        self.operator_id = operator_id
        self.reason = reason


def _ledger_ready() -> bool:
    return 'stock_ledger' in inspect(db.engine).get_table_names()


def opening_time(warehouse_id):
    """期初的时刻 T0：opening 行的记账时刻；没有（还没部署 / 还没 init-ledger）为 None"""
    from .models import StockLedger
    if not _ledger_ready():
        return None
    value = (db.session.query(func.min(StockLedger.posted_at))
             .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type == 'opening').scalar())
    return as_utc(value) if value is not None else None


def _booked_docs(warehouse_id) -> set:
    """流水里已有的 (doc_type, doc_id, movement_type)（真实记账的、已回放写入的都算，回放时跳过）"""
    from .models import StockLedger
    if not _ledger_ready():
        return set()
    rows = (db.session.query(StockLedger.doc_type, StockLedger.doc_id, StockLedger.movement_type)
            .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.doc_id.isnot(None)).distinct().all())
    return {tuple(row) for row in rows}


def collect_events(warehouse_id, until) -> list:
    """回放的事件（until 之前），按时刻与固定顺序排好"""
    from warehouse.adjustment.models import Adjustment, AdjustmentDetail
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.dn.models import DN, DNDetail
    from warehouse.location.models import Location
    from warehouse.packing.models import PackingTask
    from warehouse.putaway.models import PutawayRecord
    from warehouse.removal.models import RemovalRecord
    from warehouse.transfer.models import TransferRecord

    locations = {loc.id: loc for loc in Location.query.filter(Location.warehouse_id == warehouse_id).all()}

    def at_location(location_id):
        location = locations[location_id]
        return (LOCATION_STOCK_TYPES.get(location.location_type, 'onhand'), location_id)

    events = []
    asn_at = func.coalesce(ASN.completed_at, ASN.created_at)
    for detail, asn, moment in (db.session.query(ASNDetail, ASN, asn_at).join(ASN, ASN.id == ASNDetail.asn_id)
                                .filter(ASN.warehouse_id == warehouse_id, ASN.status == 'completed',
                                        ASN.is_active == True, ASNDetail.actual_quantity > 0).all()):
        events.append(_Event(moment, 'sort_in', detail.goods_id, detail.actual_quantity, None, ('pending', None),
                             'asn', asn.id, asn.order_number, detail.id))
    for record in (PutawayRecord.query.filter(PutawayRecord.location_id.in_(list(locations)))
                   if locations else []):
        events.append(_Event(record.putaway_time, 'putaway', record.goods_id, record.quantity, ('pending', None),
                             at_location(record.location_id), 'putaway', record.id, operator_id=record.operator_id))
    for record in (RemovalRecord.query.filter(RemovalRecord.location_id.in_(list(locations)))
                   if locations else []):
        if record.reason == 'picking':
            events.append(_Event(record.removal_time, 'pick', record.goods_id, record.quantity,
                                 at_location(record.location_id), ('picked', None), 'removal', record.id,
                                 operator_id=record.operator_id, reason='picking'))
        else:
            events.append(_Event(record.removal_time, 'removal', record.goods_id, record.quantity,
                                 at_location(record.location_id), ('pending', None), 'removal', record.id,
                                 operator_id=record.operator_id, reason=(record.reason or '')[:30] or None))
    for record in (TransferRecord.query.filter(TransferRecord.from_location_id.in_(list(locations)))
                   if locations else []):
        if record.to_location_id not in locations or record.from_location_id == record.to_location_id:
            continue
        events.append(_Event(record.transfer_time, 'transfer', record.goods_id, record.quantity,
                             at_location(record.from_location_id), at_location(record.to_location_id), 'transfer',
                             record.id, operator_id=record.operator_id))
    for detail, adjustment in (db.session.query(AdjustmentDetail, Adjustment)
                               .join(Adjustment, Adjustment.id == AdjustmentDetail.adjustment_id)
                               .filter(Adjustment.warehouse_id == warehouse_id, Adjustment.status == 'completed',
                                       Adjustment.is_active == True, Adjustment.completed_at.isnot(None)).all()):
        delta = detail.adjustment_quantity or 0
        if not delta or detail.location_id not in locations:
            continue
        leg = at_location(detail.location_id)
        events.append(_Event(adjustment.completed_at, 'adjust_gain' if delta > 0 else 'adjust_loss', detail.goods_id,
                             abs(delta), None if delta > 0 else leg, leg if delta > 0 else None, 'adjustment',
                             adjustment.id, doc_line_id=detail.id, operator_id=adjustment.operator_id,
                             reason=(adjustment.adjustment_reason or '')[:30] or None))
    packing_tasks = dict(db.session.query(PackingTask.dn_id, func.max(PackingTask.id))
                         .join(DN, DN.id == PackingTask.dn_id)
                         .filter(DN.warehouse_id == warehouse_id, PackingTask.status == 'completed',
                                 PackingTask.is_active == True).group_by(PackingTask.dn_id).all())
    for detail, dn in (db.session.query(DNDetail, DN).join(DN, DN.id == DNDetail.dn_id)
                       .filter(DN.warehouse_id == warehouse_id, DN.is_active == True).all()):
        packing_id = packing_tasks.get(dn.id)
        picked = detail.picked_quantity or 0
        packed = detail.packed_quantity or 0
        delivered = detail.delivered_quantity or 0
        if dn.packed_at is not None and dn.status in ('packed', 'delivered', 'completed', 'closed'):
            events.append(_Event(dn.packed_at, 'pack', detail.goods_id, packed, ('picked', None), ('packed', None),
                                 'packing_task', packing_id, dn.order_number, detail.id))
            events.append(_Event(dn.packed_at, 'pack_short_return', detail.goods_id, picked - packed,
                                 ('picked', None), ('pending', None), 'packing_task', packing_id, dn.order_number,
                                 detail.id))
        if dn.status == 'closed' and dn.picked_at is not None and dn.closed_at is not None:
            if dn.packed_at is not None:
                events.append(_Event(dn.closed_at, 'dn_cancel_return', detail.goods_id, packed, ('packed', None),
                                     ('pending', None), 'dn', dn.id, dn.order_number, detail.id))
            else:
                events.append(_Event(dn.closed_at, 'dn_cancel_return', detail.goods_id, picked, ('picked', None),
                                     ('pending', None), 'dn', dn.id, dn.order_number, detail.id))
        if dn.delivered_at is not None and dn.status in ('delivered', 'completed'):
            events.append(_Event(dn.delivered_at, 'ship', detail.goods_id, delivered, ('packed', None),
                                 ('in_transit', None), 'dn', dn.id, dn.order_number, detail.id))
        if dn.completed_at is not None and dn.status == 'completed':
            events.append(_Event(dn.completed_at, 'sign', detail.goods_id, delivered, ('in_transit', None), None,
                                 'dn', dn.id, dn.order_number, detail.id))

    booked = _booked_docs(warehouse_id)
    result = []
    for event in events:
        if event.quantity <= 0 or event.at is None or (until is not None and event.at >= until):
            continue
        if event.doc_id is not None and (event.doc_type, event.doc_id, event.movement) in booked:
            continue
        result.append(event)
    result.sort(key=lambda e: (e.at, _ORDER.get(e.movement, 99), e.doc_type or '', e.doc_id or 0,
                               e.doc_line_id or 0))
    return result


def _snapshots(warehouse_id, until):
    """旧快照的批：[(时刻, {(goods_id, location_id): 数量})]（until 之前）"""
    from warehouse.inventory_snapshot.models import InventorySnapshot
    rows = (db.session.query(InventorySnapshot.snapshot_time, InventorySnapshot.goods_id,
                             InventorySnapshot.location_id, func.sum(InventorySnapshot.quantity))
            .filter(InventorySnapshot.warehouse_id == warehouse_id)
            .group_by(InventorySnapshot.snapshot_time, InventorySnapshot.goods_id, InventorySnapshot.location_id)
            .all())
    batches = defaultdict(dict)
    for moment, goods_id, location_id, quantity in rows:
        moment = as_utc(moment)
        if until is None or moment < until:
            batches[moment][(goods_id, location_id)] = int(quantity or 0)
    return sorted(batches.items())


def replay(warehouse_id, until=None, apply=False, sample_limit=SAMPLE_LIMIT) -> dict:
    """回放（演练或写入），返回报告"""
    from warehouse.warehouse.models import Warehouse
    warehouse = db.session.get(Warehouse, warehouse_id)
    t0 = opening_time(warehouse_id)
    until = until or t0 or clock.now()
    events = collect_events(warehouse_id, until)
    snapshots = _snapshots(warehouse_id, until)

    balances = defaultdict(int)            # (goods_id, location_id, stock_type) → 结存
    goods_total = defaultdict(int)
    negatives = []
    lines = []
    snapshot_report = []
    snapshot_index = 0

    def compare(moment, expected):
        actual = {(g, l): q for (g, l, t), q in balances.items() if l is not None and q}
        keys = set(actual) | {k for k, q in expected.items() if q}
        mismatched = [(k, expected.get(k, 0), actual.get(k, 0)) for k in sorted(keys)
                      if expected.get(k, 0) != actual.get(k, 0)]
        snapshot_report.append({
            'snapshot_time': clock.utc_iso(moment), 'balance_date': (clock.local_date(moment, warehouse)
                                                                     - timedelta(days=1)).isoformat(),
            'rows': len(keys), 'mismatched_rows': len(mismatched),
            'abs_diff': sum(abs(e - a) for _k, e, a in mismatched),
            'samples': [{'goods_id': k[0], 'location_id': k[1], 'snapshot': e, 'replayed': a}
                        for k, e, a in mismatched[:sample_limit]],
        })

    for event in events:
        while snapshot_index < len(snapshots) and snapshots[snapshot_index][0] <= event.at:
            compare(*snapshots[snapshot_index])
            snapshot_index += 1
        legs = []
        for leg, sign in ((event.src, -1), (event.dst, 1)):
            if leg is None:
                continue
            stock_type, location_id = leg
            key = (event.goods_id, location_id, stock_type)
            balances[key] += sign * event.quantity
            if stock_type in IN_WAREHOUSE_STOCK_TYPES:
                goods_total[event.goods_id] += sign * event.quantity
            if balances[key] < 0 and sign < 0:
                negatives.append({'at': clock.utc_iso(event.at), 'movement': event.movement,
                                  'goods_id': event.goods_id, 'location_id': location_id, 'stock_type': stock_type,
                                  'balance': balances[key], 'doc_type': event.doc_type, 'doc_id': event.doc_id})
            legs.append((leg, sign))
        if apply:
            for (stock_type, location_id), sign in legs:
                other = event.dst if sign < 0 else event.src
                lines.append((event, stock_type, location_id, sign * event.quantity,
                              balances[(event.goods_id, location_id, stock_type)], goods_total[event.goods_id],
                              other[0] if other else None))
    while snapshot_index < len(snapshots):
        compare(*snapshots[snapshot_index])
        snapshot_index += 1

    # 回放到 T0 的结存 vs 期初（或当前实际）
    from .audit import bucket_balances
    if t0 is not None:
        from .models import StockLedger
        target = defaultdict(int)
        for line in StockLedger.query.filter(StockLedger.warehouse_id == warehouse_id,
                                             StockLedger.movement_type == 'opening').all():
            target[(line.goods_id, line.location_id, line.stock_type)] += line.quantity
    elif _ledger_ready():
        target = {(g, loc, t): q for (_w, g, _lot, loc, t), q in bucket_balances(warehouse_id).items()}
    else:
        target = _legacy_current(warehouse_id)
    diff_keys = set(target) | {k for k, q in balances.items() if q}
    final_diff = [(k, target.get(k, 0), balances.get(k, 0)) for k in sorted(diff_keys, key=str)
                  if target.get(k, 0) != balances.get(k, 0)]
    report = {
        'warehouse_id': warehouse_id, 'until': clock.utc_iso(until), 'opening_time': clock.utc_iso(t0),
        'events': len(events), 'first_event_at': clock.utc_iso(events[0].at) if events else None,
        'by_movement': _count_by(events),
        'negative_balances': len(negatives), 'negative_samples': negatives[:sample_limit],
        'snapshots_compared': len(snapshot_report),
        'snapshots_mismatched': sum(1 for s in snapshot_report if s['mismatched_rows']),
        'snapshot_max_abs_diff': max((s['abs_diff'] for s in snapshot_report), default=0),
        'snapshots': snapshot_report,
        'final_diff_buckets': len(final_diff),
        'final_abs_diff': sum(abs(t - r) for _k, t, r in final_diff),
        'final_diff_samples': [{'goods_id': k[0], 'location_id': k[1], 'stock_type': k[2], 'opening': t,
                                'replayed': r} for k, t, r in final_diff[:sample_limit]],
        'applied': False,
    }
    if apply:
        _apply(warehouse, t0, lines, final_diff)
        report['applied'] = True
        report['derived_lines'] = len(lines)
    return report


def _legacy_current(warehouse_id):
    """还没部署新表时（演练）：当前结存 = 库位库存 + inventory 计数器（无批次）"""
    from warehouse.goods.models import GoodsLocation
    from warehouse.inventory.models import Inventory
    from warehouse.location.models import Location
    target = defaultdict(int)
    for goods_id, location_id, location_type, quantity in (
            db.session.query(GoodsLocation.goods_id, GoodsLocation.location_id, Location.location_type,
                             GoodsLocation.quantity)
            .join(Location, Location.id == GoodsLocation.location_id)
            .filter(Location.warehouse_id == warehouse_id).all()):
        if quantity:
            target[(goods_id, location_id, LOCATION_STOCK_TYPES.get(location_type, 'onhand'))] += int(quantity)
    for inv in Inventory.query.filter(Inventory.warehouse_id == warehouse_id).all():
        damaged = min(inv.sorted_damage_stock or 0, inv.sorted_stock or 0)
        for stock_type, quantity in (('pending', (inv.sorted_stock or 0) - damaged), ('pending_damaged', damaged),
                                     ('picked', inv.picked_stock), ('packed', inv.packed_stock),
                                     ('in_transit', inv.delivered_stock)):
            if quantity:
                target[(inv.goods_id, None, stock_type)] += int(quantity)
    return target


def _count_by(events):
    counts = defaultdict(int)
    for event in events:
        counts[event.movement] += 1
    return dict(counts)


def _apply(warehouse, t0, lines, final_diff):
    """写回放的流水、在 T0 写 reconcile 并删 opening、重建 T0 之前的日终结存"""
    from . import guard
    from .history import rebuild
    from .models import StockDailyBalance, StockLedger
    if t0 is None:
        raise RuntimeError("no opening lines: run flask stock init-ledger first")
    if StockLedger.query.filter(StockLedger.warehouse_id == warehouse.id,
                                StockLedger.trace_id.like('replay-%')).first() is not None:
        raise RuntimeError("this warehouse has already been replayed")
    openings = StockLedger.query.filter(StockLedger.warehouse_id == warehouse.id,
                                        StockLedger.movement_type == 'opening').all()
    opening_total = defaultdict(int)
    for line in openings:
        opening_total[line.goods_id] = line.goods_total_after
    with guard.suspended():
        for event, stock_type, location_id, quantity, balance_after, total_after, counter in lines:
            db.session.add(StockLedger(
                posted_at=event.at, occurred_at=event.at, company_id=warehouse.company_id, warehouse_id=warehouse.id,
                goods_id=event.goods_id, lot_id=None, location_id=location_id, stock_type=stock_type,
                quantity=quantity, balance_after=balance_after, goods_total_after=total_after,
                counter_stock_type=counter, movement_type=event.movement, doc_type=event.doc_type,
                doc_id=event.doc_id, doc_no=(event.doc_no or None) and str(event.doc_no)[:100],
                doc_line_id=event.doc_line_id, reason=event.reason, operator_id=event.operator_id, source='system',
                trace_id=f"replay-{event.doc_type}-{event.doc_id or 0}"[:64], remark='derived (history replay)',
            ))
        for (goods_id, location_id, stock_type), opening, replayed in final_diff:
            db.session.add(StockLedger(
                posted_at=t0, occurred_at=t0, company_id=warehouse.company_id, warehouse_id=warehouse.id,
                goods_id=goods_id, lot_id=None, location_id=location_id, stock_type=stock_type,
                quantity=opening - replayed, balance_after=opening, goods_total_after=opening_total.get(goods_id, 0),
                counter_stock_type=None, movement_type='reconcile', source='system',
                trace_id='replay-reconcile', remark='history replay: opening minus replayed balance',
            ))
        for line in openings:
            db.session.delete(line)
        db.session.flush()
    db.session.commit()
    first = (db.session.query(func.min(StockLedger.posted_at))
             .filter(StockLedger.warehouse_id == warehouse.id).scalar())
    if first is not None:
        first_day = clock.local_date(as_utc(first), warehouse)
        last_day = clock.local_date(t0, warehouse) - timedelta(days=1)
        StockDailyBalance.query.filter(StockDailyBalance.warehouse_id == warehouse.id,
                                       StockDailyBalance.source == 'legacy_snapshot',
                                       StockDailyBalance.balance_date >= first_day,
                                       StockDailyBalance.balance_date <= last_day).delete(synchronize_session=False)
        db.session.commit()
        rebuild(warehouse.id, first_day, last_day, source='derived')
