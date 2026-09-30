"""FedEx Ship API 的请求组装与响应解析（纯函数，不碰数据库、不联网）。

字段名按 FedEx Ship API v1（developer.fedex.com 发布的 OpenAPI）。拿不准的取值集中在本文件顶部的常量里。

规则（写死在这里，改之前先看 README「承运商对接（FedEx）」）：
- 地址：FedEx 每行 streetLines ≤ 35 字符、最多 3 行，city ≤ 35 字符。放不下就拦截（不截断地址），
  名称（companyName ≤ 35）超长截断。
- 发件人地址来自一个英文地址字符串（仓库 address_en，没有则公司 address_en）：按换行 / 逗号切段，
  去掉末尾的国家名段，去掉邮编（仓库 / 公司的 zip_code；日本地址没有时认 NNN-NNNN），
  最后一段为 city，其余段按 35 字符折行。
- 收件人：consignee 的 address_line1/2 折行；邮编没有也带空串键（FedEx 对没有邮编的地区缺这个键会 422）；
  州代码只对 US / CA / PR 传（两位字母，缺了拦截）。
- 电话只保留数字。
- 重量 kg 向上取两位小数（最小 0.01）；外箱尺寸 mm → cm 向上取整。
- 申告价额按箱均分（余数给前面的箱子），合计等于申告价额。
- 商品净重：商品主数据有单件重量的行 = 单件重量 × 已打包数量；没有的行分摊「箱子总毛重 − 已知净重」
  （不为正时按数量占比分摊总毛重），按已打包数量比例分。
- 面单打印方式（label_format）：A4（激光打印机，PDF + PAPER_85X11_TOP_HALF_LABEL）/ THERMAL（4 英寸面单机，
  PDF + STOCK_4X6，页面就是 4x6 英寸）。imageType / stock 可按格式用配置覆盖（ZPLII / EPL2 也支持）。
  注意 PAPER_4X6 在 FedEx 回的是 Letter 页、面单在左上角（sandbox 实测），热敏机要用 STOCK_4X6。
- 面单存档：响应里所有文档（每箱 packageDocuments、整票 shipmentDocuments）按「面单 → 辅助运单 → 其他」排序，
  PDF / PNG 合成一个 PDF，ZPLII / EPL2 把原始指令按同样顺序拼成一个文件；每份文档的类型与页数记在 parts 里。
  国际件的辅助运单（FEDEX AWB COPY）FedEx 放在每票第一箱面单 PDF 的后几页里（不是单独的文档），请求里不用另外要。
"""
import base64
import io
import logging
import math
import re
import textwrap
from decimal import Decimal, ROUND_CEILING

# ------------------------------------------------------------------
# 常量（FedEx 取值；标注「未确认」的是文档没写清、按主流实现取的值）
# ------------------------------------------------------------------
MAX_LINE = 35                 # streetLines 每行 / city 上限
MAX_STREET_LINES = 3          # 超过 3 行 FedEx 忽略
MAX_COMPANY_NAME = 35
MAX_PERSON_NAME = 70
MAX_EMAIL = 80
MAX_PACKAGES_PER_REQUEST = 30   # requestedPackageLineItems 单次请求上限
MAX_INVOICE_REFERENCE = 30      # commercialInvoice.customerReferences INVOICE_NUMBER
MAX_PACKAGE_REFERENCE = 40      # 包裹 CUSTOMER_REFERENCE（Express）
STATE_CODE_COUNTRIES = ('US', 'CA', 'PR')
PACKAGING_TYPE = 'YOUR_PACKAGING'
LABEL_FORMAT_TYPE = 'COMMON2D'
QUANTITY_UNITS_DEFAULT = 'PCS'
COMMODITY_NUMBER_OF_PIECES = 1          # 未确认：Odoo 固定 1，karrio 用数量
COMMODITY_WEIGHT_IS_TOTAL = True        # 未确认：2025 版文档写「商品总重」，2024 版写「单件重量」
TOTAL_WEIGHT_UNIT = 'LB'                # 未确认：requestedShipment.totalWeight 文档写「磅」，karrio 也换算成磅
KG_TO_LB = Decimal('2.20462262')
ETD_SPECIAL_SERVICE = 'ELECTRONIC_TRADE_DOCUMENTS'
ETD_DOCUMENT_TYPE = 'COMMERCIAL_INVOICE'
SUPPORTED_LABEL_IMAGE_TYPES = ('PDF', 'PNG', 'ZPLII', 'EPL2')
SUPPORTED_DUTIES_PAYMENT_TYPES = ('RECIPIENT', 'SENDER')

