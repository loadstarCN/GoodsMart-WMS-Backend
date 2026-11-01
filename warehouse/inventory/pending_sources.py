"""Current pending stock split by proven receipt source; residual provenance stays unknown."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import lazyload
from extensions.db import db, as_utc
from extensions.error import BadRequestException
from warehouse.asn.models import ASN
from warehouse.common import clock
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.location.models import Location
from warehouse.putaway.models import ReceiptPendingStock
from warehouse.stock.models import GoodsLot, InventoryLotStock
from warehouse.stock.lot_policy import effective_rules


def pending_source_rows(warehouse_id, *, keyword='', source=None, damaged=False,
                        has_lot=False, overdue_days=None):
    stocks = (db.session.query(InventoryLotStock, Goods, GoodsLot).options(lazyload('*'))
              .join(Goods, Goods.id == InventoryLotStock.goods_id)
              .outerjoin(GoodsLot, GoodsLot.id == InventoryLotStock.lot_id)
              .filter(InventoryLotStock.warehouse_id == warehouse_id,
                      InventoryLotStock.sorted_stock > 0).all())
    if not stocks:
        return []
    goods_ids = {stock.goods_id for stock, _, _ in stocks}
    receipts = defaultdict(list)
    for receipt, completed_at in (db.session.query(ReceiptPendingStock, ASN.completed_at)
            .options(lazyload('*')).outerjoin(ASN, ASN.id == ReceiptPendingStock.asn_id)
            .filter(ReceiptPendingStock.warehouse_id == warehouse_id,
                    ReceiptPendingStock.goods_id.in_(goods_ids),
                    ReceiptPendingStock.good_remaining + ReceiptPendingStock.damaged_remaining > 0)):
        receipts[(receipt.goods_id, receipt.lot_id)].append((receipt, receipt.entered_at or completed_at))
    locations = defaultdict(list)
    for stock, location in (db.session.query(GoodsLocation, Location).options(lazyload('*'))
            .join(Location, Location.id == GoodsLocation.location_id)
            .filter(Location.warehouse_id == warehouse_id, GoodsLocation.goods_id.in_(goods_ids),
                    GoodsLocation.quantity > 0).order_by(Location.code, Location.id)):
        locations[(stock.goods_id, stock.lot_id)].append(dict(id=location.id, code=location.code,
            location_type=location.location_type, quantity=stock.quantity))

    rules = {goods.id: effective_rules(goods)['values'] for _, goods, _ in stocks}
    today = clock.local_today(warehouse_id)
    tracked = {goods.id for _, goods, lot in stocks if lot is not None or rules[goods.id]['mode'] != 'none'}
    tracked.update(row[0] for row in db.session.query(GoodsLot.goods_id).filter(GoodsLot.goods_id.in_(goods_ids)).distinct())
    rows = []
    for stock, goods, lot in stocks:
        key = (stock.goods_id, stock.lot_id)
        rule = rules[goods.id]
        remaining = (lot.expiry_date - today).days if lot and lot.expiry_date else None
        expiry_state = ('unknown' if remaining is None else 'below_acceptance' if remaining < rule['receive_days']
                        else 'warning' if remaining <= rule['warning_days'] else 'normal')
        base = dict(goods_id=goods.id, warehouse_id=warehouse_id,
                    goods=dict(id=goods.id, name=goods.name, code=goods.code),
                    lot_id=stock.lot_id, lot_no=lot.lot_no if lot else None,
                    expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
                    tracks_lots=goods.id in tracked, remaining_days=remaining, expiry_status=expiry_state,
                    accept_days=rule['receive_days'], warning_days=rule['warning_days'],
                    locations=locations[key][:3], location_count=len(locations[key]))
        known_good = known_bad = 0
        for receipt, at in receipts[key]:
            known_good += receipt.good_remaining
            known_bad += receipt.damaged_remaining
            rows.append(dict(base, receipt_source_id=receipt.id, asn_id=receipt.asn_id,
                             dn_id=receipt.dn_id, source_type=receipt.source_type, pending_since=as_utc(at).isoformat() if at else None,
                             sorted_stock=receipt.good_remaining + receipt.damaged_remaining,
                             sorted_damage_stock=receipt.damaged_remaining))
        unknown_good = stock.sorted_stock - stock.sorted_damage_stock - known_good
        unknown_bad = stock.sorted_damage_stock - known_bad
        if min(unknown_good, unknown_bad) < 0:
            raise BadRequestException('Receipt source balance is inconsistent; reconcile before putaway', 16173)
        if unknown_good + unknown_bad:
            rows.append(dict(base, receipt_source_id=0, asn_id=None, dn_id=None, source_type='unknown',
                             pending_since=None, sorted_stock=unknown_good + unknown_bad,
                             sorted_damage_stock=unknown_bad))

    needle = keyword.casefold().strip()
    cutoff = clock.now() - timedelta(days=overdue_days) if overdue_days is not None else None
    result = []
    for row in rows:
        if source and row['source_type'] != source:
            continue
        if damaged and not row['sorted_damage_stock']:
            continue
        if has_lot and row['lot_id'] is None:
            continue
        if cutoff and (row['pending_since'] is None or datetime.fromisoformat(row['pending_since']) >= cutoff):
            continue
        if needle and needle not in ' '.join(str(value or '') for value in (
                row['goods']['name'], row['goods']['code'], row['lot_no'],
                'ASN-' + str(row['asn_id']) if row['asn_id'] else '', 'DN-' + str(row['dn_id']) if row['dn_id'] else '')).casefold():
            continue
        row['id'] = f"{warehouse_id}:{row['goods_id']}:{row['lot_id'] or 0}:{row['receipt_source_id']}"
        row['good_quantity'] = row['sorted_stock'] - row['sorted_damage_stock']
        result.append(row)
    last = datetime.max.replace(tzinfo=timezone.utc)
    result.sort(key=lambda row: (datetime.fromisoformat(row['pending_since']) if row['pending_since'] else last,
                                row['goods']['code'], row['lot_id'] or 0, row['receipt_source_id']))
    return result
