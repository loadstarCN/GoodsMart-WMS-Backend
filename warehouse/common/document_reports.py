"""Inbound scheduled-date and outbound handover-date cohorts, before pagination."""
from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict

from flask import request
from sqlalchemy import func, literal, case

from extensions.db import db
from extensions.error import BadRequestException
from warehouse.common import clock, require_warehouse_scope


def report_period(warehouse):
    today = clock.local_today(warehouse)
    try:
        end = date.fromisoformat(request.args.get('end_date') or today.isoformat())
        start = date.fromisoformat(request.args.get('start_date') or end.replace(day=1).isoformat())
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
    except (ValueError, TypeError):
        raise BadRequestException('Invalid report date or pagination', 14009)
    if end < start or (end - start).days > 365 or page < 1 or not 1 <= per_page <= 100:
        raise BadRequestException('Report range must be 1–366 days; page size 1–100', 14009)
    return start, end, page, per_page


def duration_hours(start, end):
    if start is None or end is None or end < start:
        return None
    return (end-start).total_seconds()/3600


def duration_summary(values):
    values = sorted(value for value in values if value is not None)
    def quantile(fraction):
        if not values:
            return None
        index = (len(values)-1)*fraction
        lower = int(index)
        upper = min(lower+1, len(values)-1)
        return values[lower]+(values[upper]-values[lower])*(index-lower)
    return dict(count=len(values), median_hours=quantile(.5), p90_hours=quantile(.9))


