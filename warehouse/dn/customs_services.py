"""DN 海外件：报关快照、装箱记录、出口单证（商业发票 CI / 装箱单 PL）。

- 报关快照（dn_customs）：对接方随 DN 带来的发票号、贸易条件、收件人、运费（及可选的运送保险费、
  运送申告价额）和每个商品的 HS / 英文品名 / 成交单价。
  结构错误拒绝（16063 / 16064），内容不全照收，缺什么由 problems 列出。
- 装箱（dn_packages）：箱号 / 毛重 / 外箱尺寸，DN 为 picked / packed 时整体替换；
  已签发单证后改箱子，现有单证自动作废。
- 单证（dn_documents）：条件齐全时生成 CI + PL 的 PDF 并存库（生成即定稿）。
  单证数据不变 → 返回现有版本；变了 → 作废旧版、版本 +1。
  同表里的承运商面单（shipping_label，见 carrier_services.py）不参与这里的签发 / 作废 / 发货拦截。
- 发票数量一律取已打包数量；原产国以 WMS 商品主数据为准（报关行里的只做记录）。
- DN 发货后（delivered / completed）快照、箱子、单证全部锁定（16065）。
"""
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import current_app

from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from extensions.transaction import transactional
from warehouse.common.countries import COUNTRY_NAMES_EN, is_valid_country, normalize_country
from .models import DN, DNCustoms, DNPackage, DNDocument

DOC_CI = 'commercial_invoice'
DOC_PL = 'packing_list'
DOC_TYPES = (DOC_CI, DOC_PL)

LOCKED_DN_STATUSES = ('delivered', 'completed')       # 发货后锁定
PACKAGE_EDITABLE_STATUSES = ('picked', 'packed')      # 可录箱子的 DN 状态
MAX_PACKAGES = 99
MAX_CUSTOMS_LINES = 500
# 数值上限：运费 / 保险费 / 申告价额 / 数量存 INTEGER 列（PostgreSQL 上限 2^31-1）；
# 单价 / 行金额存 JSON，上限 1e12 让「单价 × 打包数」的合计远在 Decimal 28 位精度以内
MAX_INT_VALUE = 2 ** 31 - 1
MAX_LINE_VALUE = Decimal('1000000000000')

# 无小数位的币种（金额按整数印）
ZERO_DECIMAL_CURRENCIES = frozenset({'JPY', 'KRW', 'VND', 'CLP', 'ISK', 'PYG', 'UGX', 'XAF', 'XOF'})

# 收件人税号类型 → 单证上的标签
TAX_ID_TYPE_LABELS = {
    'EORI': 'EORI No.',
    'PCCC': 'PCCC',
    'KR_BRN': 'Business Registration No.',
    'CPF': 'CPF',
    'CNPJ': 'CNPJ',
    'EIN': 'EIN',
    'USCC': 'USCC',
    'TW_UBN': 'UBN',
    'VAT': 'VAT No.',
    'OTHER': 'Tax ID',
}

EXPORT_REASON_LABELS = {
    'SALE': 'Sale',
    'GIFT': 'Gift',
    'SAMPLE': 'Sample',
    'RETURN': 'Return',
    'REPAIR': 'Repair',
    'PERSONAL_EFFECTS': 'Personal Effects',
}

DECLARATION_TEXT = (
    "I/We hereby certify that the information on this invoice is true and correct "
    "and that the contents of this shipment are as stated above."
)

_CUSTOMS_TEXT_LIMITS = {
    'invoice_number': 50,
    'currency': 10,
    'incoterm': 20,
    'export_reason': 50,
    'recipient_country': 10,
    'recipient_tax_id': 100,
    'recipient_tax_id_type': 30,
}
_UPPER_FIELDS = ('currency', 'incoterm', 'export_reason', 'recipient_country', 'recipient_tax_id_type')
CONSIGNEE_FIELDS = (
    'name', 'company', 'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country', 'phone',
)
_CONSIGNEE_TEXT_LIMIT = 255
_LINE_TEXT_LIMITS = {
    'goods_code': 50,
    'description_en': 500,
    'hs_code': 20,
    'jp_export_code': 20,
    'origin_country': 10,
    'quantity_unit': 10,
}

# 日本出口申报（輸出申告）用的 9 位统计品目番号：货值（FOB，不含运费 / 保险费）超过这个金额（JPY）时提示补齐
JP_EXPORT_CODE_THRESHOLD_JPY = 200000

_HS_STRIP = re.compile(r'[.\s\-]')
_CURRENCY_RE = re.compile(r'^[A-Z]{3}$')
_FILE_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


# ------------------------------------------------------------------
# 通用小工具
# ------------------------------------------------------------------

def _structure_error(message: str, field: str) -> BadRequestException:
    return BadRequestException(message, 16063, field=field, details={'field': field})


def _num(value):
    """Decimal → JSON 数字（整数值输出 int，否则 float）；None 原样。"""
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _currency_places(currency) -> int:
    return 0 if (currency or '').upper() in ZERO_DECIMAL_CURRENCIES else 2


def _quantize_money(value: Decimal, currency) -> Decimal:
    places = _currency_places(currency)
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def format_money(value, currency) -> str:
    if value is None:
        return ''
    places = _currency_places(currency)
    return f"{_quantize_money(Decimal(str(value)), currency):,.{places}f}"


def format_hs_code(hs_code) -> str:
    """'950300' → '9503.00'；'9503000073' → '9503.00.0073'。"""
    digits = _HS_STRIP.sub('', str(hs_code or ''))
    if len(digits) <= 4:
        return digits
    result = f"{digits[:4]}.{digits[4:6]}"
    if len(digits) > 6:
        result += f".{digits[6:]}"
    return result


def hs_code_valid(hs_code) -> bool:
    digits = _HS_STRIP.sub('', str(hs_code or ''))
    return digits.isdigit() and 6 <= len(digits) <= 10


def printed_hs_code(hs_code) -> str:
    """CI 上印的 HS：只取前 6 位（国际通用的 HS 部分）。7–10 位是各国自己的细分
    （如日本 9 位统计番号），只在报关视图里显示，不印到发给进口国的单证上。"""
    digits = _HS_STRIP.sub('', str(hs_code or ''))
    return format_hs_code(digits[:6])


