"""文件方式（doc/shipping-flow.md §6.1）、运单服务选项（§6.4）与流程汇总里的文件操作 / awaiting_number

- GET /carrier/file-presets；承运商的 file_preset / file_template / file_account 校验（400 16135），同一请求里设预设并选 file
- 导出：逐行问题 409 16137（不导出、不建记录）；成功 → cp932 CSV（文件名、列数、DN{id}）、file 运单 exported、
  导出批次；重新导出（记录不变、换批次）；作废导出（cancelled discarded）
- 导入：dry run 预览（matched / not_found / already_has_number / same_number / invalid），提交后 exported → active、
  多件 = 多个运单号、同步到发货任务；没导出过的 DN 直接建 file 运单；再导一次 same_number；号码不同 already_has_number
- 运单服务选项：按预设声明校验（400 16139）、导出时写进文件、导出后锁定（409 16076 SHIPPING_OPTIONS_LOCKED）、
  service_name 显示服务名称
- 流程：运单步骤 export_file / import_numbers / discard_export，阶段 awaiting_number（SQL 同口径）；查询页用预设的
- 交承运商后还挂着的「已约」揽收显示为已完成
"""
import csv
import io

from .helpers import *
from .test_customs import PACKAGES, _create_dn, _force_packed, _h, _prepare, _ship
from .test_shipping_flow import _actions, _assert_stage, _filtered, _flow, _steps

from warehouse.carrier.models import Carrier
from warehouse.dn.models import CarrierFileBatch, DNCarrierShipment

ACCOUNT = {'billing_customer_code': '012345678901', 'freight_mgmt_no': '01'}


def _jp_master_data(client):
    """仓库、收件人用日本的结构化地址，承运商设成ヤマト B2クラウド的文件方式"""
    _prepare(client)
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.country_code = 'JP'
        warehouse.zip_code = '220-0012'
        warehouse.admin_area, warehouse.locality, warehouse.address_line1 = '神奈川県', '横浜市西区', 'みなとみらい2-2-1'
        warehouse.phone = '045-000-0000'
        recipient = get_recipient()
        recipient.country, recipient.zip_code = 'JP', '100-0001'
        recipient.admin_area, recipient.locality, recipient.address_line1 = '東京都', '千代田区', '千代田1-1'
        recipient.address_line2 = 'グッズビル101'
        recipient.contact, recipient.phone = '山田 太郎', '+81 90-1234-5678'
        carrier = get_carrier()
        carrier.code = 'yamato'
        carrier.waybill_method = 'file'
        carrier.file_preset = 'jp.yamato_b2cloud'
        carrier.file_account = dict(ACCOUNT)
        db.session.commit()
        return carrier.id


def _packed_dn(client, token, order_number, carrier_id=None):
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    assert client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token)).status_code == 200
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).carrier_id = carrier_id or get_carrier().id
        db.session.commit()
    return dn_id, task_id


def _export(client, token, carrier_id, dn_ids, **extra):
    return client.post('/dn/carrier-files/export', json={'carrier_id': carrier_id, 'dn_ids': dn_ids, **extra},
                       headers=_h(token))


def _csv_rows(response):
    return list(csv.reader(io.StringIO(response.data.decode('cp932'), newline='')))


def _import(client, token, carrier_id, rows, dry_run=True, encoding='cp932', header=True):
    lines = (['お客様管理番号,送り状種類,クール区分,伝票番号'] if header else []) + [','.join(r) for r in rows]
    data = ('\r\n'.join(lines) + '\r\n').encode(encoding)
    return client.post('/dn/carrier-files/import', data={
        'file': (io.BytesIO(data), 'b2_issued.csv'), 'carrier_id': str(carrier_id),
        'dry_run': 'true' if dry_run else 'false'}, headers=_h(token), content_type='multipart/form-data')


def _records(client, dn_id):
    with client.application.app_context():
        return [{'id': r.id, 'source': r.source, 'status': r.status, 'reason': r.reason, 'carrier_id': r.carrier_id,
                 'tracking_number': r.tracking_number, 'package_tracking_numbers': r.package_tracking_numbers,
                 'file_batch_id': r.file_batch_id}
                for r in DNCarrierShipment.query.filter_by(dn_id=dn_id).order_by(DNCarrierShipment.id)]


# ---------------------------------------------------------------------------
# 预设与承运商设置