def document_report(kind, warehouse, export=False):
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.dn.models import DN, DNDetail
    require_warehouse_scope(warehouse.id, 'Warehouse')
    inbound = kind == 'inbound'
    model, detail = (ASN, ASNDetail) if inbound else (DN, DNDetail)
    foreign_key = detail.asn_id if inbound else detail.dn_id
    planned = model.expected_arrival_date if inbound else model.expected_shipping_date
    actual = model.received_at if inbound else model.delivered_at
    quantity = detail.actual_quantity if inbound else detail.delivered_quantity
    sums = db.session.query(
        foreign_key.label('document_id'), func.sum(detail.quantity).label('expected'),
        func.sum(quantity).label('actual'),
        func.sum(func.abs(func.coalesce(quantity, 0) - detail.quantity)).label('difference'),
        func.sum(case((detail.quantity > func.coalesce(quantity, 0), detail.quantity-func.coalesce(quantity, 0)), else_=0)).label('short'),
        func.sum(case((func.coalesce(quantity, 0) > detail.quantity, func.coalesce(quantity, 0)-detail.quantity), else_=0)).label('over'),
        (func.sum(detail.damage_quantity) if inbound else func.sum(literal(0))).label('damage'),
    ).group_by(foreign_key).subquery()
    base = db.session.query(model.id, planned.label('planned'), actual.label('actual_at'),
                            model.status, sums.c.expected, sums.c.actual,
                            sums.c.difference, sums.c.damage, sums.c.short, sums.c.over).outerjoin(sums, sums.c.document_id == model.id).filter(
        model.warehouse_id == warehouse.id, model.is_active.is_(True), model.status != 'closed')
    dimension = request.args.get('distribution_dimension')
    dimension_value = request.args.get('distribution_value')
    if dimension is not None and (inbound or dimension not in ('carrier', 'country', 'source') or dimension_value is None):
        raise BadRequestException('Invalid distribution filter', 14009)
    if not inbound:
        from warehouse.picking.models import PickingTask
        from warehouse.packing.models import PackingTask
        def finished_tasks(task):
            return db.session.query(task.dn_id.label('dn_id'), func.max(task.completed_at).label('finished')).filter(
                task.is_active.is_(True)).group_by(task.dn_id).having(func.sum(case(
                    ((task.status == 'completed') & task.completed_at.isnot(None), 0), else_=1)) == 0).subquery()
        picking, packing = finished_tasks(PickingTask), finished_tasks(PackingTask)
        base = base.add_columns(model.created_at, picking.c.finished.label('picked'), packing.c.finished.label('packed')).outerjoin(
            picking, picking.c.dn_id == model.id).outerjoin(packing, packing.c.dn_id == model.id)
        from warehouse.carrier.models import Carrier
        from system.third_party.models import APIKey
        from warehouse.dn.models import DNCarrierShipment as CarrierShipment
        charges = db.session.query(CarrierShipment.dn_id.label('charge_dn_id'),
            CarrierShipment.net_charge.label('net_charge'), CarrierShipment.currency.label('charge_currency'),
            CarrierShipment.carrier_id.label('charge_carrier_id'), Carrier.name.label('charge_carrier_name')).outerjoin(
            Carrier, Carrier.id == CarrierShipment.carrier_id).filter(CarrierShipment.status == 'active').subquery()
        base = base.add_columns(charges.c.net_charge, charges.c.charge_currency,
            charges.c.charge_carrier_id, charges.c.charge_carrier_name).outerjoin(charges, charges.c.charge_dn_id == model.id)
        base = base.add_columns(model.carrier_id, Carrier.name.label('carrier_name'), model.ship_to,
                                model.api_key_id, APIKey.system_name.label('source_name')).outerjoin(
            Carrier, Carrier.id == model.carrier_id).outerjoin(APIKey, APIKey.id == model.api_key_id)
    party_key = 'supplier_id' if inbound else 'recipient_id'
    if request.args.get(party_key):
        try:
            party_id = int(request.args[party_key])
        except ValueError:
            raise BadRequestException('Invalid party ID', 14009)
        if party_id <= 0:
            raise BadRequestException('Invalid party ID', 14009)
        base = base.filter(getattr(model, party_key) == party_id)
    type_key = 'asn_type' if inbound else 'dn_type'
    if request.args.get(type_key):
        allowed = model.ASN_TYPES if inbound else model.DN_TYPES
        if request.args[type_key] not in allowed:
            raise BadRequestException('Invalid document type', 14009)
        base = base.filter(getattr(model, type_key) == request.args[type_key])
    start, end, page, per_page = report_period(warehouse)
    today = clock.local_today(warehouse)

    def cohort(first, last):
        summary = dict(documents=0, received=0, expected=0, actual=0, damage=0,
                       difference=0, received_expected=0, due=0, on_time=0)
        items = []
        slowest = None
        freight = {}
        receipt_differences = dict(documents=0, received=0, expected=0,
            types={key:dict(quantity=0, documents=0) for key in ('short','over','damage')}, items=[], rejected=None)
        missing_freight = 0
        durations = {key: [] for key in ('pick', 'pack', 'handover', 'total')}
        distribution = {key: {} for key in ('carrier', 'country', 'source')}
        daily = defaultdict(lambda: dict(documents=0, actual=0, on_time=0, late=0, unscheduled=0, units=[]))
        # Scalar rows avoid eager-loading every document relationship.
        period_query = (base.filter(planned >= first, planned <= last) if inbound else
                        base.filter(actual >= clock.local_midnight(first, warehouse),
                                    actual < clock.local_midnight(last + timedelta(days=1), warehouse)))
        for row in period_query.order_by(planned, model.id).yield_per(500):
            if not inbound:
                country = str((row.ship_to or {}).get('country') or '').strip().upper()
                groups = {'carrier': (str(row.carrier_id) if row.carrier_id else 'none', row.carrier_name),
                          'country': (country or 'none', country or None),
                          'source': (row.source_name or 'none', row.source_name)}
                if dimension and groups[dimension][0] != dimension_value:
                    continue
                if row.net_charge is not None and row.charge_currency:
                    freight_key = (row.charge_carrier_id, row.charge_currency.upper())
                    charge = freight.setdefault(freight_key, dict(carrier_id=row.charge_carrier_id,
                        carrier=row.charge_carrier_name, currency=row.charge_currency.upper(), count=0, total=0))
                    charge['count'] += 1
                    charge['total'] += row.net_charge
                else:
                    missing_freight += 1
                for key, (value, label) in groups.items():
                    entry = distribution[key].setdefault(value, dict(value=value, label=label, count=0))
                    entry['count'] += 1
            lead = None
            if not inbound:
                def within_period(value):
                    return value if value is not None and row.created_at is not None and row.created_at <= value <= row.actual_at else None
                picked, packed = within_period(row.picked), within_period(row.packed)
                lead = dict(pick=duration_hours(row.created_at, picked), pack=duration_hours(picked, packed),
                            handover=duration_hours(packed, row.actual_at), total=duration_hours(row.created_at, row.actual_at))
                if lead['total'] is not None and (slowest is None or lead['total'] > slowest['total_hours']):
                    known = {key: lead[key] for key in ('pick', 'pack', 'handover') if lead[key] is not None}
                    slowest = dict(id=row.id, total_hours=lead['total'],
                                   stage=max(known, key=known.get) if known else None,
                                   stage_hours=max(known.values()) if known else None)
                for key, value in lead.items():
                    durations[key].append(value)
            actual_day = clock.local_date(row.actual_at, warehouse)
            received = actual_day is not None
            due = (received or row.planned < today) if inbound else row.planned is not None
            on_time = received and row.planned is not None and actual_day <= row.planned
            bucket_day = row.planned if inbound else actual_day
            bucket = daily[bucket_day.isoformat()]
            # Bounded drill-down tiles, independent of the detail-table page.
            if not inbound and first == start and len(bucket['units']) < 20:
                bucket['units'].append(dict(id=row.id, actual=row.actual or 0,
                                           planned=row.planned.isoformat() if row.planned else None,
                                           timing='unscheduled' if row.planned is None else 'on_time' if on_time else 'late'))
            bucket['documents'] += 1
            bucket['actual'] += (row.actual or 0) if received else 0
            if received:
                bucket['unscheduled' if row.planned is None else 'on_time' if on_time else 'late'] += 1
            summary['documents'] += 1
            summary['expected'] += row.expected or 0
            summary['received'] += int(received)
            summary['due'] += int(due)
            summary['on_time'] += int(on_time)
            if received:
                for key in ('actual', 'damage', 'difference'):
                    summary[key] += getattr(row, key) or 0
                summary['received_expected'] += row.expected or 0
                if inbound:
                    receipt_differences['received'] += 1
                    receipt_differences['expected'] += row.expected or 0
                    values = {key: getattr(row,key) or 0 for key in ('short','over','damage')}
                    if any(values.values()):
                        receipt_differences['documents'] += 1
                        if len(receipt_differences['items']) < 20:
                            receipt_differences['items'].append(dict(id=row.id, planned=row.planned.isoformat(), **values))
                    for key,value in values.items():
                        receipt_differences['types'][key]['quantity'] += value
                        receipt_differences['types'][key]['documents'] += int(value > 0)
            if first == start and (export or (page - 1) * per_page <= summary['documents'] - 1 < page * per_page):
                if export and len(items)>=50000:
                    raise BadRequestException('Too many report rows to export', 14009)
                items.append(dict(id=row.id, planned=row.planned.isoformat() if row.planned else None,
                                  actual_date=actual_day.isoformat() if actual_day else None,
                                  status=row.status, expected=row.expected or 0,
                                  actual=(row.actual or 0) if received else None,
                                  damage=(row.damage or 0) if received else None,
                                  difference=(row.difference or 0) if received else None,
                                  on_time=bool(on_time) if received else None, lead_hours=lead))
        summary['on_time_rate'] = summary['on_time'] / summary['due'] if summary['due'] else None
        summary['difference_rate'] = ((summary['difference'] + summary['damage']) / summary['received_expected']
                                      if summary['received_expected'] else None)
        return summary, items, [dict(date=key, **value) for key,value in sorted(daily.items())], distribution, {key: duration_summary(values) for key, values in durations.items()}, slowest, dict(items=[{**row, 'total':float(row['total']), 'average':float(row['total']/row['count'])} for row in freight.values()], missing=missing_freight), receipt_differences

    summary, items, daily, distribution, lead_time, slowest, freight, receipt_differences = cohort(start, end)
    # Match the design's previous-month corresponding calendar dates (10/1–25 -> 9/1–25).
    # Clamp days that do not exist in the previous month, including leap February.
    def previous_month(value):
        year, month = (value.year - 1, 12) if value.month == 1 else (value.year, value.month - 1)
        return date(year, month, min(value.day, monthrange(year, month)[1]))
    previous_start, previous_end = previous_month(start), previous_month(end)
    previous, _, _, prior_distribution, previous_lead_time, _, previous_freight, previous_receipt_differences = cohort(previous_start, previous_end)
    distributions = {}
    if not inbound:
        for key in distribution:
            values = set(distribution[key]) | set(prior_distribution[key])
            distributions[key] = sorted([
                dict(value=value, label=(distribution[key].get(value) or prior_distribution[key][value])['label'],
                     count=distribution[key].get(value, {}).get('count', 0),
                     previous=prior_distribution[key].get(value, {}).get('count', 0))
                for value in values], key=lambda row: (-row['count'], -row['previous'], row['value']))
    monthly_on_time = []
    monthly_late = []
    if not inbound and not export:
        asof = min(end, today)
        months = [asof.replace(day=1)]
        for _ in range(5):
            months.insert(0, previous_month(months[0]))
        buckets = {month.isoformat()[:7]: dict(month=month.isoformat()[:7], due=0, on_time=0,
                   partial=month==asof.replace(day=1)) for month in months}
        history = base.filter(actual >= clock.local_midnight(months[0], warehouse),
                              actual < clock.local_midnight(asof+timedelta(days=1), warehouse))
        for row in history.order_by(actual, model.id).yield_per(500):
            country = str((row.ship_to or {}).get('country') or '').strip().upper()
            groups = dict(carrier=str(row.carrier_id) if row.carrier_id else 'none',
                          country=country or 'none', source=row.source_name or 'none')
            if dimension and groups[dimension] != dimension_value:
                continue
            if row.planned is None:
                continue
            actual_day = clock.local_date(row.actual_at, warehouse)
            month = actual_day.isoformat()[:7]
            bucket = buckets[month]
            bucket['due'] += 1
            bucket['on_time'] += int(actual_day <= row.planned)
            if actual_day > row.planned and month == asof.isoformat()[:7] and len(monthly_late)<20:
                monthly_late.append(dict(id=row.id, planned=row.planned.isoformat(), actual_date=actual_day.isoformat(),
                                         days_late=(actual_day-row.planned).days, carrier=row.carrier_name))
        monthly_on_time = [dict(**bucket, rate=bucket['on_time']/bucket['due'] if bucket['due'] else None)
                           for bucket in buckets.values()]
    spec_coverage = None
    if inbound and not export:
        from warehouse.goods.models import Goods
        # The specification card uses actual receipts in the period, not the planned-arrival cohort.
        received_ids = base.with_entities(model.id).filter(actual >= clock.local_midnight(start, warehouse),
            actual < clock.local_midnight(end+timedelta(days=1), warehouse)).subquery()
        specs = db.session.query(Goods.id, Goods.code, Goods.name, Goods.weight, Goods.length, Goods.width, Goods.height,
            func.sum(ASNDetail.actual_quantity).label('quantity')).join(ASNDetail, ASNDetail.goods_id == Goods.id).filter(
            ASNDetail.asn_id.in_(db.session.query(received_ids.c.id)), ASNDetail.actual_quantity > 0).group_by(
            Goods.id, Goods.code, Goods.name, Goods.weight, Goods.length, Goods.width, Goods.height)
        spec_coverage = dict(total=0, weight=0, dimensions=0, complete=0, missing_total=0, missing=[])
        missing = []
        for row in specs:
            weight = row.weight is not None and row.weight > 0
            dimensions = all(value is not None and value > 0 for value in (row.length,row.width,row.height))
            spec_coverage['total'] += 1
            spec_coverage['weight'] += int(weight)
            spec_coverage['dimensions'] += int(dimensions)
            spec_coverage['complete'] += int(weight and dimensions)
            if not (weight and dimensions):
                missing.append(dict(id=row.id,code=row.code,name=row.name,quantity=int(row.quantity),
                                    missing=[key for key,available in [('weight',weight),('dimensions',dimensions)] if not available]))
        spec_coverage['missing_total'] = len(missing)
        spec_coverage['missing'] = sorted(missing,key=lambda row:(-row['quantity'],row['id']))[:10]
    return dict(start_date=start.isoformat(), end_date=end.isoformat(), today=today.isoformat(),
                timezone=clock.warehouse_timezone_name(warehouse), summary=summary, previous=previous,
                previous_start=previous_start.isoformat(), previous_end=previous_end.isoformat(),
                receipt_differences=receipt_differences if inbound else None,
                previous_receipt_differences=previous_receipt_differences if inbound else None,
                freight=freight, previous_freight=previous_freight, spec_coverage=spec_coverage, monthly_on_time=monthly_on_time, monthly_late=monthly_late, slowest=slowest, lead_time=lead_time, previous_lead_time=previous_lead_time, distributions=distributions, daily=daily, items=items, total=summary['documents'], page=page, per_page=per_page)


def export_document_report(kind, warehouse):
    import csv
    import io
    from flask import Response
    data=document_report(kind, warehouse, export=True)
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['document_id','planned_date','actual_date','status','expected','actual','damage','difference','on_time','timezone','period_start','period_end'])
    for row in data['items']:
        writer.writerow([row[key] for key in ('id','planned','actual_date','status','expected','actual','damage','difference','on_time')]+[data['timezone'],data['start_date'],data['end_date']])
    return Response('\ufeff'+output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{kind}_report.csv"'})
