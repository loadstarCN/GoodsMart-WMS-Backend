"""承运商文件方式的模板引擎（warehouse/carrier/file_templates.py）与预设（warehouse/carrier/presets）

- 过滤器：digits / truncate / truncate_bytes / slice / date / default / map / upper / zenkaku / nospace / jp_phone / int
- value 模板的校验（未知变量 / 过滤器、括号不配对）与转义；字段内换行换成空格
- 逐列检查：必填、超长（chars / sjis_bytes）、编码不了的字符、JIS 第1・第2水准外的字
- CSV：quoting minimal / all、CRLF、cp932 / utf-8-sig 编码
- 导入解析：编码探测、表头行、按列号取匹配键 / 运单号、跳过的行、一张 DN 多行
- 三份日本预设：校验通过、渲染一张真实的国内 DN 没有问题、列数 95 / 74 / 87
"""
from datetime import date

import pytest

from warehouse.carrier import presets
from warehouse.carrier.file_templates import (
    ImportFileError, TemplateError, compile_value, merge_template, non_jis_x0208_chars, normalize_encoding,
    parse_import, render_rows, render_value, sjis_length, text_of, to_zenkaku, truncate_bytes, validate_template,
    write_csv,
)


def _render(template, context=None):
    return render_value(compile_value(template), context or {})


def _template(columns, **export):
    return validate_template({
        'code': 'test.sample',
        'export': {'columns': columns, **export},
        'import': {'reference_column': 1, 'tracking_column': 2, 'header_rows': 1},
    })


# ---------------------------------------------------------------------------
# 过滤器

@pytest.mark.parametrize('template, context, expected', [
    ('{ship_to.postal_code|digits}', {'ship_to': {'postal_code': '〒１００－０００１'}}, '1000001'),
    ('{dn.order_number|truncate:5}', {'dn': {'order_number': 'SHP-261015'}}, 'SHP-2'),
    ('{goods.summary|truncate_bytes:5}', {'goods': {'summary': 'アイウ'}}, 'アイ'),        # 3 文字目で 6 バイト
    ('{goods.summary|truncate_bytes:5}', {'goods': {'summary': 'abアイ'}}, 'abア'),
    ('{ship_to.address_line1|slice:2:4}', {'ship_to': {'address_line1': 'abcdef'}}, 'cd'),
    ('{ship_to.address_line1|slice::3}', {'ship_to': {'address_line1': 'abcdef'}}, 'abc'),
    ('{ship_to.address_line1|slice:3:}', {'ship_to': {'address_line1': 'abcdef'}}, 'def'),
    ('{ship_date|date:%Y/%m/%d}', {'ship_date': date(2026, 10, 26)}, '2026/10/26'),
    ('{options.delivery_date|date:%Y%m%d}', {'options': {'delivery_date': '2026-11-03'}}, '20261103'),
    ('{options.delivery_date|date:%Y%m%d}', {'options': {}}, ''),                       # 空值 → 空串
    ('{options.invoice_type|default:0}', {'options': {}}, '0'),
    ('{options.invoice_type|default:0}', {'options': {'invoice_type': '5'}}, '5'),
    ('{options.cool_type|map:1=A,2=B}', {'options': {'cool_type': '2'}}, 'B'),
    ('{options.cool_type|map:1=A,2=B}', {'options': {'cool_type': '9'}}, '9'),          # 没映射的原样
    ('{carrier.code|upper}', {'carrier': {'code': 'yamato'}}, 'YAMATO'),
    ('{ship_to.name|zenkaku}', {'ship_to': {'name': 'Yamada 1-2 ｶﾞｷﾞ'}}, 'Ｙａｍａｄａ　１－２　ガギ'),
    ('{ship_to.address_line1|nospace}', {'ship_to': {'address_line1': '千代田 1-1　ビル'}}, '千代田1-1ビル'),
    ('{ship_to.phone|jp_phone}', {'ship_to': {'phone': '+81 90-1234-5678'}}, '090-1234-5678'),
    ('{ship_to.phone|jp_phone}', {'ship_to': {'phone': '＋８１（３）１２３４－５６７８'}}, '03-1234-5678'),
    ('{ship_to.phone|jp_phone}', {'ship_to': {'phone': '045-000-0000'}}, '045-000-0000'),
    ('{options.cod_amount|int}', {'options': {'cod_amount': 1200.4}}, '1200'),
    ('{options.cod_amount|int}', {'options': {'cod_amount': '3,000'}}, '3000'),
    ('{package.total_weight_kg}', {'package': {'total_weight_kg': 6.5}}, '6.5'),
    ('{options.cod_amount}', {'options': {'cod_amount': 3000.0}}, '3000'),
    ('DN{dn.id}', {'dn': {'id': 163}}, 'DN163'),
    ('{{literal}} {dn.id}', {'dn': {'id': 7}}, '{literal} 7'),
    ('{ship_to.latin.name|upper}', {'ship_to': {'latin': {'name': 'Taro Yamada'}}}, 'TARO YAMADA'),
    ('{dn.remark}', {'dn': {'remark': 'line 1\r\nline 2\tend'}}, 'line 1 line 2 end'),  # 字段内换行 → 空格
    ('{ship_to.organization|default:-}', {'ship_to': {}}, '-'),
])
def test_filters(template, context, expected):
    assert _render(template, context) == expected


