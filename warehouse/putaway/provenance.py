"""Explicit receipt selection for putaway; legacy/removal stock retains unknown origin."""
from extensions.db import db
from extensions.error import BadRequestException
from warehouse.common import require_positive_int
from warehouse.inventory.services import InventoryService
from warehouse.stock.models import InventoryLotStock
from .models import ReceiptPendingStock


def sources(goods_id, warehouse_id, lot_id):
    return ReceiptPendingStock.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id, lot_id=lot_id).filter(
        ReceiptPendingStock.good_remaining+ReceiptPendingStock.damaged_remaining > 0).order_by(ReceiptPendingStock.id).all()


def consume(goods_id, location, lot_id, quantity, requested=None):
    # Same per-goods inventory lock as StockService and ASN completion serializes source changes.
    InventoryService._get_for_update(goods_id, location.warehouse_id)
    stock = InventoryLotStock.query.filter_by(goods_id=goods_id, warehouse_id=location.warehouse_id, lot_id=lot_id).first()
    total, damaged = (stock.sorted_stock, stock.sorted_damage_stock) if stock else (0, 0)
    if quantity > total:
        raise BadRequestException('Not enough sort stock.', 15007)
    if location.location_type != 'damaged' and quantity > total-damaged:
        raise BadRequestException('Damaged pending stock requires a damaged location', 15016,
            details=dict(goods_id=goods_id, location_type=location.location_type, quantity=quantity,
                         good_pending=total-damaged, damaged_pending=damaged))
    rows = sources(goods_id, location.warehouse_id, lot_id)
    unknown_good = total-damaged-sum(row.good_remaining for row in rows)
    unknown_bad = damaged-sum(row.damaged_remaining for row in rows)
    if min(unknown_good, unknown_bad) < 0:
        raise BadRequestException('Receipt source balance is inconsistent; reconcile before putaway', 16173)
    source = None
    if requested is not None and requested != 0:
        source_id = require_positive_int(requested, 'receipt_source_id')
        source = next((row for row in rows if row.id == source_id), None)
        if source is None:
            raise BadRequestException('Receipt source is unavailable in this goods, lot and warehouse scope', field='receipt_source_id')
    elif requested is None and len(rows) == 1 and unknown_good+unknown_bad == 0:
        # A single proven source is unambiguous; this is not historical attribution.
        source = rows[0]
    good, bad = (source.good_remaining, source.damaged_remaining) if source else (unknown_good, unknown_bad)
    take_bad = min(quantity, bad) if location.location_type == 'damaged' else 0
    take_good = quantity-take_bad
    if take_good > good:
        if not source and rows:
            raise BadRequestException('Select the receipt source before putting away this stock', field='receipt_source_id')
        if location.location_type != 'damaged' and quantity <= good+bad:
            raise BadRequestException('Damaged pending stock requires a damaged location', 15016,
                details=dict(goods_id=goods_id, location_type=location.location_type, quantity=quantity,
                             good_pending=good, damaged_pending=bad))
        raise BadRequestException('Not enough pending stock of this receipt and condition', 15007)
    if source:
        source.good_remaining -= take_good
        source.damaged_remaining -= take_bad
    return source, take_good, take_bad