# 面单打印方式 → 默认 (imageType, labelStockType)；配置 FEDEX_LABEL_<FORMAT>_IMAGE_TYPE / _STOCK_TYPE 可覆盖
LABEL_FORMATS = ('A4', 'THERMAL')
DEFAULT_LABEL_FORMAT = 'A4'
LABEL_FORMAT_DEFAULTS = {
    'A4': ('PDF', 'PAPER_85X11_TOP_HALF_LABEL'),   # A4 / Letter 普通纸：上半页面单，下半页折叠说明
    'THERMAL': ('PDF', 'STOCK_4X6'),               # 4x6 英寸（约 100×150 mm）热敏面单纸
}
# 面单存档文件：imageType → (扩展名, Content-Type)
LABEL_ARCHIVE_FILES = {
    'PDF': ('pdf', 'application/pdf'),
    'PNG': ('pdf', 'application/pdf'),
    'ZPLII': ('zpl', 'application/octet-stream'),
    'EPL2': ('epl', 'application/octet-stream'),
}
# 只在 URL_ONLY 模式出现的「合并件」与各箱面单重复，不进存档
_MERGED_CONTENT_TYPES = ('MERGED_LABEL_DOCUMENTS', 'MERGED_LABELS_ONLY')

# FedEx 自己的币种代码（与 ISO 4217 不同的那些；日元是 JYE，响应里可能回 JPY，两种都认）
FEDEX_CURRENCY_CODES = {
    'JPY': 'JYE', 'GBP': 'UKL', 'CHF': 'SFR', 'KRW': 'WON', 'SGD': 'SID', 'TWD': 'NTD',
    'MXN': 'NMP', 'AED': 'DHS', 'CLP': 'CHP', 'ARS': 'ARN', 'KWD': 'KUD', 'XCD': 'ECD',
    'KYD': 'CID', 'DOP': 'RDD', 'JMD': 'JAD',
}
_ISO_FROM_FEDEX = {v: k for k, v in FEDEX_CURRENCY_CODES.items()}

# 报关快照的出口理由 → commercialInvoice.shipmentPurpose
SHIPMENT_PURPOSES = {
    'SALE': 'SOLD',
    'SOLD': 'SOLD',
    'GIFT': 'GIFT',
    'SAMPLE': 'SAMPLE',
    'REPAIR': 'REPAIR_AND_RETURN',
    'RETURN': 'REPAIR_AND_RETURN',
    'PERSONAL_EFFECTS': 'PERSONAL_EFFECTS',
}
DEFAULT_SHIPMENT_PURPOSE = 'NOT_SOLD'

