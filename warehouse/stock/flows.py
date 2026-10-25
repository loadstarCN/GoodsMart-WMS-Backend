"""入库 / 出库事件：库龄、动销、周转、补货建议、出入库趋势共用（doc/库存数值系统说明.md 第十一节）。

口径（2026-10-25 定）：
- 出库 = 发货完成（ship：已打包 → 在途），时刻取业务发生时刻（实际发货 / 揽收时刻）
- 入库层 = 分拣完成（sort_in，含客户退货入库）+ 调整单盘盈（adjust_gain）。下架、少打包退回、取消退回都只是仓内搬动，
  不算新入库（不重置库龄）；期初（opening）、系统校正（reconcile）也不算
流水覆盖之前（该仓库第一条流水之前）用单据补：入库 = 入库单完成（completed_at，旧数据没有按创建时刻；实收
actual_quantity）、调整单完成的正向差值；出库 = DN 发货（delivered_at，delivered_quantity）。
"""
from sqlalchemy import func

from extensions.db import as_utc, db
from .models import StockLedger

OUTBOUND_MOVEMENTS = ('ship',)
INBOUND_MOVEMENTS = ('sort_in', 'adjust_gain')


def _cutover(warehouse_id):
    from .history import ledger_start
    return ledger_start(warehouse_id)


def outbound_events(warehouse_id, start=None, end=None, goods_ids=None) -> list:
    """[start, end) 的出库：[(goods_id, 时刻, 数量, dn_type)]"""
    from warehouse.dn.models import DN, DNDetail
    cutover = _cutover(warehouse_id)
    events = []
    query = (db.session.query(StockLedger.goods_id, StockLedger.occurred_at, StockLedger.quantity, DN.dn_type)
             .outerjoin(DN, (StockLedger.doc_type == 'dn') & (DN.id == StockLedger.doc_id))
             .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type.in_(OUTBOUND_MOVEMENTS),
                     StockLedger.quantity > 0, StockLedger.stock_type == 'in_transit'))
    if start is not None:
        query = query.filter(StockLedger.occurred_at >= start)
    if end is not None:
        query = query.filter(StockLedger.occurred_at < end)
    if goods_ids is not None:
        query = query.filter(StockLedger.goods_id.in_(list(goods_ids)))
    events.extend((goods_id, as_utc(at), int(quantity), dn_type) for goods_id, at, quantity, dn_type in query.all())
    if cutover is None or start is None or start < cutover:
        upper = cutover if end is None or (cutover is not None and cutover < end) else end
        query = (db.session.query(DNDetail.goods_id, DN.delivered_at, func.sum(DNDetail.delivered_quantity), DN.dn_type)
                 .join(DN, DN.id == DNDetail.dn_id)
                 .filter(DN.warehouse_id == warehouse_id, DN.is_active == True,
                         DN.status.in_(('delivered', 'completed')), DN.delivered_at.isnot(None),
                         DNDetail.delivered_quantity > 0))
        if start is not None:
            query = query.filter(DN.delivered_at >= start)
        if upper is not None:
            query = query.filter(DN.delivered_at < upper)
        if goods_ids is not None:
            query = query.filter(DNDetail.goods_id.in_(list(goods_ids)))
        rows = query.group_by(DNDetail.goods_id, DN.id, DN.delivered_at, DN.dn_type).all()
        events.extend((goods_id, as_utc(at), int(quantity), dn_type) for goods_id, at, quantity, dn_type in rows)
    return events


def inbound_events(warehouse_id, end=None, goods_ids=None, start=None) -> list:
    """入库层（end 之前）：[(goods_id, 时刻, 数量, movement_type)]，movement_type 为 sort_in / adjust_gain"""
    from warehouse.adjustment.models import Adjustment, AdjustmentDetail
    from warehouse.asn.models import ASN, ASNDetail
    cutover = _cutover(warehouse_id)
    events = []
    query = (db.session.query(StockLedger.goods_id, StockLedger.occurred_at, StockLedger.quantity,
                              StockLedger.movement_type)
             .filter(StockLedger.warehouse_id == warehouse_id, StockLedger.movement_type.in_(INBOUND_MOVEMENTS),
                     StockLedger.quantity > 0))
    if start is not None:
        query = query.filter(StockLedger.occurred_at >= start)
    if end is not None:
        query = query.filter(StockLedger.occurred_at < end)
    if goods_ids is not None:
        query = query.filter(StockLedger.goods_id.in_(list(goods_ids)))
    events.extend((g, as_utc(at), int(q), m) for g, at, q, m in query.all())
    if cutover is None or start is None or start < cutover:
        upper = cutover if end is None or (cutover is not None and cutover < end) else end
        asn_at = func.coalesce(ASN.completed_at, ASN.created_at)
        query = (db.session.query(ASNDetail.goods_id, asn_at, func.sum(ASNDetail.actual_quantity))
                 .join(ASN, ASN.id == ASNDetail.asn_id)
                 .filter(ASN.warehouse_id == warehouse_id, ASN.status == 'completed', ASN.is_active == True,
                         ASNDetail.actual_quantity > 0))
        adj_query = (db.session.query(AdjustmentDetail.goods_id, Adjustment.completed_at,
                                      func.sum(AdjustmentDetail.adjustment_quantity))
                     .join(Adjustment, Adjustment.id == AdjustmentDetail.adjustment_id)
                     .filter(Adjustment.warehouse_id == warehouse_id, Adjustment.status == 'completed',
                             Adjustment.is_active == True, Adjustment.completed_at.isnot(None),
                             AdjustmentDetail.adjustment_quantity > 0))
        if start is not None:
            query = query.filter(asn_at >= start)
            adj_query = adj_query.filter(Adjustment.completed_at >= start)
        if upper is not None:
            query = query.filter(asn_at < upper)
            adj_query = adj_query.filter(Adjustment.completed_at < upper)
        if goods_ids is not None:
            query = query.filter(ASNDetail.goods_id.in_(list(goods_ids)))
            adj_query = adj_query.filter(AdjustmentDetail.goods_id.in_(list(goods_ids)))
        events.extend((g, as_utc(at), int(q), 'sort_in')
                      for g, at, q in query.group_by(ASNDetail.goods_id, ASN.id, asn_at).all() if at is not None)
        events.extend((g, as_utc(at), int(q), 'adjust_gain')
                      for g, at, q in adj_query.group_by(AdjustmentDetail.goods_id, Adjustment.id,
                                                         Adjustment.completed_at).all())
    return events
