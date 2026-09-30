"""DN 海外件：报关快照 / 箱子 / 出口单证（CI / PL）/ 发货拦截与锁定 / dn.delivered 追加字段"""
from .helpers import *
from .test_isolation_outbound import _make_company_b

import base64
import hashlib
import re
import zlib
from datetime import date, datetime   # 放在 helpers 之后：helpers 以模块名导出了 datetime
from zoneinfo import ZoneInfo
from system.webhook.models import WebhookEvent
from warehouse.delivery.services import DeliveryTaskService
from warehouse.dn.customs_services import CustomsService
from warehouse.dn.models import DNCustoms, DNDocument, DNPackage
from warehouse.packing.models import PackingBatch, PackingTask, PackingTaskDetail
from warehouse.picking.models import PickingBatch, PickingTask, PickingTaskDetail

# goods.origin_country 由迁移 A（路 1）加到商品模型上。本分支单独跑测试时模型里还没有这一列，
# 这里临时挂到映射上让 create_all 建出来；合并后 hasattr 为真，这段不再生效。
if not hasattr(Goods, 'origin_country'):
    Goods.origin_country = db.Column(db.String(2), nullable=True)


def _h(token, **extra):
    return {'Authorization': f'Bearer {token}', **extra}


def _customs(**overrides):
    body = {
        'invoice_number': 'INV-TEST-001',
        'currency': 'JPY',
        'incoterm': 'DAP',
        'export_reason': 'SALE',
        'recipient_country': 'DE',
        'recipient_tax_id': 'DE123456789',
        'recipient_tax_id_type': 'EORI',
        'freight_charge': 8200,
        'consignee': {
            'name': 'Max Mustermann', 'company': 'Example GmbH',
            'address_line1': 'Hauptstrasse 1', 'address_line2': None,
            'city': 'Berlin', 'state': None, 'postal_code': '10115',
            'country': 'DE', 'phone': '+49 30 0000000',
        },
        'lines': [
            {'goods_code': 'G001', 'quantity': 3, 'unit_value': 1200, 'total_value': 3600,
             'description_en': 'Plastic figure', 'hs_code': '9503.00', 'origin_country': 'CN'},
            {'goods_code': 'G002', 'quantity': 2, 'unit_value': 21000, 'total_value': 42000,
             'description_en': 'Acrylic stand', 'hs_code': '392640', 'origin_country': 'VN'},
        ],
    }
    body.update(overrides)
    return body


PACKAGES = [
    {'package_no': 1, 'gross_weight_kg': 3.25, 'length_mm': 400, 'width_mm': 300, 'height_mm': 250},
    {'package_no': 2, 'gross_weight_kg': 3.25, 'length_mm': 400, 'width_mm': 300, 'height_mm': 250, 'remark': 'fragile'},
]


def _prepare(client, *, profile=True, origins=None):
    """公司出口资料、商品原产国、充足库存。"""
    origins = {'G001': 'CN', 'G002': 'VN'} if origins is None else origins
    with client.application.app_context():
        company = get_company()
        if profile:
            company.legal_name_en = 'Example Trading Co., Ltd.'
            company.address_en = '1-2-3 Example, Minato-ku, Tokyo 105-0000'
            company.country_code = 'JP'
            company.tax_id_label = 'Corporate No.'
            company.tax_id = '1234567890123'
            company.export_contact_name = 'Hanako Example'
            company.export_signatory_name = 'Taro Example'
            company.export_signatory_title = 'Export Manager'
        for goods in Goods.query.filter_by(company_id=company.id).all():
            goods.origin_country = origins.get(goods.code)
            inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id, get_warehouse().id)
            inventory.onhand_stock = 5000
        db.session.commit()


def _create_dn(client, token, customs='default', details=None, order_number='ORD-EXPORT-1'):
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        recipient_id = get_recipient().id
    body = {
        'recipient_id': recipient_id,
        'warehouse_id': warehouse_id,
        'shipping_address': 'Hauptstrasse 1, 10115 Berlin',
        'expected_shipping_date': date.today().isoformat(),
        'order_number': order_number,
        'details': details or [{'goods_code': 'G001', 'quantity': 3}, {'goods_code': 'G002', 'quantity': 2}],
    }
    if customs == 'default':
        body['customs'] = _customs()
    elif customs is not None:
        body['customs'] = customs
    return client.post('/dn/', json=body, headers=_h(token))


def _force_packed(client, dn_id, packed=None, status='packed'):
    """直接造出已拣 / 已打包的流程数据（拣货、打包任务 completed），并建发货任务。返回发货任务 ID。"""
    packed = packed or {}
    with client.application.app_context():
        admin = get_admin_user()
        dn = db.session.get(DN, dn_id)
        location = get_location()
        picking = PickingTask(dn_id=dn.id, status='completed', is_active=True, created_by=admin.id)
        packing = PackingTask(dn_id=dn.id, status='completed', is_active=True, created_by=admin.id)
        db.session.add_all([picking, packing])
        db.session.flush()
        picking_batch = PickingBatch(picking_task_id=picking.id, operator_id=admin.id, operation_time=datetime.now())
        packing_batch = PackingBatch(packing_task_id=packing.id, operator_id=admin.id, operation_time=datetime.now())
        db.session.add_all([picking_batch, packing_batch])
        db.session.flush()
        for detail in dn.details:
            quantity = packed.get(detail.goods.code, detail.quantity)
            db.session.add(PickingTaskDetail(
                picking_task_id=picking.id, batch_id=picking_batch.id, location_id=location.id,
                goods_id=detail.goods_id, picked_quantity=detail.quantity, operator_id=admin.id))
            if quantity:
                db.session.add(PackingTaskDetail(
                    packing_task_id=packing.id, batch_id=packing_batch.id,
                    goods_id=detail.goods_id, packed_quantity=quantity, operator_id=admin.id))
            detail.picked_quantity = detail.quantity
            detail.packed_quantity = quantity
            inventory = get_inventory_by_goods_id_and_warehouse_id(detail.goods_id, dn.warehouse_id)
            inventory.packed_stock = (inventory.packed_stock or 0) + quantity
        dn.status = status
        db.session.commit()
        if status != 'packed':
            return None
        task = DeliveryTaskService.create_delivery_task_from_dn(dn.id, admin.id)
        return task.id


def _export_dn_ready(client, token, packed=None, customs='default'):
    """已打包、已录箱子的海外 DN。返回 (dn_id, delivery_task_id)。"""
    _prepare(client)
    response = _create_dn(client, token, customs=customs)
    assert response.status_code == 201, response.get_json()
    dn_id = response.get_json()['id']
    task_id = _force_packed(client, dn_id, packed=packed)
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return dn_id, task_id


