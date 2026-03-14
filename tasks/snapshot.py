import time
from warehouse.inventory_snapshot.services import InventorySnapshotService
from warehouse.warehouse.services import WarehouseService


def run_inventory_snapshot():
    """为所有仓库创建库存快照"""
    start_time = time.time()
    warehouses = WarehouseService.list_warehouses({})
    for warehouse in warehouses:
        InventorySnapshotService.create_snapshot(warehouse.id)
    duration = time.time() - start_time
    return f"Snapshot completed in {duration:.2f} seconds, {len(warehouses)} warehouses"