# 收件人税号类型（报关快照 recipient_tax_id_type）→ FedEx tins[].tinType
# tinType 可选值：BUSINESS_NATIONAL / BUSINESS_STATE / BUSINESS_UNION / PERSONAL_NATIONAL / PERSONAL_STATE / FEDERAL
# EORI = BUSINESS_UNION 有 FedEx 官方示例；其余 FedEx 没给对应表，按「企业号 → BUSINESS_NATIONAL、个人号 → PERSONAL_NATIONAL」
TIN_TYPES = {
    'EORI': 'BUSINESS_UNION',
    'VAT': 'BUSINESS_NATIONAL',
    'PCCC': 'PERSONAL_NATIONAL',       # 韩国个人通关符号
    'KR_BRN': 'BUSINESS_NATIONAL',     # 韩国事业者登录号
    'CPF': 'PERSONAL_NATIONAL',        # 巴西个人税号
    'CNPJ': 'BUSINESS_NATIONAL',       # 巴西企业税号
    'EIN': 'BUSINESS_NATIONAL',        # 美国雇主识别号
    'USCC': 'BUSINESS_NATIONAL',       # 中国统一社会信用代码
    'TW_UBN': 'BUSINESS_NATIONAL',     # 台湾统一编号
    'OTHER': 'BUSINESS_NATIONAL',
}
DEFAULT_TIN_TYPE = 'BUSINESS_NATIONAL'
SHIPPER_TIN_TYPE = 'BUSINESS_NATIONAL'

_JP_POSTAL_RE = re.compile(r'(?<!\d)(\d{3})-?(\d{4})(?!\d)')
_SPLIT_RE = re.compile(r'[\n\r,]+')
_STATE_RE = re.compile(r'^[A-Za-z]{2}$')


class AddressError(ValueError):
    """地址无法按 FedEx 的格式放下（缺城市 / 太长等）"""


# ------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------

def fedex_currency(code) -> str:
    code = (code or '').strip().upper()
    return FEDEX_CURRENCY_CODES.get(code, code)


def iso_currency(code):
    code = (code or '').strip().upper()
    return _ISO_FROM_FEDEX.get(code, code) or None


def label_settings(config, label_format=None) -> dict:
    """面单打印方式 → {label_format, image_type, stock_type}。config 是 dict 样的配置（Flask config）。
    label_format 为空取 FEDEX_DEFAULT_LABEL_FORMAT；不认识的格式原样返回（由调用方报错）。"""
    fmt = (label_format or config.get('FEDEX_DEFAULT_LABEL_FORMAT') or DEFAULT_LABEL_FORMAT).strip().upper()
    image_default, stock_default = LABEL_FORMAT_DEFAULTS.get(fmt, LABEL_FORMAT_DEFAULTS[DEFAULT_LABEL_FORMAT])
    return {
        'label_format': fmt,
        'image_type': (config.get(f'FEDEX_LABEL_{fmt}_IMAGE_TYPE') or image_default).strip().upper(),
        'stock_type': (config.get(f'FEDEX_LABEL_{fmt}_STOCK_TYPE') or stock_default).strip().upper(),
    }


def tin_type_for(tax_id_type) -> str:
    return TIN_TYPES.get((tax_id_type or '').strip().upper(), DEFAULT_TIN_TYPE)


def shipment_purpose_for(export_reason) -> str:
    return SHIPMENT_PURPOSES.get((export_reason or '').strip().upper(), DEFAULT_SHIPMENT_PURPOSE)


def phone_digits(phone) -> str:
    return re.sub(r'\D', '', str(phone or ''))


def _clip(text, limit):
    text = ' '.join(str(text or '').split())
    return text[:limit] if text else None


def weight_kg(value) -> float:
    """kg 向上取两位小数，最小 0.01"""
    number = Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    return float(max(number, Decimal('0.01')))


def mm_to_cm(value) -> int:
    return max(int(math.ceil(int(value) / 10)), 1)


def split_evenly(total: int, count: int) -> list:
    """整数 total 均分成 count 份，余数从第一份起每份 +1；合计等于 total"""
    if count <= 0:
        return []
    base, remainder = divmod(int(total), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def wrap_lines(parts, width=MAX_LINE, max_lines=MAX_STREET_LINES) -> list:
    """地址片段（用 ", " 连成一段）按单词折成每行 ≤ width 的行，行尾逗号去掉。放不下抛 AddressError。"""
    text = ', '.join(' '.join(str(part or '').split()) for part in parts if str(part or '').strip())
    if not text:
        raise AddressError("street address is empty")
    lines = [line.rstrip(', ') for line in
             textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)]
    for line in lines:
        if len(line) > width:
            raise AddressError(f"'{line}' is longer than {width} characters")
    if len(lines) > max_lines:
        raise AddressError(f"street address does not fit in {max_lines} lines of {width} characters")
    return lines


