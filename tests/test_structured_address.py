"""结构化国际地址（doc/shipping-flow.md §6.2）与国家规则包（§6.3）

- 收件人：结构化字段的增改查，postal_code ≡ zip_code，只给结构化字段时按国家格式生成单行 address（日本 / 美国），
  null 清空；批发站发的形状（空值一律 JSON null、没有 dependent_locality / email、latin: null）
- 仓库：结构化字段、address 生成、latin → address_en（为空时）
- DN：ship_to（给了就校验存快照、没给从收件人拷贝、只给 ship_to 时生成 shipping_address）、shipping_options
  （已声明键按类型校验、没声明的照收），详情输出；收件国按 ship_to；国内件交给插件的收件人按 ship_to
- 地址排版 format_lines / format_single_line（JP local / latin、US、默认）
- 国家规则注册表：JP 规则、没登记的用默认、第三方登记；JP 报关检查行为与文案不变（另见 test_customs）；
  Corporate No. 作为日本发货人税号的默认标签，以前签发的（印 Tax ID）不算过期
"""
from .helpers import *
from .test_customs import _customs, _export_dn_ready, _h, _issue, _prepare

from datetime import date

from warehouse.common.address_format import format_lines, format_single_line, parse_address
from warehouse.country_rules import CountryRules, DEFAULT_RULES, register, registry, rules_for
from warehouse.country_rules.jp import JapanRules, jp_export_code_problem
from warehouse.shipping.route import shipping_route

JP_ADDRESS = {'country': 'JP', 'postal_code': '100-0001', 'admin_area': '東京都', 'locality': '千代田区',
              'address_line1': '千代田1-1', 'address_line2': 'グッズビル101', 'organization': 'グッズ商事',
              'name': '山田 太郎', 'phone': '090-1234-5678',
              'latin': {'name': 'Taro Yamada', 'address_line1': '1-1 Chiyoda', 'address_line2': 'Goods Bldg 101',
                        'locality': 'Chiyoda-ku', 'admin_area': 'Tokyo', 'organization': 'Goods Shoji'}}
US_ADDRESS = {'country': 'US', 'postal_code': '94043', 'admin_area': 'CA', 'locality': 'Mountain View',
              'address_line1': '1600 Amphitheatre Pkwy', 'name': 'Jane Doe', 'organization': 'Example Inc.'}


# ---------------------------------------------------------------------------
# 排版 / 国家规则

def test_format_lines_by_country():
    assert format_lines(JP_ADDRESS) == ['〒100-0001', '東京都千代田区', '千代田1-1 グッズビル101', 'グッズ商事', '山田 太郎']
    assert format_single_line(JP_ADDRESS) == '〒100-0001 東京都千代田区 千代田1-1 グッズビル101'
    assert format_lines(JP_ADDRESS, 'latin') == ['Taro Yamada', 'Goods Shoji', '1-1 Chiyoda', 'Goods Bldg 101',
                                                 'Chiyoda-ku, Tokyo 100-0001', 'Japan']
    assert format_lines(US_ADDRESS) == ['Jane Doe', 'Example Inc.', '1600 Amphitheatre Pkwy',
                                        'Mountain View, CA 94043']
    assert format_single_line(US_ADDRESS, 'latin') == '1600 Amphitheatre Pkwy, Mountain View, CA 94043, United States'
    # 没登记的国家用默认格式：城市 州 邮编；空字段的整行不要、多余的逗号去掉
    de = {'country': 'DE', 'postal_code': '10115', 'locality': 'Berlin', 'address_line1': 'Hauptstrasse 1'}
    assert format_lines(de, with_country=True) == ['Hauptstrasse 1', 'Berlin 10115', 'Germany']
    assert format_lines({'country': 'US', 'locality': 'Springfield', 'postal_code': '62701'}) == ['Springfield 62701']
    assert format_lines(None) == []


def test_country_rules_registry():
    assert isinstance(rules_for('jp'), JapanRules) and rules_for('JP').code == 'JP'
    assert rules_for('ZZ') is DEFAULT_RULES and rules_for(None) is DEFAULT_RULES
    assert rules_for('JP').exporter_tax_label == 'Corporate No.' and DEFAULT_RULES.exporter_tax_label == 'Tax ID'
    assert rules_for('JP').phone_normalize('+81 3-1234-5678') == '03-1234-5678'
    assert DEFAULT_RULES.phone_normalize('  +1  650 ') == '+1 650'
    # 日本的报关检查（从 customs_services 搬过来，文案不变）
    problems = rules_for('JP').customs_line_problems({'goods_code': 'G1', 'jp_export_code': '12345', 'hs_code': '950300'})
    assert problems == [{'code': 'JP_EXPORT_CODE_MISMATCH', 'level': 'warning',
                         'message': 'Japanese export statistics code of goods G1 is not a 9-digit number',
                         'field': 'jp_export_code'}]
    assert jp_export_code_problem('950300000', '9503.00') is None
    assert DEFAULT_RULES.customs_line_problems({'goods_code': 'G1', 'jp_export_code': '12345'}) == []

    class KoreaRules(CountryRules):
        code = 'KR'
        exporter_tax_label = 'Business Registration No.'

    try:
        register(KoreaRules)
        assert rules_for('kr').exporter_tax_label == 'Business Registration No.'
    finally:
        registry().pop('KR', None)
    with pytest.raises(ValueError):
        register(object())


