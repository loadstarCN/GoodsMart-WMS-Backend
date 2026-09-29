"""行锁辅助：库位库存（GoodsLocation）的读-改-写必须在 SELECT ... FOR UPDATE 下进行，
否则多 worker 部署时两次并发下架会互相覆盖（丢更新 / 超卖）。

SQLite（测试环境）会忽略 FOR UPDATE，PostgreSQL 生产环境生效。
写法与 picking/services.py::_assert_location_stock 一致：lazyload('*') 抑制 joined 关系，
避免 PostgreSQL 上 "FOR UPDATE cannot be applied to the nullable side of an outer join"。
"""
from sqlalchemy.orm import lazyload

from extensions.db import db


def lock_goods_location(goods_id: int, location_id: int):
    """按 (goods_id, location_id) 加锁读取库位库存记录；不存在返回 None。"""
    from warehouse.goods.models import GoodsLocation
    return (
        GoodsLocation.query
        .options(lazyload('*'))
        .filter_by(goods_id=goods_id, location_id=location_id)
        .with_for_update()
        .first()
    )


def lock_goods_locations_for_goods(goods_id: int, warehouse_id: int):
    """加锁读取某商品在某仓库下的全部库位记录（重算 / 盘点调整时用）。"""
    from warehouse.goods.models import GoodsLocation
    from warehouse.location.models import Location
    return (
        GoodsLocation.query
        .options(lazyload('*'))
        .join(Location, GoodsLocation.location_id == Location.id)
        .filter(GoodsLocation.goods_id == goods_id, Location.warehouse_id == warehouse_id)
        .with_for_update(of=GoodsLocation)
        .all()
    )