def test_file_presets_endpoint(client, access_token):
    response = client.get('/carrier/file-presets', headers=_h(access_token))
    assert response.status_code == 200
    presets = {p['code']: p for p in response.get_json()}
    assert set(presets) == {'jp.yamato_b2cloud', 'jp.sagawa_ehiden3', 'jp.japanpost_yupri_r'}
    yamato = presets['jp.yamato_b2cloud']
    assert set(yamato) >= {'code', 'name', 'country', 'version', 'notes', 'verified', 'tracking_url_template',
                           'service_options', 'account_fields', 'columns'}
    assert yamato['verified'] is False and yamato['country'] == 'JP' and len(yamato['columns']) == 95
    assert set(yamato['name']) == {'en-US', 'zh-CN', 'ja-JP'}
    assert yamato['export']['encoding'] == 'cp932'
    assert {o['key'] for o in yamato['service_options']} >= {'invoice_type', 'delivery_date', 'cod_amount'}


def test_carrier_file_settings_validation(client, access_token):
    with client.application.app_context():
        carrier_id = get_carrier().id
    url = f'/carrier/{carrier_id}'

    response = client.put(url, json={'waybill_method': 'file'}, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 16135
    assert response.get_json()['field'] == 'waybill_method'

    response = client.put(url, json={'file_preset': 'jp.nowhere'}, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'file_preset'

    response = client.put(url, json={'file_preset': 'jp.yamato_b2cloud',
                                     'file_template': {'export': {'columns': [{'header': 'A', 'value': '{dn.x}'}]}}},
                          headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'file_template.export.columns[0].value'

    response = client.put(url, json={'file_preset': 'jp.yamato_b2cloud', 'file_account': {'nope': '1'}},
                          headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'file_account.nope'

    # 同一个请求里设预设并选 file
    response = client.put(url, json={'file_preset': 'jp.yamato_b2cloud', 'waybill_method': 'file',
                                     'file_account': {'billing_customer_code': '012345678901'}},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['waybill_method'] == 'file' and data['file_preset'] == 'jp.yamato_b2cloud'
    assert data['file_template'] is None and data['file_account'] == {'billing_customer_code': '012345678901'}
    assert data['available_methods']['waybill'] == ['file', 'manual']
    mode = data['file_mode']
    assert mode['configured'] is True and mode['ready'] is False and mode['missing_account_fields'] == \
        ['freight_mgmt_no']
    assert mode['preset']['code'] == 'jp.yamato_b2cloud' and mode['preset']['verified'] is False
    assert mode['can_import'] is True and mode['encoding'] == 'cp932'

    # 改动合并到预设上（只改编码），GET 也带着
    response = client.put(url, json={'file_template': {'export': {'encoding': 'utf-8-sig'}}},
                          headers=_h(access_token))
    assert response.status_code == 400                         # JIS 第1・第2水准检查要 cp932
    assert response.get_json()['field'] == 'file_template.export.charset'
    change = {'export': {'encoding': 'utf-8-sig', 'charset': None}}
    response = client.put(url, json={'file_template': change, 'file_account': {**ACCOUNT}}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = client.get(url, headers=_h(access_token)).get_json()
    assert data['file_template'] == change and data['file_mode']['ready'] is True
    assert data['file_mode']['encoding'] == 'utf-8-sig'

    # 方式还是 file 时不能把预设去掉
    response = client.put(url, json={'file_preset': None, 'file_template': None}, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'file_preset'
    assert client.put(url, json={'waybill_method': 'manual', 'file_preset': None, 'file_template': None},
                      headers=_h(access_token)).status_code == 200
    data = client.get(url, headers=_h(access_token)).get_json()
    assert data['file_account'] is None and data['available_methods']['waybill'] == ['manual']


# ---------------------------------------------------------------------------
# 导出 → 导入

def test_export_import_flow(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, task_id = _packed_dn(client, access_token, 'SHP-FILE-1')

    flow = _assert_stage(client, access_token, dn_id, 'awaiting_waybill')
    waybill = _steps(flow)['waybill']
    actions = _actions(waybill)
    assert actions['export_file']['enabled'] is True and actions['export_file']['carrier_id'] == carrier_id
    assert actions['import_numbers']['enabled'] is True and 'discard_export' not in actions
    assert 'create_waybill' not in actions and actions['enter_tracking']['enabled'] is True
    assert waybill['summary']['options']['locked'] is False
    assert {o['key'] for o in waybill['summary']['options']['schema']} >= {'invoice_type', 'cod_amount'}

    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 200, response.data[:500]
    assert response.headers['Content-Type'] == 'text/csv; charset=Shift_JIS'
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment; filename="yamato_') and disposition.endswith('.csv"')
    batch_id = int(response.headers['X-Batch-Id'])
    rows = _csv_rows(response)
    assert len(rows) == 2 and len(rows[0]) == 95 and rows[0][0] == 'お客様管理番号'
    row = rows[1]
    assert row[0] == f'DN{dn_id}' and row[8] == '090-1234-5678' and row[10] == '1000001'
    assert row[11] == '東京都千代田区千代田1-1' and row[12] == 'グッズビル101' and row[15] == '山田 太郎'
    assert row[19] == '045-000-0000' and row[22] == '神奈川県横浜市西区みなとみらい2-2-1'
    assert row[24] == 'Company A' and row[32] == 'SHP-FILE-1' and row[37] == '2' and row[39] == '012345678901'
    records = _records(client, dn_id)
    assert records == [{'id': records[0]['id'], 'source': 'file', 'status': 'exported', 'reason': None,
                        'carrier_id': carrier_id, 'tracking_number': None, 'package_tracking_numbers': None,
                        'file_batch_id': batch_id}]

    flow = _assert_stage(client, access_token, dn_id, 'awaiting_number')
    waybill = _steps(flow)['waybill']
    assert waybill['state'] == 'current' and waybill['source'] == 'warehouse'
    assert waybill['summary']['source'] == 'file'
    export = waybill['summary']['export']
    assert export['batch_id'] == batch_id and export['export_count'] == 1 and export['file_name'].endswith('.csv')
    assert export['exported_at'] and export['exported_by']['user_name']
    assert waybill['summary']['shipment']['file']['export_count'] == 1
    assert waybill['summary']['options']['locked'] is True
    assert waybill['summary']['options']['locked_reason'] == 'WAYBILL_EXISTS'
    actions = _actions(waybill)
    assert actions['export_file']['enabled'] is True and actions['export_file']['re_export'] is True
    assert actions['discard_export'] == {'key': 'discard_export', 'enabled': True, 'shipment_id': records[0]['id']}
    assert _filtered(client, access_token, 'awaiting_number') == [dn_id]

    # 重新导出：记录不变、换批次
    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 200
    second_batch = int(response.headers['X-Batch-Id'])
    records = _records(client, dn_id)
    assert len(records) == 1 and records[0]['status'] == 'exported' and records[0]['file_batch_id'] == second_batch

    # 导入预览：一张 DN 两行（2 箱）、找不到的、没有运单号的
    other_dn, _other_task = _packed_dn(client, access_token, 'SHP-FILE-2')
    response = _import(client, access_token, carrier_id, [
        [f'DN{dn_id}', '0', '', '4012-3456-7890'],
        [f'DN{dn_id}', '0', '', '4012-3456-7891'],
        ['DN999999', '0', '', '4012-0000-0000'],
        ['OTHER-REF', '0', '', '4012-0000-0001'],
        [f'DN{other_dn}', '0', '', ''],
    ])
    assert response.status_code == 200, response.get_json()
    preview = response.get_json()
    assert preview['dry_run'] is True and preview['batch_id'] is None and preview['encoding'] == 'cp932'
    results = [(r['line'], r['dn_id'], r['tracking_numbers'], r['result']) for r in preview['rows']]
    assert results == [(2, dn_id, ['401234567890'], 'matched'), (3, dn_id, ['401234567891'], 'matched'),
                       (4, 999999, ['401200000000'], 'not_found'), (5, None, ['401200000001'], 'not_found'),
                       (6, other_dn, [], 'invalid')]
    assert preview['rows'][0]['order_number'] == 'SHP-FILE-1' and preview['rows'][0]['primary'] is True
    # 每行带 reason_code（界面按它出本地化文字）；matched 没有
    assert [r['reason_code'] for r in preview['rows']] == [None, None, 'DN_NOT_FOUND', 'NOT_WMS_REFERENCE',
                                                           'NO_TRACKING_NUMBER']
    assert preview['rows'][0]['message'] is None and preview['rows'][3]['message']
    assert preview['summary'] == {'matched': 2, 'not_found': 2, 'already_has_number': 0, 'same_number': 0,
                                  'invalid': 1, 'skipped': 0,
                                  'dns': {'matched': 1, 'not_found': 1, 'already_has_number': 0, 'same_number': 0,
                                          'invalid': 0}}
    assert _records(client, dn_id)[0]['status'] == 'exported'       # 预览不写

    # 提交
    response = _import(client, access_token, carrier_id, [
        [f'DN{dn_id}', '0', '', '4012-3456-7890'], [f'DN{dn_id}', '0', '', '4012-3456-7891']], dry_run=False)
    assert response.status_code == 200, response.get_json()
    applied = response.get_json()
    assert applied['dry_run'] is False and applied['batch_id'] and applied['summary']['matched'] == 2
    records = _records(client, dn_id)
    assert len(records) == 1
    assert records[0]['status'] == 'active' and records[0]['tracking_number'] == '401234567890'
    assert records[0]['package_tracking_numbers'] == ['401234567891'] and records[0]['file_batch_id'] == second_batch
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).tracking_number == '401234567890'
        batches = CarrierFileBatch.query.order_by(CarrierFileBatch.id).all()
        assert [(b.kind, b.row_count) for b in batches] == [('export', 1), ('export', 1), ('import', 2)]
        assert batches[-1].summary['dn_ids'] == [dn_id]

    flow = _assert_stage(client, access_token, dn_id, 'awaiting_handover')
    waybill = _steps(flow)['waybill']
    assert waybill['state'] == 'done' and waybill['summary']['tracking_number'] == '401234567890'
    assert waybill['summary']['shipment']['file']['import_batch_id'] == applied['batch_id']
    assert waybill['summary']['export'] is None
    assert flow['carrier']['tracking_url'] == \
        'https://member.kms.kuronekoyamato.co.jp/parcel/detail?pno=401234567890'
    assert 'discard_export' not in _actions(waybill)
    assert _actions(waybill)['export_file']['reason_code'] == 'WAYBILL_EXISTS'

    # 再导一次：same_number；号码不同：already_has_number
    again = _import(client, access_token, carrier_id, [[f'DN{dn_id}', '0', '', '401234567890']]).get_json()
    assert again['rows'][0]['result'] == 'same_number'
    assert again['rows'][0]['reason_code'] == 'ALREADY_IMPORTED'
    assert again['rows'][0]['existing_tracking_number'] == '401234567890'
    other = _import(client, access_token, carrier_id, [[f'DN{dn_id}', '0', '', '499999999999']]).get_json()
    assert other['rows'][0]['result'] == 'already_has_number' and '401234567890' in other['rows'][0]['message']
    assert other['rows'][0]['reason_code'] == 'HAS_OTHER_NUMBER'
    assert other['rows'][0]['existing_tracking_number'] == '401234567890'
    # 号码已属于别的 DN：other_dn_id
    owned = _import(client, access_token, carrier_id, [[f'DN{other_dn}', '0', '', '401234567890']]).get_json()
    assert owned['rows'][0]['result'] == 'invalid' and owned['rows'][0]['reason_code'] == 'NUMBER_OWNED_BY_OTHER_DN'
    assert owned['rows'][0]['other_dn_id'] == dn_id and owned['rows'][0]['existing_tracking_number'] == '401234567890'

    # 批次列表
    response = client.get(f'/dn/carrier-files/batches?carrier_id={carrier_id}', headers=_h(access_token))
    assert response.status_code == 200
    listed = response.get_json()
    assert [b['kind'] for b in listed] == ['import', 'export', 'export']
    assert set(listed[0]) >= {'id', 'kind', 'file_name', 'row_count', 'summary', 'created_at', 'created_by'}
    assert listed[0]['file_name'] == 'b2_issued.csv' and listed[0]['created_by_user']['user_name'] == 'admin'


def test_export_row_problems_block_the_whole_batch(client, access_token):
    carrier_id = _jp_master_data(client)
    ok_dn, _task = _packed_dn(client, access_token, 'SHP-FILE-OK')
    bad_dn, _task = _packed_dn(client, access_token, 'SHP-FILE-BAD')
    pending_dn = _create_dn(client, access_token, customs=None, order_number='SHP-FILE-P').get_json()['id']
    with client.application.app_context():
        dn = db.session.get(DN, bad_dn)
        dn.ship_to = {**dn.ship_to, 'name': '髙橋 ①', 'phone': None}
        db.session.commit()
        recipient_phone = get_recipient().phone
    response = _export(client, access_token, carrier_id, [ok_dn, bad_dn, pending_dn, 999999])
    assert response.status_code == 409
    body = response.get_json()
    assert body['code'] == 16137 and body['details']['total'] == 4
    rows = {r['dn_id']: r for r in body['details']['rows']}
    assert set(rows) == {bad_dn, pending_dn, 999999}
    bad = {(p['column'], p['code']) for p in rows[bad_dn]['problems']}
    assert ('お届け先名', 'CHARACTER_NOT_ALLOWED') in bad
    # ship_to 没带电话时用收件人的（dn_ship_to 补上），所以电话不缺
    assert recipient_phone and ('お届け先電話番号', 'REQUIRED') not in bad
    assert rows[bad_dn]['order_number'] == 'SHP-FILE-BAD'
    assert {p['code'] for p in rows[pending_dn]['problems']} >= {'NOT_PACKED'}
    assert [p['code'] for p in rows[999999]['problems']] == ['DN_NOT_FOUND']
    assert rows[999999]['problems'][0]['column'] is None
    for dn_id in (ok_dn, bad_dn):
        assert _records(client, dn_id) == []                       # 整批不导出、不建记录
    with client.application.app_context():
        assert CarrierFileBatch.query.count() == 0

    # 别的承运商的 DN
    with client.application.app_context():
        other = Carrier(name='Sagawa', company_id=get_company().id, created_by=get_admin_user().id)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    third, _task = _packed_dn(client, access_token, 'SHP-FILE-3', carrier_id=other_id)
    rows = _export(client, access_token, carrier_id, [third]).get_json()['details']['rows']
    assert [p['code'] for p in rows[0]['problems']] == ['OTHER_CARRIER']
    # 导入别的承运商的 DN：invalid，reason_code OTHER_CARRIER + other_carrier（界面拼「这张出库单的承运商是 …」）
    row = _import(client, access_token, carrier_id, [[f'DN{third}', '0', '', '4012-3456-0003']]).get_json()['rows'][0]
    assert (row['result'], row['reason_code'], row['other_carrier'], row['other_carrier_id']) == \
        ('invalid', 'OTHER_CARRIER', 'Sagawa', other_id)


def test_export_and_import_preconditions(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, _task = _packed_dn(client, access_token, 'SHP-FILE-PRE')
    response = _export(client, access_token, carrier_id, [])
    assert response.status_code == 400 and response.get_json()['code'] == 16138
    assert response.get_json()['field'] == 'dn_ids'
    response = _export(client, access_token, 'abc', [dn_id])
    assert response.status_code == 400 and response.get_json()['field'] == 'carrier_id'
    with client.application.app_context():
        get_carrier().file_account = {'billing_customer_code': '012345678901'}
        db.session.commit()
    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 409 and response.get_json()['code'] == 16138
    assert response.get_json()['field'] == 'file_account.freight_mgmt_no'
    with client.application.app_context():
        get_carrier().waybill_method = 'manual'
        db.session.commit()
    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 409 and response.get_json()['field'] == 'carrier_id'
    response = _import(client, access_token, carrier_id, [[f'DN{dn_id}', '0', '', '1']])
    assert response.status_code == 409 and response.get_json()['code'] == 16138
    with client.application.app_context():
        get_carrier().waybill_method = 'file'
        db.session.commit()
    response = client.post('/dn/carrier-files/import', data={'carrier_id': str(carrier_id)}, headers=_h(access_token),
                           content_type='multipart/form-data')
    assert response.status_code == 400 and response.get_json()['field'] == 'file'
    response = client.post('/dn/carrier-files/import', data={
        'file': (io.BytesIO(bytes([0x44, 0x4E, 0x31, 0x2C, 0x82, 0x20])), 'x.csv'),
        'carrier_id': str(carrier_id), 'dry_run': 'maybe'}, headers=_h(access_token),
        content_type='multipart/form-data')
    assert response.status_code == 400 and response.get_json()['field'] == 'dry_run'


def test_discard_export_and_manual_number_replaces_export(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, task_id = _packed_dn(client, access_token, 'SHP-FILE-D')
    assert _export(client, access_token, carrier_id, [dn_id]).status_code == 200

    response = client.post(f'/dn/{dn_id}/carrier-shipment/discard-export', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['waybill'] is None and 'can_create' in response.get_json()
    records = _records(client, dn_id)
    assert [(r['status'], r['reason']) for r in records] == [('cancelled', 'discarded')]
    _assert_stage(client, access_token, dn_id, 'awaiting_waybill')
    response = client.post(f'/dn/{dn_id}/carrier-shipment/discard-export', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16075

    # 导出后又手填了号码：exported 记录作废（replaced），手填的成为有效运单
    assert _export(client, access_token, carrier_id, [dn_id]).status_code == 200
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'TEL-1234'},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    records = _records(client, dn_id)
    assert [(r['source'], r['status'], r['reason']) for r in records] == [
        ('file', 'cancelled', 'discarded'), ('file', 'cancelled', 'replaced'), ('manual', 'active', None)]


def test_import_without_export_creates_file_waybill(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, task_id = _packed_dn(client, access_token, 'SHP-FILE-N')
    response = _import(client, access_token, carrier_id, [[f'DN{dn_id}', '0', '', '4999-0000-0001']], dry_run=False,
                       encoding='utf-8', header=False)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['rows'][0]['result'] == 'matched' and response.get_json()['encoding'] == 'utf-8'
    records = _records(client, dn_id)
    assert [(r['source'], r['status'], r['tracking_number'], r['file_batch_id'] is not None) for r in records] == \
        [('file', 'active', '499900000001', True)]
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).tracking_number == '499900000001'
    # 换成别的号码（手填）：file 记录 replaced
    assert client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'X-1'},
                      headers=_h(access_token)).status_code == 200
    assert [(r['source'], r['status'], r['reason']) for r in _records(client, dn_id)] == \
        [('file', 'cancelled', 'replaced'), ('manual', 'active', None)]


# ---------------------------------------------------------------------------
# 运单服务选项

def test_shipping_options_validation_export_and_lock(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, _task = _packed_dn(client, access_token, 'SHP-OPT-1')
    url = f'/dn/{dn_id}/shipping-options'
    response = client.get(url, headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['options'] == {}
    assert response.get_json()['defaults']['invoice_type'] == '0'

    for body, field in (({'options': {'nope': 1}}, 'options.nope'),
                        ({'options': {'invoice_type': 'Z'}}, 'options.invoice_type'),
                        ({'options': {'delivery_date': '2026/11/03'}}, 'options.delivery_date'),
                        ({'options': {'cod_amount': 'abc'}}, 'options.cod_amount'),
                        ({'options': {'cod_amount': -1}}, 'options.cod_amount'),
                        ({'options': 'x'}, 'options'), ({}, 'options')):
        response = client.put(url, json=body, headers=_h(access_token))
        assert response.status_code == 400, body
        assert response.get_json()['code'] == 16139 and response.get_json()['field'] == field

    response = client.put(url, json={'options': {'invoice_type': '2', 'cod_amount': '3000', 'cod_tax_amount': 272,
                                                 'delivery_date': '2026-11-03', 'delivery_time_slot': '1416',
                                                 'cool_type': ''}}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['options'] == {'invoice_type': '2', 'cod_amount': 3000, 'cod_tax_amount': 272,
                               'delivery_date': '2026-11-03', 'delivery_time_slot': '1416'}
    assert data['locked'] is False and data['schema']

    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 200
    row = _csv_rows(response)[1]
    assert row[1] == '2' and row[5] == '2026/11/03' and row[6] == '1416' and row[33] == '3000' and row[34] == '272'

    flow = _flow(client, access_token, dn_id)
    shipment = _steps(flow)['waybill']['summary']['shipment']
    assert shipment['service_type'] == '2' and shipment['service_name'] == 'Collect on delivery (Collect)'
    assert shipment['service_label']['ja-JP'] == 'コレクト'
    assert _steps(flow)['waybill']['summary']['service_name'] == 'Collect on delivery (Collect)'

    response = client.put(url, json={'options': {'invoice_type': '0'}}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076
    details = response.get_json()['details']
    assert details['reason'] == 'SHIPPING_OPTIONS_LOCKED' and details['carrier_shipment']['status'] == 'exported'

    # 作废导出后又能改
    assert client.post(f'/dn/{dn_id}/carrier-shipment/discard-export', headers=_h(access_token)).status_code == 200
    assert client.put(url, json={'options': {'invoice_type': '0'}}, headers=_h(access_token)).status_code == 200


def test_invalid_stored_option_is_an_export_problem(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, _task = _packed_dn(client, access_token, 'SHP-OPT-2')
    with client.application.app_context():
        db.session.get(DN, dn_id).shipping_options = {'invoice_type': 'Q', 'other_system_key': 'kept'}
        db.session.commit()
    rows = _export(client, access_token, carrier_id, [dn_id]).get_json()['details']['rows']
    assert [(p['column'], p['code']) for p in rows[0]['problems']] == [('options.invoice_type', 'OPTION_INVALID')]


# ---------------------------------------------------------------------------
# 揽收：交承运商后还挂着的「已约」揽收显示为已完成

def test_scheduled_pickup_shows_done_after_handover(client, access_token):
    carrier_id = _jp_master_data(client)
    with client.application.app_context():
        get_carrier().pickup_method = 'manual'
        warehouse_id = get_warehouse().id
        db.session.commit()
    dn_id, task_id = _packed_dn(client, access_token, 'SHP-PICK-1')
    _import(client, access_token, carrier_id, [[f'DN{dn_id}', '0', '', '4012-1111-2222']], dry_run=False)
    shipment_id = _records(client, dn_id)[0]['id']
    from .test_carrier_pickup import _tomorrow
    response = client.post('/dn/carrier-pickups/manual', json={
        'carrier_id': carrier_id, 'shipment_ids': [shipment_id], 'pickup_date': _tomorrow(),
        'ready_time': '13:00', 'close_time': '17:00'}, headers=_h(access_token, **{'X-WAREHOUSE-ID': str(warehouse_id)}))
    assert response.status_code == 201, response.get_json()
    assert _ship(client, access_token, task_id).status_code == 200
    flow = _flow(client, access_token, dn_id)
    pickup = _steps(flow)['pickup']
    assert pickup['state'] == 'done' and pickup['summary']['pickup']['done'] is True
    assert pickup['summary']['handed_over'] is True
    assert not [p for p in flow['problems'] if p['step'] == 'pickup']


# ---------------------------------------------------------------------------
# 插件声明的服务选项（建单时 shipment['options']）与交给插件的结构化地址

def test_plugin_service_options_and_parties(client, access_token, monkeypatch):
    from .test_carrier_shipment import FakeFedex, _create, _ready, _set_fedex
    from warehouse.carrier.integrations.fedex import FedexAdapter, client as fedex_client
    from warehouse.dn.carrier_services import CarrierShipmentService
    monkeypatch.setattr(fedex_client, '_send', FakeFedex())
    fedex_client.clear_token_cache()
    _set_fedex(client)
    monkeypatch.setattr(FedexAdapter, 'service_options', (
        {'key': 'signature', 'label': {'en-US': 'Signature'},
         'values': [{'value': 'DIRECT', 'label': 'Direct'}, {'value': 'ADULT', 'label': 'Adult'}]},))
    dn_id, _task = _ready(client, access_token)
    url = f'/dn/{dn_id}/shipping-options'
    schema = client.get(url, headers=_h(access_token)).get_json()['schema']
    assert [(o['key'], o['kind']) for o in schema] == [('signature', 'select')]
    assert client.put(url, json={'options': {'signature': 'NONE'}}, headers=_h(access_token)).status_code == 400
    assert client.put(url, json={'options': {'signature': 'ADULT'}}, headers=_h(access_token)).status_code == 200
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        dn.shipping_options = {'signature': 'ADULT', 'unknown_key': 'x'}
        db.session.commit()
        shipment = CarrierShipmentService._collect(dn)['shipment']
        assert shipment['options'] == {'signature': 'ADULT'}           # 只给插件声明了的键
        # 旧键照旧，另带 local / latin
        assert shipment['shipper']['company_name'] == 'Example Trading Co., Ltd.'
        assert shipment['shipper']['local']['name'] == 'Company A'
        assert shipment['recipient']['address_line1'] == 'Hauptstrasse 1'
        assert shipment['recipient']['latin']['locality'] == 'Berlin'
    fedex_client.clear_token_cache()
    assert _create(client, access_token, dn_id).status_code == 201
    response = client.put(url, json={'options': {'signature': 'DIRECT'}}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['details']['reason'] == 'SHIPPING_OPTIONS_LOCKED'


# ---------------------------------------------------------------------------
# 联调补充：列表行的承运商 id、承运商声明的服务选项、打包后改收件地址（导出报超长 → 改短 → 导出）

def test_list_shipping_carrier_id_and_carrier_service_options(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, task_id = _packed_dn(client, access_token, 'SHP-FILE-LIST')
    with client.application.app_context():
        other = Carrier(name='Sagawa', company_id=get_company().id, created_by=get_admin_user().id)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        dn = db.session.get(DN, dn_id)
        dn.carrier_id = other_id                       # DN 上的承运商不同：列表按发货任务的
        db.session.commit()
    items = client.get('/dn/?shipping_stage=awaiting_waybill', headers=_h(access_token)).get_json()['items']
    row = next(i for i in items if i['id'] == dn_id)
    assert row['shipping']['carrier_id'] == carrier_id and row['shipping']['tracking_number'] is None
    with client.application.app_context():
        assert row['shipping']['carrier_name'] == db.session.get(Carrier, carrier_id).name

    detail = client.get(f'/carrier/{carrier_id}', headers=_h(access_token)).get_json()
    keys = [o['key'] for o in detail['service_options']]
    assert keys[:2] == ['invoice_type', 'delivery_time_slot'] and 'cod_amount' in keys
    other_detail = client.get(f'/carrier/{other_id}', headers=_h(access_token)).get_json()
    assert other_detail['service_options'] == []


def test_ship_to_update_fixes_too_long_address(client, access_token):
    carrier_id = _jp_master_data(client)
    dn_id, _task = _packed_dn(client, access_token, 'SHP-FILE-LONG')
    url = f'/dn/{dn_id}/ship-to'
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        dn.ship_to = {**dn.ship_to, 'address_line1': '千代田一丁目一番一号' * 4}
        db.session.commit()
    response = _export(client, access_token, carrier_id, [dn_id])
    problems = response.get_json()['details']['rows'][0]['problems']
    assert response.status_code == 409 and [(p['column'], p['code']) for p in problems] == [('お届け先住所', 'TOO_LONG')]
    assert problems[0]['value'].startswith('東京都千代田区千代田一丁目')

    body = client.get(url, headers=_h(access_token)).get_json()
    assert body['locked'] is False and body['ship_to_effective']['address_line1'].startswith('千代田一丁目')
    fixed = {**body['ship_to'], 'address_line1': '千代田1-1'}
    response = client.put(url, json={'ship_to': fixed}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['ship_to']['address_line1'] == '千代田1-1' and body['ship_to']['country'] == 'JP'
    assert body['shipping_address'].startswith('〒100-0001 東京都千代田区') and '千代田1-1' in body['shipping_address']

    assert client.put(url, json={'ship_to': {**fixed, 'country': 'ZZ'}},
                      headers=_h(access_token)).get_json()['code'] == 14020
    response = client.put(url, json={'ship_to': {**fixed, 'name': 'x' * 300}}, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'ship_to.name'
    assert client.put(url, json={'nothing': 1}, headers=_h(access_token)).status_code == 400

    response = _export(client, access_token, carrier_id, [dn_id])
    assert response.status_code == 200 and _csv_rows(response)[1][11] == '東京都千代田区千代田1-1'

    # 导出后锁定（面单已按这个地址打）；作废导出后又能改
    response = client.put(url, json={'ship_to': fixed}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076
    assert response.get_json()['details']['reason'] == 'SHIP_TO_LOCKED'
    assert client.get(url, headers=_h(access_token)).get_json()['locked_reason'] == 'WAYBILL_EXISTS'
    assert client.post(f'/dn/{dn_id}/carrier-shipment/discard-export', headers=_h(access_token)).status_code == 200
    assert client.put(url, json={'ship_to': fixed}, headers=_h(access_token)).status_code == 200

    # 作废导出时已经没有导出记录 → 409 16075（details.waybill）
    response = client.post(f'/dn/{dn_id}/carrier-shipment/discard-export', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16075
    assert 'waybill' in response.get_json()['details']
