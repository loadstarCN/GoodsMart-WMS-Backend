import time
from warehouse.inventory_snapshot.services import InventorySnapshotService
from warehouse.warehouse.services import WarehouseService


def run_inventory_snapshot():
    """为所有仓库创建库存快照（旧口径：只拍库位库存）。一期保留，与由流水算出的日终结存（stock_daily_balances）
    互相核对；历史库存的查询以日终结存为准。顺带补生成日终结存与库存分析（warehouse/stock/jobs.py）"""
    from warehouse.stock.jobs import run_stock_daily
    start_time = time.time()
    warehouses = WarehouseService.list_warehouses({}).all()
    for warehouse in warehouses:
        InventorySnapshotService.create_snapshot(warehouse.id)
    daily = run_stock_daily()
    duration = time.time() - start_time
    return (f"Snapshot completed in {duration:.2f} seconds, {len(warehouses)} warehouses; "
            f"daily balances {daily['daily_balances']}, metrics {daily['metrics']}")


def run_inventory_metrics():
    """补生成日终结存后，重算各仓库当地昨天的库存分析（POST /tasks/task/inventory_metrics）"""
    from datetime import timedelta
    from extensions.db import db
    from warehouse.common import clock
    from warehouse.stock.history import generate_missing
    from warehouse.stock.metrics import generate_metrics
    start_time = time.time()
    generate_missing()
    result = {}
    for warehouse in WarehouseService.list_warehouses({}).all():
        day = clock.local_today(warehouse) - timedelta(days=1)
        result[warehouse.id] = generate_metrics(warehouse.id, day)
        db.session.commit()
    return f"Metrics completed in {time.time() - start_time:.2f} seconds: {result}"
