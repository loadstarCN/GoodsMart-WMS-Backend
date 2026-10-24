"""结构化地址（doc/shipping-flow.md §6.2）：字段、校验、按国家排版。

同一组字段名用于收件人、仓库、DN 收件地址快照（dn.ship_to）、交给插件 / 文件模板的 ship_to / shipper：
    country（ISO 3166-1 alpha-2）、postal_code、admin_area（州 / 省 / 都道府县）、locality（市区町村）、
    dependent_locality、address_line1、address_line2、organization、name、phone、email，
    以及拉丁转写 latin: {name, organization, address_line1, address_line2, locality, admin_area}
    （跨境面单用；没有时退回本地文字或报关快照里的英文收件人）。
收件人 / 仓库表里的邮编列叫 zip_code，接口两个名字都收（postal_code ≡ zip_code），输出两个都给。

排版（format_lines / format_single_line）按国家规则包的 address_format（warehouse/country_rules），
如日本：〒邮编 / 都道府県市区町村 / 番地・建物；欧美：姓名 / 街道 / 城市 州 邮编 / 国家。
校验错误一律 400：字段不是字符串 / 超长 14019（field 指明哪一项），国家代码不合法 14020。
"""
import re

from extensions.error import BadRequestException
from warehouse.common.countries import COUNTRY_NAMES_EN, is_valid_country, normalize_country

ADDRESS_FIELDS = ('country', 'postal_code', 'admin_area', 'locality', 'dependent_locality', 'address_line1',
                  'address_line2', 'organization', 'name', 'phone', 'email')
LATIN_FIELDS = ('name', 'organization', 'address_line1', 'address_line2', 'locality', 'admin_area')
# 判断「有没有结构化地址」看的字段（只有姓名 / 电话 / 邮编不算）
STRUCTURED_FIELDS = ('admin_area', 'locality', 'dependent_locality', 'address_line1', 'address_line2')
NAME_FIELDS = ('name', 'organization')
FIELD_LIMITS = {
    'country': 2, 'postal_code': 20, 'admin_area': 100, 'locality': 100, 'dependent_locality': 100,
    'address_line1': 255, 'address_line2': 255, 'organization': 255, 'name': 255, 'phone': 30, 'email': 255,
}
# 收件人 / 仓库表上的结构化地址列（与模型列宽一致）
RECIPIENT_ADDRESS_LIMITS = {
    'admin_area': 100, 'locality': 100, 'dependent_locality': 100, 'address_line1': 255, 'address_line2': 255,
    'organization': 255,
}
WAREHOUSE_ADDRESS_LIMITS = {
    'admin_area': 100, 'locality': 100, 'address_line1': 255, 'address_line2': 255,
}
TEXT_INVALID = 14019
COUNTRY_INVALID = 14020

_PLACEHOLDER = re.compile(r'\{([a-z_0-9]+)\}')
_SPACES = re.compile(r'[ \t　]{2,}')
_COMMAS = re.compile(r'\s*,(\s*,)*\s*')


def _invalid(message, field):
    return BadRequestException(message, TEXT_INVALID, field=field)


def _text(value, field, limit):
    """None / 空串 → None；字符串（数字也收，如邮编 / 电话）去首尾空白；超长 / 别的类型 400 14019"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _invalid(f"{field} must be a string", field)
    text = str(value).strip()
    if len(text) > limit:
        raise _invalid(f"{field} must not exceed {limit} characters", field)
    return text or None


def parse_country(value, field='country'):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not is_valid_country(value):
        raise BadRequestException(f"{field} must be an ISO 3166-1 alpha-2 code", COUNTRY_INVALID, field=field)
    return normalize_country(value)


def parse_latin(value, field='latin'):
    """拉丁转写 {name, organization, address_line1, address_line2, locality, admin_area}：
    null / 空对象 → None；不是对象 / 字段不合法 400 14019。不认识的键丢掉。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid(f"{field} must be an object", field)
    result = {}
    for key in LATIN_FIELDS:
        text = _text(value.get(key), f'{field}.{key}', FIELD_LIMITS[key])
        if text is not None:
            result[key] = text
    return result or None


