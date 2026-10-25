"""待上架的停留时间（不加字段，用现有单据的时刻推算；doc/库存数值系统说明.md）

上架记录不记是哪一笔入账的，所以按「先进先上」估算：某商品在某仓库的待上架（inventory.sorted_stock）由最近的入账组成，
从最近一笔往回累加，凑够当前 sorted_stock 为止，最早那一笔的时刻就是 pending_since（最早一件待上架的进入时刻）。

入账（货进 sorted_stock 的地方）与时刻：
- 分拣完成：入库单完成（status = completed、有效）的实收 actual_quantity，时刻 = 入库单完成时刻
  （completed_at，即分拣任务完成时刻；旧数据没有时按创建时刻，同 asn/stage.py 的口径）
- 少打包退回：打包完成（DN.packed_at 有值）时「已拣 − 已打包」的差额，时刻 = 打包完成时刻（DN.packed_at）
- 发货单取消退回：已拣 / 已打包未发货的 DN 取消（status = closed 且 picked_at 有值），时刻 = 取消时刻（DN.closed_at）；
  打包前取消退回已拣量，打包后取消退回已打包量
- 下架：除拣货下架（reason = picking，货走 picked_stock）以外的下架记录，时刻 = removal_time
入账合计不够当前 sorted_stock（旧数据、直接改过库存）时，按最早那一笔算（不够的部分只知道比它更早）；
一笔入账都没有 → None。

超期：进入待上架超过 overdue_days 天（默认 PENDING_PUTAWAY_OVERDUE_DAYS = 3，汇总接口的 overdue_days 参数可改）。
同样按先进先上：截止时刻之后的入账（最多 sorted_stock 件）算没超期，其余算超期。
"""
from datetime import timedelta

from sqlalchemy import func

from extensions.db import as_utc, db

PENDING_PUTAWAY_OVERDUE_DAYS = 3
MAX_OVERDUE_DAYS = 365


def _entries(pairs) -> dict:
    """{(goods_id, warehouse_id): [(时刻, 数量), ...]}：各来源的入账，按时刻从新到旧"""
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.dn.models import DN, DNDetail
    from warehouse.location.models import Location
    from warehouse.removal.models import RemovalRecord

    pairs = {(g, w) for g, w in pairs if g is not None and w is not None}
    if not pairs:
        return {}
    goods_ids = sorted({g for g, _w in pairs})
    warehouse_ids = sorted({w for _g, w in pairs})
    result = {pair: [] for pair in pairs}

    def add(rows):
        for goods_id, warehouse_id, at, quantity in rows:
            key = (goods_id, warehouse_id)
            if key in result and at is not None and quantity and quantity > 0:
                result[key].append((as_utc(at), int(quantity)))

    # 分拣完成（入库单完成）
    asn_at = func.coalesce(ASN.completed_at, ASN.created_at)
    add(db.session.query(ASNDetail.goods_id, ASN.warehouse_id, asn_at, func.sum(ASNDetail.actual_quantity))
        .join(ASN, ASN.id == ASNDetail.asn_id)
        .filter(ASN.status == 'completed', ASN.is_active.is_(True), ASNDetail.actual_quantity > 0,
                ASNDetail.goods_id.in_(goods_ids), ASN.warehouse_id.in_(warehouse_ids))
        .group_by(ASNDetail.goods_id, ASN.warehouse_id, asn_at).all())

    dn_rows = (db.session.query(DNDetail.goods_id, DN.warehouse_id, DN.status, DN.packed_at, DN.closed_at,
                                func.coalesce(func.sum(DNDetail.picked_quantity), 0),
                                func.coalesce(func.sum(DNDetail.packed_quantity), 0))
               .join(DN, DN.id == DNDetail.dn_id)
               .filter(DN.picked_at.isnot(None), DNDetail.goods_id.in_(goods_ids), DN.warehouse_id.in_(warehouse_ids))
               .group_by(DNDetail.goods_id, DN.warehouse_id, DN.id, DN.status, DN.packed_at, DN.closed_at).all())
    for goods_id, warehouse_id, status, packed_at, closed_at, picked, packed in dn_rows:
        if packed_at is not None:
            # 少打包退回：打包完成时 已拣 − 已打包
            add([(goods_id, warehouse_id, packed_at, picked - packed)])
        if status == 'closed':
            # 取消退回：打包后取消退已打包量，打包前取消退已拣量
            add([(goods_id, warehouse_id, closed_at, packed if packed_at is not None else picked)])

    # 下架（拣货下架除外）
    add(db.session.query(RemovalRecord.goods_id, Location.warehouse_id, RemovalRecord.removal_time,
                         func.sum(RemovalRecord.quantity))
        .join(Location, Location.id == RemovalRecord.location_id)
        .filter(RemovalRecord.reason != 'picking', RemovalRecord.goods_id.in_(goods_ids),
                Location.warehouse_id.in_(warehouse_ids))
        .group_by(RemovalRecord.goods_id, Location.warehouse_id, RemovalRecord.removal_time).all())

    for entries in result.values():
        entries.sort(key=lambda entry: entry[0], reverse=True)
    return result


def _pending_since(entries, sorted_stock):
    """先进先上：从最近的入账往回累加到凑够 sorted_stock，最早那一笔的时刻"""
    if not sorted_stock or sorted_stock <= 0 or not entries:
        return None
    covered = 0
    at = None
    for at, quantity in entries:
        covered += quantity
        if covered >= sorted_stock:
            break
    return at


def _overdue_quantity(entries, sorted_stock, cutoff) -> int:
    """待上架里进入时刻早于 cutoff 的件数：cutoff 之后的入账（最多 sorted_stock 件）算没超期"""
    if not sorted_stock or sorted_stock <= 0:
        return 0
    recent = sum(quantity for at, quantity in entries if at >= cutoff)
    return max(0, sorted_stock - min(recent, sorted_stock))


def attach_pending_since(inventories):
    """给一页库存行挂上 _pending_since（序列化时 Inventory.pending_since 取；一页几条查询，不逐行查）"""
    inventories = [inv for inv in (inventories or []) if inv is not None]
    pending = [inv for inv in inventories if (inv.sorted_stock or 0) > 0]
    entries = _entries((inv.goods_id, inv.warehouse_id) for inv in pending)
    for inv in inventories:
        inv._pending_since = (_pending_since(entries.get((inv.goods_id, inv.warehouse_id), []), inv.sorted_stock)
                              if (inv.sorted_stock or 0) > 0 else None)
    return inventories


def overdue_summary(rows, overdue_days: int, now) -> dict:
    """rows：[(goods_id, warehouse_id, sorted_stock)]（sorted_stock > 0 的行）→
    {overdue_quantity, overdue_inventory_count, oldest_pending_since}"""
    rows = list(rows)
    entries = _entries((goods_id, warehouse_id) for goods_id, warehouse_id, _stock in rows)
    cutoff = as_utc(now) - timedelta(days=overdue_days)
    overdue_quantity = 0
    overdue_rows = 0
    oldest = None
    for goods_id, warehouse_id, stock in rows:
        row_entries = entries.get((goods_id, warehouse_id), [])
        overdue = _overdue_quantity(row_entries, stock, cutoff)
        overdue_quantity += overdue
        overdue_rows += 1 if overdue > 0 else 0
        since = _pending_since(row_entries, stock)
        if since is not None and (oldest is None or since < oldest):
            oldest = since
    return {'overdue_quantity': overdue_quantity, 'overdue_inventory_count': overdue_rows,
            'oldest_pending_since': oldest}
