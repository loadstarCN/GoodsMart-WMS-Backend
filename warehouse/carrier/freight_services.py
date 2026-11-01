"""Scoped carrier invoice import and reconciliation. No payment side effects."""
import hashlib
from decimal import Decimal
from flask import g
from sqlalchemy.orm import lazyload
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from extensions.transaction import transactional
from system.common.permissions import get_actor_context
from warehouse.common import get_company_owned, get_warehouse_owned, require_actor_user_id, require_warehouse_scope, clock
from warehouse.common.locks import lock_row
from .models import Carrier
from .freight_models import CarrierFreightSettings, CarrierInvoice, CarrierInvoiceLine
from . import freight_files as files


def company_wide(company_id):
    is_super, permissions, actor_company = get_actor_context()
    if is_super or ('company_all_access' in permissions and actor_company == company_id):
        return True
    from warehouse.warehouse.models import Warehouse
    accessible = {row.id for row in getattr(g, 'accessible_warehouses', [])}
    needed = {row.id for row in Warehouse.query.filter_by(company_id=company_id, is_active=True)}
    return bool(needed) and needed <= accessible


def invoice_scope(invoice):
    get_company_owned(Carrier, invoice.carrier_id)
    if invoice.warehouse_id is not None:
        require_warehouse_scope(invoice.warehouse_id, 'Carrier invoice')
    elif getattr(g, 'warehouse_id', None) is not None or not company_wide(invoice.company_id):
        raise ForbiddenException('This invoice requires the full company warehouse scope', 12001)
    return invoice


def owned_invoice(invoice_id, locked=False):
    invoice = get_company_owned(CarrierInvoice, invoice_id)
    invoice_scope(invoice)
    return lock_row(invoice) if locked else invoice


def settings_values(carrier_id):
    row = db.session.get(CarrierFreightSettings, carrier_id)
    return dict(revision=row.revision if row else 0, tolerance_type=row.tolerance_type if row else 'amount',
                tolerance=str(row.tolerance) if row else '0', currency=row.currency if row else None,
                column_mapping=row.column_mapping if row else {})


def _revision(body, current):
    if not isinstance(body, dict) or type(body.get('revision')) is not int:
        raise BadRequestException('Revision is required', field='revision')
    if body['revision'] != current:
        raise ConflictException('Record changed; reload before saving', field='revision')


@transactional
def save_settings(carrier_id, body):
    carrier = get_company_owned(Carrier, carrier_id)
    lock_row(carrier)
    row = db.session.get(CarrierFreightSettings, carrier_id)
    _revision(body, row.revision if row else 0)
    kind = body.get('tolerance_type')
    if kind not in ('amount', 'percent'):
        raise BadRequestException('Invalid tolerance type', field='tolerance_type')
    try:
        tolerance = files.money(body.get('tolerance'), 'tolerance')
    except ValueError as exc:
        raise BadRequestException(str(exc), field='tolerance')
    if tolerance < 0 or tolerance >= Decimal('10000000000') or (kind == 'percent' and tolerance > 100):
        raise BadRequestException('Invalid tolerance', field='tolerance')
    currency = str(body.get('currency') or '').upper().strip() or None
    if currency and (len(currency) != 3 or not currency.isascii() or not currency.isalpha()):
        raise BadRequestException('Invalid tolerance currency', field='currency')
    mapping = body.get('column_mapping', {})
    if not isinstance(mapping, dict) or set(mapping)-set(files.COLUMNS) or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 100 for v in mapping.values()):
        raise BadRequestException('Invalid column mapping', field='column_mapping')
    if row is None:
        row = CarrierFreightSettings(carrier_id=carrier_id, revision=0)
        db.session.add(row)
    row.revision += 1
    row.tolerance_type, row.tolerance, row.currency = kind, tolerance, currency
    row.column_mapping = {key: value.strip() for key, value in mapping.items()}
    db.session.flush()
    return settings_values(carrier_id)


_FETCH_SHIPMENT = object()


def _estimate(delivery, shipment=_FETCH_SHIPMENT):
    from warehouse.dn.models import DNCarrierShipment
    if shipment is _FETCH_SHIPMENT:
        shipment = DNCarrierShipment.query.filter_by(dn_id=delivery.dn_id, status='active').order_by(DNCarrierShipment.id.desc()).first()
    if shipment and shipment.net_charge is not None:
        return shipment.net_charge, shipment.currency, 'carrier_quote'
    # The historical shipping_cost default is zero, so zero cannot prove a recorded estimate.
    if delivery.shipping_cost is not None and delivery.shipping_cost > 0:
        return Decimal(str(delivery.shipping_cost)).quantize(Decimal('.01')), delivery.currency, 'delivery_record'
    return None, None, None