def parse_address(value, field='ship_to'):
    """一个结构化地址对象（DN 的 ship_to 等）→ 规范化的 dict（只留有值的字段，latin 也是）；null → None。
    zip_code 当 postal_code 收（两个都给且不同 → 400 14019）。不认识的键丢掉。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid(f"{field} must be an object", field)
    data = dict(value)
    if data.get('postal_code') in (None, '') and data.get('zip_code') not in (None, ''):
        data['postal_code'] = data['zip_code']
    elif data.get('zip_code') not in (None, '') and str(data['zip_code']).strip() != str(data['postal_code']).strip():
        raise _invalid(f"{field}.zip_code and {field}.postal_code differ", f'{field}.postal_code')
    result = {}
    for key in ADDRESS_FIELDS:
        if key == 'country':
            code = parse_country(data.get(key), f'{field}.country')
            if code:
                result[key] = code
            continue
        text = _text(data.get(key), f'{field}.{key}', FIELD_LIMITS[key])
        if text is not None:
            result[key] = text
    latin = parse_latin(data.get('latin'), f'{field}.latin')
    if latin:
        result['latin'] = latin
    return result or None


def parse_columns(data: dict, limits: dict, prefix=None) -> dict:
    """收件人 / 仓库请求体里的结构化地址列（limits 列出的字段，再加 latin）→ 要写入的值，只含请求里带了的键
    （null / 空串 = 清空）。"""
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, limit in limits.items():
        if key in data:
            result[key] = _text(data[key], f'{prefix}.{key}' if prefix else key, limit)
    if 'latin' in data:
        result['latin'] = parse_latin(data['latin'], f'{prefix}.latin' if prefix else 'latin')
    return result


def postal_code_of(data: dict, field_prefix=None, limit=10):
    """请求体里的 zip_code / postal_code（同一个值，两个名字都收）→ (是否带了, 值)；两个都给且不同 → 400 14019"""
    if not isinstance(data, dict):
        return False, None
    has_zip, has_postal = 'zip_code' in data, 'postal_code' in data
    if not (has_zip or has_postal):
        return False, None
    name = f'{field_prefix}.postal_code' if field_prefix else 'postal_code'
    zip_code = _text(data.get('zip_code'), name, limit) if has_zip else None
    postal = _text(data.get('postal_code'), name, limit) if has_postal else None
    if has_zip and has_postal and zip_code != postal:
        raise _invalid("zip_code and postal_code differ (they are the same field)", name)
    return True, (zip_code if has_zip else postal)


# ------------------------------------------------------------------
# 排版
# ------------------------------------------------------------------

def is_structured(address) -> bool:
    """地址里有结构化的地址行（都道府县 / 城市 / 街道……），不只是姓名 / 电话 / 邮编"""
    return bool(address) and any((address.get(key) or '').strip() for key in STRUCTURED_FIELDS
                                 if isinstance(address.get(key), str))


def latin_view(address) -> dict:
    """拉丁文字的地址：latin 里有的字段覆盖本地文字（没有转写的字段用本地文字）"""
    address = dict(address or {})
    latin = address.pop('latin', None) or {}
    for key in LATIN_FIELDS:
        if latin.get(key):
            address[key] = latin[key]
    return address


def _render_line(template: str, values: dict) -> str:
    """一行模板 → 文字：空字段连同它前面的分隔一起去掉（「{locality}, {admin_area} {postal_code}」没有州时为
    「Springfield 62701」）；第一个字段前的字面量（如「〒」）只在该字段有值时保留；全空为 ''"""
    pieces = _PLACEHOLDER.split(template)          # [字面量, 字段, 字面量, 字段, …, 字面量]
    literals, keys = pieces[0::2], pieces[1::2]
    if not keys:
        return template.strip()
    out = ''
    for index, key in enumerate(keys):
        value = str(values.get(key) or '').strip()
        if not value:
            continue
        if out:
            out += literals[index] + value
        else:
            out = (literals[index] if index == 0 else '') + value
    if out:
        out += literals[-1]
    line = _COMMAS.sub(', ', out)
    line = _SPACES.sub(' ', line)
    return line.strip().strip(',').strip()


def format_lines(address, style='local', with_names=True, with_country=None) -> list:
    """地址 → 按收件国（address.country）的规则包排成几行。style：local（本地文字）/ latin（拉丁转写，没有转写的
    字段用本地文字）。with_names=False 不要姓名 / 公司行；with_country 默认 latin 时要国名行、local 时不要。"""
    from warehouse.country_rules import rules_for
    if not address:
        return []
    values = latin_view(address) if style == 'latin' else dict(address)
    country = normalize_country(values.get('country'))
    values['country_name'] = COUNTRY_NAMES_EN.get(country) or country or None
    if with_country is None:
        with_country = style == 'latin'
    lines = []
    for template in rules_for(country).address_lines(style):
        keys = set(_PLACEHOLDER.findall(template))
        if not with_names and keys and keys <= set(NAME_FIELDS):
            continue
        if not with_country and keys == {'country_name'}:
            continue
        line = _render_line(template, values)
        if line:
            lines.append(line)
    return lines


def format_single_line(address, style='local', with_names=False, limit=None) -> str:
    """排成一行（DN 的 shipping_address、收件人 / 仓库的 address 由结构化字段生成时用）；limit 截断长度"""
    from warehouse.country_rules import rules_for
    if not address:
        return ''
    country = normalize_country(address.get('country'))
    text = rules_for(country).separator(style).join(format_lines(address, style, with_names=with_names))
    return text[:limit] if limit else text


# ------------------------------------------------------------------
# 收件人 / 仓库 → 结构化地址
# ------------------------------------------------------------------

def _clean(address: dict) -> dict:
    result = {k: v for k, v in address.items() if v not in (None, '', {}, [])}
    return result


def recipient_address(recipient) -> dict:
    """收件人主数据 → 结构化地址（DN 的 ship_to 快照从这里拷）。
    name = 联系人（没有用收件方名称），organization = 收件人的 organization（没有且有联系人时用收件方名称）。"""
    if recipient is None:
        return {}
    display = getattr(recipient, 'display_name', None) or getattr(recipient, 'name', None)
    contact = (getattr(recipient, 'contact', None) or '').strip() or None
    organization = (getattr(recipient, 'organization', None) or '').strip() or None
    if organization is None and contact and display and display != contact:
        organization = display
    return _clean({
        'country': normalize_country(getattr(recipient, 'country', None)) or None,
        'postal_code': getattr(recipient, 'zip_code', None),
        'admin_area': getattr(recipient, 'admin_area', None),
        'locality': getattr(recipient, 'locality', None),
        'dependent_locality': getattr(recipient, 'dependent_locality', None),
        'address_line1': getattr(recipient, 'address_line1', None),
        'address_line2': getattr(recipient, 'address_line2', None),
        'organization': organization,
        'name': contact or display,
        'phone': getattr(recipient, 'phone', None),
        'email': getattr(recipient, 'display_email', None) or getattr(recipient, 'email', None),
        'latin': getattr(recipient, 'latin', None) or None,
    })


def warehouse_address(warehouse) -> dict:
    """仓库（没有地址时用公司的）→ 发件人的结构化地址。name / organization = 公司名（本地，没有用英文名），
    contact = 联系人；没有结构化字段的旧仓库：address_line1 = 单行地址（本地，没有用英文）。
    latin：仓库的 latin，没有时 address_line1 = 英文单行地址（address_en）、name = 公司英文名。"""
    if warehouse is None:
        return {}
    company = warehouse.company
    own = is_structured({k: getattr(warehouse, k, None) for k in STRUCTURED_FIELDS}) \
        or bool((warehouse.address or '').strip())
    company_name = (company.name if company is not None else None) or \
        (company.legal_name_en if company is not None else None)
    country = normalize_country(warehouse.country_code or (company.country_code if company is not None else None))
    address = {
        'country': country or None,
        'postal_code': warehouse.zip_code if own else (company.zip_code if company is not None else None),
        'admin_area': getattr(warehouse, 'admin_area', None),
        'locality': getattr(warehouse, 'locality', None),
        'address_line1': getattr(warehouse, 'address_line1', None),
        'address_line2': getattr(warehouse, 'address_line2', None),
        'organization': company_name,
        'name': company_name,
        'contact': warehouse.contact_name_en or (company.export_contact_name if company is not None else None),
        'phone': warehouse.phone or (company.phone if company is not None else None),
        'email': company.email if company is not None else None,
    }
    if not is_structured(address):
        text = (warehouse.address or '').strip() if own else ((company.address or '').strip() if company else '')
        address['address_line1'] = text or (warehouse.address_en or '').strip() or \
            ((company.address_en or '').strip() if company is not None else '') or None
    latin = dict(getattr(warehouse, 'latin', None) or {})
    if not latin:
        english = (warehouse.address_en or '').strip() or \
            ((company.address_en or '').strip() if company is not None else '')
        if english:
            latin['address_line1'] = english
    if company is not None and company.legal_name_en:
        latin.setdefault('name', company.legal_name_en)
        latin.setdefault('organization', company.legal_name_en)
    address['latin'] = latin or None
    return _clean(address)


def dn_ship_to(dn) -> dict:
    """DN 的收件地址：DN 的 ship_to 快照 → 收件人的结构化字段 → 旧数据（address_line1 = DN 的 shipping_address 字符串，
    邮编 / 国家 / 姓名 / 电话用收件人的）。没有收件国时用收件人的 country。"""
    recipient = dn.recipient
    base = recipient_address(recipient)
    snapshot = dict(dn.ship_to or {})
    if snapshot:
        address = dict(snapshot)
        # 快照没带的联系方式（姓名 / 电话 / 邮箱）用收件人的
        for key in ('name', 'phone', 'email', 'country', 'postal_code'):
            if not address.get(key) and base.get(key):
                address[key] = base[key]
        return _clean(address)
    if is_structured(base):
        return base
    address = {k: base.get(k) for k in ('country', 'postal_code', 'organization', 'name', 'phone', 'email')}
    address['address_line1'] = (dn.shipping_address or '').strip() or None
    address['latin'] = base.get('latin')
    return _clean(address)
