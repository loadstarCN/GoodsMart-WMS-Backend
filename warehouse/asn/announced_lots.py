"""Supplier announcements are document data, never received stock or registered lots."""
from datetime import date
import re
from extensions.error import BadRequestException
from warehouse.common import require_positive_int


def validate_announced_lots(value, quantity=None):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise BadRequestException('announced_lots must be a list of at most 100 lots')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, dict):
            raise BadRequestException('Each announced lot must be an object')
        code = row.get('lot_code')
        if not isinstance(code, str) or not code.strip() or len(code.strip()) > 40:
            raise BadRequestException('Announced lot code must contain 1 to 40 characters')
        code = code.strip()
        if code in seen:
            raise BadRequestException('Duplicate announced lot code')
        seen.add(code)
        expiry = row.get('expiry_date') or None
        if expiry is not None:
            try:
                if not isinstance(expiry, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
                    raise ValueError()
                date.fromisoformat(expiry)
            except ValueError:
                raise BadRequestException('Announced expiry_date must be YYYY-MM-DD')
        result.append({'lot_code': code, 'expiry_date': expiry,
                       'quantity': require_positive_int(row.get('quantity'), 'announced lot quantity')})
    if quantity is not None and sum(row['quantity'] for row in result) > quantity:
        raise BadRequestException('Announced lot quantity exceeds expected quantity')
    return result