def _match_lines(carrier, info, lines, warehouse_id):
    from warehouse.delivery.models import DeliveryTask
    from warehouse.dn.models import DN, DNCarrierShipment
    from warehouse.warehouse.models import Warehouse
    query = (DeliveryTask.query.options(lazyload('*')).join(DN, DN.id == DeliveryTask.dn_id)
             .join(Warehouse, Warehouse.id == DN.warehouse_id)
             .filter(Warehouse.company_id == carrier.company_id, DeliveryTask.carrier_id == carrier.id,
                     DeliveryTask.is_active.is_(True), DeliveryTask.actual_shipping_date >= info['period_from'],
                     DeliveryTask.actual_shipping_date <= info['period_to'], DN.status.in_(('delivered', 'completed'))))
    if warehouse_id is not None:
        query = query.filter(DN.warehouse_id == warehouse_id)
    candidates = {}
    tasks = query.all()
    shipments = {}
    if tasks:
        for shipment in DNCarrierShipment.query.options(lazyload('*')).filter(
                DNCarrierShipment.dn_id.in_([task.dn_id for task in tasks]), DNCarrierShipment.status == 'active'):
            shipments.setdefault(shipment.dn_id, []).append(shipment)
    for task in tasks:
        numbers = {files.normalized_tracking(task.tracking_number)}
        for shipment in shipments.get(task.dn_id, []):
            numbers.add(files.normalized_tracking(shipment.tracking_number))
            numbers.update(files.normalized_tracking(number) for number in (shipment.package_tracking_numbers or []))
        for number in numbers - {''}:
            candidates.setdefault(number, {})[task.dn_id] = task
    for line in lines:
        matches = list(candidates.get(files.normalized_tracking(line['tracking_number']), {}).values())
        line['dn_id'] = matches[0].dn_id if len(matches) == 1 else None
        line['match_status'] = 'matched' if len(matches) == 1 else 'ambiguous' if matches else 'unmatched'
        if len(matches) == 1:
            related = shipments.get(matches[0].dn_id, [])
            amount, currency, source = _estimate(matches[0], max(related, key=lambda row: row.id) if related else None)
            line.update(estimated_amount=amount, estimated_currency=currency, estimate_source=source)
    # A shipment quote covers the whole DN. Never repeat it for each package charge.
    counts = {}
    for line in lines:
        if line['dn_id']:
            counts[line['dn_id']] = counts.get(line['dn_id'], 0) + 1
    for line in lines:
        if counts.get(line['dn_id'], 0) > 1:
            line.update(estimated_amount=None, estimated_currency=None, estimate_source=None)


def check_file(carrier_id, body, content, filename):
    carrier = get_company_owned(Carrier, carrier_id)
    warehouse_id = getattr(g, 'warehouse_id', None)
    if warehouse_id is not None:
        from warehouse.warehouse.models import Warehouse
        require_warehouse_scope(warehouse_id, 'Carrier invoice')
        if db.session.get(Warehouse, warehouse_id).company_id != carrier.company_id:
            raise ForbiddenException('Carrier and warehouse belong to different companies', 12001)
    elif not company_wide(carrier.company_id):
        raise ForbiddenException('Select a warehouse to import this invoice', 12001)
    info = files.metadata(body)
    settings = settings_values(carrier_id)
    if settings['tolerance_type'] == 'amount' and settings['currency'] and settings['currency'] != info['currency']:
        raise BadRequestException('Tolerance currency differs from invoice currency', field='currency')
    parsed = files.parse(content, filename, info, settings['column_mapping'], body.get('encoding', 'auto'))
    fingerprint = hashlib.sha256(content).hexdigest()
    duplicate = CarrierInvoice.query.filter(CarrierInvoice.company_id == carrier.company_id,
        CarrierInvoice.carrier_id == carrier_id, db.or_(CarrierInvoice.invoice_number == info['invoice_number'],
                                                        CarrierInvoice.file_sha256 == fingerprint)).first()
    _match_lines(carrier, info, parsed['lines'], warehouse_id)
    return dict(carrier=carrier, warehouse_id=warehouse_id, info=info, parsed=parsed, settings=settings,
                fingerprint=fingerprint, duplicate=duplicate)


