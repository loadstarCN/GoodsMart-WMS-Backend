"""美国的国家规则：只有地址排版（City, ST ZIP）。"""
from .base import CountryRules

_US_FORMAT = (
    '{name}',
    '{organization}',
    '{address_line1}',
    '{address_line2}',
    '{locality}, {admin_area} {postal_code}',
    '{country_name}',
)


class UnitedStatesRules(CountryRules):
    code = 'US'
    address_format = {'local': _US_FORMAT, 'latin': _US_FORMAT}
