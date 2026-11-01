"""Today's work uses the warehouse calendar and actual business event timestamps."""
from datetime import timedelta
from sqlalchemy import func, case
from extensions.db import db
from warehouse.common import clock
from warehouse.dn.models import DN
from warehouse.carrier.models import Carrier
from warehouse.stock.models import StockLedger


def daily_work(warehouse, allowed):
    today = clock.local_today(warehouse)
    start = clock.local_midnight(today, warehouse)
    end = clock.local_midnight(today + timedelta(days=1), warehouse)
    result = dict(date=today.isoformat(), timezone=clock.warehouse_timezone_name(warehouse))
    if allowed('dn_read'):
        due = DN.query.filter(DN.warehouse_id == warehouse.id, DN.is_active.is_(True),
                              DN.expected_shipping_date == today, DN.status != 'closed')
        sent = DN.delivered_at.isnot(None)
        groups = db.session.query(DN.carrier_id, Carrier.name, func.count(DN.id),
                                  func.sum(case((sent, 1), else_=0))).outerjoin(Carrier, Carrier.id == DN.carrier_id).filter(
            DN.id.in_(due.with_entities(DN.id))).group_by(DN.carrier_id, Carrier.name).order_by(Carrier.name).all()
        result['dispatch'] = dict(due=sum(row[2] for row in groups), sent=sum(row[3] for row in groups),
            carriers=[dict(carrier_id=row[0], name=row[1], due=row[2], sent=row[3]) for row in groups])
        actual = DN.query.filter(DN.warehouse_id == warehouse.id)
        result['sent_today'] = actual.filter(DN.delivered_at >= start, DN.delivered_at < end).count()
        result['signed_today'] = actual.filter(DN.completed_at >= start, DN.completed_at < end).count()
    if allowed('inventory_read') or allowed('putaway_read'):
        result['putaway_today'] = db.session.query(func.coalesce(func.sum(StockLedger.quantity), 0)).filter(
            StockLedger.warehouse_id == warehouse.id, StockLedger.movement_type == 'putaway',
            StockLedger.quantity > 0, StockLedger.occurred_at >= start, StockLedger.occurred_at < end).scalar()
    return result