@pytest.mark.parametrize('template, message', [
    ('{dn.unknown}', 'unknown variable'),
    ('{ship_to.street}', 'unknown variable'),
    ('{nowhere}', 'unknown variable'),
    ('{dn.id|frobnicate}', 'unknown filter'),
    ('{dn.id|truncate}', 'needs an argument'),
    ('{dn.id|truncate:x}', 'integer'),
    ('{dn.id|upper:1}', 'takes no argument'),
    ('{dn.id|slice:1}', 'two arguments'),
    ('{dn.id|map:abc}', 'key=value'),
    ('{dn.id', 'unbalanced'),
    ('dn.id}', 'unbalanced'),
    ('{}', 'empty'),
])
def test_value_template_errors(template, message):
    with pytest.raises(TemplateError) as exc:
        compile_value(template)
    assert message in exc.value.message


def test_text_helpers():
    assert text_of(None) == '' and text_of(True) == '1' and text_of(2.0) == '2' and text_of(date(2026, 1, 2)) == \
        '2026-01-02'
    assert sjis_length('ab漢字') == 6 and sjis_length('ｱ') == 1
    assert truncate_bytes('漢字漢字', 7) == '漢字漢'
    assert to_zenkaku('A1 ~') == 'Ａ１　～'
    assert non_jis_x0208_chars('髙橋①様') == ['髙', '①']
    assert normalize_encoding('Shift_JIS') == 'cp932' and normalize_encoding('SJIS') == 'cp932'
    assert normalize_encoding('UTF-8') == 'utf-8' and normalize_encoding('utf-8-sig') == 'utf-8-sig'
    assert normalize_encoding('latin-1') is None


# ---------------------------------------------------------------------------
# 逐列检查 / CSV

def test_row_checks_required_length_encoding_and_charset():
    template = _template([
        {'header': '必須', 'value': '{ship_to.name}', 'required': True},
        {'header': 'バイト', 'value': '{ship_to.address_line1}', 'max_length': 6},
        {'header': '外字', 'value': '{ship_to.organization}'},
        {'header': '水準', 'value': '{ship_to.address_line2}'},
    ], length_unit='sjis_bytes', charset='jis_x0208')
    context = {'ship_to': {'name': '', 'address_line1': '千代田区一', 'organization': 'Café ✓',
                           'address_line2': '髙島屋'}}
    row = render_rows(template, [context])[0]
    codes = {(p['column'], p['code']) for p in row['problems']}
    assert codes == {('必須', 'REQUIRED'), ('バイト', 'TOO_LONG'), ('外字', 'UNENCODABLE'),
                     ('水準', 'CHARACTER_NOT_ALLOWED')}
    too_long = next(p for p in row['problems'] if p['code'] == 'TOO_LONG')
    assert too_long['length'] == 10 and too_long['max_length'] == 6 and too_long['length_unit'] == 'sjis_bytes'
    assert too_long['column_index'] == 2
    unencodable = next(p for p in row['problems'] if p['code'] == 'UNENCODABLE')
    assert unencodable['characters'] == ['é', '✓']             # cp932 没有带重音的拉丁字母