def _issue(client, token, dn_id):
    return client.post(f'/dn/{dn_id}/customs-documents/issue', headers=_h(token))


def _ship(client, token, task_id, **body):
    response = client.put(f'/delivery/{task_id}/process/', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return client.put(f'/delivery/{task_id}/complete/', json=body or {}, headers=_h(token))


def _codes(problems, level=None):
    return {p['code'] for p in problems if level is None or p['level'] == level}


def _pdf_streams(content: bytes) -> bytes:
    """解出 PDF 各内容流（reportlab 默认 ASCII85 + Flate），拼成一段便于检索文字。"""
    out = []
    for match in re.finditer(rb'<<(.*?)>>\s*stream\r?\n(.*?)endstream', content, re.S):
        header, data = match.group(1), match.group(2).rstrip(b'\r\n')
        if b'ASCII85Decode' in header:
            data = data.strip()
            if data.endswith(b'~>'):
                data = data[:-2]
            data = base64.a85decode(data)
        if b'FlateDecode' in header:
            data = zlib.decompress(data)
        out.append(data)
    return b'\n'.join(out)


def _pdf_pages(content: bytes) -> int:
    return len(re.findall(rb'/Type\s*/Page(?![a-zA-Z])', content))


# ---------------------------------------------------------------------------
# 报关快照
# ---------------------------------------------------------------------------

def test_create_dn_with_customs_stores_snapshot(client, access_token):
    _prepare(client)
    response = _create_dn(client, access_token)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['is_export'] is True
    assert data['customs']['invoice_number'] == 'INV-TEST-001'
    assert data['customs']['freight_charge'] == 8200
    assert [line['goods_code'] for line in data['customs']['lines']] == ['G001', 'G002']
    assert data['packages'] == []
    assert data['customs_documents'] == []

    domestic = _create_dn(client, access_token, customs=None, order_number='ORD-DOMESTIC-1')
    assert domestic.status_code == 201
    assert domestic.get_json()['is_export'] is False
    assert domestic.get_json()['customs'] is None

    listing = client.get('/dn/?per_page=50', headers=_h(access_token)).get_json()
    flags = {item['id']: item['is_export'] for item in listing['items']}
    assert flags[data['id']] is True
    assert flags[domestic.get_json()['id']] is False
    assert 'customs' not in listing['items'][0]


@pytest.mark.parametrize('customs, code, field', [
    (['not', 'an', 'object'], 16063, 'customs'),
    ('text', 16063, 'customs'),
    ({'lines': {'goods_code': 'G001'}}, 16063, 'customs.lines'),
    ({'lines': ['G001']}, 16063, 'customs.lines[0]'),
    ({'consignee': 'Max', 'lines': []}, 16063, 'customs.consignee'),
    ({'lines': [{'goods_code': 'G001', 'unit_value': 'abc'}]}, 16063, 'customs.lines[0].unit_value'),
    ({'freight_charge': -1, 'lines': []}, 16063, 'customs.freight_charge'),
    ({'insurance_charge': -1, 'lines': []}, 16063, 'customs.insurance_charge'),
    ({'insurance_charge': 12.5, 'lines': []}, 16063, 'customs.insurance_charge'),
    ({'declared_value_carriage': 'abc', 'lines': []}, 16063, 'customs.declared_value_carriage'),
    ({'declared_value_carriage': True, 'lines': []}, 16063, 'customs.declared_value_carriage'),
    ({'declared_value_carriage': {'amount': 1}, 'lines': []}, 16063, 'customs.declared_value_carriage'),
    ({'lines': [{'goods_code': 'NOT-IN-DN'}]}, 16064, 'customs.lines[0].goods_code'),
    ({'lines': [{'goods_code': 'G001'}, {'goods_code': 'G001'}]}, 16064, 'customs.lines[1].goods_code'),
])
def test_create_dn_rejects_customs_structure_errors(client, access_token, customs, code, field):
    _prepare(client)
    with client.application.app_context():
        before = DN.query.count()
    response = _create_dn(client, access_token, customs=customs)
    assert response.status_code == 400, response.get_json()
    body = response.get_json()
    assert body['code'] == code
    assert body['details']['field'] == field
    with client.application.app_context():
        assert DN.query.count() == before       # 拒绝建单，不留半截数据
        assert DNCustoms.query.count() == 0


def test_incomplete_customs_accepted_and_problems_listed(client, access_token):
    _prepare(client, origins={'G001': 'CN'})    # G002 没录原产国
    customs = _customs(
        incoterm=None, export_reason='', recipient_country=None, recipient_tax_id=None, currency='12',
        lines=[
            {'goods_code': 'G001', 'unit_value': 0, 'description_en': 'フィギュア', 'hs_code': '95.03'},
        ],
    )
    response = _create_dn(client, access_token, customs=customs)
    assert response.status_code == 201, response.get_json()
    dn_id = response.get_json()['id']

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['is_export'] is True and view['ready'] is False and view['locked'] is False
    assert {'NOT_PACKED', 'PACKAGES_MISSING', 'INCOTERM_MISSING', 'EXPORT_REASON_MISSING',
            'CURRENCY_INVALID'} <= _codes(view['problems'], 'error')
    assert 'RECIPIENT_TAX_ID_MISSING' in _codes(view['problems'], 'warning')
    # recipient_country 为空时取 consignee.country
    assert 'RECIPIENT_COUNTRY_MISSING' not in _codes(view['problems'])
    assert view['recipient_country'] == 'DE'

    # 打包后逐行检查（已打包 >0 的行）
    _force_packed(client, dn_id)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    by_goods = {}
    for p in view['problems']:
        if 'goods_code' in p:
            by_goods.setdefault(p['goods_code'], set()).add(p['code'])
    assert by_goods['G001'] == {'HS_CODE_MISSING', 'DESCRIPTION_NOT_ASCII', 'UNIT_VALUE_MISSING'}
    assert by_goods['G002'] == {'LINE_MISSING'}
    assert 'NOT_PACKED' not in _codes(view['problems'])

    lines = {line['goods_code']: line for line in view['lines']}
    assert lines['G001']['origin_country'] == 'CN' and lines['G001']['origin_source'] == 'goods'
    assert lines['G002']['origin_country'] is None


def test_origin_comes_from_goods_master_not_customs_line(client, access_token):
    _prepare(client, origins={'G001': 'CN', 'G002': None})
    response = _create_dn(client, access_token)          # 报关行里 G002 写了 VN
    dn_id = response.get_json()['id']
    _force_packed(client, dn_id)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    lines = {line['goods_code']: line for line in view['lines']}
    assert lines['G002']['origin_country'] is None
    assert lines['G002']['declared_origin_country'] == 'VN'
    assert {'code': 'ORIGIN_MISSING', 'level': 'error'}.items() <= next(
        p for p in view['problems'] if p.get('goods_code') == 'G002').items()


def test_put_customs_replaces_snapshot(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None).get_json()['id']
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=500), headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['is_export'] is True
    assert response.get_json()['customs']['freight_charge'] == 500
    assert response.get_json()['voided_documents'] == []

    bad = client.put(f'/dn/{dn_id}/customs', json={'lines': 'x'}, headers=_h(access_token))
    assert bad.status_code == 400 and bad.get_json()['code'] == 16063


