"""关税同盟登记（国内 / 跨境判断用，见 warehouse/shipping/route.py）

同一关税同盟内的两国之间发货不用报关（不出 CI / PL、建运单不带报关扩展），但仍按跨境件（international）承运。
- 默认只登记 EU（欧盟关税同盟：27 个成员国 + 摩纳哥，摩纳哥按法国关境处理）。
- 其它同盟（如海湾合作委员会、欧亚经济联盟……）的关境规则各有例外，没核实前不要登记：登记错了会漏掉报关。
- 代码一律 ISO 3166-1 alpha-2 大写。
"""
from .countries import normalize_country

CUSTOMS_UNIONS = {
    'EU': frozenset({
        'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI', 'FR', 'GR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU',
        'LV', 'MT', 'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK',
        'MC',
    }),
}


def union_of(country):
    """国家所在的关税同盟代码（如 'EU'）；不在任何登记的同盟里返回 None"""
    code = normalize_country(country)
    for name, members in CUSTOMS_UNIONS.items():
        if code in members:
            return name
    return None


def same_customs_union(origin, destination) -> bool:
    """两国在同一个登记的关税同盟里（任一方为空 → False）"""
    union = union_of(origin)
    return union is not None and union == union_of(destination)