def test_length_in_chars_and_no_silent_truncation():
    template = _template([{'header': 'Name', 'value': '{ship_to.name}', 'max_length': 4}])
    row = render_rows(template, [{'ship_to': {'name': '山田太郎様'}}])[0]
    assert [p['code'] for p in row['problems']] == ['TOO_LONG']
    assert row['values'] == ['山田太郎様']                      # 不截断，报错让人改


def test_csv_quoting_line_endings_and_encoding():
    columns = [{'header': 'A', 'value': '{dn.id}'}, {'header': '名前', 'value': '{ship_to.name}'}]
    minimal = _template(columns)
    rows = [render_rows(minimal, [{'dn': {'id': 1}, 'ship_to': {'name': '山田, 太郎'}}])[0]['values']]
    data = write_csv(minimal, rows)
    assert data == 'A,名前\r\n1,"山田, 太郎"\r\n'.encode('cp932')
    quoted = _template(columns, quoting='all', header=False, encoding='utf-8-sig', line_ending='\n')
    data = write_csv(quoted, [['1', 'Taro "T"']])
    assert data == '﻿"1","Taro ""T"""\n'.encode('utf-8')


def test_template_validation():
    with pytest.raises(TemplateError) as exc:
        validate_template({'export': {'columns': []}})
    assert exc.value.path == 'export.columns'
    with pytest.raises(TemplateError) as exc:
        validate_template({'export': {'encoding': 'latin-1', 'columns': [{'header': 'A', 'value': ''}]}})
    assert exc.value.path == 'export.encoding'
    with pytest.raises(TemplateError) as exc:
        validate_template({'export': {'columns': [{'header': 'A', 'value': '{dn.nope}'}]}})
    assert exc.value.path == 'export.columns[0].value'
    with pytest.raises(TemplateError) as exc:
        validate_template({'export': {'columns': [{'header': 'A', 'value': ''}]},
                           'import': {'reference_column': 1, 'tracking_column': 2, 'reference_parse': '^X(\\d+)$'}})
    assert exc.value.path == 'import.reference_parse'           # DN{dn.id} 解析不回 DN id
    with pytest.raises(TemplateError) as exc:
        validate_template({'export': {'columns': [{'header': 'A', 'value': ''}]},
                           'service_options': [{'key': 'speed', 'kind': 'select', 'values': []}]})
    assert exc.value.path == 'service_options[0].values'
    template = validate_template({'export': {'encoding': 'shift_jis', 'columns': [{'header': 'A', 'value': 'x'}]},
                                  'service_options': [{'key': 'speed', 'values': [{'value': 1, 'label': 'Fast'}]}]})
    assert template['export']['encoding'] == 'cp932' and template['export']['quoting'] == 'minimal'
    assert template['service_options'][0]['kind'] == 'select'
    assert template['service_options'][0]['values'] == [{'value': '1', 'label': {'en-US': 'Fast'}}]
    assert template['import'] is None and template['verified'] is False


def test_merge_template():
    base = {'export': {'encoding': 'cp932', 'columns': [1, 2]}, 'notes': 'n', 'import': {'header_rows': 1}}
    merged = merge_template(base, {'export': {'quoting': 'all', 'columns': [3]}, 'notes': None})
    assert merged == {'export': {'encoding': 'cp932', 'columns': [3], 'quoting': 'all'}, 'import': {'header_rows': 1}}
    assert base['export']['columns'] == [1, 2]                   # 不改原对象


# ---------------------------------------------------------------------------
# 导入解析

def _import_template(**overrides):
    data = {'reference_column': 2, 'tracking_column': 1, 'header_rows': 1, 'tracking_normalize': 'digits',
            'encoding': 'cp932', 'encoding_fallbacks': ['utf-8'], **overrides}
    return validate_template({'export': {'columns': [{'header': 'A', 'value': 'DN{dn.id}'}]}, 'import': data})