# ---------------------------------------------------------------------------
# 箱子
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('packages', [
    [],
    [{'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}] * 100,
    [{'gross_weight_kg': 0, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1000, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1.2345, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 'abc', 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1, 'length_mm': 0, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1, 'length_mm': 3001, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1, 'length_mm': 10.5, 'width_mm': 10, 'height_mm': 10}],
    [{'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10}],
    [{'package_no': 1, 'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10},
     {'package_no': 3, 'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    [{'package_no': 1, 'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10},
     {'package_no': 1, 'gross_weight_kg': 1, 'length_mm': 10, 'width_mm': 10, 'height_mm': 10}],
    'not-a-list',
])
def test_packages_validation(client, access_token, packages):
    _prepare(client)
    dn_id = _create_dn(client, access_token).get_json()['id']
    _force_packed(client, dn_id)
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': packages}, headers=_h(access_token))
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16066


def test_packages_status_and_auto_numbering(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token).get_json()['id']
    body = {'packages': [
        {'gross_weight_kg': '1.5', 'length_mm': 300, 'width_mm': 200, 'height_mm': 100},
        {'gross_weight_kg': 2, 'length_mm': 300.0, 'width_mm': 200, 'height_mm': 100, 'remark': ' top '},
    ]}

    pending = client.put(f'/dn/{dn_id}/packages', json=body, headers=_h(access_token))
    assert pending.status_code == 409 and pending.get_json()['code'] == 16067

    _force_packed(client, dn_id, status='picked')
    response = client.put(f'/dn/{dn_id}/packages', json=body, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    packages = response.get_json()['packages']
    assert [p['package_no'] for p in packages] == [1, 2]
    assert packages[0]['gross_weight_kg'] == 1.5
    assert packages[1]['remark'] == 'top'

    listing = client.get(f'/dn/{dn_id}/packages', headers=_h(access_token)).get_json()
    assert len(listing['packages']) == 2
    detail = client.get(f'/dn/{dn_id}', headers=_h(access_token)).get_json()
    assert [p['length_mm'] for p in detail['packages']] == [300, 300]

    # 整体替换（缩成一箱）
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': [body['packages'][0]]}, headers=_h(access_token))
    assert response.status_code == 200
    with client.application.app_context():
        assert DNPackage.query.filter_by(dn_id=dn_id).count() == 1


# ---------------------------------------------------------------------------
# 单证
# ---------------------------------------------------------------------------

def test_issue_requires_complete_conditions(client, access_token):
    _prepare(client, profile=False, origins={'G001': 'CN'})
    dn_id = _create_dn(client, access_token).get_json()['id']
    _force_packed(client, dn_id)

    response = _issue(client, access_token, dn_id)
    assert response.status_code == 409
    body = response.get_json()
    assert body['code'] == 16068
    codes = _codes(body['details']['problems'], 'error')
    assert {'PACKAGES_MISSING', 'EXPORTER_PROFILE_INCOMPLETE', 'ORIGIN_MISSING'} <= codes
    fields = {p.get('field') for p in body['details']['problems'] if p['code'] == 'EXPORTER_PROFILE_INCOMPLETE'}
    assert {'company.legal_name_en', 'company.address_en'} <= fields
    with client.application.app_context():
        assert DNDocument.query.count() == 0

    domestic_id = _create_dn(client, access_token, customs=None, order_number='ORD-D').get_json()['id']
    response = _issue(client, access_token, domestic_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16070


def test_issue_same_data_keeps_version_and_changed_data_bumps(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)

    first = _issue(client, access_token, dn_id)
    assert first.status_code == 201, first.get_json()
    body = first.get_json()
    assert body['version'] == 1
    assert [d['doc_type'] for d in body['documents']] == ['commercial_invoice', 'packing_list']
    assert all(d['status'] == 'issued' and d['size_bytes'] > 0 for d in body['documents'])
    assert body['documents'][0]['file_name'] == 'CI_INV-TEST-001_v1.pdf'
    assert body['documents'][0]['invoice_date'] == datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
    first_ids = [d['id'] for d in body['documents']]

    again = _issue(client, access_token, dn_id)
    assert again.status_code == 200
    assert [d['id'] for d in again.get_json()['documents']] == first_ids
    assert again.get_json()['version'] == 1

    # 快照内容变了：现有单证作废（customs_changed），重新出单证版本 +1
    changed = _customs()
    changed['lines'][0]['unit_value'] = 1300
    response = client.put(f'/dn/{dn_id}/customs', json=changed, headers=_h(access_token))
    assert response.status_code == 200
    assert sorted(response.get_json()['voided_documents']) == sorted(first_ids)

    second = _issue(client, access_token, dn_id)
    assert second.status_code == 201
    assert second.get_json()['version'] == 2
    assert second.get_json()['documents'][1]['file_name'] == 'PL_INV-TEST-001_v2.pdf'

    issued = client.get(f'/dn/{dn_id}/customs-documents/?status=issued', headers=_h(access_token)).get_json()
    assert {d['version'] for d in issued} == {2} and len(issued) == 2
    everything = client.get(f'/dn/{dn_id}/customs-documents/', headers=_h(access_token)).get_json()
    assert len(everything) == 4
    void = [d for d in everything if d['status'] == 'void']
    assert {d['void_reason'] for d in void} == {'customs_changed'}
    assert all(d['voided_at'] for d in void)

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert [d['version'] for d in view['current_documents']] == [2, 2]
    detail = client.get(f'/dn/{dn_id}', headers=_h(access_token)).get_json()
    assert len(detail['customs_documents']) == 4


def test_packages_change_voids_documents(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    ids = [d['id'] for d in _issue(client, access_token, dn_id).get_json()['documents']]

    # 同样的箱子：不作废
    same = client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(access_token))
    assert same.status_code == 200 and same.get_json()['voided_documents'] == []

    changed = [dict(PACKAGES[0], gross_weight_kg=4.5), PACKAGES[1]]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 200
    assert sorted(response.get_json()['voided_documents']) == sorted(ids)
    with client.application.app_context():
        docs = DNDocument.query.filter(DNDocument.id.in_(ids)).all()
        assert {d.status for d in docs} == {'void'}
        assert {d.void_reason for d in docs} == {'packages_changed'}

    reissued = _issue(client, access_token, dn_id)
    assert reissued.status_code == 201 and reissued.get_json()['version'] == 2


def test_documents_pdf_content_and_totals(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    with client.application.app_context():
        task = DeliveryTask.query.filter_by(dn_id=dn_id).first()
        task.tracking_number = 'AWB-TEST-0001'
        db.session.commit()

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['ready'] is True, view['problems']
    assert view['totals'] == {
        'quantity': 5, 'goods_value': 45600, 'freight': 8200, 'insurance': 0, 'invoice_total': 53800,
        'package_count': 2, 'gross_weight_kg': 6.5, 'net_weight_kg': 6.0,
    }
    assert view['customs']['insurance_charge'] is None and view['customs']['declared_value_carriage'] is None
    assert view['exporter']['legal_name_en'] == 'Example Trading Co., Ltd.'

    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci_meta, pl_meta = documents
    ci = client.get(f'/dn/{dn_id}/customs-documents/{ci_meta["id"]}/file', headers=_h(access_token))
    assert ci.status_code == 200
    assert ci.mimetype == 'application/pdf'
    assert ci.headers['Content-Disposition'].startswith('inline')
    assert ci.headers['X-Content-SHA256'] == hashlib.sha256(ci.data).hexdigest() == ci_meta['sha256']
    assert ci.data.startswith(b'%PDF-') and ci.data.rstrip().endswith(b'%%EOF')
    assert 1 <= _pdf_pages(ci.data) <= 3
    assert 1_000 < len(ci.data) < 300_000

    text = _pdf_streams(ci.data)
    for expected in (b'COMMERCIAL INVOICE', b'INV-TEST-001', b'ORD-EXPORT-1', b'AWB-TEST-0001',
                     b'Example Trading Co., Ltd.', b'Corporate No.: 1234567890123',
                     b'EORI No.: DE123456789', b'DAP GERMANY', b'GERMANY', b'JAPAN',
                     b'9503.00', b'3926.40', b'CN - China', b'VN - Vietnam',
                     b'Freight', b'JPY 8,200', b'Goods Value', b'JPY 45,600',
                     b'Total Invoice Value', b'JPY 53,800', b'42,000', b'Net Wt',
                     b'I/We hereby certify', b'Taro Example', b'Export Manager', b'Same as Consignee'):
        assert expected in text, expected
    assert b'Insurance' not in text                  # 没投保：不印 Insurance 行
    lowered = (text + ci.data).lower()
    assert b'exempt' not in lowered and b'consumption' not in lowered

    pl = client.get(f'/dn/{dn_id}/customs-documents/{pl_meta["id"]}/file', headers=_h(access_token))
    pl_text = _pdf_streams(pl.data)
    for expected in (b'PACKING LIST', b'1 of 2', b'2 of 2', b'40.0 x 30.0 x 25.0', b'3.250',
                     b'Total Gross Weight', b'6.500 kg', b'Total Net Weight', b'6.000 kg', b'fragile'):
        assert expected in pl_text, expected
    assert b'exempt' not in (pl_text + pl.data).lower()

    missing = client.get(f'/dn/{dn_id}/customs-documents/99999/file', headers=_h(access_token))
    assert missing.status_code == 404 and missing.get_json()['code'] == 16071


def test_invoice_uses_packed_quantity(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token, packed={'G001': 3, 'G002': 0})
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['ready'] is True, view['problems']
    assert view['totals']['quantity'] == 3
    assert view['totals']['goods_value'] == 3600
    assert view['totals']['invoice_total'] == 3600 + 8200
    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci = client.get(f'/dn/{dn_id}/customs-documents/{documents[0]["id"]}/file', headers=_h(access_token))
    text = _pdf_streams(ci.data)
    assert b'Plastic figure' in text
    assert b'Acrylic stand' not in text      # 已打包 0 的行不上发票
    assert b'JPY 11,800' in text


def test_jp_export_code_stored_checked_and_not_printed(client, access_token):
    customs = _customs()
    customs['lines'][0]['jp_export_code'] = '950300000'          # 合格：9 位且前 6 位 = HS
    customs['lines'][1]['jp_export_code'] = '3926.90.000'        # 前 6 位与 HS 392640 不一致
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    lines = {line['goods_code']: line for line in view['lines']}
    assert lines['G001']['jp_export_code'] == '950300000'
    assert lines['G002']['jp_export_code'] == '3926.90.000'
    assert view['customs']['lines'][0]['jp_export_code'] == '950300000'
    mismatch = [p for p in view['problems'] if p['code'] == 'JP_EXPORT_CODE_MISMATCH']
    assert [(p['goods_code'], p['level']) for p in mismatch] == [('G002', 'warning')]
    assert 'JP_EXPORT_CODE_MISSING' not in _codes(view['problems'])     # 合计 53,800 未超过 20 万日元
    assert view['ready'] is True                                           # 只是警告，不拦出单证

    documents = _issue(client, access_token, dn_id)
    assert documents.status_code == 201
    ci_id, pl_id = [d['id'] for d in documents.get_json()['documents']]
    for doc_id in (ci_id, pl_id):
        pdf = client.get(f'/dn/{dn_id}/customs-documents/{doc_id}/file', headers=_h(access_token)).data
        text = _pdf_streams(pdf)
        assert b'950300000' not in text and b'3926.90.000' not in text   # CI / PL 不印
    ci_text = _pdf_streams(client.get(f'/dn/{dn_id}/customs-documents/{ci_id}/file', headers=_h(access_token)).data)
    assert b'9503.00' in ci_text

    # 只改不上单证的 jp_export_code：快照更新，但单证不作废
    customs['lines'][1]['jp_export_code'] = '392640000'
    response = client.put(f'/dn/{dn_id}/customs', json=customs, headers=_h(access_token))
    assert response.status_code == 200
    assert response.get_json()['voided_documents'] == []
    assert 'JP_EXPORT_CODE_MISMATCH' not in _codes(response.get_json()['problems'])
    assert _issue(client, access_token, dn_id).status_code == 200


@pytest.mark.parametrize('code, warned', [('12345678', True), ('95030000A', True), ('950300000', False)])
def test_jp_export_code_format(client, access_token, code, warned):
    customs = _customs()
    customs['lines'][0]['jp_export_code'] = code
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert ('JP_EXPORT_CODE_MISMATCH' in _codes(view['problems'], 'warning')) is warned


def test_jp_export_code_missing_warning_over_threshold(client, access_token):
    customs = _customs()
    customs['lines'][1]['unit_value'] = 100000                   # 2 x 100,000 + 3,600 + 8,200 > 200,000
    customs['lines'][0]['jp_export_code'] = '950300000'
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['totals']['invoice_total'] == 211800
    missing = [p for p in view['problems'] if p['code'] == 'JP_EXPORT_CODE_MISSING']
    assert [(p['goods_code'], p['level']) for p in missing] == [('G002', 'warning')]
    assert view['ready'] is True
    assert _issue(client, access_token, dn_id).status_code == 201


def test_non_latin_consignee_warns_and_still_renders(client, access_token):
    customs = _customs()
    customs['consignee']['name'] = '山田 太郎'
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert 'NON_LATIN_TEXT' in _codes(view['problems'], 'warning')
    assert view['ready'] is True
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 201
    ci = client.get(f"/dn/{dn_id}/customs-documents/{response.get_json()['documents'][0]['id']}/file",
                    headers=_h(access_token))
    assert b'HeiseiKakuGo-W5' in ci.data     # 非拉丁字符退回内置 CID 字体


# ---------------------------------------------------------------------------
# 运送保险（保险费 / 运送申告价额）
# ---------------------------------------------------------------------------

def test_insurance_stored_totals_and_printed_on_ci(client, access_token):
    customs = _customs(insurance_charge=1360, declared_value_carriage='45,600')   # 数字字符串照收
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['ready'] is True, view['problems']
    assert view['customs']['insurance_charge'] == 1360
    assert view['customs']['declared_value_carriage'] == 45600
    assert view['totals']['freight'] == 8200
    assert view['totals']['insurance'] == 1360
    assert view['totals']['invoice_total'] == 45600 + 8200 + 1360
    detail = client.get(f'/dn/{dn_id}', headers=_h(access_token)).get_json()
    assert detail['customs']['insurance_charge'] == 1360
    assert detail['customs']['declared_value_carriage'] == 45600

    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci_text = _pdf_streams(client.get(f"/dn/{dn_id}/customs-documents/{documents[0]['id']}/file",
                                      headers=_h(access_token)).data)
    for expected in (b'Freight', b'JPY 8,200', b'Insurance', b'JPY 1,360', b'Total Invoice Value', b'JPY 55,160'):
        assert expected in ci_text, expected
    # Insurance 行在 Freight 与 Total Invoice Value 之间
    assert ci_text.index(b'Freight') < ci_text.index(b'Insurance') < ci_text.index(b'Total Invoice Value')
    assert b'45,600' in ci_text                         # 货值（申告价额本身不单独印）
    assert b'Declared' not in ci_text
    pl_text = _pdf_streams(client.get(f"/dn/{dn_id}/customs-documents/{documents[1]['id']}/file",
                                      headers=_h(access_token)).data)
    assert b'Insurance' not in pl_text                  # PL 不变

    with client.application.app_context():
        fields = CustomsService.delivered_webhook_fields(db.session.get(DN, dn_id))
    assert fields['invoice_total'] == {'currency': 'JPY', 'goods_value': 45600, 'freight': 8200,
                                       'insurance': 1360, 'total': 55160}


def test_insurance_keys_with_null_accepted(client, access_token):
    """对接方不保价时两个键都带、值为 null：建 DN 与 PUT customs 都照常接受（等同没带）。"""
    _prepare(client)
    response = _create_dn(client, access_token, customs=_customs(insurance_charge=None, declared_value_carriage=None))
    assert response.status_code == 201, response.get_json()
    dn_id = response.get_json()['id']
    assert response.get_json()['customs']['insurance_charge'] is None
    assert response.get_json()['customs']['declared_value_carriage'] is None
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['totals']['insurance'] == 0
    assert view['totals']['invoice_total'] == 8200          # 还没打包：货值 0 + 运费

    response = client.put(f'/dn/{dn_id}/customs', json=_customs(insurance_charge=None, declared_value_carriage=None),
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['customs']['insurance_charge'] is None
    assert response.get_json()['customs']['declared_value_carriage'] is None


def test_insurance_zero_charge_not_printed(client, access_token):
    """投保但保险费为 0（申告价额在免费额度内）：不印 Insurance 行，申告价额照存。"""
    dn_id, _task_id = _export_dn_ready(client, access_token,
                                       customs=_customs(insurance_charge=0, declared_value_carriage=45600))
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['customs']['insurance_charge'] == 0
    assert view['customs']['declared_value_carriage'] == 45600
    assert view['totals']['insurance'] == 0 and view['totals']['invoice_total'] == 53800
    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci_text = _pdf_streams(client.get(f"/dn/{dn_id}/customs-documents/{documents[0]['id']}/file",
                                      headers=_h(access_token)).data)
    assert b'Insurance' not in ci_text and b'JPY 53,800' in ci_text


def test_old_snapshot_without_insurance_keeps_document_fingerprint(client, access_token):
    """旧快照（两个新字段为空）的单证数据与加字段前完全相同 → 指纹不变，已签发的单证不会失效。"""
    dn_id, _task_id = _export_dn_ready(client, access_token)          # 旧请求：不带这两个键
    first = _issue(client, access_token, dn_id).get_json()
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        data = CustomsService._document_data(dn, CustomsService.build_view(dn))
    assert set(data) == {
        'currency', 'invoice_number', 'reference', 'awb', 'carrier', 'incoterm', 'terms', 'export_reason',
        'country_of_export', 'destination', 'exporter', 'ship_from', 'consignee', 'items', 'show_net_weight',
        'totals', 'packages', 'signatory_name', 'signatory_title', 'declaration',
    }
    assert set(data['totals']) == {'quantity', 'goods_value', 'freight', 'invoice_total', 'package_count',
                                   'gross_weight_kg', 'net_weight_kg'}

    # 显式传 null 与不传等价：不作废、再签发沿用原版本
    response = client.put(f'/dn/{dn_id}/customs',
                          json=_customs(insurance_charge=None, declared_value_carriage=None), headers=_h(access_token))
    assert response.status_code == 200
    assert response.get_json()['voided_documents'] == []
    assert response.get_json()['documents_outdated'] is False
    again = _issue(client, access_token, dn_id)
    assert again.status_code == 200 and again.get_json()['version'] == first['version'] == 1


def test_insurance_change_voids_and_bumps_version(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    first_ids = [d['id'] for d in _issue(client, access_token, dn_id).get_json()['documents']]

    # 加上保险 → 现有单证作废（customs_changed），重新签发 v2 带 Insurance 行
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(insurance_charge=680, declared_value_carriage=45600),
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert sorted(response.get_json()['voided_documents']) == sorted(first_ids)
    assert response.get_json()['totals']['invoice_total'] == 53800 + 680
    second = _issue(client, access_token, dn_id)
    assert second.status_code == 201 and second.get_json()['version'] == 2
    ci_text = _pdf_streams(client.get(f"/dn/{dn_id}/customs-documents/{second.get_json()['documents'][0]['id']}/file",
                                      headers=_h(access_token)).data)
    assert b'Insurance' in ci_text and b'JPY 680' in ci_text and b'JPY 54,480' in ci_text

    # 只改运送申告价额（不印，但计入单证指纹）→ 同样作废、升版本
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(insurance_charge=680, declared_value_carriage=50000),
                          headers=_h(access_token))
    assert len(response.get_json()['voided_documents']) == 2
    assert _issue(client, access_token, dn_id).get_json()['version'] == 3

    # 同样的数据再 PUT：不作废
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(insurance_charge=680, declared_value_carriage=50000),
                          headers=_h(access_token))
    assert response.get_json()['voided_documents'] == []

    # 整体替换：不带这两个键 = 取消保险
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(), headers=_h(access_token))
    assert response.get_json()['customs']['insurance_charge'] is None
    assert response.get_json()['customs']['declared_value_carriage'] is None
    assert response.get_json()['totals']['insurance'] == 0
    assert len(response.get_json()['voided_documents']) == 2


def test_jp_export_code_threshold_uses_goods_value_only(client, access_token):
    """20 万日元按货值（FOB）判断：运费 / 保险费不算进去"""
    customs = _customs(insurance_charge=50000, declared_value_carriage=143600)
    customs['lines'][1]['unit_value'] = 70000                    # 货值 3,600 + 140,000 ≤ 200,000（加上运费保险才超）
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['totals']['goods_value'] == 143600
    assert view['totals']['invoice_total'] == 201800
    assert 'JP_EXPORT_CODE_MISSING' not in _codes(view['problems'])

    customs['lines'][1]['unit_value'] = 98300                    # 货值 3,600 + 196,600 = 200,200 > 200,000
    response = client.put(f'/dn/{dn_id}/customs', json=customs, headers=_h(access_token))
    assert response.status_code == 200
    assert response.get_json()['totals']['goods_value'] == 200200
    missing = [p for p in response.get_json()['problems'] if p['code'] == 'JP_EXPORT_CODE_MISSING']
    assert {(p['goods_code'], p['level']) for p in missing} == {('G001', 'warning'), ('G002', 'warning')}


# ---------------------------------------------------------------------------
# 发货拦截 / 锁定 / webhook
# ---------------------------------------------------------------------------

def _webhook_key(client):
    with client.application.app_context():
        api_key = APIKey(key=hashlib.sha256(b'customs-webhook').hexdigest(), system_name='customs_webhook_test',
                         permissions=['all_access'])
        api_key.webhook_url = 'http://127.0.0.1:9/webhook'   # emit 只落库，不实际发送
        db.session.add(api_key)
        db.session.commit()
        return api_key.id


def test_ship_requires_documents_then_locks(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        db.session.commit()

    blocked = _ship(client, access_token, task_id, tracking_number='AWB-1')
    assert blocked.status_code == 409
    assert blocked.get_json()['code'] == 16069
    assert blocked.get_json()['details'] == {'missing_documents': ['commercial_invoice', 'packing_list'],
                                             'outdated': False}
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'packed'
        assert db.session.get(DeliveryTask, task_id).status == 'in_progress'

    assert _issue(client, access_token, dn_id).status_code == 201
    shipped = client.put(f'/delivery/{task_id}/complete/', json={'tracking_number': 'AWB-1'}, headers=_h(access_token))
    assert shipped.status_code == 200, shipped.get_json()

    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'delivered'
        event = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.delivered').first()
        payload = event.payload
    assert payload['tracking_number'] == 'AWB-1'
    assert [d['doc_type'] for d in payload['customs_documents']] == ['commercial_invoice', 'packing_list']
    for doc in payload['customs_documents']:
        assert doc['version'] == 1 and len(doc['sha256']) == 64
        assert doc['download_path'] == f"/warehouse/dn/{dn_id}/customs-documents/{doc['id']}/file"
        assert set(doc) == {'id', 'doc_type', 'version', 'document_number', 'invoice_date', 'issued_at',
                            'sha256', 'size_bytes', 'file_name', 'download_path'}
    assert payload['packages'] == [
        {'package_no': 1, 'gross_weight_kg': 3.25, 'length_mm': 400, 'width_mm': 300, 'height_mm': 250},
        {'package_no': 2, 'gross_weight_kg': 3.25, 'length_mm': 400, 'width_mm': 300, 'height_mm': 250},
    ]
    assert payload['invoice_total'] == {'currency': 'JPY', 'goods_value': 45600, 'freight': 8200, 'insurance': 0,
                                        'total': 53800}

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['locked'] is True and view['ready'] is False
    for response in (
        client.put(f'/dn/{dn_id}/customs', json=_customs(), headers=_h(access_token)),
        client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(access_token)),
        _issue(client, access_token, dn_id),
    ):
        assert response.status_code == 409, response.get_json()
        assert response.get_json()['code'] == 16065


def test_tracking_saved_before_shipping_prints_awb_and_bumps_version(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    first = _issue(client, access_token, dn_id).get_json()
    assert first['version'] == 1
    ci = client.get(f"/dn/{dn_id}/customs-documents/{first['documents'][0]['id']}/file", headers=_h(access_token))
    assert b'AWB No.' not in _pdf_streams(ci.data)          # 还没有运单号：不印 AWB 行

    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': ' 7946 0000 0001 '},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == '7946 0000 0001'
    assert response.get_json()['status'] == 'pending'

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['documents_outdated'] is True           # 运单号计入单证数据：提示重出，不自动作废
    assert [d['version'] for d in view['current_documents']] == [1, 1]

    second = _issue(client, access_token, dn_id)
    assert second.status_code == 201 and second.get_json()['version'] == 2
    ci = client.get(f"/dn/{dn_id}/customs-documents/{second.get_json()['documents'][0]['id']}/file",
                    headers=_h(access_token))
    assert b'AWB No.' in _pdf_streams(ci.data) and b'7946 0000 0001' in _pdf_streams(ci.data)
    assert client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()['documents_outdated'] is False

    # 完成发货时运单号与 CI 上印的 AWB 不同 → 409 16080（先保存运单号并重出单证）；相同照常
    rejected = _ship(client, access_token, task_id, tracking_number='7946 0000 0002')
    assert rejected.status_code == 409 and rejected.get_json()['code'] == 16080
    assert rejected.get_json()['details'] == {'document_tracking_number': '7946 0000 0001',
                                              'tracking_number': '7946 0000 0002'}
    shipped = client.put(f'/delivery/{task_id}/complete/', json={'tracking_number': '7946 0000 0001'},
                         headers=_h(access_token))
    assert shipped.status_code == 200, shipped.get_json()
    assert shipped.get_json()['tracking_number'] == '7946 0000 0001'

    locked = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'X'}, headers=_h(access_token))
    assert locked.status_code == 409 and locked.get_json()['code'] == 16065


def test_is_export_visible_on_nested_dn_of_tasks(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    with client.application.app_context():
        packing_id = PackingTask.query.filter_by(dn_id=dn_id).first().id
    packing = client.get(f'/packing/{packing_id}', headers=_h(access_token)).get_json()
    assert packing['dn']['is_export'] is True
    assert len(packing['dn']['packages']) == 2
    delivery = client.get(f'/delivery/{task_id}', headers=_h(access_token)).get_json()
    assert delivery['dn']['is_export'] is True


def test_domestic_dn_ships_unchanged(client, access_token):
    _prepare(client)
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-DOM').get_json()['id']
    task_id = _force_packed(client, dn_id)
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        db.session.commit()

    response = _ship(client, access_token, task_id, tracking_number='DOM-1')
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        payload = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.delivered').first().payload
    assert set(payload) == {'dn_id', 'status', 'order_number', 'tracking_number', 'details'}

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['is_export'] is False and view['problems'] == [] and view['customs'] is None


def test_customs_endpoints_cross_company(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    doc_id = _issue(client, access_token, dn_id).get_json()['documents'][0]['id']
    headers_b, _warehouse_b = _make_company_b(client, company_admin=True)

    for method, url, body in (
        ('get', f'/dn/{dn_id}/customs', None),
        ('put', f'/dn/{dn_id}/customs', _customs()),
        ('get', f'/dn/{dn_id}/packages', None),
        ('put', f'/dn/{dn_id}/packages', {'packages': PACKAGES}),
        ('post', f'/dn/{dn_id}/customs-documents/issue', None),
        ('get', f'/dn/{dn_id}/customs-documents/', None),
        ('get', f'/dn/{dn_id}/customs-documents/{doc_id}/file', None),
    ):
        response = getattr(client, method)(url, json=body, headers=headers_b)
        assert response.status_code == 403, (method, url, response.get_json())

    assert client.get('/dn/99999/customs', headers=_h(access_token)).status_code == 404

    # 别的 DN 的单证 ID → 404 16071
    other_dn = _create_dn(client, access_token, customs=None, order_number='ORD-OTHER').get_json()['id']
    response = client.get(f'/dn/{other_dn}/customs-documents/{doc_id}/file', headers=_h(access_token))
    assert response.status_code == 404 and response.get_json()['code'] == 16071


# ---------------------------------------------------------------------------
# 公司 / 仓库出口资料、异常 details
# ---------------------------------------------------------------------------

def test_company_and_warehouse_export_profile(client, access_token):
    with client.application.app_context():
        company_id = get_company().id
        warehouse_id = get_warehouse().id

    response = client.put(f'/company/{company_id}', json={
        'legal_name_en': '  Example Trading Co., Ltd. ', 'address_en': '1-2-3 Example, Tokyo',
        'country_code': 'jp', 'tax_id_label': 'Corporate No.', 'tax_id': '1234567890123',
        'export_contact_name': 'Hanako Example', 'export_signatory_name': 'Taro Example',
        'export_signatory_title': 'Manager',
    }, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['legal_name_en'] == 'Example Trading Co., Ltd.'
    assert data['country_code'] == 'JP'
    assert data['export_signatory_title'] == 'Manager'

    bad = client.put(f'/company/{company_id}', json={'country_code': 'ZZ'}, headers=_h(access_token))
    assert bad.status_code == 400 and bad.get_json()['code'] == 14020
    too_long = client.put(f'/company/{company_id}', json={'tax_id': 'x' * 41}, headers=_h(access_token))
    assert too_long.status_code == 400 and too_long.get_json()['code'] == 14019

    response = client.put(f'/warehouse/{warehouse_id}', json={
        'address_en': '9-9 Warehouse St, Chiba', 'country_code': 'jp', 'contact_name_en': 'Ichiro Example',
    }, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['country_code'] == 'JP'
    assert response.get_json()['address_en'] == '9-9 Warehouse St, Chiba'

    created = client.post('/company/', json={'name': 'Company C'}, headers=_h(access_token))
    assert created.status_code == 201
    assert created.get_json()['country_code'] == 'JP'       # 列默认值

    # 仓库地址 ≠ 公司地址 → 单证出 Ship From
    _prepare(client)
    dn_id = _create_dn(client, access_token).get_json()['id']
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['exporter']['ship_from']['address_en'] == '9-9 Warehouse St, Chiba'


def test_error_response_details_backward_compatible(client, access_token):
    response = client.get('/dn/99999', headers=_h(access_token))
    assert response.status_code == 404
    assert 'details' not in response.get_json()


# ---------------------------------------------------------------------------
# HS 截断 / 数值上限 / 发货人非拉丁字符 / 字体嵌入 / 国家默认值 / 列表预加载
# ---------------------------------------------------------------------------

def test_ci_prints_only_first_six_hs_digits(client, access_token):
    customs = _customs()
    customs['lines'][0]['hs_code'] = '9503.00.0073'             # 10 位：CI 只印前 6 位
    customs['lines'][1]['hs_code'] = '392640000'                # 9 位
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    lines = {line['goods_code']: line for line in view['lines']}
    assert lines['G001']['hs_code_formatted'] == '9503.00.0073'   # 视图照常显示全部位数
    assert lines['G002']['hs_code_formatted'] == '3926.40.000'
    assert 'HS_CODE_MISSING' not in _codes(view['problems'])

    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci_text = _pdf_streams(client.get(f"/dn/{dn_id}/customs-documents/{documents[0]['id']}/file",
                                      headers=_h(access_token)).data)
    assert b'9503.00' in ci_text and b'3926.40' in ci_text
    assert b'9503.00.0073' not in ci_text and b'3926.40.000' not in ci_text


@pytest.mark.parametrize('path, value, field', [
    (('lines', 0, 'unit_value'), 1e28, 'customs.lines[0].unit_value'),
    (('lines', 0, 'unit_value'), '1' + '0' * 30, 'customs.lines[0].unit_value'),
    (('lines', 0, 'unit_value'), -1e13, 'customs.lines[0].unit_value'),
    (('lines', 0, 'total_value'), 10 ** 13, 'customs.lines[0].total_value'),
    (('lines', 0, 'quantity'), 2 ** 31, 'customs.lines[0].quantity'),
    (('freight_charge',), 2 ** 31, 'customs.freight_charge'),
    (('insurance_charge',), 2 ** 31, 'customs.insurance_charge'),
    (('declared_value_carriage',), '99999999999', 'customs.declared_value_carriage'),
])
def test_customs_numbers_over_limit_rejected(client, access_token, path, value, field):
    _prepare(client)
    customs = _customs()
    target = customs
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    response = _create_dn(client, access_token, customs=customs)
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16063
    assert response.get_json()['details'] == {'field': field}

    # PUT 也一样（以前入库后 GET customs / 出单证 500）
    dn_id = _create_dn(client, access_token, order_number='ORD-LIMIT-2').get_json()['id']
    response = client.put(f'/dn/{dn_id}/customs', json=customs, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 16063


def test_customs_numbers_at_limit_accepted(client, access_token):
    customs = _customs(freight_charge=2 ** 31 - 1, insurance_charge=2 ** 31 - 1,
                       declared_value_carriage=2 ** 31 - 1)
    customs['lines'][0]['unit_value'] = 10 ** 12
    customs['lines'][0]['total_value'] = '1000000000000'
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token))
    assert view.status_code == 200, view.get_json()
    assert view.get_json()['totals']['goods_value'] == 3 * 10 ** 12 + 42000
    assert _issue(client, access_token, dn_id).status_code == 201


def test_freight_with_cents_still_rejected(client, access_token):
    """运费 / 保险费 / 申告价额的列是 INTEGER：带分的币种（USD 等）也只能收整数"""
    _prepare(client)
    for key in ('freight_charge', 'insurance_charge', 'declared_value_carriage'):
        response = _create_dn(client, access_token, customs=_customs(currency='USD', **{key: 12.5}))
        assert response.status_code == 400 and response.get_json()['code'] == 16063, key
        assert response.get_json()['details'] == {'field': f'customs.{key}'}
    assert _create_dn(client, access_token, customs=_customs(currency='USD', freight_charge=12)).status_code == 201


def test_exporter_non_latin_text_is_error(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    with client.application.app_context():
        company = get_company()
        company.export_signatory_name = '山田 太郎'
        warehouse = get_warehouse()
        warehouse.address_en = '千葉県市川市 1-2-3'                # 仓库地址 ≠ 公司地址 → 印 Ship From
        db.session.commit()

    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    errors = {(p['code'], p['field']) for p in view['problems'] if p['level'] == 'error'}
    assert ('EXPORTER_NON_LATIN_TEXT', 'company.export_signatory_name') in errors
    assert ('EXPORTER_NON_LATIN_TEXT', 'warehouse.address_en') in errors
    assert 'NON_LATIN_TEXT' not in _codes(view['problems'])
    assert view['ready'] is False
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16068

    with client.application.app_context():
        get_company().export_signatory_name = 'Taro Example'
        get_warehouse().address_en = None
        db.session.commit()
    assert _issue(client, access_token, dn_id).status_code == 201


def test_exporter_full_width_phone_points_to_source_field(client, access_token):
    dn_id, _task_id = _export_dn_ready(client, access_token)
    with client.application.app_context():
        get_warehouse().phone = '０３-１２３４-５６７８'
        db.session.commit()
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert ('EXPORTER_NON_LATIN_TEXT', 'warehouse.phone') in {(p['code'], p.get('field')) for p in view['problems']}


def _vera_ttf():
    import os
    import reportlab
    return os.path.join(os.path.dirname(reportlab.__file__), 'fonts', 'Vera.ttf')


@pytest.mark.parametrize('font_path', [None, 'vera'])
def test_pdf_font_path_embeds_configured_ttf(client, access_token, monkeypatch, font_path):
    """CUSTOMS_PDF_FONT_PATH 配了 TTF → 字体子集嵌入 PDF（FontFile2），基础字体印不出的字符用它印；
    没配 → 保持现状：Helvetica 不嵌入，印不出的字符退回内置 CID 字体"""
    monkeypatch.setitem(client.application.config, 'CUSTOMS_PDF_FONT_PATH',
                        _vera_ttf() if font_path else None)
    customs = _customs()
    customs['consignee']['name'] = 'Łukasz Čapek'   # 拉丁字母，但不在 Helvetica（cp1252）里
    dn_id, _task_id = _export_dn_ready(client, access_token, customs=customs)
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert 'NON_LATIN_TEXT' not in _codes(view['problems'])
    documents = _issue(client, access_token, dn_id).get_json()['documents']
    ci_id = documents[0]['id']
    ci = client.get(f'/dn/{dn_id}/customs-documents/{ci_id}/file', headers=_h(access_token)).data
    if font_path:
        assert b'/FontFile2' in ci                 # TrueType 子集嵌入
        assert b'HeiseiKakuGo-W5' not in ci        # 没有退回不嵌入的 CID 字体
    else:
        assert b'/FontFile2' not in ci
        assert b'HeiseiKakuGo-W5' in ci


def test_country_code_null_not_masked_in_output(client, access_token):
    with client.application.app_context():
        company = get_company()
        warehouse = get_warehouse()
        company.country_code = None
        warehouse.country_code = None
        db.session.commit()
        company_id, warehouse_id = company.id, warehouse.id

    assert client.get(f'/company/{company_id}', headers=_h(access_token)).get_json()['country_code'] is None
    assert client.get(f'/warehouse/{warehouse_id}', headers=_h(access_token)).get_json()['country_code'] is None

    # 单证据此提示补齐（不再被输出默认值 JP 掩盖）
    _prepare(client)
    with client.application.app_context():
        get_company().country_code = None
        db.session.commit()
    dn_id = _create_dn(client, access_token).get_json()['id']
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert view['exporter']['country_code'] is None
    assert ('EXPORTER_PROFILE_INCOMPLETE', 'company.country_code') in {
        (p['code'], p.get('field')) for p in view['problems']}

    # 新建不传 country_code 仍走列默认值 JP
    created = client.post('/warehouse/', json={'name': 'Warehouse Z', 'company_id': company_id},
                          headers=_h(access_token))
    assert created.status_code == 201, created.get_json()
    assert created.get_json()['country_code'] == 'JP'


def test_dn_list_preloads_customs_and_details(client, access_token):
    from sqlalchemy import event
    _prepare(client)
    ids = [_create_dn(client, access_token, order_number=f'ORD-LIST-{i}').get_json()['id'] for i in range(3)]
    ids.append(_create_dn(client, access_token, customs=None, order_number='ORD-LIST-DOM').get_json()['id'])

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with client.application.app_context():
        engine = db.engine
    event.listen(engine, 'before_cursor_execute', record)
    try:
        response = client.get('/dn/?per_page=50', headers=_h(access_token))
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert response.status_code == 200, response.get_json()
    flags = {item['id']: item['is_export'] for item in response.get_json()['items']}
    assert [flags[i] for i in ids] == [True, True, True, False]
    assert sum('FROM dn_customs' in s for s in statements) == 1
    assert sum('FROM dn_details' in s for s in statements) == 1
