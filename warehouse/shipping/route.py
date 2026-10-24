"""国内 / 跨境（route）：一张 DN 从哪国发到哪国、要不要报关。不写死任何国家。

- 发货国 origin_country = 仓库的 country_code，没有用公司的。
- 收件国 destination_country = 报关快照的收件国（recipient_country，没有用快照 consignee.country）
  → 收件人的 country。（第二期加 DN 收件地址快照的国家，排在报关快照之后。）
- international = 发货国 != 收件国；任一方未知按国内（False）。
- requires_customs = international 且两国不在同一关税同盟（warehouse/common/customs_unions.py）；
  有报关快照的 DN 一律 True（来源系统明确给了报关数据）。
- 国家代码只认有效的 ISO 3166-1 alpha-2（大写）；无效 / 空的按未知。

建运单时插件按 scope_of(route) 判断能不能承运（CarrierAdapter.scopes），requires_customs 时报关扩展参与
（warehouse/dn/customs_extension.py）。
"""
from warehouse.common.countries import is_valid_country, normalize_country
from warehouse.common.customs_unions import same_customs_union

SCOPE_DOMESTIC = 'domestic'
SCOPE_INTERNATIONAL = 'international'
SCOPES = (SCOPE_DOMESTIC, SCOPE_INTERNATIONAL)


def _country(value):
    code = normalize_country(value)
    return code if is_valid_country(code) else None


def origin_country(dn):
    """发货国：仓库的国家，没有用公司的；都没有为 None"""
    warehouse = dn.warehouse
    company = warehouse.company if warehouse is not None else None
    return _country(getattr(warehouse, 'country_code', None)) or _country(getattr(company, 'country_code', None))


def destination_country(dn):
    """收件国：报关快照的收件国（recipient_country → consignee.country）→ 收件人的 country；都没有为 None"""
    customs = dn.customs
    if customs is not None:
        code = _country(customs.recipient_country) or _country((customs.consignee or {}).get('country'))
        if code:
            return code
    return _country(getattr(dn.recipient, 'country', None))


def shipping_route(dn) -> dict:
    """→ {origin_country, destination_country, international, requires_customs}"""
    origin = origin_country(dn)
    destination = destination_country(dn)
    international = bool(origin and destination and origin != destination)
    requires_customs = dn.customs is not None or (international and not same_customs_union(origin, destination))
    return {
        'origin_country': origin,
        'destination_country': destination,
        'international': international,
        'requires_customs': requires_customs,
    }


def scope_of(route: dict) -> str:
    """这票货的承运范围（对照插件的 scopes）：跨境或要报关 → international，否则 domestic。
    有报关快照、但发货国 / 收件国没填全的 DN 也按 international（报关数据说明要出关境）。"""
    if route['international'] or route['requires_customs']:
        return SCOPE_INTERNATIONAL
    return SCOPE_DOMESTIC