def split_address_text(text, country_code, postal_code=None, country_name=None) -> dict:
    """一个英文地址字符串 → {streetLines, city, postalCode, countryCode}（规则见模块说明）"""
    country_code = (country_code or '').strip().upper()
    segments = [' '.join(s.split()) for s in _SPLIT_RE.split(str(text or ''))]
    segments = [s for s in segments if s]
    if not segments:
        raise AddressError("address is empty")

    country_words = {w for w in (country_code, (country_name or '').upper()) if w}
    # 末尾的国家名：单独一段，或粘在最后一段末尾（"Fukuoka 810-0000 Japan"）
    if segments and segments[-1].upper() in country_words:
        segments.pop()
    if segments and country_name:
        tail = re.compile(rf'\s+{re.escape(country_name)}\.?$', re.I)
        segments[-1] = tail.sub('', segments[-1]).strip()

    postal = ' '.join(str(postal_code or '').replace('〒', ' ').split()) or None
    if not postal and country_code == 'JP':
        for segment in segments:
            match = _JP_POSTAL_RE.search(segment)
            if match:
                postal = f"{match.group(1)}-{match.group(2)}"
                break
    if postal:
        variants = {postal, postal.replace('-', ''), postal.replace(' ', '')}
        digits = re.sub(r'\D', '', postal)
        if country_code == 'JP' and len(digits) == 7:
            variants |= {digits, f"{digits[:3]}-{digits[3:]}"}
        cleaned = []
        for segment in segments:
            for variant in sorted(variants, key=len, reverse=True):
                segment = segment.replace(variant, ' ')
            segment = ' '.join(segment.replace('〒', ' ').split()).strip(' ,.-')
            if segment:
                cleaned.append(segment)
        segments = cleaned

    if len(segments) < 2:
        raise AddressError("cannot tell the street from the city; separate them with commas "
                           "(e.g. '1-2-3 Example, Minato-ku, Tokyo 105-0000')")
    city = segments[-1]
    if len(city) > MAX_LINE:
        raise AddressError(f"city '{city}' is longer than {MAX_LINE} characters")
    result = {
        'streetLines': wrap_lines(segments[:-1]),
        'city': city,
        'postalCode': postal or '',
        'countryCode': country_code,
    }
    return result


def consignee_address(consignee: dict, country_code: str) -> dict:
    """报关快照 consignee → FedEx address。邮编没有也带空串键；州代码只对 US / CA / PR 传。"""
    country_code = (country_code or '').strip().upper()
    lines = [consignee.get('address_line1'), consignee.get('address_line2')]
    if not any(lines):
        raise AddressError("address_line1 is missing")
    street = []
    for line in lines:
        if line:
            street.extend(wrap_lines([line]))
    if len(street) > MAX_STREET_LINES:
        raise AddressError(f"street address does not fit in {MAX_STREET_LINES} lines of {MAX_LINE} characters")
    city = ' '.join(str(consignee.get('city') or '').split())
    if not city:
        raise AddressError("city is missing")
    if len(city) > MAX_LINE:
        raise AddressError(f"city is longer than {MAX_LINE} characters")
    address = {
        'streetLines': street,
        'city': city,
        'postalCode': ' '.join(str(consignee.get('postal_code') or '').split()),
        'countryCode': country_code,
    }
    if country_code in STATE_CODE_COUNTRIES:
        state = (consignee.get('state') or '').strip()
        if not _STATE_RE.match(state):
            raise AddressError(f"a 2-letter state / province code is required for {country_code}")
        address['stateOrProvinceCode'] = state.upper()
    return address


