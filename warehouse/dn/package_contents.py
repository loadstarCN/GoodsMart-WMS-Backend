"""Carton contents are explicitly assigned after packing, never guessed from totals."""
from collections import defaultdict
from extensions.db import db
from extensions.transaction import transactional
from extensions.error import BadRequestException, ConflictException
from warehouse.stock.models import DNStockAllocation, GoodsLot


def options(dn):
    allocations = DNStockAllocation.query.filter_by(dn_id=dn.id).all()
    grouped = defaultdict(list)
    for row in allocations:
        grouped[row.goods_id].append(row)
    result = []
    for detail in dn.details:
        if not detail.packed_quantity:
            continue
        rows = [(row.lot_id, row.packed_quantity) for row in grouped[detail.goods_id] if row.packed_quantity]
        if not grouped[detail.goods_id]:
            rows = [(None, detail.packed_quantity)]
        for lot_id, quantity in rows:
            lot = db.session.get(GoodsLot, lot_id) if lot_id else None
            result.append(dict(goods_id=detail.goods_id, goods_code=detail.goods.code, goods_name=detail.goods.name,
                               lot_id=lot_id, lot_no=lot.lot_no if lot else None,
                               expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
                               quantity=quantity))
    return dict(items=result, packages=[row.to_dict() for row in dn.packages], editable=dn.status=='packed')


@transactional
def save(dn, data):
    from .customs_services import CustomsService
    from .carrier_services import CarrierShipmentService
    dn = CustomsService._lock(dn)
    CustomsService._assert_not_locked(dn)
    if dn.status != 'packed':
        raise ConflictException('Complete packing before assigning carton contents.', 16067)
    CarrierShipmentService.assert_packages_editable(dn)
    packages = data.get('packages') if isinstance(data, dict) else None
    expected = {row.package_no:row for row in dn.packages}
    if not expected or not isinstance(packages, list) or len(packages) != len(expected):
        raise BadRequestException('Provide contents for every saved carton.', 16066)
    available = {(row['goods_id'], row['lot_id']):row for row in options(dn)['items']}
    totals, normalized, seen = defaultdict(int), {}, set()
    clearing = all(isinstance(row,dict) and row.get('contents') is None for row in packages)
    for package in packages:
        number = package.get('package_no') if isinstance(package, dict) else None
        if type(number) is not int or number not in expected or number in seen:
            raise BadRequestException('Carton numbers must match the saved packages exactly.', 16066)
        seen.add(number)
        if clearing:
            normalized[number] = None
            continue
        lines = package.get('contents')
        if not isinstance(lines, list) or not lines or len(lines) > 1000:
            raise BadRequestException('Each carton needs at least one content line.', 16066)
        normalized[number], keys = [], set()
        for line in lines:
            if not isinstance(line, dict):
                raise BadRequestException('Invalid carton content.', 16066)
            goods_id, lot_id, quantity = line.get('goods_id'), line.get('lot_id'), line.get('quantity')
            if type(goods_id) is not int or (lot_id is not None and type(lot_id) is not int) or type(quantity) is not int or quantity <= 0:
                raise BadRequestException('Content quantities must be positive whole numbers.', 16066)
            key = (goods_id,lot_id)
            if key not in available or key in keys:
                raise BadRequestException('Select a unique packed product and lot per carton.', 16066)
            keys.add(key)
            totals[key] += quantity
            normalized[number].append(dict(available[key], quantity=quantity))
    if not clearing and any(totals[key] != row['quantity'] for key,row in available.items()):
        raise BadRequestException('Carton totals must exactly match actual packed goods and lots.', 16066)
    changed = any(row.contents != normalized[number] for number,row in expected.items())
    for number,row in expected.items():
        row.contents = normalized[number]
    voided = CustomsService._void_current_documents(dn,'packages_changed') if changed and dn.customs else []
    return dict(packages=[row.to_dict() for row in dn.packages],voided_documents=voided)
