"""Attach warehouse-specific latest daily metrics without replacing live balances."""
from sqlalchemy import and_, func, tuple_
from extensions.db import db
from warehouse.common import clock
from warehouse.stock.models import InventoryMetricsDaily


def attach_latest_metrics(inventories):
    for inventory in inventories:
        inventory.metrics = None
    if not inventories:
        return
    warehouse_ids = {row.warehouse_id for row in inventories}
    latest = (db.session.query(InventoryMetricsDaily.warehouse_id.label('warehouse_id'),
                              func.max(InventoryMetricsDaily.metric_date).label('metric_date'))
              .filter(InventoryMetricsDaily.warehouse_id.in_(warehouse_ids))
              .group_by(InventoryMetricsDaily.warehouse_id).subquery())
    by_pair = {}
    pairs = [(row.goods_id, row.warehouse_id) for row in inventories]
    for start in range(0, len(pairs), 300):
        rows = (InventoryMetricsDaily.query.join(latest, and_(
            InventoryMetricsDaily.warehouse_id == latest.c.warehouse_id,
            InventoryMetricsDaily.metric_date == latest.c.metric_date))
            .filter(tuple_(InventoryMetricsDaily.goods_id, InventoryMetricsDaily.warehouse_id).in_(pairs[start:start+300])))
        for row in rows:
            by_pair[(row.goods_id, row.warehouse_id)] = dict(
                metric_date=row.metric_date.isoformat(), out_qty_30d=row.out_qty_30d,
                weighted_age_days=row.weighted_age_days, turnover_days_30d=row.turnover_days_30d,
                last_out_at=clock.utc_iso(row.last_out_at), abc_class=row.abc_class, status=row.status)
    for inventory in inventories:
        inventory.metrics = by_pair.get((inventory.goods_id, inventory.warehouse_id))
