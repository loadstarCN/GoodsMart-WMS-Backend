"""Permission-scoped upcoming work in the selected warehouse's local calendar."""
from datetime import timedelta
from sqlalchemy import func
from extensions.db import db
from warehouse.common import clock
from warehouse.asn.models import ASN
from warehouse.cyclecount.models import CycleCountTask
from warehouse.dn.models import CarrierPickup


def upcoming_schedule(warehouse, allowed):
    start = clock.local_today(warehouse)
    end = start + timedelta(days=7)
    days = {start + timedelta(days=i): {} for i in range(7)}
    if allowed('asn_read'):
        for day, count in db.session.query(ASN.expected_arrival_date, func.count(ASN.id)).filter(
                ASN.warehouse_id == warehouse.id, ASN.is_active.is_(True), ASN.status == 'pending',
                ASN.expected_arrival_date >= start, ASN.expected_arrival_date < end
        ).group_by(ASN.expected_arrival_date):
            days[day]['asn'] = count
    if allowed('cycle_count_read'):
        query = db.session.query(CycleCountTask.scheduled_date).filter(
            CycleCountTask.warehouse_id == warehouse.id, CycleCountTask.is_active.is_(True),
            CycleCountTask.status == 'pending',
            CycleCountTask.scheduled_date >= clock.local_midnight(start, warehouse),
            CycleCountTask.scheduled_date < clock.local_midnight(end, warehouse))
        for scheduled, in query:
            day = clock.local_date(scheduled, warehouse)
            days[day]['cyclecount'] = days[day].get('cyclecount', 0) + 1
    if any(allowed(key) for key in ('dn_read', 'packing_read', 'delivery_read')):
        for day, count in db.session.query(CarrierPickup.pickup_date, func.count(CarrierPickup.id)).filter(
                CarrierPickup.warehouse_id == warehouse.id, CarrierPickup.status == 'scheduled',
                CarrierPickup.pickup_date >= start, CarrierPickup.pickup_date < end
        ).group_by(CarrierPickup.pickup_date):
            days[day]['pickup'] = count
    return {'start_date': start.isoformat(), 'end_date': (end-timedelta(days=1)).isoformat(),
            'timezone': clock.warehouse_timezone_name(warehouse),
            'items': [{'date': day.isoformat(), **counts} for day, counts in days.items()]}
