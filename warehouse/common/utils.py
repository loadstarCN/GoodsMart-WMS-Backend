from flask import g

def add_warehouse_filter(filters: dict) -> dict:
    """
    根据 g 对象中的 warehouse 信息，为过滤条件添加相应的仓库过滤键：
    - 如果 g.warehouse_id 存在，则添加 'warehouse_id'；
    - 否则，如果 g.accessible_warehouses 存在，则添加 'warehouse_ids'（一个仓库 ID 列表）。
    """
    warehouse_id = getattr(g, "warehouse_id", None)
    if warehouse_id:
        filters['warehouse_id'] = warehouse_id
    else:
        accessible_warehouses = getattr(g, "accessible_warehouses", [])
        if accessible_warehouses:
            filters['warehouse_ids'] = [warehouse.id for warehouse in accessible_warehouses]
    return filters