def test_jp_checks_only_for_japanese_exports(client, access_token):
    customs = _customs()
    customs['lines'][1]['jp_export_code'] = '3926.90.000'
    dn_id, _task = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert 'JP_EXPORT_CODE_MISMATCH' in {p['code'] for p in view['problems']}
    with client.application.app_context():
        get_warehouse().country_code = 'DE'
        get_company().country_code = 'DE'
        db.session.commit()
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert not {p['code'] for p in view['problems']} & {'JP_EXPORT_CODE_MISMATCH', 'JP_EXPORT_VALUE_UNVERIFIED'}


def test_corporate_number_label_and_legacy_documents(client, access_token):
    from warehouse.dn.customs_services import CustomsService, _data_hash
    dn_id, _task = _export_dn_ready(client, access_token)
    with client.application.app_context():
        get_company().tax_id_label = None
        db.session.commit()
    assert _issue(client, access_token, dn_id).status_code == 201
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        data = CustomsService._document_data(dn, CustomsService.build_view(dn))
        assert data['exporter']['tax'] == 'Corporate No.: 1234567890123'
        # 以前（默认标签是 Tax ID 时）签发的单证：内容相同就不算过期
        legacy = dict(data, exporter=dict(data['exporter'], tax='Tax ID: 1234567890123'))
        assert _data_hash(legacy) in CustomsService._content_hashes(dn, data)
        for doc in dn.customs_documents:
            doc.data_sha256 = _data_hash(legacy)
        db.session.commit()
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['documents_outdated'] is False


# ---------------------------------------------------------------------------
# 收件人 / 仓库

def _post_recipient(client, token, body):
    return client.post('/recipient/', json={'company_id': 1, **body}, headers=_h(token))


