"""行锁辅助：库位库存（GoodsLocation）的读-改-写必须在 SELECT ... FOR UPDATE 下进行，
否则多 worker 部署时两次并发下架会互相覆盖（丢更新 / 超卖）。

SQLite（测试环境）会忽略 FOR UPDATE，PostgreSQL 生产环境生效。
写法与 picking/services.py::_assert_location_stock 一致：lazyload('*') 抑制 joined 关系，
避免 PostgreSQL 上 "FOR UPDATE cannot be applied to the nullable side of an outer join"。

lock_row：单据 / 任务的状态转换、批次追加前锁单据行并重读状态，防止并发重复提交重复记账。

全系统统一的加锁顺序（B-103）：
    父单据（ASN / DN / 调整单 / 盘点单）→ 任务行 → 库存行 Inventory（按 (goods_id, warehouse_id) 升序）
    → 库位库存行 GoodsLocation（按 (goods_id, location_id) 升序）
一次操作涉及多个商品 / 库位时，先用 lock_inventory_rows / lock_inventory_pairs / lock_goods_location_keys
按这个顺序把要动的行一次锁齐，之后逐行的 InventoryService._get_for_update / lock_goods_location 只是重入已持有的锁。
两个请求商品顺序相反时不再互相等待（PostgreSQL 检测到死锁会中止其中一个 → 500）。
库位行（Location）只在上架 / 移库 / 正向调整时加共享锁；持排他锁的一方（停用 / 修改 / 删除库位）不锁库存行，不参与这个顺序。
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import lazyload

from extensions.db import db
from extensions.error import NotFoundException


def _order_part(value):
    """排序键的一段：None 排最前；整数按数值；其它（理论上调用前已校验过）按字符串，避免混合类型比较报 TypeError"""
    if value is None:
        return (0, 0, '')
    if isinstance(value, int) and not isinstance(value, bool):
        return (1, value, '')
    return (2, 0, str(value))


def lock_order_key(*parts) -> tuple:
    """多行加锁的统一排序键，如 lock_order_key(goods_id, location_id)"""
    return tuple(_order_part(part) for part in parts)


def _is_row_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def lock_inventory_pairs(pairs) -> dict:
    """按 (goods_id, warehouse_id) 升序逐行 SELECT ... FOR UPDATE 锁住库存行，返回 {(goods_id, warehouse_id): Inventory}。

    - 多商品操作在动任何库存行之前调用（见模块说明的加锁顺序）
    - 不存在的行跳过（之后逐行操作照旧报 43001 或新建）；重复项、非整数 id（留给后面的逐行校验报错）忽略
    """
    from warehouse.inventory.models import Inventory
    keys = sorted(
        {(g, w) for g, w in pairs if _is_row_id(g) and _is_row_id(w)},
        key=lambda key: lock_order_key(*key),
    )
    locked = {}
    for goods_id, warehouse_id in keys:
        row = (
            Inventory.query
            .options(lazyload('*'))
            .filter_by(goods_id=goods_id, warehouse_id=warehouse_id)
            .with_for_update()
            .first()
        )
        if row is not None:
            locked[(goods_id, warehouse_id)] = row
    return locked


def lock_inventory_rows(goods_ids, warehouse_id) -> dict:
    """同一仓库下多个商品的库存行，按 goods_id 升序加锁（lock_inventory_pairs 的简写）"""
    return lock_inventory_pairs((goods_id, warehouse_id) for goods_id in goods_ids)


def lock_goods_location_keys(keys) -> dict:
    """按 (goods_id, location_id) 升序逐行锁住库位库存行，返回 {(goods_id, location_id): GoodsLocation | None}。
    批量上架 / 下架 / 移库在逐条处理前调用；须在 lock_inventory_* 之后（库存行先于库位库存行）"""
    ordered = sorted(
        {(g, l) for g, l in keys if _is_row_id(g) and _is_row_id(l)},
        key=lambda key: lock_order_key(*key),
    )
    return {key: lock_goods_location(*key) for key in ordered}


def lock_stock_for_bulk(entries):
    """批量上架 / 下架 / 移库逐条处理前，按统一顺序一次锁齐要动的行（B-103）：
    先库存行（按 (goods_id, 库位所在仓库) 升序），再库位库存行（按 (goods_id, location_id) 升序）。
    entries：可迭代的 (goods_id, location_id)。逐条处理仍按请求顺序进行（返回的记录顺序不变），只是重入已持有的锁；
    非整数 / 不存在的 id 跳过，逐条处理时照旧报错。"""
    from warehouse.location.models import Location
    keys = {(g, l) for g, l in entries if _is_row_id(g) and _is_row_id(l)}
    pairs = set()
    for goods_id, location_id in keys:
        location = db.session.get(Location, location_id)
        if location is not None:
            pairs.add((goods_id, location.warehouse_id))
    lock_inventory_pairs(pairs)
    lock_goods_location_keys(keys)


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
    - 加锁顺序统一：先父单据（ASN / DN / 调整单 / 盘点单）、后任务行，最后才是库存 / 库位库存行（见模块说明），避免死锁
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


def lock_or_create_goods_location(goods_id: int, location_id: int):
    """加锁读取库位库存行；不存在则新建一行（quantity=0，由调用方加量），返回 (record, created)。

    新建放在 savepoint 里（B-107）：同一 (goods_id, location_id) 第一次被并发上架 / 移入时，后到的一方
    撞唯一约束 uix_goods_location → 只回滚 savepoint，再加锁读取对方已提交的行，不再 500。
    PostgreSQL 上后到的 INSERT 会等先到的事务结束：对方提交 → 唯一键冲突走这里；对方回滚 → 本次插入成功。
    """
    from warehouse.goods.models import GoodsLocation
    record = lock_goods_location(goods_id, location_id)
    if record is not None:
        return record, False
    try:
        with db.session.begin_nested():
            record = GoodsLocation(goods_id=goods_id, location_id=location_id, quantity=0)
            db.session.add(record)
            db.session.flush()
    except IntegrityError:
        record = lock_goods_location(goods_id, location_id)
        if record is None:
            raise
        return record, False
    return record, True


def lock_goods_locations_for_goods(goods_id: int, warehouse_id: int):
    """加锁读取某商品在某仓库下的全部库位记录（重算 / 盘点调整时用），按 location_id 升序加锁。"""
    from warehouse.goods.models import GoodsLocation
    from warehouse.location.models import Location
    return (
        GoodsLocation.query
        .options(lazyload('*'))
        .join(Location, GoodsLocation.location_id == Location.id)
        .filter(GoodsLocation.goods_id == goods_id, Location.warehouse_id == warehouse_id)
        .order_by(GoodsLocation.location_id)
        .with_for_update(of=GoodsLocation)
        .all()
    )
