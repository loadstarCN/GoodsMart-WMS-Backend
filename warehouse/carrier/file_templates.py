"""承运商文件方式的模板引擎（doc/shipping-flow.md §6.1）：没有 API 合同时，按承运商软件的导入格式导出 CSV，
在承运商软件里打完单，再把带运单号的 CSV 导回。本模块只处理数据（不碰数据库）：
- 模板校验（validate_template）：结构、编码、列、变量与过滤器、导入设置、服务选项、账号项。
- 导出（render_rows / write_csv）：每张 DN 一行，按列的 value 模板渲染，逐列检查（必填、超长、编码不了的字符、
  不在 JIS 第1・第2水准的字），字段内换行 / 制表符换成空格；有问题的行由调用方整批拦下（不静默截断）。
- 导入（parse_import）：按编码（及备选编码）解码、跳过表头行、按 1 起的列号取匹配键与运单号、跳过指定的行
  （如佐川的削除区分 = 2），一张 DN 可以有多行（多件 = 多个运单号）。

模板（预设 warehouse/carrier/presets/<country>/<code>.json 同一结构）：
    {code, name{语言}, country, version, sources[], notes, verified, tracking_url_template, tracking_url_fallback?,
     service_option?（哪个服务选项代表服务种类，运单上的 service_type / service_name 用它）,
     export: {format: 'csv', encoding: 'cp932' | 'utf-8' | 'utf-8-sig', delimiter, quoting: 'minimal' | 'all',
              header: bool, line_ending: '\\r\\n' | '\\n', length_unit: 'chars' | 'sjis_bytes', row_unit: 'dn',
              charset?: 'jis_x0208', columns: [{header, value, required?, max_length?, notes?}]},
     import: {encoding, encoding_fallbacks[], delimiter?, header_rows, reference_column, reference_value: 'DN{dn.id}',
              reference_parse: '^DN(\\d+)$', tracking_column, tracking_normalize: 'digits' | null,
              skip_rows_when?: {column, equals}},
     service_options: [{key, kind: 'select' | 'date' | 'money' | 'text', label{语言}, values?[{value, label}],
                        default?, help?, max_length?}],
     account_fields: [{key, label{语言}, required, help?}]}
编码写 shift_jis / sjis / windows-31j 的一律按 cp932（Python 的 shift_jis 编不了「～」「①」等）。

value 模板：字面量 + {变量|过滤器:参数|…}，{{ / }} 是字面的花括号。变量：
    {dn.id} {dn.order_number} {dn.remark} {dn.expected_shipping_date} {dn.special_handling} {dn.packaging_info}
    {ship_date}（出货日 = 仓库当地今天）
    {ship_to.*} {shipper.*}（结构化地址：country postal_code admin_area locality dependent_locality address_line1
        address_line2 organization name phone email contact address_text country_name，及 latin.<字段>）
    {goods.summary}（商品名，「 / 」分隔）{goods.first_name} {goods.quantity}（件数合计）{goods.count}（品种数）
    {package.count}（箱数，没录箱子为空）{package.total_weight_kg}
    {options.<key>}（DN 的运单服务选项，没设时用选项的 default）{carrier.account.<key>} {carrier.name} {carrier.code}
过滤器：|digits |truncate:n |truncate_bytes:n（按 cp932 字节，不切断双字节字）|slice:a:b |date:%Y/%m/%d（空值输出空串）
    |default:x |map:k=v,k=v |upper |zenkaku（半角转全角）|nospace（去半角 / 全角空格）
    |jp_phone（+81 → 0 开头国内号码，全角数字转半角）|int
"""
import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ENCODING_ALIASES = {
    'cp932': 'cp932', 'shift_jis': 'cp932', 'shift-jis': 'cp932', 'sjis': 'cp932', 'shiftjis': 'cp932',
    'windows-31j': 'cp932', 'ms932': 'cp932', 'mskanji': 'cp932',
    'utf-8': 'utf-8', 'utf8': 'utf-8',
    'utf-8-sig': 'utf-8-sig', 'utf8-sig': 'utf-8-sig', 'utf-8-bom': 'utf-8-sig',
}
ENCODING_LABELS = {'cp932': 'Shift_JIS (CP932)', 'utf-8': 'UTF-8', 'utf-8-sig': 'UTF-8 (BOM)'}
CONTENT_TYPE_CHARSETS = {'cp932': 'Shift_JIS', 'utf-8': 'utf-8', 'utf-8-sig': 'utf-8'}
QUOTING = {'minimal': csv.QUOTE_MINIMAL, 'all': csv.QUOTE_ALL}
LENGTH_UNITS = ('chars', 'sjis_bytes')
LINE_ENDINGS = ('\r\n', '\n')
CHARSETS = ('jis_x0208',)
OPTION_KINDS = ('select', 'date', 'money', 'text')
TRACKING_NORMALIZE = ('digits',)

