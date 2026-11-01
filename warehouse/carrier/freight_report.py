"""Shipment-level comparison. Estimates are counted once per DN, in their original currency."""
from datetime import timedelta
from decimal import Decimal
from flask import g, request
from sqlalchemy.orm import lazyload
from extensions.error import BadRequestException
from system.common.permissions import get_actor_company_id
from warehouse.common import clock, get_company_owned
from warehouse.delivery.models import DeliveryTask
from warehouse.dn.models import DN, DNCarrierShipment, DNPackage, DNCustoms
from warehouse.recipient.models import Recipient
from warehouse.warehouse.models import Warehouse
from .models import Carrier
from .freight_models import CarrierInvoiceLine
from . import freight_files as files
from . import freight_services as service


def report(invoice_query, export=False, dn_id=None):
    try:
        end = files.iso_date(request.args['end_date'], 'end_date') if request.args.get('end_date') else clock.local_today()
        start = files.iso_date(request.args['start_date'], 'start_date') if request.args.get('start_date') else end-timedelta(days=29)
        page, size = int(request.args.get('page', 1)), int(request.args.get('per_page', 20))
        if start > end or (end-start).days > 366 or page < 1 or not 1 <= size <= 100:
            raise ValueError('Use a date range of at most 367 days and valid pagination')
    except ValueError as exc:
        raise BadRequestException(str(exc))
    query = (DeliveryTask.query.options(lazyload('*')).join(DN, DN.id == DeliveryTask.dn_id)
        .join(Warehouse, Warehouse.id == DN.warehouse_id)
        .filter(DeliveryTask.is_active.is_(True), DN.is_active.is_(True), DN.status.in_(('delivered', 'completed'))))
    if dn_id is None:
        query = query.filter(DeliveryTask.actual_shipping_date >= start, DeliveryTask.actual_shipping_date <= end)
    else:
        query = query.filter(DN.id == dn_id, DeliveryTask.actual_shipping_date.isnot(None))
    company_id, warehouse_id = get_actor_company_id(), getattr(g, 'warehouse_id', None)
    if company_id is not None:
        query = query.filter(Warehouse.company_id == company_id)
    if warehouse_id is not None:
        query = query.filter(DN.warehouse_id == warehouse_id)
    elif company_id is not None and not service.company_wide(company_id):
        query = query.filter(DN.warehouse_id.in_([w.id for w in getattr(g, 'accessible_warehouses', [])]))
    if request.args.get('carrier_id'):
        try:
            carrier_id = int(request.args['carrier_id'])
        except ValueError:
            raise BadRequestException('Invalid carrier ID')
        get_company_owned(Carrier, carrier_id)
        query = query.filter(DeliveryTask.carrier_id == carrier_id)
    # One shipment per DN; retain the latest active task if legacy data contains duplicates.
    tasks = {}
    for task in query.order_by(DeliveryTask.id):
        tasks[task.dn_id] = task
    invoices = {row.id: row for row in invoice_query}
    invoice_ids = list(invoices)
    billed = {}
    if invoice_ids and tasks:
        for line in CarrierInvoiceLine.query.filter(CarrierInvoiceLine.invoice_id.in_(invoice_ids), CarrierInvoiceLine.dn_id.in_(list(tasks))):
            billed.setdefault(line.dn_id, []).append(line)
    shipments = {}
    if tasks:
        for shipment in DNCarrierShipment.query.options(lazyload('*')).filter(DNCarrierShipment.dn_id.in_(list(tasks)), DNCarrierShipment.status == 'active').order_by(DNCarrierShipment.id):
            shipments[shipment.dn_id] = shipment
    carriers = {row.id: row.name for row in Carrier.query.filter(Carrier.id.in_({task.carrier_id for task in tasks.values()}))} if tasks else {}
    documents = {row.id: row for row in DN.query.options(lazyload('*')).filter(DN.id.in_(list(tasks)))} if tasks else {}
    customs = {row.dn_id: row.recipient_country for row in DNCustoms.query.filter(DNCustoms.dn_id.in_(list(tasks)))} if tasks else {}
    recipients = dict(Recipient.query.with_entities(Recipient.id, Recipient.country).filter(Recipient.id.in_({row.recipient_id for row in documents.values()})).all()) if documents else {}
    packages = {}
    if tasks:
        for package in DNPackage.query.options(lazyload('*')).filter(DNPackage.dn_id.in_(list(tasks))):
            packages.setdefault(package.dn_id, []).append(package)
    countries = set()
    country_filter = request.args.get('country', '').strip().upper()
    if country_filter and (len(country_filter) != 2 or not country_filter.isascii() or not country_filter.isalpha()):
        raise BadRequestException('Expected a two-letter destination country', field='country')
    wanted = request.args.get('state')
    if wanted and wanted not in ('unbilled','no_estimate','currency_mismatch','matched','difference','accepted','disputed'):
        raise BadRequestException('Invalid reconciliation state', field='state')
    rows, totals = [], {}
    for dn_id, task in tasks.items():
        amount, currency, source = service._estimate(task, shipments.get(dn_id))
        lines, amounts = billed.get(dn_id, []), {}
        charge_breakdown = {}
        for line in lines:
            unit = invoices[line.invoice_id].currency
            amounts[unit] = amounts.get(unit, Decimal(0))+line.amount
            for name, value in (line.charges or {'unitemized': str(line.amount)}).items():
                group = charge_breakdown.setdefault(unit, {})
                group[name] = group.get(name, Decimal(0)) + Decimal(str(value))
        comparable = amount is not None and len(amounts) == 1 and currency in amounts
        difference = amounts[currency]-amount if comparable else None
        # Count the DN estimate once. Supplementary invoices must not each reuse the
        # whole quote; use the strictest of their captured tolerances for the total.
        limits = [abs(amount)*invoice.tolerance/100 if invoice.tolerance_type == 'percent' else invoice.tolerance
                  for invoice in (invoices[key] for key in {line.invoice_id for line in lines})] if comparable else []
        within_tolerance = comparable and abs(difference) <= min(limits, default=Decimal(0))
        state = 'unbilled' if not lines else 'no_estimate' if amount is None else 'currency_mismatch' if not comparable else 'matched' if within_tolerance else 'difference'
        if any(line.review_status == 'disputed' for line in lines):
            state = 'disputed'
        elif lines and all(line.review_status == 'accepted' for line in lines):
            state = 'accepted'
        dn = documents[dn_id]
        country = (customs.get(dn_id) or recipients.get(dn.recipient_id) or '').upper()
        if country:
            countries.add(country)
        if country_filter and country_filter != country:
            continue
        boxes = packages.get(dn_id, [])
        actual_weight = sum((box.gross_weight_kg for box in boxes), Decimal(0)) if boxes else None
        weights = {}
        for invoice_id in {line.invoice_id for line in lines}:
            invoice_lines = [line for line in lines if line.invoice_id == invoice_id]
            weights[invoice_id] = (str(sum((line.billable_weight for line in invoice_lines), Decimal(0)))
                if all(line.billable_weight is not None for line in invoice_lines) else None)
        row = dict(dn_id=dn_id, tracking_number=task.tracking_number, shipping_date=task.actual_shipping_date.isoformat(),
            order_number=dn.order_number, destination_country=country or None, package_count=len(boxes) if boxes else None,
            actual_weight=str(actual_weight) if actual_weight is not None else None, billed_weights=weights,
            service=shipments[dn_id].service_type if dn_id in shipments else None,
            carrier_id=task.carrier_id, carrier_name=carriers.get(task.carrier_id), estimated_amount=str(amount) if amount is not None else None,
            estimated_currency=currency, estimate_source=source, billed={key:str(value) for key,value in amounts.items()},
            difference=str(difference) if difference is not None else None, state=state,
            charge_breakdown={unit:{name:str(value) for name,value in values.items()} for unit,values in charge_breakdown.items()},
            invoices=[dict(id=key, invoice_number=invoices[key].invoice_number, status=invoices[key].status) for key in sorted({line.invoice_id for line in lines})])
        if wanted and wanted != state:
            continue
        rows.append(row)
        for unit in set(amounts) | ({currency} if amount is not None and currency else set()):
            total = totals.setdefault(unit, dict(estimate=Decimal(0), billed=Decimal(0), comparable_estimate=Decimal(0), comparable_billed=Decimal(0), estimate_count=0,billed_count=0,comparable_count=0))
            if unit == currency and amount is not None:
                total['estimate'] += amount
                total['estimate_count'] += 1
            total['billed'] += amounts.get(unit, Decimal(0))
            if unit in amounts:
                total['billed_count'] += 1
            if comparable and unit == currency:
                total['comparable_count'] += 1
                total['comparable_estimate'] += amount
                total['comparable_billed'] += amounts[unit]
    rows.sort(key=lambda row:(row['shipping_date'],row['dn_id']), reverse=True)
    distributions = {}
    for row in rows:
        units = set(row['billed']) | ({row['estimated_currency']} if row['estimated_amount'] is not None and row['estimated_currency'] else set())
        for unit in units:
            panels = distributions.setdefault(unit, {key:{} for key in ('carrier','country','service','charges')})
            for name, value in row['charge_breakdown'].get(unit, {}).items():
                group = panels['charges'].setdefault(name, dict(id=name,label=name,count=0,billed=Decimal(0),estimate=Decimal(0),billed_count=0,estimate_count=0,difference=Decimal(0),comparable_count=0))
                group['count'] += 1
                group['billed_count'] += 1
                group['billed'] += Decimal(value)
            for key, label, identity in (('carrier',row['carrier_name'],row['carrier_id']),
                                         ('country',row['destination_country'],row['destination_country']),
                                         ('service',row['service'],row['service'])):
                group = panels[key].setdefault(identity,dict(id=identity,label=label,count=0,billed=Decimal(0),estimate=Decimal(0),billed_count=0,estimate_count=0,difference=Decimal(0),comparable_count=0))
                group['count'] += 1
                if unit in row['billed']:
                    group['billed'] += Decimal(row['billed'][unit]); group['billed_count'] += 1
                if row['estimated_currency'] == unit and row['estimated_amount'] is not None:
                    group['estimate'] += Decimal(row['estimated_amount']); group['estimate_count'] += 1
                if row['estimated_currency'] == unit and row['difference'] is not None:
                    group['difference'] += Decimal(row['difference']); group['comparable_count'] += 1
    charts = {unit:{key:[{field:str(value) if isinstance(value,Decimal) else value for field,value in group.items()}
                        for group in sorted(groups.values(), key=lambda v: (-abs(v['billed'] if v['billed_count'] else v['estimate']), str(v['id'])))]
                    for key,groups in panels.items()} for unit,panels in distributions.items()}
    # Invoice periods and shipment dates are different scopes. Keep this section
    # separate: unlinked charges must not disappear or inflate shipment totals.
    period_invoices = [invoice for invoice in invoices.values()
                       if invoice.period_from <= end and invoice.period_to >= start]
    overview = [service.invoice_dict(invoice) for invoice in period_invoices]
    invoice_totals = {}
    for invoice in period_invoices:
        total = invoice_totals.setdefault(invoice.currency, dict(amount=Decimal(0), unlinked_amount=Decimal(0), unlinked_count=0))
        for line in invoice.lines:
            total['amount'] += line.amount
            if line.dn_id is None:
                total['unlinked_amount'] += line.amount
                total['unlinked_count'] += 1
    invoice_summary = dict(total=len(overview), pending=sum(row['status']=='pending' for row in overview),
        confirmed=sum(row['status']=='confirmed' for row in overview), unresolved=sum(row['unresolved'] for row in overview),
        items=overview[:6], totals={unit:{key:str(value) if isinstance(value,Decimal) else value for key,value in total.items()}
                                  for unit,total in invoice_totals.items()})
    return dict(items=rows if export else rows[(page-1)*size:page*size], total=len(rows), page=page, per_page=size,
        states={key:sum(row['state']==key for row in rows) for key in ('unbilled','no_estimate','currency_mismatch','matched','difference','accepted','disputed')},
        distributions=charts,
        invoice_summary=invoice_summary,
        countries=sorted(countries),
        start_date=start.isoformat(), end_date=end.isoformat(),
        totals={unit:{key:str(value) for key,value in total.items()} for unit,total in totals.items()})