def test_recipient_structured_fields(client, access_token):
    response = _post_recipient(client, access_token, {
        'name': '山田商店', 'contact': '山田 太郎', 'country': 'jp', 'postal_code': '100-0001',
        'admin_area': '東京都', 'locality': '千代田区', 'address_line1': '千代田1-1', 'address_line2': 'グッズビル101',
        'organization': 'グッズ商事', 'latin': {'name': 'Taro Yamada', 'address_line1': '1-1 Chiyoda', 'x': 'dropped'},
    })
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['zip_code'] == data['postal_code'] == '100-0001'
    assert data['address'] == '〒100-0001 東京都千代田区 千代田1-1 グッズビル101'
    assert data['admin_area'] == '東京都' and data['organization'] == 'グッズ商事'
    assert data['latin'] == {'name': 'Taro Yamada', 'address_line1': '1-1 Chiyoda'}
    recipient_id = data['id']

    detail = client.get(f'/recipient/{recipient_id}', headers=_h(access_token)).get_json()
    assert detail['locality'] == '千代田区' and detail['dependent_locality'] is None

    # 改了结构化字段、没给 address → 重新生成；null 清空
    response = client.put(f'/recipient/{recipient_id}', json={'address_line2': None, 'zip_code': '100-0002'},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['address_line2'] is None and data['postal_code'] == '100-0002'
    assert data['address'] == '〒100-0002 東京都千代田区 千代田1-1'
    # 给了 address 就用给的
    data = client.put(f'/recipient/{recipient_id}', json={'locality': '中央区', 'address': '手書きの住所'},
                      headers=_h(access_token)).get_json()
    assert data['address'] == '手書きの住所' and data['locality'] == '中央区'

    for body, code, field in (({'zip_code': '1', 'postal_code': '2'}, 14019, 'postal_code'),
                              ({'admin_area': 'x' * 101}, 14019, 'admin_area'),
                              ({'latin': 'Tokyo'}, 14019, 'latin'),
                              ({'locality': ['x']}, 14019, 'locality')):
        response = client.put(f'/recipient/{recipient_id}', json=body, headers=_h(access_token))
        assert response.status_code == 400 and response.get_json()['code'] == code, body
        assert response.get_json()['field'] == field


def test_recipient_us_address(client, access_token):
    response = _post_recipient(client, access_token, {
        'name': 'Example Inc.', 'contact': 'Jane Doe', 'country': 'US', 'zip_code': '94043', 'admin_area': 'CA',
        'locality': 'Mountain View', 'address_line1': '1600 Amphitheatre Pkwy'})
    assert response.status_code == 201
    assert response.get_json()['address'] == '1600 Amphitheatre Pkwy, Mountain View, CA 94043'


WHOLESALE_RECIPIENT = {
    'name': '卸 太郎', 'address': '〒530-0001 大阪府大阪市北区梅田1-1-1', 'zip_code': '530-0001', 'phone': '06-0000-0000',
    'email': 'buyer@example.com', 'contact': '卸 太郎', 'country': 'JP', 'is_active': True,
    'external_reference': 'ws-address-77', 'postal_code': '530-0001', 'admin_area': '大阪府', 'locality': '大阪市北区',
    'address_line1': '梅田1-1-1', 'address_line2': None, 'organization': None, 'latin': None,
}


def test_wholesale_payload_shape(client, access_token):
    """批发站发的形状：结构化字段的空值一律是 JSON null；没有 dependent_locality / email；shipping_options 不带"""
    _prepare(client)
    response = _post_recipient(client, access_token, WHOLESALE_RECIPIENT)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['address'] == WHOLESALE_RECIPIENT['address']          # 给了单行地址就用给的
    assert data['address_line2'] is None and data['organization'] is None and data['latin'] is None
    recipient_id = data['id']

    # 更新：null 清空已存的值
    update = dict(WHOLESALE_RECIPIENT, address_line2='梅田ビル5F', organization='卸商事', latin={'name': 'Taro Oroshi'})
    assert client.put(f'/recipient/{recipient_id}', json=update, headers=_h(access_token)).status_code == 200
    data = client.put(f'/recipient/{recipient_id}', json=WHOLESALE_RECIPIENT, headers=_h(access_token)).get_json()
    assert data['address_line2'] is None and data['organization'] is None and data['latin'] is None

    with client.application.app_context():
        warehouse_id = get_warehouse().id
    ship_to = {'country': 'JP', 'postal_code': '530-0001', 'admin_area': '大阪府', 'locality': '大阪市北区',
               'address_line1': '梅田1-1-1', 'address_line2': None, 'organization': None, 'name': '卸 太郎',
               'phone': '06-0000-0000', 'latin': None}
    response = client.post('/dn/', json={
        'recipient_id': recipient_id, 'warehouse_id': warehouse_id, 'shipping_address': WHOLESALE_RECIPIENT['address'],
        'expected_shipping_date': warehouse_today().isoformat(), 'order_number': 'WS-77', 'ship_to': ship_to,
        'details': [{'goods_code': 'G001', 'quantity': 1}]}, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    dn_id = response.get_json()['id']
    detail = client.get(f'/dn/{dn_id}', headers=_h(access_token)).get_json()
    assert detail['ship_to'] == {'country': 'JP', 'postal_code': '530-0001', 'admin_area': '大阪府',
                                 'locality': '大阪市北区', 'address_line1': '梅田1-1-1', 'name': '卸 太郎',
                                 'phone': '06-0000-0000'}
    assert detail['shipping_options'] is None and detail['shipping_address'] == WHOLESALE_RECIPIENT['address']
    assert detail['ship_to_effective']['email'] == 'buyer@example.com'      # 没带的联系方式用收件人的


def test_warehouse_structured_fields(client, access_token):
    with client.application.app_context():
        company_id = get_company().id
    response = client.post('/warehouse/', json={
        'name': 'Yokohama DC', 'company_id': company_id, 'country_code': 'JP', 'postal_code': '220-0012',
        'admin_area': '神奈川県', 'locality': '横浜市西区', 'address_line1': 'みなとみらい2-2-1',
        'latin': {'address_line1': '2-2-1 Minatomirai', 'locality': 'Nishi-ku, Yokohama', 'admin_area': 'Kanagawa'},
    }, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['zip_code'] == data['postal_code'] == '220-0012'
    assert data['address'] == '〒220-0012 神奈川県横浜市西区 みなとみらい2-2-1'
    assert data['address_en'] == '2-2-1 Minatomirai, Nishi-ku, Yokohama, Kanagawa 220-0012, Japan'
    warehouse_id = data['id']
    data = client.put(f'/warehouse/{warehouse_id}', json={'address_line2': 'ランドマーク10F', 'latin': None},
                      headers=_h(access_token)).get_json()
    assert data['address'] == '〒220-0012 神奈川県横浜市西区 みなとみらい2-2-1 ランドマーク10F'
    assert data['latin'] is None and data['address_en'].startswith('2-2-1 Minatomirai')   # address_en 不跟着清


# ---------------------------------------------------------------------------
# DN 的 ship_to / shipping_options

def _dn_body(recipient_id, warehouse_id, **extra):
    return {'recipient_id': recipient_id, 'warehouse_id': warehouse_id, 'expected_shipping_date': warehouse_today().isoformat(),
            'details': [{'goods_code': 'G001', 'quantity': 1}], **extra}


def test_dn_ship_to_copy_generate_and_validate(client, access_token):
    _prepare(client)
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        legacy_recipient_id = get_recipient().id
        get_recipient().country = 'JP'
        db.session.commit()
    structured = _post_recipient(client, access_token, {
        'name': 'Example Inc.', 'contact': 'Jane Doe', 'country': 'US', 'zip_code': '94043', 'admin_area': 'CA',
        'locality': 'Mountain View', 'address_line1': '1600 Amphitheatre Pkwy', 'phone': '+1 650 000 0000'}).get_json()

    # 只给 ship_to：生成单行 shipping_address；国家缺省用收件人的
    response = client.post('/dn/', json=_dn_body(legacy_recipient_id, warehouse_id, ship_to={
        k: v for k, v in JP_ADDRESS.items() if k != 'country'}), headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['shipping_address'] == '〒100-0001 東京都千代田区 千代田1-1 グッズビル101'
    assert data['ship_to']['country'] == 'JP' and data['ship_to']['latin']['name'] == 'Taro Yamada'

    # 没给 ship_to：从收件人的结构化字段拷贝，shipping_address 也能省
    response = client.post('/dn/', json=_dn_body(structured['id'], warehouse_id), headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['ship_to'] == {'country': 'US', 'postal_code': '94043', 'admin_area': 'CA',
                               'locality': 'Mountain View', 'address_line1': '1600 Amphitheatre Pkwy',
                               'organization': 'Example Inc.', 'name': 'Jane Doe', 'phone': '+1 650 000 0000'}
    assert data['shipping_address'] == '1600 Amphitheatre Pkwy, Mountain View, CA 94043'
    with client.application.app_context():
        dn = db.session.get(DN, data['id'])
        dn.ship_to = dict(dn.ship_to, country='DE')
        assert shipping_route(dn)['destination_country'] == 'DE'       # 收件国按 ship_to

    # 旧收件人（没有结构化字段）：ship_to 为空，shipping_address 必填
    response = client.post('/dn/', json=_dn_body(legacy_recipient_id, warehouse_id, shipping_address='x'),
                           headers=_h(access_token))
    assert response.status_code == 201 and response.get_json()['ship_to'] is None
    response = client.post('/dn/', json=_dn_body(legacy_recipient_id, warehouse_id), headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 40000

    for ship_to, code, field in (({'country': 'XX', 'address_line1': 'a'}, 14020, 'ship_to.country'),
                                 ({'address_line1': 'a' * 256}, 14019, 'ship_to.address_line1'),
                                 ('Tokyo', 14019, 'ship_to'),
                                 ({'address_line1': 'a', 'latin': {'name': 1.5}}, 14019, 'ship_to.latin.name')):
        response = client.post('/dn/', json=_dn_body(legacy_recipient_id, warehouse_id, shipping_address='x',
                                                     ship_to=ship_to), headers=_h(access_token))
        assert response.status_code == 400, ship_to
        assert response.get_json()['code'] == code and response.get_json()['field'] == field


def test_dn_shipping_options_on_create(client, access_token):
    _prepare(client)
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        recipient_id = get_recipient().id
        carrier = get_carrier()
        carrier.waybill_method, carrier.file_preset = 'file', 'jp.sagawa_ehiden3'
        db.session.commit()
        carrier_id = carrier.id
    body = _dn_body(recipient_id, warehouse_id, shipping_address='x', carrier_id=carrier_id,
                    shipping_options={'speed': '003', 'delivery_date': '2026-11-03', 'source_note': 'keep me'})
    response = client.post('/dn/', json=body, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['shipping_options'] == {'speed': '003', 'delivery_date': '2026-11-03',
                                                       'source_note': 'keep me'}
    body['shipping_options'] = {'speed': '999'}
    response = client.post('/dn/', json=body, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 16139
    assert response.get_json()['field'] == 'shipping_options.speed'
    body['shipping_options'] = ['speed']
    assert client.post('/dn/', json=body, headers=_h(access_token)).get_json()['code'] == 16139


def test_parse_address_aliases():
    assert parse_address({'zip_code': '100-0001', 'name': ' A ', 'email': None}) == {'postal_code': '100-0001',
                                                                                    'name': 'A'}
    assert parse_address({'latin': None, 'organization': None}) is None
    with pytest.raises(Exception):
        parse_address({'zip_code': '1', 'postal_code': '2'})
