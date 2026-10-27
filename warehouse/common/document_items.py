"""Read-only item previews shared by ASN/DN forms. Never creates goods or reserves stock."""
import csv
import io
import re

from flask import request, Response
from sqlalchemy.orm import lazyload

from extensions.error import BadRequestException
from warehouse.goods import tabular
from warehouse.goods.models import Goods
from warehouse.inventory.availability import availability

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_CODES = 1000
MAX_QUANTITY = 2147483647


def invalid(reason):
    raise BadRequestException('Cannot read item list', 15019, 'items', details={'reason': reason})


def read_rows():
    if request.content_length and request.content_length > MAX_BYTES + 65536:
        invalid('TOO_LARGE')
    if request.is_json:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('text'), str):
            invalid('EMPTY')
        text = body['text'].lstrip('\ufeff')
        if len(text.encode('utf-8')) > MAX_BYTES:
            invalid('TOO_LARGE')
        delimiter = '\t' if '\t' in text.split('\n', 1)[0] else ','
        rows = []
        try:
            for line, cells in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True), 1):
                if any(cell.strip() for cell in cells):
                    rows.append((line, cells))
                if len(rows) > MAX_ROWS + 1:
                    invalid('TOO_MANY_ROWS')
        except csv.Error:
            invalid('NOT_CSV')
        header = body.get('has_header', False)
        if not isinstance(header, bool):
            invalid('HEADER_INVALID')
        metadata = {}
    else:
        uploaded = request.files.get('file')
        if uploaded is None:
            invalid('EMPTY')
        fmt = tabular.file_format(uploaded.filename)
        if fmt is None:
            invalid('FORMAT_INVALID')
        content = uploaded.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            invalid('TOO_LARGE')
        encoding = tabular.normalize_encoding(request.form.get('encoding'))
        if encoding is None:
            invalid('UNKNOWN_ENCODING')
        try:
            table = tabular.read_table(content, fmt, encoding=encoding, sheet=request.form.get('sheet'), max_rows=MAX_ROWS)
        except tabular.TableError as exc:
            invalid(exc.reason)
        rows = table.rows
        header = request.form.get('has_header', 'true').lower() == 'true'
        metadata = {'sheet': table.sheet, 'sheets': table.sheets, 'encoding': table.encoding}
    if header:
        rows = rows[1:]
    if not rows:
        invalid('EMPTY')
    if len(rows) > MAX_ROWS:
        invalid('TOO_MANY_ROWS')
    return rows, metadata


def preview(warehouse, *, outbound):
    rows, metadata = read_rows()
    grouped, errors = {}, []
    for line, cells in rows:
        code = cells[0].strip() if cells else ''
        quantity = (cells[1].strip() if len(cells) > 1 else '') or '1'
        remark = cells[2].strip() if len(cells) > 2 else ''
        reason = None
        if not code or len(code) > 50:
            reason = 'CODE_INVALID'
        elif not re.fullmatch(r'[0-9]+', quantity) or len(quantity) > 10 or not 0 < int(quantity) <= MAX_QUANTITY:
            reason = 'QUANTITY_INVALID'
        elif any(cell.strip() for cell in cells[3:]):
            reason = 'EXTRA_COLUMNS'
        if reason:
            errors.append({'lines': [line], 'code': code, 'quantity': quantity, 'remark': remark, 'error': reason})
            continue
        item = grouped.setdefault(code, {'lines': [], 'code': code, 'quantity': 0, 'remarks': []})
        item['lines'].append(line)
        item['quantity'] += int(quantity)
        if remark and remark not in item['remarks']:
            item['remarks'].append(remark)
    if len(grouped) > MAX_CODES:
        invalid('TOO_MANY_CODES')
    if outbound:
        goods = {item['code']: item for item in availability(warehouse, list(grouped))['items']}
    else:
        goods = {}
        codes = list(grouped)
        for start in range(0, len(codes), 500):
            for item in Goods.query.options(lazyload('*')).filter(
                Goods.company_id == warehouse.company_id, Goods.code.in_(codes[start:start + 500])
            ).all():
                goods[item.code] = {'goods_id': item.id, 'code': item.code, 'name': item.name,
                                    'unit': item.unit, 'thumbnail_url': item.thumbnail_url, 'is_active': bool(item.is_active)}
    result = list(errors)
    for code, item in grouped.items():
        match = goods.get(code)
        error = 'NOT_FOUND' if match is None else ('INACTIVE' if not match['is_active'] else None)
        if item['quantity'] > MAX_QUANTITY:
            error = 'QUANTITY_INVALID'
        result.append({**item, 'remarks': None, 'remark': '；'.join(item['remarks']), 'goods': match, 'error': error})
    result.sort(key=lambda item: item['lines'][0])
    return {**metadata, 'warehouse_id': warehouse.id, 'items': result,
            'valid_count': sum(not item['error'] for item in result), 'error_count': sum(bool(item['error']) for item in result)}


def template():
    lang = request.args.get('lang', 'en-US')
    headers = {'zh-CN': ['商品编码', '数量', '备注'], 'ja-JP': ['商品コード', '数量', '備考']}.get(lang, ['Goods code', 'Quantity', 'Remark'])
    fmt = request.args.get('format', 'csv')
    if fmt not in ('csv', 'xlsx'):
        invalid('FORMAT_INVALID')
    content = (tabular.write_xlsx('Items', headers, [['0012345678901', 1, '']], ['text', None, 'text'])
               if fmt == 'xlsx' else tabular.write_csv(headers, [['0012345678901', 1, '']], 'utf-8-sig'))
    return Response(content, mimetype=tabular.content_type(fmt), headers={
        'Content-Disposition': f'attachment; filename="items-template.{fmt}"', 'Cache-Control': 'no-store'})