MAX_COLUMNS = 300
MAX_VALUE_TEMPLATE = 500
MAX_OPTIONS = 30
OPTION_TEXT_MAX = 100

ADDRESS_VARIABLES = frozenset({
    'country', 'postal_code', 'admin_area', 'locality', 'dependent_locality', 'address_line1', 'address_line2',
    'organization', 'name', 'phone', 'email', 'contact', 'address_text', 'country_name',
})
LATIN_VARIABLES = frozenset({'name', 'organization', 'address_line1', 'address_line2', 'locality', 'admin_area'})
FIXED_VARIABLES = {
    'dn': frozenset({'id', 'order_number', 'remark', 'expected_shipping_date', 'special_handling', 'packaging_info'}),
    'goods': frozenset({'summary', 'first_name', 'quantity', 'count'}),
    'package': frozenset({'count', 'total_weight_kg'}),
}
_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
_TOKEN_RE = re.compile(r'\{\{|\}\}|\{([^{}]*)\}')
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)
_WHITESPACE = re.compile(r'[\s　]+')
_NEWLINES = re.compile(r'[\r\n\t\v\f]+')


class TemplateError(ValueError):
    """模板不合法：path = 出错的位置（如 export.columns[3].value），message = 英文说明"""

    def __init__(self, path, message):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


# ------------------------------------------------------------------
# 文字工具
# ------------------------------------------------------------------

def normalize_encoding(name):
    """编码名 → cp932 / utf-8 / utf-8-sig；不支持的为 None"""
    return ENCODING_ALIASES.get(str(name or '').strip().lower().replace('_', '-')) or \
        ENCODING_ALIASES.get(str(name or '').strip().lower())