def allocate_commodity_weights(lines, gross_total) -> list:
    """每行商品重量（kg，Decimal）。lines: [{quantity, unit_weight_kg|None}]；规则见模块说明。"""
    gross_total = Decimal(str(gross_total or 0))
    known = [Decimal(str(l['unit_weight_kg'])) * l['quantity'] if l.get('unit_weight_kg') is not None else None
             for l in lines]
    unknown_qty = sum(l['quantity'] for l, w in zip(lines, known) if w is None)
    if unknown_qty:
        total_qty = sum(l['quantity'] for l in lines) or 1
        remaining = gross_total - sum(w for w in known if w is not None)
        if remaining <= 0:
            remaining = gross_total * Decimal(unknown_qty) / Decimal(total_qty)
    result = []
    for line, weight in zip(lines, known):
        if weight is None:
            weight = remaining * Decimal(line['quantity']) / Decimal(unknown_qty)
        result.append(weight)
    return result


# ------------------------------------------------------------------
# 请求组装
# ------------------------------------------------------------------

def _money(amount, currency):
    return {'amount': amount, 'currency': currency}


def build_ship_request(*, account_number, settings, label, ship_date, shipper, recipient, packages,
                       commodities, invoice, reference=None, etd_document_id=None) -> dict:
    """组装 POST /ship/v1/shipments 请求体。

    settings: {service_type, pickup_type, duties_payment_type}
    label: {image_type, stock_type}（label_settings() 的结果）
    shipper / recipient: {company_name, person_name, phone, email, address{...}, tins[]}
    packages: [{gross_weight_kg, length_mm, width_mm, height_mm, declared_value|None}]
    commodities: [{description, origin_country, hs_code, quantity, quantity_unit, unit_value, amount,
                   weight_kg, part_number}]
    invoice: {invoice_number, currency(ISO), incoterm, export_reason, freight, insurance,
              goods_value, declared_value|None}
    """
    currency = fedex_currency(invoice['currency'])

    def party(data):
        contact = {'phoneNumber': phone_digits(data.get('phone'))}
        company = _clip(data.get('company_name'), MAX_COMPANY_NAME)
        person = _clip(data.get('person_name'), MAX_PERSON_NAME)
        email = _clip(data.get('email'), MAX_EMAIL)
        if person:
            contact['personName'] = person
        if company:
            contact['companyName'] = company
        if email:
            contact['emailAddress'] = email
        result = {'contact': contact, 'address': data['address']}
        if data.get('tins'):
            result['tins'] = data['tins']
        return result

    package_items = []
    for index, package in enumerate(packages, start=1):
        item = {
            'sequenceNumber': index,
            'weight': {'units': 'KG', 'value': weight_kg(package['gross_weight_kg'])},
            'dimensions': {
                'length': mm_to_cm(package['length_mm']),
                'width': mm_to_cm(package['width_mm']),
                'height': mm_to_cm(package['height_mm']),
                'units': 'CM',
            },
        }
        if package.get('declared_value') is not None:
            item['declaredValue'] = _money(package['declared_value'], currency)
        # 面单上印 REF（DN 订单号）与 INV（发票号），仓库贴单时好核对
        references = []
        if reference:
            references.append({'customerReferenceType': 'CUSTOMER_REFERENCE',
                               'value': str(reference)[:MAX_PACKAGE_REFERENCE]})
        if invoice.get('invoice_number'):
            references.append({'customerReferenceType': 'INVOICE_NUMBER',
                               'value': str(invoice['invoice_number'])[:MAX_INVOICE_REFERENCE]})
        if references:
            item['customerReferences'] = references
        package_items.append(item)

    commodity_items = []
    for line in commodities:
        weight = Decimal(str(line['weight_kg']))
        if not COMMODITY_WEIGHT_IS_TOTAL and line['quantity']:
            weight = weight / Decimal(line['quantity'])
        commodity_items.append({
            'description': str(line['description'])[:450],
            'countryOfManufacture': line['origin_country'],
            'harmonizedCode': re.sub(r'\D', '', str(line['hs_code'] or '')),
            'quantity': line['quantity'],
            'quantityUnits': line.get('quantity_unit') or QUANTITY_UNITS_DEFAULT,
            'numberOfPieces': COMMODITY_NUMBER_OF_PIECES,
            'unitPrice': _money(line['unit_value'], currency),
            'customsValue': _money(line['amount'], currency),
            'weight': {'units': 'KG', 'value': weight_kg(weight)},
            **({'partNumber': str(line['part_number'])[:50]} if line.get('part_number') else {}),
        })

    commercial_invoice = {
        'termsOfSale': invoice['incoterm'],
        'shipmentPurpose': shipment_purpose_for(invoice.get('export_reason')),
        'customerReferences': [{
            'customerReferenceType': 'INVOICE_NUMBER',
            'value': str(invoice['invoice_number'])[:MAX_INVOICE_REFERENCE],
        }],
    }
    if invoice.get('freight'):
        commercial_invoice['freightCharge'] = _money(invoice['freight'], currency)

    duties_type = (settings.get('duties_payment_type') or 'RECIPIENT').upper()
    duties_payment = {'paymentType': duties_type}
    if duties_type == 'SENDER':
        duties_payment['payor'] = {'responsibleParty': {'accountNumber': {'value': account_number}}}

    customs = {
        'dutiesPayment': duties_payment,
        'isDocumentOnly': False,
        'commercialInvoice': commercial_invoice,
        'commodities': commodity_items,
        'totalCustomsValue': _money(invoice['goods_value'], currency),
    }
    if invoice.get('insurance'):
        customs['insuranceCharge'] = _money(invoice['insurance'], currency)

    gross_total = sum((Decimal(str(p['gross_weight_kg'])) for p in packages), Decimal(0))
    total_weight = gross_total * KG_TO_LB if TOTAL_WEIGHT_UNIT == 'LB' else gross_total
    requested = {
        'shipDatestamp': ship_date.isoformat(),
        'serviceType': settings['service_type'],
        'packagingType': PACKAGING_TYPE,
        'pickupType': settings['pickup_type'],
        'shipper': party(shipper),
        'recipients': [party(recipient)],
        'shippingChargesPayment': {
            'paymentType': 'SENDER',
            'payor': {'responsibleParty': {'accountNumber': {'value': account_number}}},
        },
        'labelSpecification': {
            'imageType': label['image_type'],
            'labelStockType': label['stock_type'],
            'labelFormatType': LABEL_FORMAT_TYPE,
        },
        'customsClearanceDetail': customs,
        'totalPackageCount': len(package_items),
        'totalWeight': float(total_weight.quantize(Decimal('0.1'), rounding=ROUND_CEILING)),
        'requestedPackageLineItems': package_items,
    }
    if invoice.get('declared_value') is not None:
        requested['totalDeclaredValue'] = _money(invoice['declared_value'], currency)
    if etd_document_id:
        requested['shipmentSpecialServices'] = {
            'specialServiceTypes': [ETD_SPECIAL_SERVICE],
            'etdDetail': {
                'attachedDocuments': [{
                    'documentType': ETD_DOCUMENT_TYPE,
                    'documentReference': str(invoice['invoice_number'])[:MAX_INVOICE_REFERENCE],
                    'description': 'Commercial Invoice',
                    'documentId': etd_document_id,
                }],
            },
        }
    return {
        'labelResponseOptions': 'LABEL',
        'accountNumber': {'value': account_number},
        'requestedShipment': requested,
    }


