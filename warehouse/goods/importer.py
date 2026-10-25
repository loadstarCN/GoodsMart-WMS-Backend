"""商品导入（POST /goods/bulk_upload：CSV / Excel，dry_run 预校验与执行）。

流程：读文件（tabular）→ 认表头（columns.match_header：中 / 英 / 日表头与列键都认）→ 逐行逐格校验（一次收集全部错误）→
与已有商品比对出每行的结果 → dry_run 只返回预览（不写任何东西），执行时写库。

每行的结果（action）：
- create：编码不存在 → 新增（单位缺省 pcs、货币缺省公司默认货币、缩略图缺省 = 图片 URL）
- update：编码已存在且有字段要变（changes：[{field, from, to}]）；补空白 / 覆盖同时把停用的恢复启用（is_active false → true）
- unchanged：编码已存在，按所选方式没有任何字段要变
- skip：编码已存在、方式为 skip（只新增）
- error：有错误（errors：[{field, column, code, reason, value, message, …}]），不导入

编码已存在时（overwrite）：
- skip：跳过；append（补空白）：只填原来为空的字段
- override（覆盖）：文件里有值的单元格替换原值，**空单元格一律保留原值、不清空**（要清空在商品编辑里做）
- active：只把停用的恢复启用（旧方式，界面不再提供，接口保留）
缩略图留空：新增时 = 图片 URL；更新时原缩略图为空或与原图片相同（原来就是跟着图片的）则跟着新图片走，否则保留。

执行：
- 带 import_token（dry_run 返回的）：重新校验；文件 / 方式 / 编码 / 工作表不同（FILE_CHANGED）或每行的结果与预览时不同
  （DATA_CHANGED，期间商品被改过）→ 409 16141，请重新校验。一致则导入没有错误的行、跳过错误行
- 不带 import_token（旧接口 / API 调用）：有任何错误整批不导入，按第一个错误返回 400（业务码、details 与以前相同）
- 每次执行记一条导入记录（goods_imports）：原文件与错误行文件（原格式 + 末列「错误」）保留 30 天；新增 / 更新的商品记在
  goods_import_items（商品列表 ?import_id=）。整份文件不能用 / 不带 token 有错误而没导入的也记（status failed）
"""
import hashlib
import hmac
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace

from flask import current_app
from sqlalchemy.orm import lazyload

from extensions import db
from extensions.error import BadRequestException, ConflictException
from warehouse.common import clock
from . import tabular
from .columns import (
    COLUMNS_BY_KEY, ERROR_COLUMN_LABELS, GOODS_IMPORT_COLUMNS, GOODS_IMPORT_REQUIRED_COLUMNS, LANGS, header_language,
    match_header,
)
from .models import Goods, GoodsImport, GoodsImportItem
from .sanitize import sanitize_description
from .services import (
    GOODS_NUMBER_RULES, GoodsService, _emit_spec_changes, _spec_change, clean_origin_country, goods_number_error,
    goods_spec_missing, goods_spec_snapshot,
)

IMPORT_MAX_ROWS = 10_000
IMPORT_FILE_RETENTION_DAYS = 30
IMPORT_ERROR_DETAIL_ROWS = 200       # 导入记录里存的错误行（详情接口用；完整的看错误行文件）
DISPLAY_TEXT_LIMIT = 200             # 预览里显示的文字（描述等）超过就截断

# 业务码（extensions/error.py、README 登记）
FILE_NOT_SUPPORTED = 10004       # 不是 .csv / .xlsx：details {filename}
COLUMNS_MISSING = 10005          # 缺必填列：details {columns}
VALUE_REQUIRED = 10006           # 必填值为空：details {row, columns}
NUMBER_INVALID = 10012           # 不是数字 / 价格为负 / 折扣价高于价格：details {row, columns, value | code}
COUNTRY_INVALID = 10014          # 原产国不是 ISO 3166-1 alpha-2：details {row, columns, value}
FILE_NOT_READABLE = 10016        # 文件读不了：details {reason: UNKNOWN_ENCODING | NOT_UTF8 | NOT_SHIFT_JIS | NOT_XLSX | NOT_CSV}
DATE_INVALID = 10017             # 日期不合法：details {row, columns, value}
FILE_TOO_LARGE = 10018           # 超过 10,000 行 / Excel 解压后过大：details {max_rows} | {max_bytes}
PARAM_INVALID = 10019            # 导入参数不合法（overwrite / encoding / sheet / lang / import_token）：field
TEXT_TOO_LONG = 10020            # 文字超过列的长度：details {row, columns, max_length}
GOODS_NUMBER_INVALID = 16100     # 数值超出范围 / NaN：details {field, row, columns}
DUPLICATE_CODE = 16101           # 文件内编码重复：details {code, rows, columns}
TOKEN_MISMATCH = 16141           # 409 import_token 与这次的文件 / 结果对不上：details {reason: FILE_CHANGED | DATA_CHANGED}

