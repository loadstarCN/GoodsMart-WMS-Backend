"""Explicit physical lot selection when only part of mixed picked lots is packed."""
from extensions import db
from extensions.error import BadRequestException
from warehouse.stock.models import DNStockAllocation, GoodsLot
from warehouse.goods.models import Goods


def options(task):
    rows=DNStockAllocation.query.filter_by(dn_id=task.dn_id).order_by(DNStockAllocation.goods_id,DNStockAllocation.id).all()
    packed={}
    for detail in task.task_details:
        packed[detail.goods_id]=packed.get(detail.goods_id,0)+(detail.packed_quantity or 0)
    grouped={}
    for row in rows:
        grouped.setdefault(row.goods_id,[]).append(row)
    items=[]
    required=False
    for goods_id,allocations in grouped.items():
        goods=db.session.get(Goods,goods_id)
        picked=sum(row.picked_quantity for row in allocations)
        quantity=packed.get(goods_id,0)
        mixed=len([row for row in allocations if row.picked_quantity>0])>1 and 0<quantity<picked
        required=required or mixed
        for row in allocations:
            lot=db.session.get(GoodsLot,row.lot_id) if row.lot_id else None
            items.append(dict(id=row.id,goods_id=goods_id,goods_code=goods.code,goods_name=goods.name,
                lot_id=row.lot_id,lot_no=lot.lot_no if lot else None,picked_quantity=row.picked_quantity,
                goods_packed_quantity=quantity,requires_selection=mixed,
                packed_quantity=row.packed_quantity if task.status=='completed' else
                    None if mixed else row.picked_quantity if quantity==picked else quantity))
    return dict(items=items,required=required)


def validate(task, choices):
    data=options(task)
    if choices is None:
        if data['required']:
            raise BadRequestException('Select the actual packed lots before completing a partial mixed-lot pack',16172,field='lot_allocations')
        return None
    if not isinstance(choices,list):
        raise BadRequestException('Expected lot allocations',16172,field='lot_allocations')
    expected={row['id']:row for row in data['items']}
    selected={}
    for choice in choices:
        if not isinstance(choice,dict) or type(choice.get('id')) is not int or type(choice.get('quantity')) is not int:
            raise BadRequestException('Allocation ID and whole quantity are required',16172,field='lot_allocations')
        row=expected.get(choice['id'])
        if row is None or choice['id'] in selected or not 0<=choice['quantity']<=row['picked_quantity']:
            raise BadRequestException('Invalid or duplicate packed lot allocation',16172,field='lot_allocations')
        selected[choice['id']]=choice['quantity']
    if set(selected)!=set(expected):
        raise BadRequestException('Confirm every picked lot, including zero packed quantities',16172,field='lot_allocations')
    for goods_id in {row['goods_id'] for row in expected.values()}:
        rows=[row for row in expected.values() if row['goods_id']==goods_id]
        if sum(selected[row['id']] for row in rows)!=rows[0]['goods_packed_quantity']:
            raise BadRequestException('Packed lot totals must equal the saved packing quantity',16172,field='lot_allocations')
    return selected
