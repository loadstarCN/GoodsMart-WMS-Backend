import time
from celery import shared_task
from warehouse.inventory_snapshot.services import InventorySnapshotService
from warehouse.warehouse.services import WarehouseService

@shared_task
def inventory_snapshot_task():
    """创建库存快照的异步任务"""
    
    start_time = time.time()
    # 获取所有仓库ID
    filters = {}
    warehouses = WarehouseService.list_warehouses(filters)
    # 遍历每个仓库ID，创建库存快照
    for warehouse in warehouses:
        InventorySnapshotService.create_snapshot(warehouse.id)
    
    end_time = time.time()
    duration = end_time - start_time
    # 返回任务执行的时间
    return f"Task completed after {duration} seconds"