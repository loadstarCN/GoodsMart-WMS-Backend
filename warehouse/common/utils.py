from flask import g, request

from extensions.error import ForbiddenException


def scoped_cache_key(*args, **kwargs) -> str:
    """视图缓存键：路径 + 排序后的查询参数 + 调用方仓库范围。

    给 @cache.cached(make_cache_key=scoped_cache_key) 用，须放在 @warehouse_required() 之内
    （装饰器顺序上在它下面），这样 g.warehouse_id / g.accessible_warehouses 已就绪。
    缓存在 Redis 里跨 worker 共享，键里不带仓库范围会把 A 公司的统计返回给 B 公司。
    """
    query = sorted(request.args.items(multi=True))
    warehouse_id = getattr(g, "warehouse_id", None)
    accessible = getattr(g, "accessible_warehouses", None)
    if warehouse_id:
        scope = f"w{warehouse_id}"
    elif accessible is not None:
        scope = "ws" + ",".join(str(i) for i in sorted(w.id for w in accessible))
    else:
        scope = "all"  # 平台用户 / 未绑定公司的超级密钥：本就不受仓库范围限制
    return f"view/{request.path}?{query}|{scope}"

def add_warehouse_filter(filters: dict) -> dict:
    """
    根据 g 对象中的 warehouse 信息，为过滤条件添加相应的仓库过滤键：
    - 如果 g.warehouse_id 存在，则添加 'warehouse_id'；
    - 否则，如果 g.accessible_warehouses 存在，则添加 'warehouse_ids'（一个仓库 ID 列表）；
    - g.accessible_warehouses 已设置但为空（受仓库范围限制却一个仓库都没有）→ 403，
      不能退化成不加过滤（否则返回全平台数据）。
    未设置 g.accessible_warehouses 的调用方（平台用户、未绑定公司的超级密钥）不受仓库范围限制。
    """
    warehouse_id = getattr(g, "warehouse_id", None)
    if warehouse_id:
        filters['warehouse_id'] = warehouse_id
    else:
        accessible_warehouses = getattr(g, "accessible_warehouses", None)
        if accessible_warehouses is not None:
            if not accessible_warehouses:
                raise ForbiddenException("No accessible warehouses", 12001)
            filters['warehouse_ids'] = [warehouse.id for warehouse in accessible_warehouses]
    return filters
