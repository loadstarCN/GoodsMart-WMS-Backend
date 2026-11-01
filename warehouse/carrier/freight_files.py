"""Strict carrier invoice CSV/XLSX parsing, shared by preview and import."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from extensions.error import BadRequestException
from warehouse.goods import tabular

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
COLUMNS = ('tracking_number', 'shipping_date', 'service', 'destination_country', 'billable_weight',
           'base', 'fuel', 'remote', 'other', 'tax', 'total')
CHARGES = ('base', 'fuel', 'remote', 'other', 'tax')
ALIASES = {
    'tracking_number': ('tracking_number', 'tracking number', 'air waybill number', 'air waybill no.'),
    'shipping_date': ('shipping_date', 'shipment date', 'ship date'),
    'service': ('service', 'service type'),
    'destination_country': ('destination_country', 'destination country'),
    'billable_weight': ('billable_weight', 'rated weight', 'billable weight'),
    'base': ('base', 'transportation charge', 'base charge'),
    'fuel': ('fuel', 'fuel surcharge'), 'remote': ('remote', 'remote area surcharge'),
    'other': ('other', 'other charges'), 'tax': ('tax', 'tax amount'),
    'total': ('total', 'net charge', 'total amount'),
}


def money(value, field, scale=2):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value).strip().replace(',', ''))
        if not number.is_finite() or abs(number) >= Decimal('100000000000') or number != number.quantize(Decimal(1).scaleb(-scale)):
            raise ValueError()
        return number
    except (ValueError, InvalidOperation):
        raise ValueError(f'{field}: invalid decimal (at most {scale} decimal places)')


def iso_date(value, field):
    try:
        text = str(value).strip()
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise ValueError(f'{field}: expected YYYY-MM-DD')


def normalized_tracking(value):
    return re.sub(r'[\s-]+', '', str(value or '')).upper()


def metadata(body):
    number = str(body.get('invoice_number') or '').strip()
    currency = str(body.get('currency') or '').strip().upper()
    if not 1 <= len(number) <= 100 or any(ord(char) < 32 for char in number):
        raise BadRequestException('Invoice number is required (1-100 characters)', field='invoice_number')
    if not re.fullmatch('[A-Z]{3}', currency):
        raise BadRequestException('Currency must be a three-letter code', field='currency')
    try:
        dates = {key: iso_date(body.get(key), key) for key in ('invoice_date', 'period_from', 'period_to')}
    except ValueError as exc:
        raise BadRequestException(str(exc))
    if dates['period_from'] > dates['period_to']:
        raise BadRequestException('Invalid shipment period', field='period_to')
    return dict(invoice_number=number, currency=currency, **dates)


def inspect_metadata(content, filename, mapping=None, encoding='auto'):
    """Read repeated invoice columns; conflicting values require explicit correction."""
    if not content or len(content) > MAX_BYTES:
        raise BadRequestException('Invoice file must be nonempty and at most 5 MB', field='file')
    fmt = tabular.file_format(filename)
    if fmt is None:
        raise BadRequestException('Expected CSV or XLSX file', field='file')
    try:
        table = tabular.read_table(content, fmt, encoding=encoding, max_rows=MAX_ROWS)
    except tabular.TableError as exc:
        raise BadRequestException(exc.message, field='file', details={'reason': exc.reason})
    if len(table.rows) < 2:
        raise BadRequestException('Invoice contains no detail rows', field='file')
    headers = [str(value).strip().casefold() for value in table.rows[0][1]]
    aliases = {
        'invoice_number': ('invoice_number', 'invoice number', 'invoice no.'),
        'invoice_date': ('invoice_date', 'invoice date'),
        'currency': ('currency', 'currency code'),
        'period_from': ('period_from', 'period from'),
        'period_to': ('period_to', 'period to'),
        'shipping_date': ((mapping or {})['shipping_date'].casefold(),) if (mapping or {}).get('shipping_date') else ALIASES['shipping_date'],
    }
    result = {}
    for field, names in aliases.items():
        indices = [i for i, header in enumerate(headers) if header in names]
        if len(indices) > 1:
            raise BadRequestException('Ambiguous invoice column', field=field)
        if not indices:
            continue
        index = indices[0]
        values = {str(cells[index]).strip() for _, cells in table.rows[1:] if index < len(cells) and str(cells[index]).strip()}
        if not values:
            continue
        if field != 'shipping_date' and len(values) != 1:
            raise BadRequestException('Invoice metadata must have one consistent value', field=field)
        if field in ('invoice_date', 'period_from', 'period_to', 'shipping_date'):
            try:
                values = {iso_date(value, field).isoformat() for value in values}
            except ValueError as exc:
                raise BadRequestException(str(exc), field=field)
        if field == 'shipping_date':
            result.setdefault('period_from', min(values))
            result.setdefault('period_to', max(values))
        else:
            result[field] = next(iter(values))
    if 'currency' in result:
        result['currency'] = result['currency'].upper()
        if not re.fullmatch('[A-Z]{3}', result['currency']):
            raise BadRequestException('Currency must be a three-letter code', field='currency')
    if 'invoice_number' in result and (len(result['invoice_number']) > 100 or any(ord(c) < 32 for c in result['invoice_number'])):
        raise BadRequestException('Invalid invoice number', field='invoice_number')
    if result.get('period_from') and result.get('period_to') and result['period_from'] > result['period_to']:
        raise BadRequestException('Invalid shipment period', field='period_to')
    return result


def parse(content, filename, info, mapping=None, encoding='auto'):
    if not content or len(content) > MAX_BYTES:
        raise BadRequestException('Invoice file must be nonempty and at most 5 MB', field='file')
    fmt = tabular.file_format(filename)
    if fmt is None:
        raise BadRequestException('Expected CSV or XLSX file', field='file')
    try:
        table = tabular.read_table(content, fmt, encoding=encoding, max_rows=MAX_ROWS)
    except tabular.TableError as exc:
        raise BadRequestException(exc.message, field='file', details={'reason': exc.reason})
    if len(table.rows) < 2:
        raise BadRequestException('Invoice contains no detail rows', field='file')
    headers = [str(value).strip().casefold() for value in table.rows[0][1]]
    mapping = mapping or {}
    indices = {}
    for field in COLUMNS:
        names = [str(mapping[field]).strip().casefold()] if field in mapping else ALIASES[field]
        matches = [index for index, header in enumerate(headers) if header in names]
        if len(matches) > 1:
            raise BadRequestException('Ambiguous invoice column', field=field)
        if matches:
            indices[field] = matches[0]
    for field in ('tracking_number', 'shipping_date', 'total'):
        if field not in indices:
            raise BadRequestException('Required invoice column is missing', field=field, details={'headers': headers})
    lines, errors, seen = [], [], set()
    for row_number, cells in table.rows[1:]:
        row = {field: str(cells[index]).strip() if index < len(cells) else '' for field, index in indices.items()}
        try:
            tracking = row.get('tracking_number', '')
            normalized = normalized_tracking(tracking)
            if not normalized or len(tracking) > 100 or any(ord(char) < 32 for char in tracking):
                raise ValueError('tracking_number: required, at most 100 characters')
            if normalized in seen:
                raise ValueError('tracking_number: duplicate row; combine charge components into one row')
            seen.add(normalized)
            shipping = iso_date(row.get('shipping_date'), 'shipping_date')
            if not info['period_from'] <= shipping <= info['period_to']:
                raise ValueError('shipping_date: outside the invoice shipment period')
            amount = money(row.get('total'), 'total')
            charges = {field: money(row[field], field) for field in CHARGES if row.get(field)}
            if charges and sum(charges.values()) != amount:
                raise ValueError('total: does not equal the charge component sum')
            country = row.get('destination_country', '').upper()
            if country and not re.fullmatch('[A-Z]{2}', country):
                raise ValueError('destination_country: expected a two-letter code')
            weight = money(row['billable_weight'], 'billable_weight', 3) if row.get('billable_weight') else None
            if weight is not None and (weight < 0 or weight >= Decimal('1000000000')):
                raise ValueError('billable_weight: must be between 0 and 999999999.999')
            service = row.get('service', '')
            if len(service) > 100:
                raise ValueError('service: at most 100 characters')
            lines.append(dict(line_number=row_number, tracking_number=tracking, shipping_date=shipping,
                service=service or None, destination_country=country or None, billable_weight=weight,
                amount=amount, charges={key: str(value) for key, value in charges.items()}))
        except ValueError as exc:
            errors.append(dict(row=row_number, message=str(exc)))
    return dict(lines=lines, errors=errors[:100], error_count=len(errors), format=fmt,
                encoding=table.encoding, total=sum((row['amount'] for row in lines), Decimal(0)))