def test_parse_import_rows_and_encodings():
    template = _import_template(skip_rows_when={'column': 3, 'equals': '2'})
    text = ('送り状No.,管理番号,削除区分\r\n'
            '1234-5678-9012,DN12,0\r\n'
            '1234-5678-9013,DN12,0\r\n'
            '9999-0000-0000,DN13,2\r\n'
            ',DN14,0\r\n'
            '5555,ORDER-1,0\r\n'
            '\r\n')
    for data in (text.encode('cp932'), text.encode('utf-8'), b'\xef\xbb\xbf' + text.encode('utf-8')):
        parsed = parse_import(template, data)
        assert parsed['skipped'] == 1
        rows = [(r['line'], r['reference'], r['dn_id'], r['tracking_number']) for r in parsed['rows']]
        assert rows == [(2, 'DN12', 12, '123456789012'), (3, 'DN12', 12, '123456789013'), (5, 'DN14', 14, None),
                        (6, 'ORDER-1', None, '5555')]
    assert parse_import(template, text.encode('utf-8'))['encoding'] == 'utf-8'
    assert parse_import(template, text.encode('cp932'))['encoding'] == 'cp932'


def test_parse_import_header_detection_and_errors():
    template = _import_template()
    # 没勾「1行目に見出し」：第 1 行就是数据（匹配键解析得出 DN）→ 不当表头跳过
    parsed = parse_import(template, b'111122223333,DN7\r\n')
    assert [(r['dn_id'], r['tracking_number']) for r in parsed['rows']] == [(7, '111122223333')]
    with pytest.raises(ImportFileError):
        parse_import(_import_template(encoding_fallbacks=[]), bytes([0x44, 0x4E, 0x31, 0x2C, 0x82, 0x20]))  # 非 cp932
    no_import = validate_template({'export': {'columns': [{'header': 'A', 'value': ''}]}})
    with pytest.raises(ImportFileError):
        parse_import(no_import, b'x')


# ---------------------------------------------------------------------------
# 预设

JP_CONTEXT = {
    'dn': {'id': 163, 'order_number': 'SHP-261015-37210'},
    'ship_date': date(2026, 10, 26),
    'ship_to': {'country': 'JP', 'postal_code': '100-0001', 'admin_area': '東京都', 'locality': '千代田区',
                'address_line1': '千代田1-1', 'address_line2': 'グッズビル101', 'name': '山田 太郎',
                'phone': '+81 90-1234-5678'},
    'shipper': {'country': 'JP', 'postal_code': '220-0012', 'admin_area': '神奈川県', 'locality': '横浜市西区',
                'address_line1': 'みなとみらい2-2-1', 'name': 'グッズマート株式会社', 'organization': 'グッズマート株式会社',
                'phone': '045-000-0000'},
    'goods': {'summary': 'アクリルスタンド / フィギュア', 'quantity': 3, 'count': 2},
    'package': {'count': 2, 'total_weight_kg': 6.5},
    'options': {},
    'carrier': {'name': 'Yamato', 'code': 'yamato', 'account': {'billing_customer_code': '012345678901',
                                                                  'freight_mgmt_no': '01'}},
}


@pytest.mark.parametrize('code, columns, header', [
    ('jp.yamato_b2cloud', 95, True), ('jp.sagawa_ehiden3', 74, True), ('jp.japanpost_yupri_r', 87, False),
])
def test_presets_render_a_domestic_jp_dn(code, columns, header):
    template = presets.preset_template(code)
    assert template is not None and template['code'] == code and template['verified'] is False
    assert template['export']['encoding'] == 'cp932' and template['import']['encoding'] in ('cp932', 'utf-8-sig')
    assert len(template['export']['columns']) == columns
    row = render_rows(template, [JP_CONTEXT])[0]
    assert row['problems'] == []
    assert len(row['values']) == columns
    data = write_csv(template, [row['values']])
    lines = data.decode('cp932').split('\r\n')
    assert lines[-1] == '' and len(lines) == (3 if header else 2)
    assert 'DN163' in lines[-2] and '0901234567' not in lines[-2] and '090-1234-5678' in lines[-2]


