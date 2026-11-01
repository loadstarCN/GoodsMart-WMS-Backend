"""Read-only picking worksheet: suggestions are not inventory reservations."""
from collections import defaultdict
from sqlalchemy import func
from extensions.db import db
from warehouse.goods.models import GoodsLocation
from warehouse.location.models import Location
from .models import PickingTask, PickingTaskDetail
from warehouse.stock.lots import assert_shippable, lot_priority
from warehouse.stock.lot_policy import effective_rules
from extensions.error import BadRequestException
from warehouse.location.ordering import location_key


def print_plan(task):
    if task.status == 'completed':
        return dict(mode='record', rows=[dict(goods_id=row.goods_id, goods=row.goods.name,
                    code=row.goods.code, location=row.location.code, location_id=row.location_id, quantity=row.picked_quantity,
                    picking_order=row.location.picking_order,
                    lot_id=row.lot_id, lot_no=row.lot.lot_no if row.lot else None,
                    expiry_date=row.lot.expiry_date.isoformat() if row.lot and row.lot.expiry_date else None)
                    for row in sorted(task.task_details, key=lambda row: (location_key(row.location,task.dn.warehouse), row.id))], shortages=[])
    demand, goods = defaultdict(int), {}
    for detail in task.dn.details:
        demand[detail.goods_id] += max(0, (detail.quantity or 0)-(detail.picked_quantity or 0))
        goods[detail.goods_id] = detail.goods
    for detail in task.task_details:
        if detail.goods_id in demand:
            demand[detail.goods_id] = max(0, demand[detail.goods_id]-(detail.picked_quantity or 0))
    demand = {gid: quantity for gid, quantity in demand.items() if quantity > 0}
    reserved = dict(((gid, lid, lot_id), qty) for gid, lid, lot_id, qty in db.session.query(
        PickingTaskDetail.goods_id, PickingTaskDetail.location_id, PickingTaskDetail.lot_id, func.sum(PickingTaskDetail.picked_quantity)
    ).join(PickingTask).filter(PickingTask.status == 'in_progress', PickingTask.is_active.is_(True),
        PickingTaskDetail.goods_id.in_(list(demand))).group_by(PickingTaskDetail.goods_id, PickingTaskDetail.location_id, PickingTaskDetail.lot_id))
    stock = db.session.query(GoodsLocation).join(
        Location, Location.id == GoodsLocation.location_id).filter(Location.warehouse_id == task.dn.warehouse_id,
        Location.is_active.is_(True), Location.location_type == 'standard', GoodsLocation.quantity > 0,
        GoodsLocation.goods_id.in_(list(demand))).all()
    strategies = {gid: effective_rules(product)['values']['strategy'] for gid, product in goods.items()}
    stock.sort(key=lambda row: (row.goods_id, lot_priority(row.lot, strategies[row.goods_id]), location_key(row.location,task.dn.warehouse), row.id))
    rows = []
    for stock_row in stock:
        gid, lid, lot_id = stock_row.goods_id, stock_row.location_id, stock_row.lot_id
        try:
            assert_shippable(gid, lot_id, task.dn.warehouse_id)
        except BadRequestException:
            continue
        take = min(demand[gid], max(0, stock_row.quantity-(reserved.get((gid,lid,lot_id)) or 0)))
        if take:
            rows.append(dict(goods_id=gid, goods=goods[gid].name, code=goods[gid].code,
                             location=stock_row.location.code, location_id=stock_row.location_id, quantity=take, lot_id=lot_id,
                             picking_order=stock_row.location.picking_order,
                             lot_no=stock_row.lot.lot_no if stock_row.lot else None,
                             expiry_date=stock_row.lot.expiry_date.isoformat() if stock_row.lot and stock_row.lot.expiry_date else None))
            demand[gid] -= take
    shortages = [dict(goods_id=gid, goods=goods[gid].name, code=goods[gid].code, quantity=quantity)
                 for gid, quantity in demand.items() if quantity > 0]
    locations = {row.location_id: row.location for row in stock}
    rows.sort(key=lambda row: location_key(locations[row['location_id']],task.dn.warehouse))
    return dict(mode='suggestion', rows=rows, shortages=shortages)