def line_state(line, currency, tolerance_type, tolerance):
    get = lambda key: line.get(key) if isinstance(line, dict) else getattr(line, key)
    if get('review_status') == 'external':
        return 'external'
    if get('review_status') in ('accepted', 'disputed'):
        return get('review_status')
    if get('dn_id') is None:
        return 'unmatched'
    estimate = get('estimated_amount')
    if estimate is None:
        return 'no_estimate'
    if get('estimated_currency') != currency:
        return 'currency_mismatch'
    limit = abs(estimate)*Decimal(str(tolerance))/100 if tolerance_type == 'percent' else Decimal(str(tolerance))
    return 'matched' if abs(get('amount')-estimate) <= limit else 'difference'


def line_dict(line, invoice):
    state = line_state(line, invoice.currency, invoice.tolerance_type, invoice.tolerance)
    comparable = line.estimated_amount is not None and line.estimated_currency == invoice.currency
    return dict(id=line.id, line_number=line.line_number, tracking_number=line.tracking_number,
        shipping_date=line.shipping_date.isoformat(), service=line.service, destination_country=line.destination_country,
        billable_weight=str(line.billable_weight) if line.billable_weight is not None else None,
        amount=str(line.amount), charges=line.charges, dn_id=line.dn_id, estimated_amount=str(line.estimated_amount) if line.estimated_amount is not None else None,
        estimated_currency=line.estimated_currency, estimate_source=line.estimate_source,
        difference=str(line.amount-line.estimated_amount) if comparable else None,
        state=state, review_status=line.review_status, note=line.note)


def invoice_dict(invoice, details=False):
    rows = [line_dict(line, invoice) for line in invoice.lines]
    result = dict(id=invoice.id, company_id=invoice.company_id, carrier_id=invoice.carrier_id,
        carrier_name=invoice.carrier.name, warehouse_id=invoice.warehouse_id,
        invoice_number=invoice.invoice_number, invoice_date=invoice.invoice_date.isoformat(),
        period_from=invoice.period_from.isoformat(), period_to=invoice.period_to.isoformat(), currency=invoice.currency,
        status=invoice.status, revision=invoice.revision, file_name=invoice.file_name, file_encoding=invoice.file_encoding,
        total=str(sum((line.amount for line in invoice.lines), Decimal(0))), line_count=len(rows),
        unresolved=sum(row['state'] not in ('matched', 'accepted', 'disputed', 'external') for row in rows),
        created_at=invoice.created_at.isoformat() if invoice.created_at else None,
        confirmed_at=invoice.confirmed_at.isoformat() if invoice.confirmed_at else None,
        tolerance_type=invoice.tolerance_type, tolerance=str(invoice.tolerance))
    if details:
        result['lines'] = rows
        from system.user.models import User
        for field in ('created_by', 'confirmed_by'):
            actor_id = getattr(invoice, field)
            actor = db.session.get(User, actor_id) if actor_id else None
            result[field] = {'id': actor_id, 'name': actor.user_name if actor else None} if actor_id else None
    return result


def preview(result):
    settings, parsed, info = result['settings'], result['parsed'], result['info']
    lines = []
    for source in parsed['lines']:
        line = {key: str(value) if isinstance(value, Decimal) else value.isoformat() if hasattr(value, 'isoformat') else value for key, value in source.items()}
        line['state'] = line_state(source, info['currency'], settings['tolerance_type'], settings['tolerance'])
        comparable = source.get('estimated_amount') is not None and source.get('estimated_currency') == info['currency']
        line['difference'] = str(source['amount']-source['estimated_amount']) if comparable else None
        lines.append(line)
    return dict(errors=parsed['errors'], error_count=parsed['error_count'], lines=lines,
        total=str(parsed['total']), currency=info['currency'], file_encoding=parsed['encoding'],
        duplicate=bool(result['duplicate']), can_import=not parsed['error_count'] and not result['duplicate'],
        tolerance_type=settings['tolerance_type'], tolerance=settings['tolerance'])