# ------------------------------------------------------------------
# 响应解析
# ------------------------------------------------------------------

def _alerts(*containers) -> list:
    result = []
    for container in containers:
        for alert in (container or {}).get('alerts') or []:
            if isinstance(alert, dict):
                result.append({'code': alert.get('code'), 'alert_type': alert.get('alertType'),
                               'message': alert.get('message')})
    return result


def _net_charge(completed: dict):
    rating = completed.get('shipmentRating') or {}
    details = [d for d in rating.get('shipmentRateDetails') or [] if isinstance(d, dict)]
    if not details:
        return None, None
    actual = rating.get('actualRateType')
    chosen = next((d for d in details if actual and d.get('rateType') == actual), details[0])
    amount = chosen.get('totalNetCharge')
    if amount is None:
        amount = chosen.get('totalNetFedExCharge')
    try:
        amount = Decimal(str(amount)) if amount is not None and not isinstance(amount, bool) else None
    except ArithmeticError:
        amount = None
    return amount, iso_currency(chosen.get('currency'))


def _document(item, source, package_sequence=None, tracking_number=None) -> dict:
    encoded = item.get('encodedLabel')
    return {
        'source': source,
        'package_sequence': package_sequence,
        'tracking_number': tracking_number or item.get('trackingNumber'),
        'content_type': (item.get('contentType') or 'LABEL').upper(),
        'doc_type': (item.get('docType') or '').upper() or None,
        'copies': item.get('copiesToPrint'),
        'content': base64.b64decode(encoded) if encoded else None,
        'url': item.get('url'),
    }


