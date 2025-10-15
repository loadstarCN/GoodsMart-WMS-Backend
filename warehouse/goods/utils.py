import re

def validate_jan(jan_code):
    """
    Validate JAN/EAN code (13 digits).
    :param jan_code: The JAN/EAN code to validate.
    :return: Boolean indicating whether the code is valid.
    """
    if not re.match(r'^\d{13}$', jan_code):
        return False
    digits = [int(d) for d in jan_code]
    checksum = sum(digits[i] if i % 2 == 0 else digits[i] * 3 for i in range(len(digits) - 1))
    check_digit = (10 - (checksum % 10)) % 10
    return check_digit == digits[-1]