def jp_export_code_problem(jp_export_code, hs_code):
    """9 位日本出口统计品目番号：非 9 位数字或前 6 位与 HS 不一致 → 返回原因，否则 None。"""
    digits = _HS_STRIP.sub('', str(jp_export_code or ''))
    if not (digits.isdigit() and len(digits) == 9):
        return "is not a 9-digit number"
    if hs_code_valid(hs_code) and digits[:6] != _HS_STRIP.sub('', str(hs_code))[:6]:
        return "does not match the first 6 digits of the HS code"
    return None


def is_ascii_text(text) -> bool:
    return all(32 <= ord(ch) < 127 for ch in text)


def _is_latin_char(ch: str) -> bool:
    """拉丁字符 = ASCII / Latin-1 补充 / Latin 扩展 A·B / Latin 扩展附加 / 常用标点。"""
    code = ord(ch)
    return (
        code < 0x250
        or 0x1E00 <= code <= 0x1EFF
        or 0x2000 <= code <= 0x206F
        or ch in '€™'   # € ™
    )


def has_non_latin(text) -> bool:
    return any(not _is_latin_char(ch) for ch in str(text or ''))


def _data_hash(data: dict) -> str:
    """单证数据指纹：同样的数据 → 同样的指纹 → 不升版本。"""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')
    ).hexdigest()


def country_name(code):
    """ISO 3166-1 alpha-2 → 英文国名；空值或无效代码返回 None。"""
    return COUNTRY_NAMES_EN.get(normalize_country(code)) or None


def _goods_origin(goods):
    """商品主数据的原产国（goods.origin_country，迁移 A 加的列）；没有录入返回 None。"""
    value = getattr(goods, 'origin_country', None)
    value = (value or '').strip().upper()
    return value or None


def _country_label(code) -> str:
    name = country_name(code)
    return f"{code} - {name}" if name else (code or '')


def _document_timezone():
    tz_name = current_app.config.get('DOCUMENT_TIMEZONE') or 'Asia/Tokyo'
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — 配错时区不能让出单证 500
        current_app.logger.warning("Invalid DOCUMENT_TIMEZONE %r, falling back to UTC", tz_name)
        return ZoneInfo('UTC')


# ------------------------------------------------------------------
# 报关快照：结构校验（16063 / 16064），内容不全照收
# ------------------------------------------------------------------