def text_of(value) -> str:
    """模板变量的值 → 文字：None → ''；整数值的数（含 Decimal / float）不带小数；日期 → YYYY-MM-DD"""
    if value is None:
        return ''
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        value = Decimal(repr(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            return ''
        if value == value.to_integral_value():
            return str(value.quantize(Decimal(1)))
        return format(value.normalize(), 'f')
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def cp932_char_bytes(ch: str) -> int:
    """一个字符的 cp932 字节数（编码不了的按 2 算）"""
    try:
        return len(ch.encode('cp932'))
    except UnicodeEncodeError:
        return 2


def sjis_length(text: str) -> int:
    return sum(cp932_char_bytes(ch) for ch in text)


def truncate_bytes(text: str, limit: int) -> str:
    """按 cp932 字节截断，不切断双字节字"""
    total, out = 0, []
    for ch in text:
        size = cp932_char_bytes(ch)
        if total + size > limit:
            break
        out.append(ch)
        total += size
    return ''.join(out)


def to_zenkaku(text: str) -> str:
    """半角 → 全角：ASCII 可见字符 → 全角形，空格 → 全角空格，半角片假名（含浊点）→ 全角片假名"""
    out, kana = [], []

    def flush():
        if kana:
            converted = unicodedata.normalize('NFKC', ''.join(kana))
            out.append(converted.replace('゙', '゛').replace('゚', '゜'))
            kana.clear()

    for ch in text:
        code = ord(ch)
        if 0xFF61 <= code <= 0xFF9F:
            kana.append(ch)
            continue
        flush()
        if ch == ' ':
            out.append('　')
        elif 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        else:
            out.append(ch)
    flush()
    return ''.join(out)


def unencodable_chars(text: str, encoding: str) -> list:
    """text 里用 encoding 编码不了的字符（去重、按出现顺序）"""
    if encoding in ('utf-8', 'utf-8-sig'):
        return []
    found = []
    for ch in text:
        try:
            ch.encode(encoding)
        except UnicodeEncodeError:
            if ch not in found:
                found.append(ch)
    return found


def non_jis_x0208_chars(text: str) -> list:
    """cp932 编得了、但不在 JIS X 0208（JIS 第1・第2水准及非汉字）里的字符：NEC 特殊文字（①、㈱ …）、
    NEC 选定 IBM 扩展 / IBM 扩展（髙、﨑 …）、外字区"""
    found = []
    for ch in text:
        try:
            raw = ch.encode('cp932')
        except UnicodeEncodeError:
            continue
        if len(raw) == 2 and (raw[0] == 0x87 or raw[0] in (0xED, 0xEE) or 0xF0 <= raw[0] <= 0xFC):
            if ch not in found:
                found.append(ch)
    return found


def _parse_date(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(text[:10] if fmt != '%Y%m%d' else text[:8], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


# ------------------------------------------------------------------
# 过滤器
# ------------------------------------------------------------------

def _int_arg(name, arg, minimum=0):
    try:
        number = int(str(arg).strip())
    except (TypeError, ValueError):
        raise ValueError(f"|{name} needs an integer argument") from None
    if number < minimum:
        raise ValueError(f"|{name} needs an integer of at least {minimum}")
    return number


def _slice_args(arg):
    parts = str(arg or '').split(':')
    if len(parts) != 2:
        raise ValueError("|slice needs two arguments: |slice:start:end (either may be empty)")
    result = []
    for part in parts:
        part = part.strip()
        if part == '':
            result.append(None)
            continue
        try:
            result.append(int(part))
        except ValueError:
            raise ValueError("|slice arguments must be integers") from None
    return tuple(result)


def _map_args(arg):
    mapping = {}
    for item in str(arg or '').split(','):
        if not item:
            continue
        if '=' not in item:
            raise ValueError("|map needs key=value pairs separated by commas")
        key, _, value = item.partition('=')
        mapping[key] = value
    if not mapping:
        raise ValueError("|map needs at least one key=value pair")
    return mapping


def _date_arg(arg):
    fmt = str(arg or '')
    if not fmt:
        raise ValueError("|date needs a format, e.g. |date:%Y/%m/%d")
    try:
        date(2026, 1, 2).strftime(fmt)
    except ValueError:
        raise ValueError("|date format is invalid") from None
    return fmt


def _f_int(value):
    text = text_of(value).strip().replace(',', '')
    if not text:
        return ''
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite():
        return ''
    return str(number.to_integral_value(rounding=ROUND_HALF_UP))


def _f_date(value, fmt):
    parsed = _parse_date(value)
    if parsed is None:
        return '' if value in (None, '') else text_of(value)
    return parsed.strftime(fmt)


def _jp_phone(value):
    from warehouse.country_rules.jp import jp_phone
    return jp_phone(value)


# 过滤器：名字 → (参数解析（None = 不带参数）, 函数(值, 参数) → 值)
FILTERS = {
    'digits': (None, lambda v, _a: re.sub(r'\D', '', unicodedata.normalize('NFKC', text_of(v)))),
    'truncate': (lambda a: _int_arg('truncate', a, 1), lambda v, n: text_of(v)[:n]),
    'truncate_bytes': (lambda a: _int_arg('truncate_bytes', a, 1), lambda v, n: truncate_bytes(text_of(v), n)),
    'slice': (_slice_args, lambda v, ab: text_of(v)[ab[0]:ab[1]]),
    'date': (_date_arg, _f_date),
    'default': (lambda a: str(a), lambda v, d: d if text_of(v) == '' else v),
    'map': (_map_args, lambda v, m: m.get(text_of(v), v)),
    'upper': (None, lambda v, _a: text_of(v).upper()),
    'zenkaku': (None, lambda v, _a: to_zenkaku(text_of(v))),
    'nospace': (None, lambda v, _a: _WHITESPACE.sub('', text_of(v))),
    'jp_phone': (None, lambda v, _a: _jp_phone(v)),
    'int': (None, lambda v, _a: _f_int(v)),
}


# ------------------------------------------------------------------
# value 模板：编译与渲染
# ------------------------------------------------------------------

def _check_variable(path: tuple, where: str):
    root = path[0]
    if root == 'ship_date':
        if len(path) != 1:
            raise TemplateError(where, "{ship_date} has no fields")
        return
    if root in FIXED_VARIABLES:
        if len(path) != 2 or path[1] not in FIXED_VARIABLES[root]:
            raise TemplateError(where, f"unknown variable {{{'.'.join(path)}}} (known: "
                                       f"{', '.join(root + '.' + k for k in sorted(FIXED_VARIABLES[root]))})")
        return
    if root in ('ship_to', 'shipper'):
        if len(path) == 2 and path[1] in ADDRESS_VARIABLES:
            return
        if len(path) == 3 and path[1] == 'latin' and path[2] in LATIN_VARIABLES:
            return
        raise TemplateError(where, f"unknown variable {{{'.'.join(path)}}}: address fields are "
                                   f"{', '.join(sorted(ADDRESS_VARIABLES))} and latin.<field>")
    if root == 'options':
        if len(path) != 2:
            raise TemplateError(where, "use {options.<key>}")
        return
    if root == 'carrier':
        if len(path) == 2 and path[1] in ('name', 'code'):
            return
        if len(path) == 3 and path[1] == 'account':
            return
        raise TemplateError(where, "use {carrier.name}, {carrier.code} or {carrier.account.<key>}")
    raise TemplateError(where, f"unknown variable {{{'.'.join(path)}}}")


def compile_value(template: str, where='value') -> list:
    """value 模板 → [字面量 str | (变量路径 tuple, [(过滤器名, 参数)])]；不合法抛 TemplateError"""
    if not isinstance(template, str):
        raise TemplateError(where, "must be a string")
    if len(template) > MAX_VALUE_TEMPLATE:
        raise TemplateError(where, f"must not exceed {MAX_VALUE_TEMPLATE} characters")
    parts, position = [], 0
    for match in _TOKEN_RE.finditer(template):
        literal = template[position:match.start()]
        if '{' in literal or '}' in literal:
            raise TemplateError(where, "unbalanced braces (write {{ / }} for literal braces)")
        if literal:
            parts.append(literal)
        position = match.end()
        token = match.group(0)
        if token in ('{{', '}}'):
            parts.append(token[0])
            continue
        expression = match.group(1).strip()
        if not expression:
            raise TemplateError(where, "empty {}")
        pieces = expression.split('|')
        path = tuple(p.strip() for p in pieces[0].split('.'))
        if not all(_IDENT_RE.match(p) for p in path):
            raise TemplateError(where, f"invalid variable {{{pieces[0].strip()}}}")
        _check_variable(path, where)
        filters = []
        for piece in pieces[1:]:
            name, sep, arg = piece.strip().partition(':')
            name = name.strip()
            spec = FILTERS.get(name)
            if spec is None:
                raise TemplateError(where, f"unknown filter |{name} (known: {', '.join(sorted(FILTERS))})")
            parse_arg = spec[0]
            if parse_arg is None:
                if sep:
                    raise TemplateError(where, f"|{name} takes no argument")
                filters.append((name, None))
                continue
            if not sep:
                raise TemplateError(where, f"|{name} needs an argument")
            try:
                filters.append((name, parse_arg(arg)))
            except ValueError as exc:
                raise TemplateError(where, str(exc)) from None
        parts.append((path, filters))
    tail = template[position:]
    if '{' in tail or '}' in tail:
        raise TemplateError(where, "unbalanced braces (write {{ / }} for literal braces)")
    if tail:
        parts.append(tail)
    return parts


def lookup(context: dict, path: tuple):
    value = context
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
        if value is None:
            return None
    return value


def render_value(compiled: list, context: dict) -> str:
    """编译好的 value 模板 + 一张 DN 的上下文 → 文字（字段内换行 / 制表符换成空格，去首尾空白）"""
    out = []
    for part in compiled:
        if isinstance(part, str):
            out.append(part)
            continue
        path, filters = part
        value = lookup(context, path)
        for name, arg in filters:
            value = FILTERS[name][1](value, arg)
        out.append(text_of(value))
    return _NEWLINES.sub(' ', ''.join(out)).strip()


# ------------------------------------------------------------------
# 模板校验
# ------------------------------------------------------------------

def _label(value, where):
    if value is None:
        return None
    if isinstance(value, str):
        return {'en-US': value}
    if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return dict(value)
    raise TemplateError(where, "must be a text or {locale: text}")


def _bool(value, where, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TemplateError(where, "must be true or false")
    return value


def _int(value, where, minimum, maximum, default=None):
    if value is None:
        if default is None:
            raise TemplateError(where, "is required")
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise TemplateError(where, f"must be an integer from {minimum} to {maximum}")
    return value


def _encoding(value, where, default=None):
    if value is None and default is not None:
        return default
    encoding = normalize_encoding(value)
    if encoding is None:
        raise TemplateError(where, "must be cp932 (shift_jis), utf-8 or utf-8-sig")
    return encoding


def _delimiter(value, where):
    if value is None:
        return ','
    if not isinstance(value, str) or len(value) != 1 or value in '"\r\n':
        raise TemplateError(where, "must be one character (e.g. , or a tab)")
    return value


def _url(value, where):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not _URL_RE.match(value.strip()) or len(value) > 500:
        raise TemplateError(where, "must be an http(s) URL")
    return value.strip()


def _validate_export(export) -> dict:
    if not isinstance(export, dict):
        raise TemplateError('export', "is required (an object)")
    result = dict(export)
    if (export.get('format') or 'csv') != 'csv':
        raise TemplateError('export.format', "only csv is supported")
    result['format'] = 'csv'
    result['encoding'] = _encoding(export.get('encoding'), 'export.encoding', 'cp932')
    result['delimiter'] = _delimiter(export.get('delimiter'), 'export.delimiter')
    quoting = export.get('quoting') or 'minimal'
    if quoting not in QUOTING:
        raise TemplateError('export.quoting', "must be minimal or all")
    result['quoting'] = quoting
    result['header'] = _bool(export.get('header'), 'export.header', True)
    line_ending = export.get('line_ending') or '\r\n'
    if line_ending not in LINE_ENDINGS:
        raise TemplateError('export.line_ending', "must be \\r\\n or \\n")
    result['line_ending'] = line_ending
    unit = export.get('length_unit') or 'chars'
    if unit not in LENGTH_UNITS:
        raise TemplateError('export.length_unit', "must be chars or sjis_bytes")
    result['length_unit'] = unit
    if (export.get('row_unit') or 'dn') != 'dn':
        raise TemplateError('export.row_unit', "only dn (one row per DN) is supported")
    result['row_unit'] = 'dn'
    charset = export.get('charset')
    if charset is not None and charset not in CHARSETS:
        raise TemplateError('export.charset', "must be jis_x0208 or null")
    if charset == 'jis_x0208' and result['encoding'] != 'cp932':
        raise TemplateError('export.charset', "jis_x0208 needs the cp932 encoding")
    result['charset'] = charset
    columns = export.get('columns')
    if not isinstance(columns, list) or not columns:
        raise TemplateError('export.columns', "must be a non-empty list")
    if len(columns) > MAX_COLUMNS:
        raise TemplateError('export.columns', f"must not exceed {MAX_COLUMNS} columns")
    normalized = []
    for index, column in enumerate(columns):
        where = f'export.columns[{index}]'
        if not isinstance(column, dict):
            raise TemplateError(where, "must be an object {header, value, required?, max_length?}")
        header = column.get('header')
        if not isinstance(header, str) or len(header) > 200:
            raise TemplateError(f'{where}.header', "must be a text of at most 200 characters")
        if result['header'] and unencodable_chars(header, result['encoding']):
            raise TemplateError(f'{where}.header', f"cannot be written in {ENCODING_LABELS[result['encoding']]}")
        value = column.get('value', '')
        if value is None:
            value = ''
        compile_value(value, f'{where}.value')
        max_length = column.get('max_length')
        if max_length is not None:
            max_length = _int(max_length, f'{where}.max_length', 1, 100000)
        normalized.append({**column, 'header': header, 'value': value,
                           'required': _bool(column.get('required'), f'{where}.required', False),
                           'max_length': max_length})
    result['columns'] = normalized
    return result


def _validate_import(data, export) -> dict | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TemplateError('import', "must be an object or null")
    result = dict(data)
    result['encoding'] = _encoding(data.get('encoding'), 'import.encoding', 'cp932')
    fallbacks = data.get('encoding_fallbacks') or []
    if not isinstance(fallbacks, list):
        raise TemplateError('import.encoding_fallbacks', "must be a list")
    result['encoding_fallbacks'] = [_encoding(e, f'import.encoding_fallbacks[{i}]') for i, e in enumerate(fallbacks)]
    result['delimiter'] = _delimiter(data.get('delimiter', export.get('delimiter') if export else None),
                                     'import.delimiter')
    result['header_rows'] = _int(data.get('header_rows'), 'import.header_rows', 0, 20, 0)
    result['reference_column'] = _int(data.get('reference_column'), 'import.reference_column', 1, 1000)
    result['tracking_column'] = _int(data.get('tracking_column'), 'import.tracking_column', 1, 1000)
    pattern = data.get('reference_parse') or r'^DN(\d+)$'
    try:
        compiled = re.compile(pattern)
    except (re.error, TypeError):
        raise TemplateError('import.reference_parse', "must be a regular expression") from None
    if compiled.groups < 1:
        raise TemplateError('import.reference_parse', "needs a group that captures the DN id, e.g. ^DN(\\d+)$")
    result['reference_parse'] = pattern
    reference_value = data.get('reference_value') or 'DN{dn.id}'
    sample = render_value(compile_value(reference_value, 'import.reference_value'), {'dn': {'id': 12345}})
    match = compiled.search(sample)
    if not match or match.group(1) != '12345':
        raise TemplateError('import.reference_parse', f"does not read the DN id back from reference_value "
                                                      f"({reference_value} → {sample})")
    result['reference_value'] = reference_value
    normalize = data.get('tracking_normalize')
    if normalize is not None and normalize not in TRACKING_NORMALIZE:
        raise TemplateError('import.tracking_normalize', "must be digits or null")
    result['tracking_normalize'] = normalize
    skip = data.get('skip_rows_when')
    if skip is not None:
        if not isinstance(skip, dict):
            raise TemplateError('import.skip_rows_when', "must be an object {column, equals}")
        equals = skip.get('equals')
        values = equals if isinstance(equals, list) else [equals]
        if not values or not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in values):
            raise TemplateError('import.skip_rows_when.equals', "must be a text or a list of texts")
        result['skip_rows_when'] = {**skip, 'column': _int(skip.get('column'), 'import.skip_rows_when.column', 1, 1000),
                                    'equals': [str(v) for v in values]}
    return result


def validate_service_options(options, where='service_options') -> list:
    """服务选项声明（模板的 service_options、插件的 CarrierAdapter.service_options）→ 规范化后的列表
    （kind 缺省：有 values 为 select，否则 text）；不合法抛 TemplateError"""
    try:
        return _validate_options(list(options) if isinstance(options, tuple) else options)
    except TemplateError as exc:
        raise TemplateError(exc.path.replace('service_options', where, 1), exc.message) from None


def _validate_options(options) -> list:
    if options is None:
        return []
    if not isinstance(options, list) or len(options) > MAX_OPTIONS:
        raise TemplateError('service_options', f"must be a list of at most {MAX_OPTIONS} options")
    result, keys = [], set()
    for index, item in enumerate(options):
        where = f'service_options[{index}]'
        if not isinstance(item, dict):
            raise TemplateError(where, "must be an object {key, kind, label, values?}")
        key = item.get('key')
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise TemplateError(f'{where}.key', "must be lower-case letters, digits and _ (max 40)")
        if key in keys:
            raise TemplateError(f'{where}.key', f"duplicate option {key}")
        keys.add(key)
        kind = item.get('kind') or ('select' if item.get('values') is not None else 'text')
        if kind not in OPTION_KINDS:
            raise TemplateError(f'{where}.kind', "must be select, date, money or text")
        option = {**item, 'key': key, 'kind': kind, 'label': _label(item.get('label'), f'{where}.label') or
                  {'en-US': key}}
        if kind == 'select':
            values = item.get('values')
            if not isinstance(values, list) or not values:
                raise TemplateError(f'{where}.values', "a select option needs a non-empty list of values")
            normalized = []
            for v_index, entry in enumerate(values):
                if not isinstance(entry, dict) or not isinstance(entry.get('value'), (str, int)) \
                        or isinstance(entry.get('value'), bool):
                    raise TemplateError(f'{where}.values[{v_index}]', "must be an object {value, label}")
                normalized.append({**entry, 'value': str(entry['value']),
                                   'label': _label(entry.get('label'), f'{where}.values[{v_index}].label') or
                                   {'en-US': str(entry['value'])}})
            option['values'] = normalized
            default = item.get('default')
            if default not in (None, '') and str(default) not in {v['value'] for v in normalized}:
                raise TemplateError(f'{where}.default', "must be one of the values")
        if item.get('max_length') is not None:
            option['max_length'] = _int(item.get('max_length'), f'{where}.max_length', 1, OPTION_TEXT_MAX)
        result.append(option)
    return result


def _validate_account_fields(fields) -> list:
    if fields is None:
        return []
    if not isinstance(fields, list) or len(fields) > 20:
        raise TemplateError('account_fields', "must be a list of at most 20 fields")
    result, keys = [], set()
    for index, item in enumerate(fields):
        where = f'account_fields[{index}]'
        if not isinstance(item, dict) or not isinstance(item.get('key'), str) or not _KEY_RE.match(item['key']):
            raise TemplateError(f'{where}.key', "must be lower-case letters, digits and _ (max 40)")
        if item['key'] in keys:
            raise TemplateError(f'{where}.key', f"duplicate account field {item['key']}")
        keys.add(item['key'])
        result.append({**item, 'label': _label(item.get('label'), f'{where}.label') or {'en-US': item['key']},
                       'required': _bool(item.get('required'), f'{where}.required', False)})
    return result


def validate_template(template) -> dict:
    """完整模板（预设合并改动之后）→ 规范化后的模板（编码名统一成 cp932 / utf-8 / utf-8-sig、补默认值）；
    不合法抛 TemplateError(path, message)"""
    if not isinstance(template, dict):
        raise TemplateError('', "must be an object")
    result = dict(template)
    result['export'] = _validate_export(template.get('export'))
    result['import'] = _validate_import(template.get('import'), result['export'])
    result['service_options'] = _validate_options(template.get('service_options'))
    result['account_fields'] = _validate_account_fields(template.get('account_fields'))
    result['tracking_url_template'] = _url(template.get('tracking_url_template'), 'tracking_url_template')
    if result['tracking_url_template'] and '{tracking_number}' not in result['tracking_url_template']:
        raise TemplateError('tracking_url_template', "must contain {tracking_number}")
    result['tracking_url_fallback'] = _url(template.get('tracking_url_fallback'), 'tracking_url_fallback')
    service_option = template.get('service_option')
    if service_option is not None and service_option not in {o['key'] for o in result['service_options']}:
        raise TemplateError('service_option', "must be the key of one of the service_options")
    result['name'] = _label(template.get('name'), 'name') or {'en-US': str(template.get('code') or 'file')}
    result['verified'] = bool(template.get('verified', False))
    return result


def merge_template(base, override):
    """预设 + 承运商的改动（file_template）：对象逐键合并，列表 / 标量整个替换，改动里值为 null 的键删掉"""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override if override is not None else base
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_template(merged[key], value)
        else:
            merged[key] = value
    return merged


# ------------------------------------------------------------------
# 导出
# ------------------------------------------------------------------

def _length(text: str, unit: str) -> int:
    return sjis_length(text) if unit == 'sjis_bytes' else len(text)


def _unit_text(unit: str) -> str:
    return 'bytes (Shift_JIS)' if unit == 'sjis_bytes' else 'characters'


def uses_package_fields(template) -> bool:
    """导出的列是否离不开箱子数据：有列用了 {package.*} 且没带 |default（带 |default 的，如预设的
    {package.count|default:1}，没录箱子时按默认值导出）。打包这一步据此决定没录箱子要不要提示（PACKAGES_MISSING）"""
    export = (template or {}).get('export') or {}
    for index, column in enumerate(export.get('columns') or []):
        try:
            parts = compile_value(column.get('value') or '', f'export.columns[{index}].value')
        except TemplateError:
            continue
        for part in parts:
            if isinstance(part, tuple) and part[0][:1] == ('package',) and \
                    not any(name == 'default' for name, _arg in part[1]):
                return True
    return False


def render_rows(template: dict, contexts: list) -> list:
    """每张 DN 的上下文 → [{values: [每列文字], problems: [{column, column_index, code, message, ...}]}]。
    检查：REQUIRED（必填为空）、TOO_LONG（按 length_unit 超长；地址 / 姓名不截断，报错让人改）、
    UNENCODABLE（编码不了的字符）、CHARACTER_NOT_ALLOWED（charset jis_x0208 时不在 JIS 第1・第2水准的字）。"""
    export = template['export']
    unit, encoding, charset = export['length_unit'], export['encoding'], export.get('charset')
    compiled = [compile_value(column['value'], f'export.columns[{i}].value')
                for i, column in enumerate(export['columns'])]
    results = []
    for context in contexts:
        values, problems = [], []
        for index, (column, parts) in enumerate(zip(export['columns'], compiled), start=1):
            header = column['header'] or f'#{index}'
            text = render_value(parts, context)
            values.append(text)

            def add(code, message, **extra):
                problems.append({'column': header, 'column_index': index, 'code': code, 'message': message,
                                 **extra})

            if not text:
                if column['required']:
                    add('REQUIRED', f"{header} (column {index}) is required but empty")
                continue
            bad = unencodable_chars(text, encoding)
            if bad:
                add('UNENCODABLE', f"{header} (column {index}) has characters that cannot be written in "
                                   f"{ENCODING_LABELS[encoding]}: {' '.join(bad)}", characters=bad, value=text)
            elif charset == 'jis_x0208':
                outside = non_jis_x0208_chars(text)
                if outside:
                    add('CHARACTER_NOT_ALLOWED', f"{header} (column {index}) has characters the carrier software does "
                                                 f"not accept (outside JIS level 1 / 2): {' '.join(outside)}",
                        characters=outside, value=text)
            if column['max_length'] is not None:
                length = _length(text, unit)
                if length > column['max_length']:
                    add('TOO_LONG', f"{header} (column {index}) is {length} {_unit_text(unit)} long; at most "
                                    f"{column['max_length']} allowed", max_length=column['max_length'],
                        length=length, length_unit=unit, value=text)
        results.append({'values': values, 'problems': problems})
    return results


def write_csv(template: dict, rows: list) -> bytes:
    """表头（export.header 时）+ 各行 → 按模板编码的 CSV 字节（行尾 line_ending）。
    行里的字符都已检查过编码；调用方保证没有问题行。"""
    export = template['export']
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, delimiter=export['delimiter'], quotechar='"', doublequote=True,
                        quoting=QUOTING[export['quoting']], lineterminator=export['line_ending'])
    if export['header']:
        writer.writerow([column['header'] for column in export['columns']])
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode(export['encoding'])


def content_type(template: dict) -> str:
    return f"text/csv; charset={CONTENT_TYPE_CHARSETS[template['export']['encoding']]}"


# ------------------------------------------------------------------
# 导入
# ------------------------------------------------------------------

class ImportFileError(ValueError):
    """导回的文件读不了（编码不对、不是 CSV……）"""


def decode(data: bytes, settings: dict):
    """→ (文字, 实际用的编码)。有 BOM 按 UTF-8；候选编码里有 UTF-8 且内容是合法 UTF-8 时按 UTF-8
    （Shift_JIS 的日文几乎不会恰好是合法 UTF-8，反过来却常常「解得开」成乱码）；否则按候选顺序试。"""
    candidates = [settings['encoding']] + [e for e in settings.get('encoding_fallbacks') or []
                                           if e != settings['encoding']]
    if data.startswith(b'\xef\xbb\xbf'):
        return data[3:].decode('utf-8', errors='strict'), 'utf-8-sig'
    utf8_allowed = any(e in ('utf-8', 'utf-8-sig') for e in candidates)
    if utf8_allowed:
        try:
            return data.decode('utf-8'), 'utf-8'
        except UnicodeDecodeError:
            pass
    for encoding in candidates:
        if encoding in ('utf-8', 'utf-8-sig'):
            continue
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    labels = ' / '.join(ENCODING_LABELS.get(e, e) for e in candidates)
    raise ImportFileError(f"The file cannot be read as {labels}")


def normalize_tracking(value, mode) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    if mode == 'digits':
        return re.sub(r'\D', '', text)
    return re.sub(r'\s+', '', text)


def parse_import(template: dict, data: bytes, max_rows=5000) -> dict:
    """导回的运单号文件 → {encoding, rows: [{line, reference, dn_id | None, tracking_number, raw_tracking}],
    skipped（按 skip_rows_when 跳过的行数）}。表头行（header_rows）只在它不像数据行（匹配键解析不出 DN）时跳过。
    读不了抛 ImportFileError。"""
    settings = template.get('import')
    if not settings:
        raise ImportFileError("This file template cannot import tracking numbers (no import settings)")
    text, encoding = decode(data, settings)
    pattern = re.compile(settings['reference_parse'])
    ref_index, track_index = settings['reference_column'] - 1, settings['tracking_column'] - 1
    skip = settings.get('skip_rows_when')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=settings['delimiter'])
    rows, skipped, data_rows = [], 0, 0
    try:
        for index, cells in enumerate(reader):
            line = reader.line_num
            if not any((cell or '').strip() for cell in cells):
                continue
            reference = (cells[ref_index] if ref_index < len(cells) else '').strip()
            reference = unicodedata.normalize('NFKC', reference)
            match = pattern.search(reference) if reference else None
            if index < settings['header_rows'] and match is None:
                continue                                    # 表头
            if skip is not None:
                cell = cells[skip['column'] - 1].strip() if skip['column'] - 1 < len(cells) else ''
                if cell in skip['equals']:
                    skipped += 1
                    continue
            data_rows += 1
            if data_rows > max_rows:
                raise ImportFileError(f"The file has more than {max_rows} rows")
            raw = cells[track_index] if track_index < len(cells) else ''
            dn_id = None
            if match is not None:
                try:
                    dn_id = int(match.group(1))
                except (TypeError, ValueError):
                    dn_id = None
            rows.append({'line': line, 'reference': reference or None, 'dn_id': dn_id,
                         'tracking_number': normalize_tracking(raw, settings.get('tracking_normalize')) or None,
                         'raw_tracking': raw.strip() or None})
    except csv.Error as exc:
        raise ImportFileError(f"The file is not a valid CSV file: {exc}") from None
    return {'encoding': encoding, 'rows': rows, 'skipped': skipped}
