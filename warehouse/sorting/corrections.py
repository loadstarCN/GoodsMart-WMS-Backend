"""Corrections to a completed receipt. Original sorting details remain intact."""
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from extensions.transaction import transactional
from system.webhook.services import emit
from warehouse.asn.spec_capture import assert_capturable_on_complete
from warehouse.common import clock, require_non_negative_int, require_positive_int
from warehouse.inventory.services import InventoryService
from warehouse.putaway.models import ReceiptPendingStock
from warehouse.stock.lots import assert_receivable, lot_for_goods
from warehouse.stock.models import GoodsLot, InventoryLotStock
from warehouse.stock.service import Leg, StockOp

from .models import SortingQuantityCorrection
from .services import SortingTaskService

QUANTITIES = ('sorted_quantity', 'damage_quantity', 'rejected_quantity')
STOCK_QUANTITIES = QUANTITIES[:2]
ZERO = dict.fromkeys(QUANTITIES, 0)


def _body(data):
    if not isinstance(data, dict):
        raise BadRequestException('Expected a JSON object')
    return data


def _note(value, field, required=True):
    if not required and value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > 500 or (required and not value.strip()):
        raise BadRequestException('Enter a reason of 1 to 500 characters', field=field)
    return value.strip() or None


def _counts(data):
    data = _body(data)
    if any(data.get(field) is None for field in QUANTITIES):
        raise BadRequestException('All three quantities are required', field='quantities')
    return {field: require_non_negative_int(data[field], field) for field in QUANTITIES}


def _assert_completed(task):
    if task.status != 'completed' or task.asn.status != 'completed':
        raise ConflictException('Only completed receipts can be corrected', 17032)


def _history(task):
    return SortingQuantityCorrection.query.filter_by(task_id=task.id).order_by(SortingQuantityCorrection.id).all()


def _current(task, history):
    counts = {}
    for line in task.task_details:
        values = counts.setdefault((line.goods_id, line.lot_id), ZERO.copy())
        for field in QUANTITIES:
            values[field] += getattr(line, field) or 0
    for correction in history:
        if correction.status == 'approved':
            counts[correction.goods_id, correction.lot_id] = correction.after
    return counts


def _view(correction, lot_no):
    return dict(id=correction.id, goods_id=correction.goods_id, lot_id=correction.lot_id, lot_no=lot_no,
                before=correction.before, after=correction.after, reason=correction.reason, status=correction.status,
                requested_by=correction.requested_by, requester=correction.requester.user_name,
                requested_at=clock.local_iso(correction.requested_at),
                reviewed_by=correction.reviewed_by, reviewer=correction.reviewer.user_name if correction.reviewer else None,
                reviewed_at=clock.local_iso(correction.reviewed_at), review_note=correction.review_note)


def snapshot(task):
    _assert_completed(task)
    history = _history(task)
    counts = _current(task, history)
    lot_ids = {lot_id for _, lot_id in counts if lot_id} | {row.lot_id for row in history if row.lot_id}
    lots = dict(db.session.query(GoodsLot.id, GoodsLot.lot_no).filter(GoodsLot.id.in_(lot_ids)).all())
    return {
        'rows': [dict(goods_id=goods_id, lot_id=lot_id, lot_no=lots.get(lot_id), quantities=quantities)
                 for (goods_id, lot_id), quantities in counts.items()],
        'corrections': [_view(row, lots.get(row.lot_id)) for row in reversed(history)],
    }


def _asn_lines(task, goods_id):
    lines = sorted((line for line in task.asn.details if line.goods_id == goods_id), key=lambda line: line.id)
    if not lines:
        raise BadRequestException('Goods are not part of this ASN', 16057, field='goods_id')
    return lines


def _check_plan(lines, delta):
    quantity = sum((getattr(line, field) or 0) for line in lines for field in QUANTITIES) + sum(delta.values())
    if quantity > sum(line.quantity for line in lines) + SortingTaskService.OVERAGE_TOLERANCE:
        raise BadRequestException('Corrected quantity exceeds the ASN planned quantity', 17033, field='quantities')


def _delta(before, after):
    return {field: after[field] - before[field] for field in QUANTITIES}


@transactional
def submit(task, actor_id, data):
    task = SortingTaskService._lock(task)
    _assert_completed(task)
    data = _body(data)
    goods_id = require_positive_int(data.get('goods_id'), 'goods_id')
    lines = _asn_lines(task, goods_id)
    lot = lot_for_goods(goods_id, data.get('lot_id'))
    lot_id = lot.id if lot else None
    before, after = _counts(data.get('before')), _counts(data.get('after'))
    reason = _note(data.get('reason'), 'reason')
    history = _history(task)
    for previous in reversed(history):
        if (previous.goods_id, previous.lot_id) != (goods_id, lot_id):
            continue
        if previous.status != 'pending':
            continue
        if (previous.requested_by == actor_id and previous.before == before and previous.after == after
                and previous.reason == reason):
            return _view(previous, lot.lot_no if lot else None), 200
        raise ConflictException('This SKU and lot already have a pending correction', 17034)
    current = _current(task, history).get((goods_id, lot_id), ZERO)
    if current != before:
        raise ConflictException('The recorded quantities changed. Reload the sorting task', 17035)
    delta = _delta(before, after)
    if not any(delta.values()):
        raise BadRequestException('Change at least one quantity', field='quantities')
    _check_plan(lines, delta)
    if sum(delta[field] for field in STOCK_QUANTITIES) > 0:
        assert_receivable(goods_id, lot_id, task.asn.warehouse_id)
    correction = SortingQuantityCorrection(task_id=task.id, goods_id=goods_id, lot_id=lot_id, before=before,
                                           after=after, reason=reason, requested_by=actor_id, requested_at=clock.now())
    db.session.add(correction)
    db.session.flush()
    return _view(correction, lot.lot_no if lot else None), 201