def _document_rank(document) -> int:
    """面单 → 辅助运单 → 其他"""
    if document['content_type'] == 'LABEL':
        return 0
    if 'AUXILIARY' in document['content_type']:
        return 1
    return 2


def parse_ship_response(body: dict) -> dict:
    """Ship API 成功响应 → {tracking_number, package_tracking_numbers, service_type, ship_date,
    net_charge(Decimal|None), currency, documents[...], transaction_id, alerts}。

    documents：所有 packageDocuments（按箱号）与 shipmentDocuments，按「面单 → 辅助运单 → 其他」稳定排序；
    每项 {source, package_sequence, tracking_number, content_type, doc_type, copies, content(bytes|None), url}。
    取不到主运单号抛 ValueError（此时无法取消，调用方记日志）。
    """
    output = body.get('output') or {}
    shipments = output.get('transactionShipments') or []
    if not shipments or not isinstance(shipments[0], dict):
        raise ValueError("FedEx response has no transactionShipments")
    shipment = shipments[0]
    completed = shipment.get('completedShipmentDetail') or {}
    master = (shipment.get('masterTrackingNumber')
              or ((completed.get('masterTrackingId') or {}).get('trackingNumber')))
    pieces = [p for p in shipment.get('pieceResponses') or [] if isinstance(p, dict)]
    if not master and pieces:
        master = pieces[0].get('masterTrackingNumber') or pieces[0].get('trackingNumber')
    if not master:
        raise ValueError("FedEx response has no master tracking number")

    pieces.sort(key=lambda p: p.get('packageSequenceNumber') or 0)
    documents = []
    package_tracking = []
    for index, piece in enumerate(pieces, start=1):
        sequence = piece.get('packageSequenceNumber') or index
        if piece.get('trackingNumber'):
            package_tracking.append(piece['trackingNumber'])
        for item in piece.get('packageDocuments') or []:
            if isinstance(item, dict):
                documents.append(_document(item, 'package', sequence, piece.get('trackingNumber')))
    for item in shipment.get('shipmentDocuments') or []:
        if isinstance(item, dict):
            documents.append(_document(item, 'shipment'))
    documents.sort(key=_document_rank)

    amount, currency = _net_charge(completed)
    return {
        'tracking_number': str(master),
        'package_tracking_numbers': package_tracking,
        'service_type': shipment.get('serviceType'),
        'ship_date': shipment.get('shipDatestamp'),
        'net_charge': amount,
        'currency': currency,
        'documents': documents,
        'transaction_id': body.get('transactionId'),
        'alerts': _alerts(output, shipment),
    }