@transactional
def import_invoice(carrier_id, body, content, filename):
    carrier = get_company_owned(Carrier, carrier_id)
    lock_row(carrier)
    result = check_file(carrier_id, body, content, filename)
    if result['duplicate']:
        raise ConflictException('Invoice number or file has already been imported', field='invoice_number')
    if result['parsed']['error_count']:
        raise BadRequestException('Invoice file has invalid rows', field='file', details={'errors': result['parsed']['errors']})
    settings, parsed = result['settings'], result['parsed']
    invoice = CarrierInvoice(company_id=carrier.company_id, carrier_id=carrier_id, warehouse_id=result['warehouse_id'],
        **result['info'], file_name=filename.replace('\\', '/').split('/')[-1][:255], file_format=parsed['format'],
        file_encoding=parsed['encoding'], file_content=content, file_sha256=result['fingerprint'],
        tolerance_type=settings['tolerance_type'], tolerance=Decimal(settings['tolerance']), created_by=require_actor_user_id())
    db.session.add(invoice)
    for source in parsed['lines']:
        values = {key: value for key, value in source.items() if key != 'match_status'}
        invoice.lines.append(CarrierInvoiceLine(**values))
    db.session.flush()
    return invoice_dict(invoice, True)


@transactional
def review_line(invoice_id, line_id, body):
    invoice = owned_invoice(invoice_id, locked=True)
    _revision(body, invoice.revision)
    if invoice.status != 'pending':
        raise ConflictException('Confirmed invoice is locked')
    line = next((row for row in invoice.lines if row.id == line_id), None)
    if line is None:
        raise BadRequestException('Line does not belong to this invoice', field='line_id')
    review = body.get('review_status', 'unreviewed')
    if review not in ('unreviewed', 'accepted', 'disputed', 'external'):
        raise BadRequestException('Invalid review status', field='review_status')
    note = str(body.get('note') or '').strip()
    if len(note) > 500 or (review in ('accepted', 'disputed', 'external') and not note):
        raise BadRequestException('A review reason is required (at most 500 characters)', field='note')
    if 'dn_id' in body and body['dn_id'] != line.dn_id:
        from warehouse.dn.models import DN
        from warehouse.delivery.models import DeliveryTask
        from warehouse.common import require_positive_int
        dn_id = require_positive_int(body['dn_id'], 'dn_id')
        dn = get_warehouse_owned(DN, dn_id)
        if dn.warehouse.company_id != invoice.company_id:
            raise ForbiddenException('DN belongs to another company', 12001)
        require_warehouse_scope(dn.warehouse_id, 'DN')
        if invoice.warehouse_id is not None and dn.warehouse_id != invoice.warehouse_id:
            raise BadRequestException('DN belongs to another warehouse', field='dn_id')
        task = DeliveryTask.query.filter_by(dn_id=dn_id, carrier_id=invoice.carrier_id, is_active=True).order_by(DeliveryTask.id.desc()).first()
        if task is None or dn.status not in ('delivered', 'completed'):
            raise BadRequestException('DN must have shipped with this carrier', field='dn_id')
        if task.actual_shipping_date is None or not invoice.period_from <= task.actual_shipping_date <= invoice.period_to:
            raise BadRequestException('DN shipping date is outside this invoice period', field='dn_id')
        line.dn_id = dn_id
        line.estimated_amount, line.estimated_currency, line.estimate_source = _estimate(task)
        related = [row for row in invoice.lines if row.dn_id == dn_id]
        if len(related) > 1:
            for row in related:
                row.estimated_amount = row.estimated_currency = row.estimate_source = None
    if review == 'external':
        line.dn_id = None
        line.estimated_amount = line.estimated_currency = line.estimate_source = None
    if review == 'accepted' and line.dn_id is None:
        raise BadRequestException('Link a DN or mark this as an external charge', field='dn_id')
    line.review_status, line.note = review, note
    line.reviewed_by, line.reviewed_at = require_actor_user_id(), clock.now()
    invoice.revision += 1
    db.session.flush()
    return invoice_dict(invoice, True)


@transactional
def confirm(invoice_id, body):
    invoice = owned_invoice(invoice_id, locked=True)
    _revision(body, invoice.revision)
    target = body.get('confirmed')
    if type(target) is not bool:
        raise BadRequestException('confirmed must be a boolean', field='confirmed')
    if target:
        if invoice_dict(invoice)['unresolved']:
            raise ConflictException('Review all unresolved lines before confirmation')
        invoice.status, invoice.confirmed_by, invoice.confirmed_at = 'confirmed', require_actor_user_id(), clock.now()
    else:
        invoice.status, invoice.confirmed_by, invoice.confirmed_at = 'pending', None, None
    invoice.revision += 1
    db.session.flush()
    return invoice_dict(invoice, True)
