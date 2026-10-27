"""Read-only picking worksheet: suggestions are not inventory reservations."""
from collections import defaultdict
from sqlalchemy import func
from extensions.db import db
from warehouse.goods.models import GoodsLocation
from warehouse.location.models import Location
from .models import PickingTask, PickingTaskDetail


def print_plan(task):
    if task.status == 'completed':
        return dict(mode='record', rows=[dict(goods_id=row.goods_id, goods=row.goods.name,
                    code=row.goods.code, location=row.location.code, quantity=row.picked_quantity)
                    for row in sorted(task.task_details, key=lambda row: (row.location.code, row.id))], shortages=[])
    demand, goods = defaultdict(int), {}
    for detail in task.dn.details:
        demand[detail.goods_id] += max(0, (detail.quantity or 0)-(detail.picked_quantity or 0))
        goods[detail.goods_id] = detail.goods
    for detail in task.task_details:
        demand[detail.goods_id] = max(0, demand[detail.goods_id]-(detail.picked_quantity or 0))
    reserved = dict(((gid, lid), qty) for gid, lid, qty in db.session.query(
        PickingTaskDetail.goods_id, PickingTaskDetail.location_id, func.sum(PickingTaskDetail.picked_quantity)
    ).join(PickingTask).filter(PickingTask.status == 'in_progress', PickingTask.is_active.is_(True),
        PickingTaskDetail.goods_id.in_(list(demand))).group_by(PickingTaskDetail.goods_id, PickingTaskDetail.location_id))
    stock = db.session.query(GoodsLocation.goods_id, Location.id, Location.code, GoodsLocation.quantity).join(
        Location, Location.id == GoodsLocation.location_id).filter(Location.warehouse_id == task.dn.warehouse_id,
        Location.is_active.is_(True), Location.location_type == 'standard', GoodsLocation.lot_id.is_(None),
        GoodsLocation.goods_id.in_(list(demand))).order_by(Location.code, GoodsLocation.goods_id)
    rows = []
    for gid, lid, code, quantity in stock:
        take = min(demand[gid], max(0, quantity-(reserved.get((gid,lid)) or 0)))
        if take:
            rows.append(dict(goods_id=gid, goods=goods[gid].name, code=goods[gid].code, location=code, quantity=take))
            demand[gid] -= take
    shortages = [dict(goods_id=gid, goods=goods[gid].name, code=goods[gid].code, quantity=quantity)
                 for gid, quantity in demand.items() if quantity > 0]
    return dict(mode='suggestion', rows=rows, shortages=shortages)