# ------------------------------------------------------------------
# 面单存档
# ------------------------------------------------------------------

def _png_to_pdf(content: bytes) -> bytes:
    """PNG 面单 → 单页 PDF（按 203 dpi 定页面大小）"""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    image = ImageReader(io.BytesIO(content))
    width_px, height_px = image.getSize()
    width, height = width_px * 72 / 203, height_px * 72 / 203
    pdf = canvas.Canvas(out, pagesize=(width, height))
    pdf.drawImage(image, 0, 0, width=width, height=height)
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def _part(document, archived, pages=None, note=None) -> dict:
    part = {k: document[k] for k in ('source', 'package_sequence', 'tracking_number', 'content_type',
                                     'doc_type', 'copies')}
    part.update({'pages': pages, 'archived': archived})
    if note:
        part['note'] = note
    return part


def build_label_archive(documents, image_type) -> dict:
    """所有面单文档 → 一个存档文件 {content, extension, content_type, parts}。

    PDF / PNG：PDF 文档逐页拼接、PNG 每张转一页，合成一个 PDF（只有一份 PDF 时原样保存）；
    ZPLII / EPL2：同类型的原始指令按顺序拼接。其它类型、只给了 URL 的、合并件不进存档，parts 里 archived=false。
    一份都存不进去抛 ValueError。
    """
    image_type = (image_type or 'PDF').upper()
    if image_type not in LABEL_ARCHIVE_FILES:
        raise ValueError(f"Label image type {image_type} is not supported")
    extension, content_type = LABEL_ARCHIVE_FILES[image_type]
    parts = []
    pdf_chunks = []     # (bytes, pages)
    raw_chunks = []
    pdf_mode = extension == 'pdf'

    pypdf_logger = logging.getLogger('pypdf')
    level = pypdf_logger.level
    # FedEx 的面单 PDF 字典里有重复键，pypdf 每页都会记 warning（无害）
    pypdf_logger.setLevel(logging.ERROR)
    try:
        from pypdf import PdfReader, PdfWriter
        for document in documents:
            kind = document['doc_type'] or image_type
            if document['content'] is None:
                parts.append(_part(document, False, note='no content (URL only)'))
            elif document['content_type'] in _MERGED_CONTENT_TYPES:
                parts.append(_part(document, False, note='merged duplicate'))
            elif pdf_mode and kind in ('PDF', 'PNG'):
                content = document['content'] if kind == 'PDF' else _png_to_pdf(document['content'])
                pages = len(PdfReader(io.BytesIO(content)).pages)
                pdf_chunks.append(content)
                parts.append(_part(document, True, pages))
            elif not pdf_mode and kind == image_type:
                text = document['content'].rstrip(b'\r\n') + b'\n'
                pages = text.count(b'^XA') if image_type == 'ZPLII' else None
                raw_chunks.append(text)
                parts.append(_part(document, True, pages))
            else:
                parts.append(_part(document, False, note=f'{kind} cannot be archived as {extension}'))

        if pdf_mode:
            if not pdf_chunks:
                raise ValueError("FedEx response has no label that can be archived as PDF")
            if len(pdf_chunks) == 1:
                content = pdf_chunks[0]
            else:
                writer = PdfWriter()
                for chunk in pdf_chunks:
                    writer.append(PdfReader(io.BytesIO(chunk)))
                out = io.BytesIO()
                writer.write(out)
                content = out.getvalue()
        else:
            if not raw_chunks:
                raise ValueError(f"FedEx response has no {image_type} label")
            content = b''.join(raw_chunks)
    finally:
        pypdf_logger.setLevel(level)
    return {'content': content, 'extension': extension, 'content_type': content_type, 'parts': parts}