MODES = GoodsImport.MODES

# 错误原因的文案（错误行文件末列按语言写；接口里的 message 用 en-US，界面按 reason 自己翻译）
_MESSAGES = {
    'REQUIRED': {'en-US': 'is required', 'zh-CN': '为空（必填）', 'ja-JP': '必須です'},
    'DUPLICATE': {'en-US': 'duplicates row {first_row}', 'zh-CN': '与第 {first_row} 行重复',
                  'ja-JP': '{first_row} 行目と重複しています'},
    'INVALID_NUMBER': {'en-US': 'not a valid number: {value}', 'zh-CN': '不是有效数字：{value}',
                       'ja-JP': '数値ではありません：{value}'},
    'NEGATIVE': {'en-US': 'cannot be negative: {value}', 'zh-CN': '不能为负数：{value}',
                 'ja-JP': 'マイナスにはできません：{value}'},
    'OUT_OF_RANGE': {'en-US': 'out of range ({min} - {max}): {value}', 'zh-CN': '超出范围（{min} – {max}）：{value}',
                     'ja-JP': '範囲外です（{min}〜{max}）：{value}'},
    'INVALID_COUNTRY': {'en-US': 'not an ISO 3166-1 alpha-2 country code: {value}',
                        'zh-CN': '不是有效的国家代码（ISO 3166-1 两位字母）：{value}',
                        'ja-JP': '国コード（ISO 3166-1 の 2 文字）ではありません：{value}'},
    'INVALID_DATE': {'en-US': 'not a valid date (YYYY-MM-DD): {value}', 'zh-CN': '不是有效日期（YYYY-MM-DD）：{value}',
                     'ja-JP': '日付ではありません（YYYY-MM-DD）：{value}'},
    'TOO_LONG': {'en-US': 'longer than {max_length} characters', 'zh-CN': '超过 {max_length} 个字符',
                 'ja-JP': '{max_length} 文字を超えています'},
    'DISCOUNT_ABOVE_PRICE': {'en-US': 'the discount price is higher than the price', 'zh-CN': '折扣价高于价格',
                             'ja-JP': '割引価格が価格を上回っています'},
    'DISCOUNT_WITHOUT_PRICE': {'en-US': 'a discount price needs a price', 'zh-CN': '有折扣价时必须有价格',
                               'ja-JP': '割引価格には価格が必要です'},
}
_NUMBER_RANGES = {
    'weight': ('0.001', '99999.999'), 'length': ('1', '2147483647'), 'width': ('1', '2147483647'),
    'height': ('1', '2147483647'), 'price': ('0', '9999999999.99'), 'discount_price': ('0', '9999999999.99'),
}
_THOUSANDS = re.compile(r'[+-]?\d{1,3}(,\d{3})+(\.\d+)?')
_DATE = re.compile(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]00:00(?::00)?)?')
_TEXT_FIELDS = tuple(key for key in GOODS_IMPORT_COLUMNS if COLUMNS_BY_KEY[key].kind == 'text')
_SPEC_ATTRS = ('weight', 'length', 'width', 'height', 'origin_country')


class ImportRequest(SimpleNamespace):
    """company_id, company, mode, encoding（auto / utf-8 / cp932）, sheet, lang, file_name, content"""


