import re

def validate_jan(jan_code):
    """
    Validate a JAN / GTIN code: 8, 12 (UPC-A), 13 or 14 digits with a valid check digit.
    Codes are kept as they are -- a 12-digit UPC-A is NOT zero-padded to 13 digits.
    :param jan_code: The JAN/EAN code to validate.
    :return: Boolean indicating whether the code is valid.
    """
    if not re.fullmatch(r'[0-9]{8}|[0-9]{12,14}', jan_code or ''):
        return False
    digits = [int(d) for d in jan_code]
    body, check = digits[:-1], digits[-1]
    # GTIN mod-10: from the right (excluding the check digit), odd positions x3
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check