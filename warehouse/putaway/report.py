"""Quantity-weighted timing from recorded receipt completion to each actual putaway."""
from datetime import timedelta
from extensions.db import db
from warehouse.common import clock
from warehouse.asn.models import ASN
from warehouse.location.models import Location
from .models import PutawayRecord, ReceiptPendingStock

BUCKETS = ('day_0_1','day_1_2','day_2_3','day_3_5','day_5_7','day_over_7')


def putaway_timing(warehouse, start, end, supplier_id=None, asn_type=None):
    query = db.session.query(PutawayRecord.id, PutawayRecord.quantity, PutawayRecord.putaway_time,
        ReceiptPendingStock.asn_id, ASN.completed_at).join(Location, Location.id == PutawayRecord.location_id).outerjoin(
        ReceiptPendingStock, ReceiptPendingStock.id == PutawayRecord.receipt_source_id).outerjoin(
        ASN, ASN.id == ReceiptPendingStock.asn_id).filter(Location.warehouse_id == warehouse.id,
        PutawayRecord.putaway_time >= clock.local_midnight(start, warehouse),
        PutawayRecord.putaway_time < clock.local_midnight(end+timedelta(days=1), warehouse))
    if supplier_id:
        query = query.filter(ASN.supplier_id == supplier_id)
    if asn_type:
        query = query.filter(ASN.asn_type == asn_type)
    result = dict(known_quantity=0, unknown_quantity=0, median_days=None, p90_days=None, overdue_quantity=0,
                  buckets={key:dict(quantity=0, records=0, items=[]) for key in BUCKETS})
    weighted = []
    for row in query.order_by(PutawayRecord.putaway_time, PutawayRecord.id):
        if row.completed_at is None or row.putaway_time < row.completed_at:
            result['unknown_quantity'] += row.quantity
            continue
        days = (row.putaway_time-row.completed_at).total_seconds()/86400
        index = next((i for i, limit in enumerate((1,2,3,5,7)) if days <= limit), 5)
        bucket = result['buckets'][BUCKETS[index]]
        bucket['quantity'] += row.quantity
        bucket['records'] += 1
        if len(bucket['items']) < 20:
            bucket['items'].append(dict(id=row.id, asn_id=row.asn_id, quantity=row.quantity, days=round(days,3), at=clock.local_iso(row.putaway_time, warehouse)))
        result['known_quantity'] += row.quantity
        result['overdue_quantity'] += row.quantity if days > 3 else 0
        weighted.append((days, row.quantity))
    if weighted:
        weighted.sort()
        for key, percentile in (('median_days', .5),('p90_days', .9)):
            threshold = result['known_quantity']*percentile
            cumulative = 0
            for days, quantity in weighted:
                cumulative += quantity
                if cumulative >= threshold:
                    result[key] = round(days,6)
                    break
    return result
