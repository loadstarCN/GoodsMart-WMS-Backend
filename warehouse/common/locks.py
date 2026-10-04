"""行锁辅助：库位库存（GoodsLocation）的读-改-写必须在 SELECT ... FOR UPDATE 下进行，
否则多 worker 部署时两次并发下架会互相覆盖（丢更新 / 超卖）。

SQLite（测试环境）会忽略 FOR UPDATE，PostgreSQL 生产环境生效。
写法与 picking/services.py::_assert_location_stock 一致：lazyload('*') 抑制 joined 关系，
避免 PostgreSQL 上 "FOR UPDATE cannot be applied to the nullable side of an outer join"。

lock_row：单据 / 任务的状态转换、批次追加前锁单据行并重读状态，防止并发重复提交重复记账。
"""
from sqlalchemy.orm import lazyload

from extensions.db import db
from extensions.error import NotFoundException


def lock_row(obj, *, shared: bool = False):
    """单据 / 任务状态转换前加行锁：SELECT id ... FOR UPDATE，拿到锁后 refresh 重读状态，返回 obj。

    shared=True 改加共享锁（FOR SHARE）：只为读状态、不改该行的一方用（如上架 / 移库读目标库位的启用状态），
    彼此不互斥，只与改该行的 FOR UPDATE（如停用库位）互斥；批量上架按不同顺序碰到同几个库位也不会互相死锁。

    并发的重复提交（PG 读已提交）会在这把锁上等前一个请求提交，refresh 后按新状态
    在调用方现有的状态检查处挡下，不再按内存里的旧状态重复记账。
    - 只按主键查 id 列：DN / ASN / 各任务模型都有 lazy='joined' 关系，直接对模型 with_for_update()
      会生成 outer join + FOR UPDATE，PostgreSQL 报 FeatureNotSupported（同 CustomsService._lock）
    - 加锁查询会先 autoflush，refresh 不会丢掉本事务里已改未刷的数据；同一事务重复锁同一行无副作用
    - 等锁期间行被并发删除 → 404（13001）
    - 加锁顺序统一：先父单据（ASN / DN / 调整单 / 盘点单）、后任务行，最后才是库存 / 库位行，避免死锁
    """
    if obj is None:
        return None
    model = type(obj)
    locked = db.session.query(model.id).filter(model.id == obj.id).with_for_update(read=shared).first()
    if locked is None:
        raise NotFoundException(f"{model.__name__} with id {obj.id} not found", 13001)
    db.session.refresh(obj)
    return obj


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