def test_preset_details():
    yamato = presets.preset_template('jp.yamato_b2cloud')
    row = render_rows(yamato, [JP_CONTEXT])[0]['values']
    assert row[0] == 'DN163' and row[1] == '0' and row[4] == '2026/10/26'
    assert row[10] == '1000001' and row[11] == '東京都千代田区千代田1-1' and row[15] == '山田 太郎'
    assert row[37] == '2' and row[39] == '012345678901' and row[41] == '01'
    assert yamato['tracking_url_template'] == 'https://member.kms.kuronekoyamato.co.jp/parcel/detail?pno={tracking_number}'
    assert yamato['export']['charset'] == 'jis_x0208' and yamato['import']['tracking_column'] == 4
    # 必填账号项：請求先顧客コード / 運賃管理番号
    assert [f['key'] for f in yamato['account_fields'] if f['required']] == ['billing_customer_code',
                                                                             'freight_mgmt_no']
    missing = render_rows(yamato, [dict(JP_CONTEXT, carrier={'account': {}})])[0]['problems']
    assert {p['column'] for p in missing} == {'請求先顧客コード', '運賃管理番号'}

    sagawa = presets.preset_template('jp.sagawa_ehiden3')
    row = render_rows(sagawa, [JP_CONTEXT])[0]['values']
    assert row[4] == '東京都千代田区' and row[5] == '千代田１－１' and row[7] == '山田　太郎' and row[9] == 'DN163'
    assert sagawa['tracking_url_fallback'] == 'https://e-customer.sagawa-exp.co.jp/k2k'
    assert sagawa['import']['skip_rows_when'] == {'column': 84, 'header': '削除区分', 'equals': ['2']}
    assert sagawa['import']['encoding'] == 'utf-8-sig' and sagawa['import']['encoding_fallbacks'] == ['cp932']

    japanpost = presets.preset_template('jp.japanpost_yupri_r')
    assert japanpost['export']['quoting'] == 'all' and japanpost['export']['header'] is False
    assert japanpost['tracking_url_template'] == \
        'https://trackings.post.japanpost.jp/services/srv/search/direct?reqCodeNo1={tracking_number}'
    data = write_csv(japanpost, [render_rows(japanpost, [JP_CONTEXT])[0]['values']]).decode('cp932')
    assert data.startswith('"DN163","","","20261026"')
    slots = {v['value'] for o in japanpost['service_options'] if o['key'] == 'delivery_time_slot' for v in o['values']}
    assert '56' not in slots and '57' in slots

    described = {p['code']: p for p in presets.describe()}
    assert set(described) >= {'jp.yamato_b2cloud', 'jp.sagawa_ehiden3', 'jp.japanpost_yupri_r'}
    assert described['jp.sagawa_ehiden3']['verified'] is False
    assert len(described['jp.japanpost_yupri_r']['columns']) == 87
    assert described['jp.yamato_b2cloud']['columns'][0] == {'header': 'お客様管理番号', 'required': False,
                                                           'max_length': 50}


def test_presets_flag_long_addresses_instead_of_truncating():
    sagawa = presets.preset_template('jp.sagawa_ehiden3')
    context = dict(JP_CONTEXT, ship_to=dict(JP_CONTEXT['ship_to'], address_line1='大字下吉田字上ノ原１２３４番地の５６７'))
    problems = render_rows(sagawa, [context])[0]['problems']
    assert [(p['column'], p['code']) for p in problems] == [('お届け先住所２', 'TOO_LONG')]
    yamato = presets.preset_template('jp.yamato_b2cloud')
    context = dict(JP_CONTEXT, ship_to=dict(JP_CONTEXT['ship_to'], name='髙橋 一郎'))
    problems = render_rows(yamato, [context])[0]['problems']
    assert [(p['column'], p['code']) for p in problems] == [('お届け先名', 'CHARACTER_NOT_ALLOWED')]