def _opt_text(value, field: str, limit: int, upper: bool = False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _structure_error(f"{field} must be a string", field)
    text = str(value).strip()
    if len(text) > limit:
        raise _structure_error(f"{field} must not exceed {limit} characters", field)
    if upper:
        text = text.upper()
    return text or None


def _opt_decimal(value, field: str, maximum: Decimal = None):
    """数字或数字字符串 → Decimal；None / 空串 → None；其它类型 16063。
    给了 maximum 时绝对值超过它也 16063（防止金额运算溢出 Decimal 精度 / 数据库整数列）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _structure_error(f"{field} must be a number", field)
    if isinstance(value, (int, float)):
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip().replace(',', '')
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise _structure_error(f"{field} must be a number", field)
    else:
        raise _structure_error(f"{field} must be a number", field)
    if not number.is_finite():
        raise _structure_error(f"{field} must be a number", field)
    if maximum is not None and abs(number) > maximum:
        raise _structure_error(f"{field} must not exceed {maximum:,}", field)
    return number


def _opt_non_negative_int(value, field: str, maximum: int = MAX_INT_VALUE):
    """非负整数（默认上限 = 数据库 INTEGER 上限）；超上限 / 小数 / 负数 16063。
    运费 / 保险费 / 申告价额的列是 INTEGER，只能收整数（带分的币种也按整数收，见 README）。"""
    number = _opt_decimal(value, field, maximum=Decimal(maximum))
    if number is None:
        return None
    if number != number.to_integral_value() or number < 0:
        raise _structure_error(f"{field} must be a non-negative integer", field)
    return int(number)


def parse_customs(raw, dn_goods_codes) -> dict:
    """校验并规范化报关快照。

    结构错误（customs 非对象、lines 非数组、行非对象、字段类型错误、超长）→ 400 16063（details.field）；
    行的 goods_code 不在 DN 明细里或重复 → 400 16064。缺字段 / 空值 / 单价 ≤0 等内容问题照收。
    """
    if not isinstance(raw, dict):
        raise _structure_error("customs must be a JSON object", 'customs')

    result = {}
    for field, limit in _CUSTOMS_TEXT_LIMITS.items():
        result[field] = _opt_text(raw.get(field), f'customs.{field}', limit, upper=field in _UPPER_FIELDS)
    result['freight_charge'] = _opt_non_negative_int(raw.get('freight_charge'), 'customs.freight_charge')
    # 运送保险（可选）：不带这两个键的请求照旧（视为没投保）
    result['insurance_charge'] = _opt_non_negative_int(raw.get('insurance_charge'), 'customs.insurance_charge')
    result['declared_value_carriage'] = _opt_non_negative_int(
        raw.get('declared_value_carriage'), 'customs.declared_value_carriage')

    consignee_raw = raw.get('consignee')
    if consignee_raw is None:
        result['consignee'] = None
    elif not isinstance(consignee_raw, dict):
        raise _structure_error("customs.consignee must be a JSON object", 'customs.consignee')
    else:
        consignee = {}
        for key in CONSIGNEE_FIELDS:
            consignee[key] = _opt_text(
                consignee_raw.get(key), f'customs.consignee.{key}', _CONSIGNEE_TEXT_LIMIT,
                upper=(key == 'country'),
            )
        result['consignee'] = consignee

    lines_raw = raw.get('lines')
    if lines_raw is None:
        lines_raw = []
    if not isinstance(lines_raw, list):
        raise _structure_error("customs.lines must be an array", 'customs.lines')
    if len(lines_raw) > MAX_CUSTOMS_LINES:
        raise _structure_error(f"customs.lines must not exceed {MAX_CUSTOMS_LINES} items", 'customs.lines')

    dn_goods_codes = set(dn_goods_codes)
    seen = set()
    lines = []
    for index, item in enumerate(lines_raw):
        prefix = f'customs.lines[{index}]'
        if not isinstance(item, dict):
            raise _structure_error(f"{prefix} must be a JSON object", prefix)
        goods_code = _opt_text(item.get('goods_code'), f'{prefix}.goods_code', _LINE_TEXT_LIMITS['goods_code'])
        if not goods_code or goods_code not in dn_goods_codes:
            raise BadRequestException(
                f"{prefix}.goods_code {goods_code!r} is not in the DN details", 16064,
                field=f'{prefix}.goods_code', details={'field': f'{prefix}.goods_code', 'goods_code': goods_code},
            )
        if goods_code in seen:
            raise BadRequestException(
                f"{prefix}.goods_code {goods_code!r} is duplicated", 16064,
                field=f'{prefix}.goods_code', details={'field': f'{prefix}.goods_code', 'goods_code': goods_code},
            )
        seen.add(goods_code)
        unit_value = _opt_decimal(item.get('unit_value'), f'{prefix}.unit_value', maximum=MAX_LINE_VALUE)
        total_value = _opt_decimal(item.get('total_value'), f'{prefix}.total_value', maximum=MAX_LINE_VALUE)
        lines.append({
            'goods_code': goods_code,
            'quantity': _opt_non_negative_int(item.get('quantity'), f'{prefix}.quantity'),
            'unit_value': _num(unit_value),
            'total_value': _num(total_value),
            'description_en': _opt_text(item.get('description_en'), f'{prefix}.description_en',
                                        _LINE_TEXT_LIMITS['description_en']),
            'hs_code': _opt_text(item.get('hs_code'), f'{prefix}.hs_code', _LINE_TEXT_LIMITS['hs_code']),
            'jp_export_code': _opt_text(item.get('jp_export_code'), f'{prefix}.jp_export_code',
                                        _LINE_TEXT_LIMITS['jp_export_code']),
            'origin_country': _opt_text(item.get('origin_country'), f'{prefix}.origin_country',
                                        _LINE_TEXT_LIMITS['origin_country'], upper=True),
            'quantity_unit': _opt_text(item.get('quantity_unit'), f'{prefix}.quantity_unit',
                                       _LINE_TEXT_LIMITS['quantity_unit'], upper=True) or 'PCS',
        })
    result['lines'] = lines
    return result


# ------------------------------------------------------------------
# 装箱：校验（16066）
# ------------------------------------------------------------------

def _package_error(message: str, field: str) -> BadRequestException:
    return BadRequestException(message, 16066, field=field, details={'field': field})


def _package_int(value, field: str, low: int, high: int) -> int:
    if value is None or isinstance(value, bool):
        raise _package_error(f"{field} is required and must be an integer", field)
    try:
        number = Decimal(str(value).strip()) if isinstance(value, (int, float, str)) else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite() or number != number.to_integral_value():
        raise _package_error(f"{field} must be an integer", field)
    number = int(number)
    if not low <= number <= high:
        raise _package_error(f"{field} must be between {low} and {high}", field)
    return number


def parse_packages(raw) -> list:
    """{"packages": [...]} → 规范化后的箱子列表（按箱号排序）。不合法 400 16066。"""
    packages_raw = raw.get('packages') if isinstance(raw, dict) else None
    if not isinstance(packages_raw, list):
        raise _package_error("packages must be an array", 'packages')
    if not 1 <= len(packages_raw) <= MAX_PACKAGES:
        raise _package_error(f"packages must contain 1 to {MAX_PACKAGES} items", 'packages')

    result = []
    for index, item in enumerate(packages_raw):
        prefix = f'packages[{index}]'
        if not isinstance(item, dict):
            raise _package_error(f"{prefix} must be a JSON object", prefix)

        package_no = item.get('package_no')
        package_no = index + 1 if package_no is None else _package_int(
            package_no, f'{prefix}.package_no', 1, MAX_PACKAGES)

        weight_raw = item.get('gross_weight_kg')
        weight = None
        if weight_raw is not None and not isinstance(weight_raw, bool) and isinstance(weight_raw, (int, float, str)):
            try:
                weight = Decimal(str(weight_raw).strip())
            except InvalidOperation:
                weight = None
        if weight is None or not weight.is_finite():
            raise _package_error(f"{prefix}.gross_weight_kg is required and must be a number",
                                 f'{prefix}.gross_weight_kg')
        if weight != weight.quantize(Decimal('0.001')):
            raise _package_error(f"{prefix}.gross_weight_kg allows at most 3 decimal places",
                                 f'{prefix}.gross_weight_kg')
        if not Decimal('0.01') <= weight <= Decimal('999.999'):
            raise _package_error(f"{prefix}.gross_weight_kg must be between 0.01 and 999.999",
                                 f'{prefix}.gross_weight_kg')

        remark = item.get('remark')
        if remark is not None:
            if not isinstance(remark, str):
                raise _package_error(f"{prefix}.remark must be a string", f'{prefix}.remark')
            remark = remark.strip()
            if len(remark) > 255:
                raise _package_error(f"{prefix}.remark must not exceed 255 characters", f'{prefix}.remark')
            remark = remark or None

        result.append({
            'package_no': package_no,
            'gross_weight_kg': weight.quantize(Decimal('0.001')),
            'length_mm': _package_int(item.get('length_mm'), f'{prefix}.length_mm', 1, 3000),
            'width_mm': _package_int(item.get('width_mm'), f'{prefix}.width_mm', 1, 3000),
            'height_mm': _package_int(item.get('height_mm'), f'{prefix}.height_mm', 1, 3000),
            'remark': remark,
        })

    numbers = sorted(p['package_no'] for p in result)
    if numbers != list(range(1, len(result) + 1)):
        raise _package_error("package_no must be consecutive starting from 1 without duplicates",
                             'packages.package_no')
    return sorted(result, key=lambda p: p['package_no'])


def _package_signature(packages) -> list:
    """箱子内容指纹（比较是否真的改了）"""
    sig = []
    for p in packages:
        if isinstance(p, DNPackage):
            p = {'package_no': p.package_no, 'gross_weight_kg': p.gross_weight_kg, 'length_mm': p.length_mm,
                 'width_mm': p.width_mm, 'height_mm': p.height_mm, 'remark': p.remark}
        sig.append((
            p['package_no'], str(Decimal(str(p['gross_weight_kg'])).quantize(Decimal('0.001'))),
            p['length_mm'], p['width_mm'], p['height_mm'], p.get('remark') or None,
        ))
    return sorted(sig)


class CustomsService:

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------

    @staticmethod
    def _lock(dn: DN) -> DN:
        """对 DN 行加锁（只查主键，避免 joined eager load 撞 PostgreSQL 的 FOR UPDATE 限制）并刷新状态。"""
        db.session.query(DN.id).filter(DN.id == dn.id).with_for_update().first()
        db.session.refresh(dn)
        return dn

    @staticmethod
    def is_locked(dn: DN) -> bool:
        return dn.status in LOCKED_DN_STATUSES

    @staticmethod
    def _assert_not_locked(dn: DN):
        if CustomsService.is_locked(dn):
            raise ConflictException(
                f"DN {dn.id} has been shipped; customs data, packages and documents are locked.", 16065
            )

    @staticmethod
    def goods_codes_by_id(goods_ids) -> dict:
        from warehouse.goods.models import Goods
        goods_ids = list(goods_ids)
        if not goods_ids:
            return {}
        rows = db.session.query(Goods.id, Goods.code).filter(Goods.id.in_(goods_ids)).all()
        return {gid: code for gid, code in rows}

    @staticmethod
    def current_documents(dn: DN) -> dict:
        """当前有效（issued）的出口单证（CI / PL）：{doc_type: DNDocument}，同类型取最新版本。"""
        docs = (
            DNDocument.query
            .filter(DNDocument.dn_id == dn.id, DNDocument.status == 'issued', DNDocument.doc_type.in_(DOC_TYPES))
            .order_by(DNDocument.version.desc(), DNDocument.id.desc())
            .all()
        )
        current = {}
        for doc in docs:
            current.setdefault(doc.doc_type, doc)
        return current

    @staticmethod
    def _void_current_documents(dn: DN, reason: str) -> list:
        now = datetime.now()
        voided = []
        for doc in DNDocument.query.filter(DNDocument.dn_id == dn.id, DNDocument.status == 'issued',
                                           DNDocument.doc_type.in_(DOC_TYPES)).all():
            doc.status = 'void'
            doc.voided_at = now
            doc.void_reason = reason
            voided.append(doc.id)
        if voided:
            db.session.flush()
        return sorted(voided)

    # ------------------------------------------------------------------
    # 报关快照
    # ------------------------------------------------------------------

    @staticmethod
    def parse_for_new_dn(raw, goods_ids) -> dict:
        """DN 新建时解析 customs（商品编码取 DN 明细里的商品）。"""
        codes = CustomsService.goods_codes_by_id(goods_ids).values()
        return parse_customs(raw, codes)

    @staticmethod
    def _snapshot_fields(parsed: dict) -> dict:
        return {
            'invoice_number': parsed.get('invoice_number'),
            'currency': parsed.get('currency'),
            'incoterm': parsed.get('incoterm'),
            'export_reason': parsed.get('export_reason'),
            'recipient_country': parsed.get('recipient_country'),
            'recipient_tax_id': parsed.get('recipient_tax_id'),
            'recipient_tax_id_type': parsed.get('recipient_tax_id_type'),
            'freight_charge': parsed.get('freight_charge'),
            'insurance_charge': parsed.get('insurance_charge'),
            'declared_value_carriage': parsed.get('declared_value_carriage'),
            'consignee': parsed.get('consignee'),
            'lines': parsed.get('lines') or [],
        }

    @staticmethod
    def store_snapshot(dn: DN, parsed: dict, user_id) -> bool:
        """写入 / 整体替换快照；返回内容是否有变化。"""
        fields = CustomsService._snapshot_fields(parsed)
        snapshot = dn.customs
        if snapshot is None:
            snapshot = DNCustoms(dn_id=dn.id, created_by=user_id, **fields)
            db.session.add(snapshot)
            db.session.flush()
            db.session.expire(dn, ['customs'])
            return True

        before = {key: getattr(snapshot, key) for key in fields}
        if json.dumps(before, sort_keys=True, default=str) == json.dumps(fields, sort_keys=True, default=str):
            return False
        for key, value in fields.items():
            setattr(snapshot, key, value)
        snapshot.created_by = user_id
        snapshot.updated_at = datetime.now()
        db.session.flush()
        return True

    @staticmethod
    @transactional
    def replace_customs(dn: DN, raw, user_id) -> dict:
        """PUT 快照：DN 未发货才可；内容有变且已签发单证 → 单证作废（customs_changed）。"""
        dn = CustomsService._lock(dn)
        CustomsService._assert_not_locked(dn)
        if isinstance(raw, dict) and isinstance(raw.get('customs'), dict) and 'lines' not in raw:
            raw = raw['customs']
        codes = {d.goods.code for d in dn.details if d.goods is not None}
        parsed = parse_customs(raw, codes)
        changed = CustomsService.store_snapshot(dn, parsed, user_id)
        voided = []
        # 只有印在单证上的内容变了才作废（例如只改了不上单证的 jp_export_code 不作废）
        if changed and CustomsService.current_documents(dn) and CustomsService.build_view(dn)['documents_outdated']:
            voided = CustomsService._void_current_documents(dn, 'customs_changed')
        view = CustomsService.build_view(dn)
        view['voided_documents'] = voided
        return view

    # ------------------------------------------------------------------
    # 装箱
    # ------------------------------------------------------------------

    @staticmethod
    def packages_payload(dn: DN) -> list:
        return [p.to_dict() for p in dn.packages]

    @staticmethod
    @transactional
    def replace_packages(dn: DN, raw, user_id) -> dict:
        """整体替换箱子。DN 须为 picked / packed（已发货 16065，其它 16067）；
        内容有变且已签发单证 → 单证作废（packages_changed）。"""
        dn = CustomsService._lock(dn)
        CustomsService._assert_not_locked(dn)
        if dn.status not in PACKAGE_EDITABLE_STATUSES:
            raise ConflictException(
                f"Packages can only be edited when the DN is picked or packed (current: {dn.status}).", 16067
            )
        packages = parse_packages(raw)

        if _package_signature(dn.packages) == _package_signature(packages):
            return {'packages': CustomsService.packages_payload(dn), 'voided_documents': []}

        # 已在承运商建了运单：箱数 / 重量 / 尺寸已随运单提交，先取消运单再改箱子
        from .carrier_services import CarrierShipmentService
        if CarrierShipmentService.has_active_shipment(dn):
            raise ConflictException(
                "Packages cannot be changed while an active carrier shipment exists; cancel the shipment first.",
                16076,
            )

        # 先删后插：同一次 flush 里 UOW 会先 INSERT 再 DELETE，撞 (dn_id, package_no) 唯一约束
        for old in list(dn.packages):
            db.session.delete(old)
        db.session.flush()
        for item in packages:
            db.session.add(DNPackage(dn_id=dn.id, created_by=user_id, **item))
        db.session.flush()
        db.session.expire(dn, ['packages'])

        voided = CustomsService._void_current_documents(dn, 'packages_changed')
        return {'packages': CustomsService.packages_payload(dn), 'voided_documents': voided}

    # ------------------------------------------------------------------
    # 视图（GET customs）与 problems
    # ------------------------------------------------------------------

    @staticmethod
    def _exporter(dn: DN) -> dict:
        company = dn.warehouse.company
        warehouse = dn.warehouse
        company_country = (company.country_code or '').upper() or None
        warehouse_country = (warehouse.country_code or '').upper() or None
        ship_from = None
        warehouse_address = (warehouse.address_en or '').strip()
        if warehouse_address and ' '.join(warehouse_address.split()).lower() != \
                ' '.join((company.address_en or '').split()).lower():
            ship_from = {
                'name': company.legal_name_en,
                'address_en': warehouse_address,
                'country_code': warehouse_country,
                'country_name': country_name(warehouse_country),
                'contact_name': warehouse.contact_name_en,
                'phone': warehouse.phone,
            }
        return {
            'legal_name_en': company.legal_name_en,
            'address_en': company.address_en,
            'country_code': company_country,
            'country_name': country_name(company_country),
            'export_country_code': warehouse_country or company_country,
            'phone': warehouse.phone or company.phone,
            'email': company.email,
            'contact_name': company.export_contact_name,
            'tax_id_label': company.tax_id_label,
            'tax_id': company.tax_id,
            'signatory_name': company.export_signatory_name,
            'signatory_title': company.export_signatory_title,
            'ship_from': ship_from,
        }

    @staticmethod
    def _exporter_printed_text(dn: DN, exporter: dict) -> list:
        """发货人一侧印在单证上的文本：[(说明, 值, problems.field)]。field = 该去改的主数据字段（表.列）。"""
        phone_field = 'warehouse.phone' if (dn.warehouse.phone or '').strip() else 'company.phone'
        items = [
            ('legal_name_en', exporter.get('legal_name_en'), 'company.legal_name_en'),
            ('address_en', exporter.get('address_en'), 'company.address_en'),
            ('contact_name', exporter.get('contact_name'), 'company.export_contact_name'),
            ('signatory_name', exporter.get('signatory_name'), 'company.export_signatory_name'),
            ('signatory_title', exporter.get('signatory_title'), 'company.export_signatory_title'),
            ('tax_id_label', exporter.get('tax_id_label'), 'company.tax_id_label'),
            ('tax_id', exporter.get('tax_id'), 'company.tax_id'),
            ('phone', exporter.get('phone'), phone_field),
            ('email', exporter.get('email'), 'company.email'),
        ]
        ship_from = exporter.get('ship_from')
        if ship_from:
            items += [
                ('ship_from.address_en', ship_from.get('address_en'), 'warehouse.address_en'),
                ('ship_from.contact_name', ship_from.get('contact_name'), 'warehouse.contact_name_en'),
            ]
        return items

    @staticmethod
    def _consignee(dn: DN, customs: DNCustoms | None) -> dict:
        """Consignee / Ship To：报关快照里的 consignee 优先，没有则退回 DN 收货人。"""
        consignee = dict((customs.consignee if customs else None) or {})
        if any(consignee.get(key) for key in CONSIGNEE_FIELDS):
            return {key: consignee.get(key) for key in CONSIGNEE_FIELDS}
        recipient = dn.recipient
        return {
            'name': getattr(recipient, 'contact', None) or getattr(recipient, 'name', None),
            'company': getattr(recipient, 'name', None),
            'address_line1': dn.shipping_address,
            'address_line2': None,
            'city': None,
            'state': None,
            'postal_code': getattr(recipient, 'zip_code', None),
            'country': (getattr(recipient, 'country', None) or '').upper() or None,
            'phone': getattr(recipient, 'phone', None),
        }

    @staticmethod
    def _delivery_task(dn: DN):
        from warehouse.delivery.models import DeliveryTask
        return (
            dn.delivery_tasks
            .filter(DeliveryTask.is_active.is_(True))
            .order_by(DeliveryTask.id.desc())
            .first()
        )

    @staticmethod
    def build_view(dn: DN) -> dict:
        customs = dn.customs
        currency = (customs.currency if customs else None)
        customs_lines = {line.get('goods_code'): line for line in ((customs.lines if customs else None) or [])}
        locked = CustomsService.is_locked(dn)
        problems = []

        def problem(code, level, message, goods_code=None, field=None):
            item = {'code': code, 'level': level, 'message': message}
            if goods_code is not None:
                item['goods_code'] = goods_code
            if field is not None:
                item['field'] = field
            problems.append(item)

        # ---- 明细（按商品聚合；发票数量 = 已打包数量）----
        aggregated = {}
        for detail in sorted(dn.details, key=lambda d: d.id or 0):
            row = aggregated.get(detail.goods_id)
            if row is None:
                row = aggregated[detail.goods_id] = {'goods': detail.goods, 'planned': 0, 'packed': 0}
            row['planned'] += detail.quantity or 0
            row['packed'] += detail.packed_quantity or 0

        lines = []
        goods_value = Decimal(0)
        total_qty = 0
        net_total = Decimal(0)
        net_known = True
        for goods_id, row in aggregated.items():
            goods = row['goods']
            code = goods.code if goods else None
            line = customs_lines.get(code) or {}
            unit_value = Decimal(str(line['unit_value'])) if line.get('unit_value') is not None else None
            packed = row['packed']
            amount = None
            if unit_value is not None:
                amount = _quantize_money(unit_value * packed, currency)
            origin = _goods_origin(goods) if goods else None
            weight = Decimal(str(goods.weight)) if goods is not None and goods.weight is not None else None
            net_weight = (weight * packed).quantize(Decimal('0.001')) if weight is not None else None
            lines.append({
                'goods_id': goods_id,
                'goods_code': code,
                'goods_name': goods.name if goods else None,
                'planned_quantity': row['planned'],
                'packed_quantity': packed,
                'unit_value': _num(unit_value),
                'amount': _num(amount),
                'description_en': line.get('description_en'),
                'hs_code': line.get('hs_code'),
                'hs_code_formatted': format_hs_code(line.get('hs_code')) if line.get('hs_code') else None,
                'jp_export_code': line.get('jp_export_code'),
                'origin_country': origin,
                'origin_source': 'goods' if origin else None,
                'declared_origin_country': line.get('origin_country'),
                'quantity_unit': line.get('quantity_unit') or 'PCS',
                'unit_weight_kg': float(weight) if weight is not None else None,
                'net_weight_kg': float(net_weight) if net_weight is not None else None,
                'has_customs_line': bool(line),
            })
            if packed <= 0:
                continue   # 已打包 0 的行不上发票，也不检查
            total_qty += packed
            if amount is not None and unit_value > 0:
                goods_value += amount
            if net_weight is None:
                net_known = False
            else:
                net_total += net_weight

            if customs is None:
                continue
            if not line:
                problem('LINE_MISSING', 'error', f"No customs line for goods {code}", goods_code=code)
                continue
            if not line.get('hs_code') or not hs_code_valid(line.get('hs_code')):
                problem('HS_CODE_MISSING', 'error', f"HS code of goods {code} is missing or not 6-10 digits",
                        goods_code=code, field='hs_code')
            description = line.get('description_en')
            if not description:
                problem('DESCRIPTION_MISSING', 'error', f"English description of goods {code} is missing",
                        goods_code=code, field='description_en')
            elif not is_ascii_text(description):
                problem('DESCRIPTION_NOT_ASCII', 'error',
                        f"English description of goods {code} contains non-ASCII characters",
                        goods_code=code, field='description_en')
            if not origin:
                problem('ORIGIN_MISSING', 'error',
                        f"Country of origin of goods {code} is not set in the goods master data",
                        goods_code=code, field='origin_country')
            if unit_value is None or unit_value <= 0:
                problem('UNIT_VALUE_MISSING', 'error', f"Unit value of goods {code} is missing or not positive",
                        goods_code=code, field='unit_value')
            if line.get('jp_export_code'):
                reason = jp_export_code_problem(line.get('jp_export_code'), line.get('hs_code'))
                if reason:
                    problem('JP_EXPORT_CODE_MISMATCH', 'warning',
                            f"Japanese export statistics code of goods {code} {reason}",
                            goods_code=code, field='jp_export_code')

        # ---- 箱子 ----
        packages = CustomsService.packages_payload(dn)
        gross_total = sum((Decimal(str(p['gross_weight_kg'])) for p in packages), Decimal(0))

        # ---- 单据级检查 ----
        exporter = CustomsService._exporter(dn)
        consignee = CustomsService._consignee(dn, customs)
        recipient_country = None
        if customs is not None:
            if not locked and dn.status != 'packed':
                problem('NOT_PACKED', 'error', f"DN must be packed before issuing documents (current: {dn.status})")
            elif not locked and total_qty <= 0:
                problem('NOT_PACKED', 'error', "DN has no packed quantity")
            if not packages:
                problem('PACKAGES_MISSING', 'error', "No packages recorded")
            for field, value in (
                ('legal_name_en', exporter['legal_name_en']),
                ('address_en', exporter['address_en']),
                ('phone', exporter['phone']),
                ('country_code', exporter['export_country_code']),
            ):
                if not value:
                    problem('EXPORTER_PROFILE_INCOMPLETE', 'error',
                            f"Exporter profile is incomplete: {field} is missing", field=f'company.{field}')
            if not customs.incoterm:
                problem('INCOTERM_MISSING', 'error', "Incoterm is missing", field='incoterm')
            if not customs.export_reason:
                problem('EXPORT_REASON_MISSING', 'error', "Export reason is missing", field='export_reason')
            if not customs.currency or not _CURRENCY_RE.match(customs.currency):
                problem('CURRENCY_INVALID', 'error', "Currency must be a 3-letter ISO 4217 code", field='currency')
            recipient_country = customs.recipient_country or (consignee.get('country') or None)
            if not recipient_country or not is_valid_country(recipient_country):
                problem('RECIPIENT_COUNTRY_MISSING', 'error',
                        "Recipient country is missing or not a valid ISO 3166-1 alpha-2 code",
                        field='recipient_country')
                recipient_country = None
            if not customs.recipient_tax_id:
                problem('RECIPIENT_TAX_ID_MISSING', 'warning', "Recipient tax ID is not provided",
                        field='recipient_tax_id')
            # 收件人的非拉丁字符（如收件国本地文字）只提示；发货人（WMS 自己的出口资料）的一律拦
            for key in CONSIGNEE_FIELDS:
                if has_non_latin(consignee.get(key)):
                    problem('NON_LATIN_TEXT', 'warning', f"Consignee {key} contains non-Latin characters",
                            field=f'consignee.{key}')
            for label, value, field in CustomsService._exporter_printed_text(dn, exporter):
                if has_non_latin(value):
                    problem('EXPORTER_NON_LATIN_TEXT', 'error',
                            f"Exporter {label} contains non-Latin characters; use English / Latin letters only",
                            field=field)
            if total_qty > 0 and not net_known:
                problem('NET_WEIGHT_UNKNOWN', 'warning',
                        "Net weight cannot be calculated: some goods have no unit weight")
            if packages and net_known and total_qty > 0 and gross_total < net_total:
                problem('GROSS_LT_NET', 'warning', "Total gross weight is less than total net weight")

        freight = Decimal(customs.freight_charge) if customs and customs.freight_charge is not None else Decimal(0)
        insurance = (Decimal(customs.insurance_charge)
                     if customs and customs.insurance_charge is not None else Decimal(0))
        invoice_total = goods_value + freight + insurance
        # 日本正式出口申报（申告价格 = FOB，即货值合计超过 20 万日元）要 9 位统计品目番号：
        # 按货值判断，运费 / 保险费不算进去；缺的行给警告，不拦出单证
        if customs is not None and (customs.currency or '').upper() == 'JPY' \
                and goods_value > JP_EXPORT_CODE_THRESHOLD_JPY:
            for line in lines:
                if line['packed_quantity'] > 0 and line['has_customs_line'] and not line['jp_export_code']:
                    problem('JP_EXPORT_CODE_MISSING', 'warning',
                            f"Japanese export statistics code of goods {line['goods_code']} is missing "
                            f"(goods value exceeds {JP_EXPORT_CODE_THRESHOLD_JPY:,} JPY)",
                            goods_code=line['goods_code'], field='jp_export_code')
        totals = {
            'quantity': total_qty,
            'goods_value': _num(goods_value),
            'freight': _num(freight),
            'insurance': _num(insurance),
            'invoice_total': _num(invoice_total),
            'package_count': len(packages),
            'gross_weight_kg': float(gross_total.quantize(Decimal('0.001'))),
            'net_weight_kg': float(net_total) if (net_known and total_qty > 0) else None,
        }
        current_docs = CustomsService.current_documents(dn)
        view = {
            'dn_id': dn.id,
            'status': dn.status,
            'is_export': customs is not None,
            'locked': locked,
            'customs': customs.to_dict() if customs else None,
            'invoice_number': CustomsService._invoice_number(dn),
            'recipient_country': recipient_country,
            'consignee': consignee,
            'lines': lines,
            'packages': packages,
            'totals': totals,
            'exporter': exporter,
            'problems': problems,
            'ready': (
                customs is not None and not locked
                and not any(p['level'] == 'error' for p in problems)
            ),
            'current_documents': [current_docs[t].to_meta() for t in DOC_TYPES if t in current_docs],
            'documents_outdated': False,
        }
        # 当前单证与现在的数据（含运单号 AWB）不一致 → 提示重新签发（不自动作废，也不拦发货）
        if customs is not None and current_docs:
            data_sha256 = _data_hash(CustomsService._document_data(dn, view))
            view['documents_outdated'] = any(doc.data_sha256 != data_sha256 for doc in current_docs.values())
        return view

    @staticmethod
    def _invoice_number(dn: DN) -> str:
        customs = dn.customs
        return (customs.invoice_number if customs else None) or dn.order_number or f"DN-{dn.id}"

    # ------------------------------------------------------------------
    # 单证
    # ------------------------------------------------------------------

    @staticmethod
    def _document_data(dn: DN, view: dict) -> dict:
        """单证内容（不含日期 / 版本）：同样的数据 → 同样的指纹 → 不升版本。"""
        customs = dn.customs
        currency = customs.currency
        exporter = view['exporter']
        consignee = view['consignee']
        destination = view['recipient_country']
        delivery_task = CustomsService._delivery_task(dn)
        carrier = (delivery_task.carrier if delivery_task and delivery_task.carrier else None) or dn.carrier
        export_country = exporter['export_country_code']
        tax_type = (customs.recipient_tax_id_type or '').upper()
        net_known = view['totals']['net_weight_kg'] is not None

        items = []
        for line in view['lines']:
            if line['packed_quantity'] <= 0:
                continue
            items.append({
                'goods_code': line['goods_code'],
                'description': line['description_en'],
                'hs_code': printed_hs_code(line['hs_code']),
                'origin': _country_label(line['origin_country']),
                'quantity': line['packed_quantity'],
                'unit': line['quantity_unit'],
                'unit_value': format_money(line['unit_value'], currency),
                'amount': format_money(line['amount'], currency),
                'net_weight_kg': (f"{Decimal(str(line['net_weight_kg'])):.3f}" if net_known else None),
            })

        packages = view['packages']
        totals = view['totals']
        data = {
            'currency': currency,
            'invoice_number': view['invoice_number'],
            'reference': dn.order_number,
            'awb': (delivery_task.tracking_number if delivery_task else None) or None,
            'carrier': carrier.name if carrier else None,
            'incoterm': customs.incoterm,
            'terms': f"{customs.incoterm} {(country_name(destination) or destination or '').upper()}".strip(),
            'export_reason': EXPORT_REASON_LABELS.get(
                (customs.export_reason or '').upper(),
                (customs.export_reason or '').replace('_', ' ').title()),
            'country_of_export': (country_name(export_country) or export_country or '').upper(),
            'destination': (country_name(destination) or destination or '').upper(),
            'exporter': {
                'name': exporter['legal_name_en'],
                'address': exporter['address_en'],
                'country': (country_name(exporter['country_code']) or exporter['country_code'] or '').upper(),
                'phone': exporter['phone'],
                'email': exporter['email'],
                'contact': exporter['contact_name'],
                'tax': (f"{exporter['tax_id_label'] or 'Tax ID'}: {exporter['tax_id']}"
                        if exporter['tax_id'] else None),
            },
            'ship_from': ({
                'name': exporter['ship_from']['name'],
                'address': exporter['ship_from']['address_en'],
                'country': (exporter['ship_from']['country_name'] or exporter['ship_from']['country_code'] or '').upper(),
                'contact': exporter['ship_from']['contact_name'],
                'phone': exporter['ship_from']['phone'],
            } if exporter['ship_from'] else None),
            'consignee': {
                'name': consignee.get('name'),
                'company': consignee.get('company'),
                'address_lines': [v for v in (consignee.get('address_line1'), consignee.get('address_line2')) if v],
                'city_line': ' '.join(v for v in (consignee.get('city'), consignee.get('state'),
                                                 consignee.get('postal_code')) if v) or None,
                'country': (country_name(destination) or destination or '').upper(),
                'phone': consignee.get('phone'),
                'tax': (f"{TAX_ID_TYPE_LABELS.get(tax_type, 'Tax ID')}: {customs.recipient_tax_id}"
                        if customs.recipient_tax_id else None),
            },
            'items': items,
            'show_net_weight': net_known,
            'totals': {
                'quantity': totals['quantity'],
                'goods_value': format_money(totals['goods_value'], currency),
                'freight': format_money(totals['freight'], currency),
                'invoice_total': format_money(totals['invoice_total'], currency),
                'package_count': totals['package_count'],
                'gross_weight_kg': f"{Decimal(str(totals['gross_weight_kg'])):.3f}",
                'net_weight_kg': (f"{Decimal(str(totals['net_weight_kg'])):.3f}" if net_known else None),
            },
            'packages': [{
                'label': f"{p['package_no']} of {len(packages)}",
                'dimensions_cm': ' x '.join(f"{p[k] / 10:.1f}" for k in ('length_mm', 'width_mm', 'height_mm')),
                'gross_weight_kg': f"{Decimal(str(p['gross_weight_kg'])):.3f}",
                'remark': p['remark'],
            } for p in packages],
            'signatory_name': exporter['signatory_name'],
            'signatory_title': exporter['signatory_title'],
            'declaration': DECLARATION_TEXT,
        }
        # 运送保险：保险费 > 0 时 CI 在 Freight 下单列 Insurance；运送申告价额不印，但计入单证指纹。
        # 只在有值时加键 —— 旧快照（两个都为空）的指纹不变，已签发的单证不会因升级而失效。
        if customs.insurance_charge:
            data['totals']['insurance'] = format_money(totals['insurance'], currency)
        if customs.declared_value_carriage is not None:
            data['declared_value_carriage'] = format_money(customs.declared_value_carriage, currency)
        return data

    @staticmethod
    def _file_name(prefix: str, invoice_number: str, version: int) -> str:
        safe = _FILE_SAFE.sub('_', invoice_number or '').strip('_')[:80] or 'DN'
        return f"{prefix}_{safe}_v{version}.pdf"

    @staticmethod
    @transactional
    def issue_documents(dn: DN, user_id) -> tuple:
        """签发 CI + PL。返回 (http_status, {version, documents})：
        201 新签发；200 单证数据未变，返回现有版本。条件不全 409 16068（details.problems）。"""
        from .customs_documents import render_commercial_invoice, render_packing_list

        dn = CustomsService._lock(dn)
        CustomsService._assert_not_locked(dn)
        if dn.customs is None:
            raise ConflictException(f"DN {dn.id} is not an export shipment (no customs data).", 16070)

        view = CustomsService.build_view(dn)
        if any(p['level'] == 'error' for p in view['problems']):
            raise ConflictException(
                "Customs documents cannot be issued; resolve the listed problems first.", 16068,
                details={'problems': view['problems']},
            )

        data = CustomsService._document_data(dn, view)
        data_sha256 = _data_hash(data)

        current = CustomsService.current_documents(dn)
        if all(t in current for t in DOC_TYPES) and all(current[t].data_sha256 == data_sha256 for t in DOC_TYPES):
            return 200, {
                'version': current[DOC_CI].version,
                'documents': [current[t].to_meta() for t in DOC_TYPES],
            }

        CustomsService._void_current_documents(dn, 'data_changed')
        max_version = (
            db.session.query(db.func.max(DNDocument.version))
            .filter(DNDocument.dn_id == dn.id, DNDocument.doc_type.in_(DOC_TYPES))
            .scalar()
        )
        version = (max_version or 0) + 1
        issued_at = datetime.now()
        invoice_date = datetime.now(_document_timezone()).date()
        meta = {'version': version, 'invoice_date': invoice_date.isoformat()}

        documents = []
        for doc_type, prefix, render in (
            (DOC_CI, 'CI', render_commercial_invoice),
            (DOC_PL, 'PL', render_packing_list),
        ):
            content = render(data, meta)
            doc = DNDocument(
                dn_id=dn.id,
                doc_type=doc_type,
                version=version,
                document_number=data['invoice_number'],
                invoice_date=invoice_date,
                status='issued',
                sha256=hashlib.sha256(content).hexdigest(),
                data_sha256=data_sha256,
                size_bytes=len(content),
                file_name=CustomsService._file_name(prefix, data['invoice_number'], version),
                content=content,
                issued_at=issued_at,
                issued_by=user_id,
            )
            db.session.add(doc)
            documents.append(doc)
        db.session.flush()
        db.session.expire(dn, ['customs_documents'])
        return 201, {'version': version, 'documents': [doc.to_meta() for doc in documents]}

    @staticmethod
    def list_documents(dn: DN, status=None, doc_type=None) -> list:
        query = DNDocument.query.filter(DNDocument.dn_id == dn.id)
        if status:
            query = query.filter(DNDocument.status == status)
        if doc_type:
            query = query.filter(DNDocument.doc_type == doc_type)
        return query.order_by(DNDocument.version.desc(), DNDocument.id.asc()).all()

    @staticmethod
    def get_document(dn: DN, doc_id: int) -> DNDocument:
        doc = db.session.get(DNDocument, doc_id)
        if doc is None or doc.dn_id != dn.id:
            raise NotFoundException(f"Customs document {doc_id} not found for DN {dn.id}", 16071)
        return doc

    # ------------------------------------------------------------------
    # 发货拦截 / dn.delivered 追加字段
    # ------------------------------------------------------------------

    @staticmethod
    def assert_ready_to_ship(dn: DN) -> dict:
        """发货前检查（按当前已存的数据）。国内件不检查，返回 {}。

        海外件（带报关快照）：
        - 没有当前有效的 CI 或 PL → 409 16069，details {missing_documents: [...], outdated: false}
        - 有但已过期（数据指纹变了：箱子 / 快照 / 运单号 / 公司资料等改过没重出）
          → 409 16069，details {missing_documents: [], outdated: true}
        通过时返回 {'awb': 当前单证上印的运单号（没印为 None）}。
        """
        if dn.customs is None:
            return {}
        current = CustomsService.current_documents(dn)
        missing = [t for t in DOC_TYPES if t not in current]
        if missing:
            raise ConflictException(
                "Export shipment requires a current commercial invoice and packing list before shipping.",
                16069, details={'missing_documents': missing, 'outdated': False},
            )
        if CustomsService.build_view(dn)['documents_outdated']:
            raise ConflictException(
                "The commercial invoice / packing list no longer match the current data "
                "(packages, customs data or tracking number changed); issue them again before shipping.",
                16069, details={'missing_documents': [], 'outdated': True},
            )
        # 单证未过期 ⇒ 印在 CI 上的 AWB 就是 _document_data 里的 awb（发货任务上现存的运单号）
        delivery_task = CustomsService._delivery_task(dn)
        return {'awb': (delivery_task.tracking_number if delivery_task else None) or None}

    @staticmethod
    def delivered_webhook_fields(dn: DN) -> dict:
        """dn.delivered 追加字段（只对海外 DN）：当前单证、箱子、发票合计。"""
        if dn.customs is None:
            return {}
        current = CustomsService.current_documents(dn)
        documents = []
        for doc_type in DOC_TYPES:
            doc = current.get(doc_type)
            if doc is None:
                continue
            documents.append({
                'id': doc.id,
                'doc_type': doc.doc_type,
                'version': doc.version,
                'document_number': doc.document_number,
                'invoice_date': doc.invoice_date.isoformat() if doc.invoice_date else None,
                'issued_at': doc.issued_at.isoformat() if doc.issued_at else None,
                'sha256': doc.sha256,
                'size_bytes': doc.size_bytes,
                'file_name': doc.file_name,
                'download_path': f"/warehouse/dn/{dn.id}/customs-documents/{doc.id}/file",
            })
        view = CustomsService.build_view(dn)
        totals = view['totals']
        return {
            'customs_documents': documents,
            'packages': [
                {k: p[k] for k in ('package_no', 'gross_weight_kg', 'length_mm', 'width_mm', 'height_mm')}
                for p in view['packages']
            ],
            'invoice_total': {
                'currency': dn.customs.currency,
                'goods_value': totals['goods_value'],
                'freight': totals['freight'],
                'insurance': totals['insurance'],
                'total': totals['invoice_total'],
            },
        }