def message(reason, lang='en-US', **params) -> str:
    template = _MESSAGES[reason].get(lang) or _MESSAGES[reason]['en-US']
    return template.format_map(defaultdict(str, {k: ('' if v is None else v) for k, v in params.items()}))


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _display(value):
    """预览里显示的值：Decimal → 数字，日期 → ISO，长文字截断"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str) and len(value) > DISPLAY_TEXT_LIMIT:
        return value[:DISPLAY_TEXT_LIMIT] + '…'
    return value


def _secret() -> bytes:
    key = current_app.config.get('SECRET_KEY') or current_app.config.get('JWT_SECRET_KEY') or 'goods-import'
    return str(key).encode('utf-8')


def _mac(kind, payload) -> str:
    text = kind + '|' + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hmac.new(_secret(), text.encode('utf-8'), hashlib.sha256).hexdigest()[:32]


class RowPlan:
    """一行的校验与比对结果"""
    __slots__ = ('row', 'cells', 'code', 'name', 'values', 'errors', 'action', 'goods', 'proposed', 'data',
                 'spec_missing')

    def __init__(self, row, cells):
        self.row = row
        self.cells = cells
        self.code = None
        self.name = None
        self.values = {}
        self.errors = []
        self.action = None
        self.goods = None
        self.proposed = {}
        self.data = None
        self.spec_missing = []

    def to_dict(self) -> dict:
        result = {'row': self.row, 'action': self.action, 'code': self.code, 'name': self.name,
                  'goods_id': self.goods.id if self.goods is not None else None}
        if self.action == 'error':
            result['errors'] = [_public_error(error) for error in self.errors]
        elif self.action == 'create':
            result['values'] = {key: _display(value) for key, value in self.data.items()
                                if key in GOODS_IMPORT_COLUMNS and value is not None}
            result['spec_missing'] = self.spec_missing
        elif self.action == 'update':
            result['changes'] = [
                {'field': field, 'from': _display(getattr(self.goods, field)), 'to': _display(value)}
                for field, value in self.proposed.items()]
            result['spec_missing'] = self.spec_missing
        return result


def _public_error(error) -> dict:
    return {key: value for key, value in error.items() if not key.startswith('_')}


class Analysis:
    """一份文件的校验结果（dry_run 的预览与执行共用）"""

    def __init__(self, request, table, column_map, ignored, read_only, header_cells, header_lang):
        self.request = request
        self.table = table
        self.column_map = column_map            # [(列号, 表头, 列键)]
        self.ignored = ignored
        self.read_only = read_only
        self.header_cells = header_cells
        self.header_lang = header_lang
        self.rows = []
        self.sha256 = hashlib.sha256(request.content).hexdigest()

    @property
    def columns(self):
        return [field for _index, _header, field in self.column_map]

    def header_of(self, field):
        for _index, header, key in self.column_map:
            if key == field:
                return header
        return None

    def counts(self) -> dict:
        counts = {'create': 0, 'update': 0, 'unchanged': 0, 'skip': 0, 'error': 0}
        for plan in self.rows:
            counts[plan.action] += 1
        return counts

    def file_info(self) -> dict:
        table = self.table
        return {
            'name': self.request.file_name, 'format': table.format, 'encoding': table.encoding,
            'encoding_detected': table.encoding_detected, 'sheet': table.sheet, 'sheets': table.sheets,
            'rows': len(self.rows), 'columns': len(self.header_cells),
        }

    def token(self) -> str:
        request = self.request
        file_part = _mac('goods-import-file-v1', [request.company_id, request.mode, self.table.encoding,
                                                  self.table.sheet, self.sha256])
        # 每行的结果连同更新前的值（预览里给用户看的「原值 → 新值」）都签进去：期间商品被改过就对不上
        plan = [[p.row, p.action, p.goods.id if p.goods is not None else None,
                 sorted([field, str(getattr(p.goods, field)), str(value)] for field, value in p.proposed.items()),
                 sorted(error['reason'] for error in p.errors)] for p in self.rows]
        return file_part + _mac('goods-import-plan-v1', plan)

    def error_lang(self) -> str:
        return self.request.lang or self.header_lang or 'en-US'


# ------------------------------------------------------------------
# 逐格校验
# ------------------------------------------------------------------

def _error(field, column, code, reason, value=None, legacy=None, **params) -> dict:
    """一个单元格（或一行）的错误；_legacy = 不带 token 执行时抛的 400 {message, details, field}"""
    error = {'field': field, 'column': column, 'code': code, 'reason': reason, 'value': value, **params}
    error['message'] = f"{field}: {message(reason, value=value, **params)}"
    error['_legacy'] = legacy
    return error


def _legacy(message_text, row, columns, **details):
    payload = {'row': row, 'columns': list(columns)}
    payload.update({k: v for k, v in details.items() if v is not None})
    return {'message': message_text, 'details': payload, 'field': columns[0] if len(columns) == 1 else None}


def _parse_number(field, raw, row, column):
    text = unicodedata.normalize('NFKC', raw).strip()
    if _THOUSANDS.fullmatch(text):
        text = text.replace(',', '')
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, _error(field, column, NUMBER_INVALID, 'INVALID_NUMBER', raw,
                            _legacy(f"Row {row}: invalid number for '{field}': {raw}", row, [field], value=raw))
    range_params = dict(zip(('min', 'max'), _NUMBER_RANGES[field]))

    def number_error(reason):
        legacy_error = goods_number_error(field, row)
        return _error(field, column, GOODS_NUMBER_INVALID, reason, raw,
                      {'message': legacy_error.message, 'details': legacy_error.details, 'field': field},
                      **range_params)

    if not number.is_finite():
        return None, number_error('INVALID_NUMBER')
    if number < 0:
        if field == 'price':
            return None, _error(field, column, NUMBER_INVALID, 'NEGATIVE', raw,
                                _legacy(f"Row {row}: price must be >= 0", row, ['price'], value=str(number)))
        return None, number_error('NEGATIVE')
    quantum, minimum, maximum, cast = GOODS_NUMBER_RULES[field]
    try:
        number = number.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None, number_error('OUT_OF_RANGE')
    if number < minimum or number > maximum:
        return None, number_error('OUT_OF_RANGE')
    return (int(number) if cast is int else number), None


def _parse_date(field, raw, row, column):
    text = unicodedata.normalize('NFKC', raw).strip()
    match = _DATE.fullmatch(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))), None
        except ValueError:
            pass
    return None, _error(field, column, DATE_INVALID, 'INVALID_DATE', raw,
                        _legacy(f"Row {row}: invalid date for '{field}': {raw}", row, [field], value=raw))


def _parse_cell(field, raw, row, column):
    """单元格 → (值, 错误)；空单元格 → (None, None)"""
    if _blank(raw):
        return None, None
    kind = COLUMNS_BY_KEY[field].kind
    if kind in ('number', 'integer'):
        return _parse_number(field, raw, row, column)
    if kind == 'date':
        return _parse_date(field, raw, row, column)
    if kind == 'country':
        try:
            return clean_origin_country(raw.strip()), None
        except BadRequestException:
            return None, _error(field, column, COUNTRY_INVALID, 'INVALID_COUNTRY', raw,
                                _legacy(f"Row {row}: invalid origin_country: {raw}", row, ['origin_country'],
                                        value=raw))
    text = raw.strip()
    if field == 'description':
        return sanitize_description(text), None
    max_length = COLUMNS_BY_KEY[field].max_length
    if max_length and len(text) > max_length:
        return None, _error(field, column, TEXT_TOO_LONG, 'TOO_LONG', None,
                            _legacy(f"Row {row}: {field} is longer than {max_length} characters", row, [field],
                                    max_length=max_length),
                            max_length=max_length)
    return text, None


# ------------------------------------------------------------------
# 读文件、认表头
# ------------------------------------------------------------------

def _read(request):
    fmt = tabular.file_format(request.file_name)
    if fmt is None:
        raise BadRequestException("Only CSV or Excel (.xlsx) files are supported.", FILE_NOT_SUPPORTED,
                                  details={'filename': request.file_name})
    try:
        return tabular.read_table(request.content, fmt, encoding=request.encoding, sheet=request.sheet,
                                  max_rows=IMPORT_MAX_ROWS)
    except tabular.TableError as exc:
        if exc.reason in ('TOO_MANY_ROWS', 'TOO_LARGE'):
            raise BadRequestException(exc.message, FILE_TOO_LARGE, details={'reason': exc.reason, **exc.details})
        if exc.reason == 'SHEET_NOT_FOUND':
            raise BadRequestException(exc.message, PARAM_INVALID, 'sheet', details=exc.details)
        raise BadRequestException(exc.message, FILE_NOT_READABLE, details={'reason': exc.reason, **exc.details})


def analyze(request) -> Analysis:
    """读文件、认表头、逐行校验并与已有商品比对。整份文件不能用 → BadRequestException（业务码见模块开头）"""
    table = _read(request)
    header_cells = [cell.strip() for cell in table.rows[0][1]] if table.rows else []
    column_map, ignored, read_only, seen = [], [], [], set()
    for index, text in enumerate(header_cells):
        if not text:
            continue
        key = match_header(text)
        if key is None or key in seen:
            ignored.append(text)
            continue
        if not COLUMNS_BY_KEY[key].importable:
            ignored.append(text)
            read_only.append(text)
            continue
        seen.add(key)
        column_map.append((index, text, key))
    missing = [key for key in GOODS_IMPORT_REQUIRED_COLUMNS if key not in seen]
    if missing:
        raise BadRequestException(f"Missing required columns: {', '.join(missing)}", COLUMNS_MISSING,
                                  details={'columns': missing})

    analysis = Analysis(request, table, column_map, ignored, read_only, header_cells, header_language(header_cells))
    index_of = {field: index for index, _header, field in column_map}
    first_rows = {}
    for row, cells in table.rows[1:]:
        plan = RowPlan(row, cells)
        for index, header, field in column_map:
            raw = cells[index] if index < len(cells) else ''
            value, error = _parse_cell(field, raw, row, header)
            plan.values[field] = value
            if error is not None:
                plan.errors.append(error)
        # 编码 / 名称本身有错（超长）时显示原文
        for key in ('code', 'name'):
            raw = cells[index_of[key]] if index_of[key] < len(cells) else ''
            setattr(plan, key, plan.values.get(key) or raw.strip() or None)
        required_missing = [key for key in GOODS_IMPORT_REQUIRED_COLUMNS
                            if plan.values.get(key) is None and not any(e['field'] == key for e in plan.errors)]
        for key in reversed(required_missing):
            plan.errors.insert(0, _error(key, analysis.header_of(key), VALUE_REQUIRED, 'REQUIRED', None,
                                         _legacy(f"Row {row}: Missing values for {', '.join(required_missing)}",
                                                 row, required_missing)))
        code = plan.values.get('code')
        if code is not None:
            if code in first_rows:
                first = first_rows[code]
                plan.errors.insert(len(required_missing), _error(
                    'code', analysis.header_of('code'), DUPLICATE_CODE, 'DUPLICATE', code,
                    {'message': f"Row {row}: duplicate code '{code}' (first seen in row {first})", 'field': 'code',
                     'details': {'code': code, 'rows': [first, row], 'columns': ['code']}},
                    first_row=first))
            else:
                first_rows[code] = row
        analysis.rows.append(plan)

    _plan(analysis)
    return analysis


# ------------------------------------------------------------------
# 与已有商品比对
# ------------------------------------------------------------------

def _existing_goods(company_id, codes) -> dict:
    found = {}
    codes = list(codes)
    for start in range(0, len(codes), 1000):
        chunk = codes[start:start + 1000]
        for goods in (Goods.query.options(lazyload('*'))
                      .filter(Goods.company_id == company_id, Goods.code.in_(chunk)).all()):
            found[goods.code] = goods
    return found


def _same(field, current, new) -> bool:
    if field in GOODS_NUMBER_RULES:
        if current is None:
            return False
        quantum = GOODS_NUMBER_RULES[field][0]
        try:
            return Decimal(str(current)).quantize(quantum) == Decimal(str(new)).quantize(quantum)
        except InvalidOperation:
            return False
    if isinstance(new, date):
        return current == new
    return (current or '') == new


def _spec_missing(source) -> list:
    if isinstance(source, dict):
        source = SimpleNamespace(**{attr: source.get(attr) for attr in _SPEC_ATTRS})
    return goods_spec_missing(source)


def _price_error(plan, analysis, price, discount, discount_from_file, merged):
    """折扣价不能高于价格；有折扣价必须有价格（数据库约束 chk_discount_price）"""
    if discount is None:
        return None
    if price is None:
        if not discount_from_file:
            return None
        reason = 'DISCOUNT_WITHOUT_PRICE'
    elif Decimal(str(discount)) > Decimal(str(price)):
        reason = 'DISCOUNT_ABOVE_PRICE'
    else:
        return None
    row, column = plan.row, analysis.header_of('discount_price') or analysis.header_of('price')
    if merged:
        legacy = {'message': f"Row {row}: discount_price must be <= price (goods {plan.code})", 'field': None,
                  'details': {'columns': ['discount_price', 'price'], 'code': plan.code, 'row': row}}
    else:
        legacy = _legacy(f"Row {row}: discount_price must be <= price", row, ['discount_price', 'price'],
                         value=str(discount))
    return _error('discount_price', column, NUMBER_INVALID, reason, _display(discount), legacy,
                  price=_display(price))


def _plan(analysis):
    request = analysis.request
    existing = _existing_goods(request.company_id, {p.code for p in analysis.rows if not p.errors and p.code})
    default_currency = getattr(request.company, 'default_currency', None) or 'JPY'
    for plan in analysis.rows:
        if plan.errors:
            plan.action = 'error'
            continue
        goods = existing.get(plan.code)
        if goods is None:
            _plan_create(plan, analysis, default_currency)
        else:
            plan.goods = goods
            _plan_update(plan, analysis, goods, request.mode)
        if plan.errors:
            plan.action = 'error'


def _plan_create(plan, analysis, default_currency):
    data = {field: plan.values.get(field) for field in GOODS_IMPORT_COLUMNS}
    data['unit'] = data['unit'] or 'pcs'
    data['currency'] = data['currency'] or default_currency
    if data['thumbnail_url'] is None and data['image_url'] is not None:
        data['thumbnail_url'] = data['image_url']
    data['company_id'] = analysis.request.company_id
    error = _price_error(plan, analysis, data['price'], data['discount_price'], True, merged=False)
    if error is not None:
        plan.errors.append(error)
        return
    plan.data = data
    plan.action = 'create'
    plan.spec_missing = _spec_missing(data)


def _plan_update(plan, analysis, goods, mode):
    values = plan.values
    if mode == 'skip':
        plan.action = 'skip'
        return
    proposed = {}
    if mode in ('append', 'override'):
        for field in GOODS_IMPORT_COLUMNS:
            if field in ('code', 'thumbnail_url'):
                continue
            new = values.get(field)
            if new is None:
                continue                        # 空单元格 / 没有这一列：保留原值
            current = getattr(goods, field)
            if mode == 'append' and not _blank(current):
                continue
            if not _same(field, current, new):
                proposed[field] = new
        thumbnail = values.get('thumbnail_url')
        if thumbnail is None and values.get('image_url') is not None and (
                _blank(goods.thumbnail_url) or goods.thumbnail_url == goods.image_url):
            thumbnail = values['image_url']     # 缩略图原来跟着图片：跟着新图片走
        if thumbnail is not None and not (mode == 'append' and not _blank(goods.thumbnail_url)) \
                and not _same('thumbnail_url', goods.thumbnail_url, thumbnail):
            proposed['thumbnail_url'] = thumbnail
        error = _price_error(plan, analysis, proposed.get('price', goods.price),
                             proposed.get('discount_price', goods.discount_price),
                             'discount_price' in proposed, merged=True)
        if error is not None:
            plan.errors.append(error)
            return
    if not goods.is_active:
        proposed['is_active'] = True
    plan.proposed = proposed
    plan.action = 'update' if proposed else 'unchanged'
    merged = {attr: proposed.get(attr, getattr(goods, attr)) for attr in _SPEC_ATTRS}
    plan.spec_missing = _spec_missing(merged)


# ------------------------------------------------------------------
# 预览、执行
# ------------------------------------------------------------------

def preview(request) -> dict:
    """dry_run：只校验不写入，逐行结果 + import_token"""
    analysis = analyze(request)
    return {
        'dry_run': True,
        'import_token': analysis.token(),
        'mode': request.mode,
        'file': analysis.file_info(),
        'header_lang': analysis.header_lang,
        'columns': analysis.columns,
        'column_map': [{'index': index, 'header': header, 'field': field}
                       for index, header, field in analysis.column_map],
        'ignored_columns': analysis.ignored,
        'read_only_columns': analysis.read_only,
        'summary': {'rows': len(analysis.rows), **analysis.counts()},
        'rows': [plan.to_dict() for plan in analysis.rows],
    }


def _legacy_exception(plan) -> BadRequestException:
    error = plan.errors[0]
    legacy = error['_legacy']
    return BadRequestException(legacy['message'], error['code'], legacy.get('field'), details=legacy['details'])


def _check_token(analysis, token):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token.strip()):
        raise BadRequestException("import_token is not valid; validate the file again (dry_run=true)",
                                  PARAM_INVALID, 'import_token')
    token = token.strip()
    expected = analysis.token()
    if hmac.compare_digest(token, expected):
        return
    reason = 'DATA_CHANGED' if hmac.compare_digest(token[:32], expected[:32]) else 'FILE_CHANGED'
    raise ConflictException(
        "The file, the options or the goods changed since the validation; validate the file again",
        TOKEN_MISMATCH, details={'reason': reason})


def run(request, user_id, token=None) -> dict:
    """执行导入（带 token：跳过错误行；不带：有错误整批不导入、400）。每次都记导入记录"""
    try:
        analysis = analyze(request)
    except BadRequestException as exc:
        _record_failure(request, None, exc, user_id)
        raise
    if token is not None:
        _check_token(analysis, token)
    else:
        error_rows = [plan for plan in analysis.rows if plan.action == 'error']
        if error_rows:
            exc = _legacy_exception(error_rows[0])
            _record_failure(request, analysis, exc, user_id)
            raise exc
    try:
        record = _apply(analysis, user_id)
        result = _result(analysis, record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return result


def _result(analysis, record) -> dict:
    counts = analysis.counts()
    processed = counts['create'] + counts['update'] + counts['unchanged']
    return {
        'message': f"Bulk upload successful. Processed {processed} goods.",
        'import_id': record.id,
        'status': record.status,
        'mode': analysis.request.mode,
        'processed': processed,
        'created': counts['create'],
        'updated': counts['update'],
        'unchanged': counts['unchanged'],
        'skipped': counts['skip'],
        'errors': counts['error'],
        'has_error_file': record.error_file_name is not None,
        'file': analysis.file_info(),
        'columns': analysis.columns,
        'ignored_columns': analysis.ignored,
        'read_only_columns': analysis.read_only,
    }


def _apply(analysis, user_id) -> GoodsImport:
    now = clock.now()
    spec_changes, items = [], []
    for plan in analysis.rows:
        if plan.action == 'create':
            goods = GoodsService._build_goods(plan.data, user_id)
            plan.goods = goods
            spec_changes.append(_spec_change(goods, {}, 'import'))
        elif plan.action == 'update':
            goods = plan.goods
            before = goods_spec_snapshot(goods)
            for field, value in plan.proposed.items():
                setattr(goods, field, value)
            goods.updated_at = now
            spec_changes.append(_spec_change(goods, before, 'import'))
    db.session.flush()
    _emit_spec_changes(spec_changes)

    counts = analysis.counts()
    imported = counts['create'] + counts['update'] + counts['unchanged'] + counts['skip']
    status = 'completed' if not counts['error'] else ('partial' if imported else 'failed')
    record = _new_record(analysis.request, analysis, status, user_id)
    db.session.add(record)
    db.session.flush()
    for plan in analysis.rows:
        if plan.action in ('create', 'update'):
            items.append({'import_id': record.id, 'goods_id': plan.goods.id, 'action': plan.action})
    if items:
        db.session.execute(GoodsImportItem.__table__.insert(), items)
    return record


def _new_record(request, analysis, status, user_id, failure=None) -> GoodsImport:
    table = analysis.table if analysis is not None else None
    counts = analysis.counts() if analysis is not None else {}
    if failure is not None:
        # 整批没导入：只留错误行数
        counts = {'error': counts.get('error', 0)}
    keep_file = tabular.file_format(request.file_name) is not None
    record = GoodsImport(
        company_id=request.company_id,
        file_name=(request.file_name or '')[:255] or None,
        file_format=tabular.file_format(request.file_name),
        encoding=table.encoding if table is not None else None,
        encoding_detected=table.encoding_detected if table is not None and table.format == 'csv' else None,
        sheet_name=(table.sheet or None) if table is not None else None,
        mode=request.mode,
        status=status,
        row_count=len(analysis.rows) if analysis is not None else 0,
        created_count=counts.get('create', 0),
        updated_count=counts.get('update', 0),
        unchanged_count=counts.get('unchanged', 0),
        skipped_count=counts.get('skip', 0),
        error_count=counts.get('error', 0),
        columns=analysis.columns if analysis is not None else None,
        ignored_columns=analysis.ignored if analysis is not None else None,
        failure=failure,
        original_file=request.content if keep_file else None,
        original_size=len(request.content) if keep_file else None,
        files_expire_at=clock.now() + timedelta(days=IMPORT_FILE_RETENTION_DAYS),
        created_by=user_id,
    )
    if analysis is not None:
        error_rows = [plan for plan in analysis.rows if plan.action == 'error']
        if error_rows:
            record.errors = [{'row': plan.row, 'code': plan.code, 'name': plan.name,
                              'errors': [_public_error(error) for error in plan.errors]}
                             for plan in error_rows[:IMPORT_ERROR_DETAIL_ROWS]]
            content, file_name = build_error_file(analysis)
            record.error_file = content
            record.error_file_name = file_name
    return record


def _record_failure(request, analysis, exc, user_id):
    """执行失败（整份文件不能用 / 不带 token 有错误）也记一条 failed 的导入记录（独立提交，导入本身不写任何商品）"""
    db.session.rollback()
    failure = {'code': exc.biz_code, 'message': exc.message, 'details': exc.details}
    try:
        db.session.add(_new_record(request, analysis, 'failed', user_id, failure=failure))
        db.session.commit()
    except Exception:  # noqa: BLE001 - 记录失败不影响给调用方的错误响应
        db.session.rollback()
        current_app.logger.exception("Failed to record the failed goods import")


# ------------------------------------------------------------------
# 错误行文件：原格式（CSV 同编码 / Excel）+ 末列「错误」
# ------------------------------------------------------------------

def _error_text(plan, analysis, lang) -> str:
    separator = '; ' if lang == 'en-US' else '；'
    parts = []
    for error in plan.errors:
        column = error.get('column') or COLUMNS_BY_KEY[error['field']].labels[lang]
        params = {k: v for k, v in error.items() if k not in ('field', 'column', 'code', 'reason', 'message')
                  and not k.startswith('_')}
        parts.append(f"{column}: {message(error['reason'], lang, **params)}")
    return separator.join(parts)


def build_error_file(analysis, lang=None):
    """→ (字节, 文件名)；没有错误行 → (None, None)"""
    error_rows = [plan for plan in analysis.rows if plan.action == 'error']
    if not error_rows:
        return None, None
    lang = lang or analysis.error_lang()
    if lang == 'zh-CN' and analysis.table.encoding == 'cp932':
        lang = 'ja-JP'          # Shift_JIS 写不下简体字：Shift_JIS 的 CSV 错误说明用日文
    width = len(analysis.header_cells)
    header = list(analysis.header_cells) + [ERROR_COLUMN_LABELS[lang]]
    rows = []
    for plan in error_rows:
        cells = list(plan.cells[:width]) + [''] * max(0, width - len(plan.cells))
        rows.append(cells + [_error_text(plan, analysis, lang)])
    stem = re.sub(r'\.[^.]*$', '', analysis.request.file_name or 'goods') or 'goods'
    if analysis.table.format == 'xlsx':
        content = tabular.write_xlsx(analysis.table.sheet or 'Goods', header, rows, kinds=['text'] * len(header))
        return content, f"{stem}_errors.xlsx"
    encoding = 'cp932' if analysis.table.encoding == 'cp932' else 'utf-8-sig'
    return tabular.write_csv(header, rows, encoding), f"{stem}_errors.csv"


def error_file_for(request):
    """dry_run 阶段下载错误行（POST /goods/bulk_upload/errors）：→ (字节, 文件名, Content-Type)；没有错误行 → None"""
    analysis = analyze(request)
    content, file_name = build_error_file(analysis)
    if content is None:
        return None
    encoding = analysis.table.encoding
    return content, file_name, tabular.content_type(analysis.table.format, 'cp932' if encoding == 'cp932' else None)


def resolve_lang(value):
    """错误行文件的语言参数：空 → None（按文件表头的语言）；不认识 → 400 10019"""
    from .columns import resolve_lang as _resolve
    if value in (None, ''):
        return None
    lang = _resolve(value)
    if lang is None:
        raise BadRequestException(f"lang must be one of {', '.join(LANGS)}", PARAM_INVALID, 'lang')
    return lang


# ------------------------------------------------------------------
# 定时清理：原文件 / 错误行文件保留 30 天
# ------------------------------------------------------------------

def purge_expired_files(now=None) -> int:
    now = now or clock.now()
    records = (GoodsImport.query
               .filter(GoodsImport.files_purged_at.is_(None), GoodsImport.files_expire_at.isnot(None),
                       GoodsImport.files_expire_at <= now)
               .limit(500).all())
    for record in records:
        record.original_file = None
        record.error_file = None
        record.files_purged_at = now
    if records:
        db.session.commit()
    return len(records)
