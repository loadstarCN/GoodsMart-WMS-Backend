"""已确认的历史 bug 造成的库存计数差额的校正（flask stock fix-legacy-pending；doc/库存数值系统说明.md 第十节第 4 小节）。

B-06（58c6a68，2026-09-29 03:50:30 UTC 之前）：拣货下架（removal_records.reason = 'picking'）走了普通下架，货同时进了
  sorted_stock（待上架）和 picked_stock —— 待上架里多出拣货下架的数量（实物并不在待上架区）。
  校正：待上架（良品）→（仓外），数量 = min(修复前拣货下架量, 计数器 − 单据推算的待上架, 待上架良品)，不扣到负数、
  不动待上架里的破损部分。
  单据推算的待上架 = 入库单完成的实收 + 非拣货下架 + B-54 修复后的少打包退回 + 拣货后取消退回 + 本命令 B-54 校正退回的量
  − 上架记录（都是现行规则下进 / 出待上架的单据；修复前的拣货下架不算）。
B-54（2f8327e，2026-10-04 15:18 UTC 之前）：少打包时差额留在 picked_stock（已拣），没有退回待上架，此后再也不会结算。
  校正（--include-picked）：已拣 → 待上架（良品），按现行规则退回，之后由仓库上架放回或调整报损；
  数量 = min(修复前少打包差额, 已拣计数器 − 拣货完成、打包未完成（status = picked）的 DN 的已拣量)。
每笔校正都经 StockService 写流水（movement_type = correction，reason = B-06 / B-54，doc_type = legacy_fix、doc_no = bug 编号，
备注写明原因与依据，trace_id = fix-<bug>-<时刻>）；已有同一 bug 校正流水的商品跳过（幂等）。只处理无批次库存（一期没有批次）。
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import case, func

from extensions.db import db
from warehouse.common import clock
from .models import InventoryLotStock, StockLedger

B06_CUTOFF = datetime(2026, 9, 29, 3, 50, 30, tzinfo=timezone.utc)
B54_CUTOFF = datetime(2026, 10, 4, 15, 18, 0, tzinfo=timezone.utc)
B06 = 'B-06'
B54 = 'B-54'
B06_REMARK = 'B-06：2026-09-29 前拣货下架误计入待上架（修复前拣货下架 {bug} 件，单据推算待上架 {derived} 件）'
B54_REMARK = 'B-54：2026-10-04 前少打包的差额留在已拣，按现行规则退回待上架（修复前少打包差额 {short} 件）'


def _by_goods(query):
    return {goods_id: int(quantity or 0) for goods_id, quantity in query.all()}


def _corrected_goods(warehouse_id, reason):
    return {g for (g,) in db.session.query(StockLedger.goods_id).filter(
        StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type == 'correction',
        StockLedger.reason == reason).distinct().all()}


def pending_plan(warehouse_id, before=B06_CUTOFF, short_pack_from=B54_CUTOFF, goods_ids=None) -> list:
    """B-06 的校正计划：每个商品 {goods_id, sorted_stock, sorted_damage_stock, asn_actual, putaway, removal_other,
    short_pack_return, cancel_return, derived, picking_before_fix, excess, deduct, skipped}"""
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.dn.models import DN, DNDetail
    from warehouse.location.models import Location
    from warehouse.putaway.models import PutawayRecord
    from warehouse.removal.models import RemovalRecord

    def scoped(query, column):
        return query.filter(column.in_(goods_ids)) if goods_ids is not None else query

    stocks = {row.goods_id: row for row in scoped(InventoryLotStock.query.filter(
        InventoryLotStock.warehouse_id == warehouse_id, InventoryLotStock.lot_id.is_(None)),
        InventoryLotStock.goods_id).all()}
    asn = _by_goods(scoped(db.session.query(ASNDetail.goods_id, func.sum(ASNDetail.actual_quantity))
                           .join(ASN, ASN.id == ASNDetail.asn_id)
                           .filter(ASN.warehouse_id == warehouse_id, ASN.status == 'completed', ASN.is_active == True),
                           ASNDetail.goods_id).group_by(ASNDetail.goods_id))
    putaway = _by_goods(scoped(db.session.query(PutawayRecord.goods_id, func.sum(PutawayRecord.quantity))
                               .join(Location, Location.id == PutawayRecord.location_id)
                               .filter(Location.warehouse_id == warehouse_id), PutawayRecord.goods_id)
                        .group_by(PutawayRecord.goods_id))
    removal_query = scoped(db.session.query(
        RemovalRecord.goods_id,
        func.sum(case((RemovalRecord.reason != 'picking', RemovalRecord.quantity), else_=0)),
        func.sum(case(((RemovalRecord.reason == 'picking') & (RemovalRecord.removal_time < before),
                       RemovalRecord.quantity), else_=0)))
        .join(Location, Location.id == RemovalRecord.location_id)
        .filter(Location.warehouse_id == warehouse_id), RemovalRecord.goods_id).group_by(RemovalRecord.goods_id)
    removal_other, picking_before = {}, {}
    for goods_id, other, picking in removal_query.all():
        removal_other[goods_id] = int(other or 0)
        picking_before[goods_id] = int(picking or 0)
    short = _by_goods(scoped(db.session.query(DNDetail.goods_id,
                                              func.sum(DNDetail.picked_quantity - DNDetail.packed_quantity))
                             .join(DN, DN.id == DNDetail.dn_id)
                             .filter(DN.warehouse_id == warehouse_id, DN.is_active == True, DN.packed_at.isnot(None),
                                     DN.packed_at >= short_pack_from,
                                     DNDetail.picked_quantity > DNDetail.packed_quantity),
                             DNDetail.goods_id).group_by(DNDetail.goods_id))
    cancel = _by_goods(scoped(db.session.query(DNDetail.goods_id, func.sum(case(
        (DN.packed_at.isnot(None), DNDetail.packed_quantity), else_=DNDetail.picked_quantity)))
        .join(DN, DN.id == DNDetail.dn_id)
        .filter(DN.warehouse_id == warehouse_id, DN.is_active == True, DN.status == 'closed',
                DN.picked_at.isnot(None)), DNDetail.goods_id).group_by(DNDetail.goods_id))
    # B-54 校正退回待上架的量（ledger），也是现行规则下进待上架的
    returned = _by_goods(scoped(db.session.query(StockLedger.goods_id, func.sum(StockLedger.quantity))
                                .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type == 'correction',
                                        StockLedger.reason == B54, StockLedger.stock_type == 'pending'),
                                StockLedger.goods_id).group_by(StockLedger.goods_id))
    done = _corrected_goods(warehouse_id, B06)

    plan = []
    for goods_id in sorted(set(stocks) | {g for g, q in picking_before.items() if q}):
        row = stocks.get(goods_id)
        sorted_stock = int(row.sorted_stock or 0) if row is not None else 0
        damaged = min(int(row.sorted_damage_stock or 0), sorted_stock) if row is not None else 0
        derived = (asn.get(goods_id, 0) + removal_other.get(goods_id, 0) + short.get(goods_id, 0)
                   + cancel.get(goods_id, 0) + returned.get(goods_id, 0) - putaway.get(goods_id, 0))
        bug = picking_before.get(goods_id, 0)
        excess = sorted_stock - derived
        deduct = max(0, min(bug, excess, sorted_stock - damaged))
        skipped = None
        if goods_id in done:
            skipped, deduct = 'ALREADY_CORRECTED', 0
        if not sorted_stock and not bug:
            continue
        plan.append({'goods_id': goods_id, 'sorted_stock': sorted_stock, 'sorted_damage_stock': damaged,
                     'asn_actual': asn.get(goods_id, 0), 'putaway': putaway.get(goods_id, 0),
                     'removal_other': removal_other.get(goods_id, 0), 'short_pack_return': short.get(goods_id, 0),
                     'cancel_return': cancel.get(goods_id, 0), 'b54_return': returned.get(goods_id, 0),
                     'derived': derived, 'picking_before_fix': bug,
                     'excess': excess, 'deduct': deduct, 'skipped': skipped})
    return plan


def picked_plan(warehouse_id, short_pack_before=B54_CUTOFF, goods_ids=None) -> list:
    """B-54 的校正计划：每个商品 {goods_id, picked_stock, in_picking, short_pack_before_fix, residue, move, skipped}"""
    from warehouse.dn.models import DN, DNDetail

    def scoped(query, column):
        return query.filter(column.in_(goods_ids)) if goods_ids is not None else query

    stocks = {row.goods_id: row for row in scoped(InventoryLotStock.query.filter(
        InventoryLotStock.warehouse_id == warehouse_id, InventoryLotStock.lot_id.is_(None)),
        InventoryLotStock.goods_id).all()}
    in_picking = _by_goods(scoped(db.session.query(DNDetail.goods_id, func.sum(DNDetail.picked_quantity))
                                  .join(DN, DN.id == DNDetail.dn_id)
                                  .filter(DN.warehouse_id == warehouse_id, DN.is_active == True, DN.status == 'picked'),
                                  DNDetail.goods_id).group_by(DNDetail.goods_id))
    short = _by_goods(scoped(db.session.query(DNDetail.goods_id,
                                              func.sum(DNDetail.picked_quantity - DNDetail.packed_quantity))
                             .join(DN, DN.id == DNDetail.dn_id)
                             .filter(DN.warehouse_id == warehouse_id, DN.is_active == True, DN.packed_at.isnot(None),
                                     DN.packed_at < short_pack_before,
                                     DNDetail.picked_quantity > DNDetail.packed_quantity),
                             DNDetail.goods_id).group_by(DNDetail.goods_id))
    done = _corrected_goods(warehouse_id, B54)
    plan = []
    for goods_id in sorted({g for g, row in stocks.items() if row.picked_stock} | {g for g, q in short.items() if q}):
        row = stocks.get(goods_id)
        picked = int(row.picked_stock or 0) if row is not None else 0
        residue = picked - in_picking.get(goods_id, 0)
        move = max(0, min(short.get(goods_id, 0), residue))
        skipped = None
        if goods_id in done:
            skipped, move = 'ALREADY_CORRECTED', 0
        plan.append({'goods_id': goods_id, 'picked_stock': picked, 'in_picking': in_picking.get(goods_id, 0),
                     'short_pack_before_fix': short.get(goods_id, 0), 'residue': residue, 'move': move,
                     'skipped': skipped})
    return plan


def fix_legacy_pending(warehouse_id, apply=False, include_picked=False, before=B06_CUTOFF,
                       short_pack_cutoff=B54_CUTOFF, commit_every=200) -> dict:
    """演练（默认）或写入。写入时逐个商品在其 Inventory 行锁下重算计划再记账（与线上作业并发也不会多扣）。
    返回 {pending: [...], picked: [...], totals}"""
    from warehouse.common.locks import lock_inventory_pairs
    from .service import Leg, StockOp
    pending = pending_plan(warehouse_id, before, short_pack_cutoff)
    picked = picked_plan(warehouse_id, short_pack_cutoff) if include_picked else []
    report = {'warehouse_id': warehouse_id, 'applied': bool(apply), 'pending': pending, 'picked': picked}
    if apply:
        stamp = clock.now().strftime('%Y%m%d%H%M%S')
        done = 0
        for bug, rows, key in ((B06, pending, 'deduct'), (B54, picked, 'move')):
            for item in rows:
                if not item[key]:
                    continue
                goods_id = item['goods_id']
                lock_inventory_pairs([(goods_id, warehouse_id)])
                fresh = (pending_plan(warehouse_id, before, short_pack_cutoff, [goods_id]) if bug == B06
                         else picked_plan(warehouse_id, short_pack_cutoff, [goods_id]))
                current = next((r for r in fresh if r['goods_id'] == goods_id), None)
                quantity = current[key] if current else 0
                item['applied'] = quantity
                if not quantity:
                    continue
                if bug == B06:
                    op = StockOp(warehouse_id, 'correction', doc_type='legacy_fix', doc_no=B06, reason=B06,
                                 remark=B06_REMARK.format(bug=current['picking_before_fix'], derived=current['derived']),
                                 trace_id=f'fix-{B06}-{stamp}')
                    op.move(goods_id, quantity, src=Leg.pending())
                else:
                    op = StockOp(warehouse_id, 'correction', doc_type='legacy_fix', doc_no=B54, reason=B54,
                                 remark=B54_REMARK.format(short=current['short_pack_before_fix']),
                                 trace_id=f'fix-{B54}-{stamp}')
                    op.move(goods_id, quantity, src=Leg.picked(), dst=Leg.pending())
                op.commit()
                done += 1
                if done % commit_every == 0:
                    db.session.commit()
        db.session.commit()
    totals = defaultdict(int)
    for item in pending:
        for field in ('sorted_stock', 'derived', 'picking_before_fix', 'deduct'):
            totals[f'pending_{field}'] += item[field]
        totals['pending_goods_to_fix'] += 1 if item['deduct'] else 0
        totals['pending_goods_mismatch'] += 1 if not item['skipped'] and item['excess'] != item['picking_before_fix'] else 0
        totals['pending_already_corrected'] += 1 if item['skipped'] else 0
    for item in picked:
        for field in ('picked_stock', 'in_picking', 'short_pack_before_fix', 'move'):
            totals[f'picked_{field}'] += item[field]
        totals['picked_goods_to_fix'] += 1 if item['move'] else 0
    report['totals'] = dict(totals)
    return report