def _update_stock(asn, correction, delta, reviewer_id):
    goods_id, lot_id = correction.goods_id, correction.lot_id
    if not asn.putaway_tracked:
        raise ConflictException('This receipt has no traceable pending stock. Verify stock and downstream documents', 17036)
    InventoryService._get_for_update(goods_id, asn.warehouse_id)
    sources = ReceiptPendingStock.query.filter_by(warehouse_id=asn.warehouse_id, goods_id=goods_id,
                                                   lot_id=lot_id).populate_existing().all()
    matching = [row for row in sources if row.asn_id == asn.id]
    if len(matching) > 1 or (not matching and any(correction.before[field] for field in STOCK_QUANTITIES)):
        raise ConflictException('The pending balance of this receipt cannot be verified', 17036)
    stock = InventoryLotStock.query.filter_by(warehouse_id=asn.warehouse_id, goods_id=goods_id,
                                               lot_id=lot_id).populate_existing().first()
    good = stock.sorted_stock - stock.sorted_damage_stock if stock else 0
    damaged = stock.sorted_damage_stock if stock else 0
    if (sum(row.good_remaining for row in sources) > good
            or sum(row.damaged_remaining for row in sources) > damaged):
        raise ConflictException('The pending balance of this receipt cannot be verified', 17036)
    source = matching[0] if matching else ReceiptPendingStock(
        asn_id=asn.id, warehouse_id=asn.warehouse_id, goods_id=goods_id, lot_id=lot_id,
        good_received=0, damaged_received=0, good_remaining=0, damaged_remaining=0,
    )
    good_delta, damaged_delta = (delta[field] for field in STOCK_QUANTITIES)
    if source.good_remaining + good_delta < 0 or source.damaged_remaining + damaged_delta < 0:
        raise ConflictException('The units to be deducted have already moved. Verify putaway and downstream documents',
                                17037, details={'good_remaining': source.good_remaining,
                                                'damaged_remaining': source.damaged_remaining})
    if good_delta + damaged_delta > 0:
        assert_receivable(goods_id, lot_id, asn.warehouse_id)
    op = StockOp(asn.warehouse_id, 'correction', doc_type='sorting_correction', doc_id=correction.id,
                 doc_no=asn.order_number or f'ASN-{asn.id}', operator_id=reviewer_id, reason='receipt_quantity',
                 remark=correction.reason[:255])
    for change, is_damaged in ((good_delta, False), (damaged_delta, True)):
        leg = Leg.pending(lot_id, damaged=is_damaged)
        op.move(goods_id, abs(change), src=leg if change < 0 else None, dst=leg if change >= 0 else None)
    op.commit()
    source.good_received += good_delta
    source.good_remaining += good_delta
    source.damaged_received += damaged_delta
    source.damaged_remaining += damaged_delta
    db.session.add(source)


@transactional
def review(task, correction_id, actor_id, data):
    task = SortingTaskService._lock(task)
    _assert_completed(task)
    data = _body(data)
    decision = data.get('decision')
    if decision not in ('approved', 'rejected'):
        raise BadRequestException('Choose approved or rejected', field='decision')
    note = _note(data.get('note'), 'note', required=decision == 'rejected')
    history = _history(task)
    correction = next((row for row in history if row.id == correction_id), None)
    if correction is None:
        raise NotFoundException('Quantity correction not found', 13001)
    if correction.requested_by == actor_id:
        raise ConflictException('A different user must review this correction', 17038)
    lot = lot_for_goods(correction.goods_id, correction.lot_id)
    if correction.status != 'pending':
        if correction.status == decision and correction.reviewed_by == actor_id and correction.review_note == note:
            return _view(correction, lot.lot_no if lot else None)
        raise ConflictException('This correction has already been reviewed', 17039)
    if decision == 'approved':
        current = _current(task, history).get((correction.goods_id, correction.lot_id), ZERO)
        if current != correction.before:
            raise ConflictException('The recorded quantities changed. Reject this correction and submit it again', 17035)
        lines = _asn_lines(task, correction.goods_id)
        delta = _delta(correction.before, correction.after)
        _check_plan(lines, delta)
        line = lines[0]
        quantities = {field: (getattr(line, field) or 0) + delta[field] for field in QUANTITIES}
        if min(quantities.values()) < 0:
            raise ConflictException('The ASN total cannot be reconciled with the sorting details', 17036)
        if any(delta[field] for field in STOCK_QUANTITIES):
            _update_stock(task.asn, correction, delta, actor_id)
        for field, quantity in quantities.items():
            setattr(line, field, quantity)
        line.actual_quantity = sum(quantities[field] for field in STOCK_QUANTITIES)
        if sum(delta[field] for field in STOCK_QUANTITIES) > 0:
            assert_capturable_on_complete(task.asn)
        emit('asn.quantity_corrected', dict(
            asn_id=task.asn_id, order_number=task.asn.order_number, correction_id=correction.id,
            goods_code=line.goods.code, lot_no=lot.lot_no if lot else None,
            before=correction.before, after=correction.after, quantities=quantities, actual_quantity=line.actual_quantity,
        ), api_key_id=task.asn.api_key_id)
    correction.status = decision
    correction.reviewed_by = actor_id
    correction.reviewed_at = clock.now()
    correction.review_note = note
    db.session.flush()
    db.session.expire(correction, ['reviewer'])
    return _view(correction, lot.lot_no if lot else None)
