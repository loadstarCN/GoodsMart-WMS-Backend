"""海外 DN 在 FedEx 自动建运单（Ship API + ETD）：前置条件、请求体、写库、建单记录状态（pending / failed /
unknown / dismissed）、补偿取消、取消流程、时间预算、公司白名单、运单号 / 报关 / 发货任务锁定。

FedEx 一律 mock：替换 warehouse.dn.fedex_client._send，不联网。
"""
from .helpers import *
from .test_customs import (
    PACKAGES, _customs, _create_dn, _export_dn_ready, _force_packed, _h, _issue, _pdf_pages, _pdf_streams,
    _prepare, _ship,
)
from .test_isolation_outbound import _make_company_b

import base64
import hashlib
import io
import json
from datetime import datetime as _datetime, timedelta as _timedelta
from decimal import Decimal
from http.client import RemoteDisconnected
from urllib.parse import urlparse

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from warehouse.delivery.services import DeliveryTaskService
from warehouse.dn import carrier_services, fedex_client
from warehouse.dn.carrier_services import CarrierShipmentService
from warehouse.dn.customs_services import CustomsService
from warehouse.dn.fedex_shipment import (
    AddressError, allocate_commodity_weights, build_label_archive, consignee_address, fedex_currency, iso_currency,
    split_address_text, split_evenly, tin_type_for, wrap_lines,
)
from warehouse.dn.models import DNCarrierShipment, DNDocument

API_KEY = 'test-fedex-api-key'
SECRET = 'test-fedex-secret-key'
ACCOUNT = '000000000'


def _label_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(288, 432))
    pdf.drawString(20, 400, text)
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def _label_pdf_pages(*texts) -> bytes:
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(288, 432))
    for text in texts:
        pdf.drawString(20, 400, text)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def _ship_body(tracking='794600000001', packages=2, image=b'PDF', shipment_documents=None):
    pieces = []
    for seq in range(1, packages + 1):
        if image == b'PDF':
            content = (_label_pdf_pages('LABEL 1', 'AWB COPY 1', 'AWB COPY 2') if seq == 1
                       else _label_pdf_pages(f'LABEL {seq}'))
        else:
            content = image
        pieces.append({
            'trackingNumber': tracking if seq == 1 else f'{tracking[:-1]}{seq}',
            'masterTrackingNumber': tracking,
            'packageSequenceNumber': seq,
            'packageDocuments': [{'contentType': 'LABEL', 'docType': 'PDF' if image == b'PDF' else 'ZPLII',
                                  'copiesToPrint': 1, 'encodedLabel': base64.b64encode(content).decode()}],
        })
    return {
        'transactionId': 'tx-ship-0001',
        'output': {'transactionShipments': [{
            'masterTrackingNumber': tracking,
            'serviceType': 'INTERNATIONAL_ECONOMY',
            'shipDatestamp': '2026-09-30',
            'pieceResponses': list(reversed(pieces)),     # 故意乱序：按 packageSequenceNumber 排
            'completedShipmentDetail': {
                'masterTrackingId': {'trackingNumber': tracking},
                'shipmentRating': {
                    'actualRateType': 'PAYOR_ACCOUNT_SHIPMENT',
                    'shipmentRateDetails': [
                        {'rateType': 'PAYOR_LIST_SHIPMENT', 'totalNetCharge': 99999.0, 'currency': 'JYE'},
                        {'rateType': 'PAYOR_ACCOUNT_SHIPMENT', 'totalNetCharge': 12345.0, 'currency': 'JYE'},
                    ],
                },
            },
            'alerts': [{'code': 'SHIP.RECIPIENT.POSTALCITY.MISMATCH', 'alertType': 'NOTE', 'message': 'note'}],
            **({'shipmentDocuments': shipment_documents} if shipment_documents else {}),
        }]},
    }


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError('not json')
        return self._body


class FakeFedex:
    """记录请求、按路径回预设响应；某条路径可设成抛异常"""

    def __init__(self):
        self.calls = []
        self.responses = {
            '/oauth/token': [(200, {'access_token': 'token-1', 'token_type': 'bearer', 'expires_in': 3599})],
            '/ship/v1/shipments': [(200, _ship_body())],
            '/ship/v1/shipments/cancel': [(200, {'transactionId': 'tx-cancel-0001', 'output': {
                'cancelledShipment': True, 'cancelledHistory': True, 'successMessage': 'Success'}})],
            '/sandbox/documents/v1/etds/upload': [(201, {'output': {'meta': {
                'documentType': 'CI', 'docId': '090493e181586308', 'folderId': ['0b0493e1812f8921']}}})],
        }
        self.errors = {}

    def set(self, path, *responses):
        self.responses[path] = list(responses)

    def __call__(self, method, url, **kwargs):
        path = urlparse(url).path
        self.calls.append({'method': method, 'url': url, 'path': path, **kwargs})
        if path in self.errors:
            raise self.errors[path]
        queue = self.responses[path]
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(status, body)

    def of(self, path):
        return [c for c in self.calls if c['path'] == path]

    def ship_request(self, index=-1):
        return self.of('/ship/v1/shipments')[index]['json']


@pytest.fixture
def fedex(client, monkeypatch):
    fake = FakeFedex()
    monkeypatch.setattr(fedex_client, '_send', fake)
    fedex_client.clear_token_cache()
    with client.application.app_context():
        company_id = get_company().id
    client.application.config.update(
        FEDEX_API_BASE='https://apis-sandbox.fedex.com',
        FEDEX_API_KEY=API_KEY, FEDEX_SECRET_KEY=SECRET, FEDEX_ACCOUNT_NUMBER=ACCOUNT,
        FEDEX_ETD_ENABLED=False, FEDEX_DUTIES_PAYMENT_TYPE='RECIPIENT',
        FEDEX_ALLOWED_COMPANY_IDS=f' {company_id} ,',
    )
    yield fake
    fedex_client.clear_token_cache()


def _use_fedex(client, task_id, code='fedex'):
    with client.application.app_context():
        carrier = get_carrier()
        carrier.code = code
        db.session.get(DeliveryTask, task_id).carrier_id = carrier.id
        db.session.commit()


def _ready(client, token, **kwargs):
    dn_id, task_id = _export_dn_ready(client, token, **kwargs)
    _use_fedex(client, task_id)
    return dn_id, task_id


def _create(client, token, dn_id):
    return client.post(f'/dn/{dn_id}/carrier-shipment', headers=_h(token))


def _codes(response):
    return [b['code'] for b in response.get_json()['details']['blockers']]


def _counts(client, dn_id):
    with client.application.app_context():
        return {
            'shipments': DNCarrierShipment.query.filter_by(dn_id=dn_id).count(),
            'documents': DNDocument.query.filter_by(dn_id=dn_id).count(),
            'tracking': DeliveryTask.query.filter_by(dn_id=dn_id).first().tracking_number,
        }


def _records(client, dn_id):
    """该 DN 的建单记录（按 id）"""
    with client.application.app_context():
        return [{
            'id': r.id, 'status': r.status, 'reason': r.reason, 'tracking_number': r.tracking_number,
            'transaction_id': r.transaction_id, 'sender_country': r.sender_country,
            'cancel_transaction_id': r.cancel_transaction_id, 'error_message': r.error_message,
            'dismissed_by': r.dismissed_by, 'dismissed_at': r.dismissed_at,
        } for r in DNCarrierShipment.query.filter_by(dn_id=dn_id).order_by(DNCarrierShipment.id)]


def _states(client, dn_id):
    return [(r['status'], r['reason'], r['tracking_number']) for r in _records(client, dn_id)]


def _get(client, token, dn_id):
    return client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(token)).get_json()


def _cancel(client, token, dn_id):
    return client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(token))


def _dismiss(client, token, dn_id, body=None):
    return client.post(f'/dn/{dn_id}/carrier-shipment/dismiss',
                       json={'confirm': True} if body is None else body, headers=_h(token))


def _company_id(client):
    with client.application.app_context():
        return get_company().id


UNRESOLVED_KEYS = {'id', 'status', 'reason', 'tracking_number', 'transaction_id', 'created_at', 'updated_at'}


# ---------------------------------------------------------------------------
# 未启用 / 前置条件
# ---------------------------------------------------------------------------

def test_disabled_without_credentials(client, access_token, monkeypatch):
    calls = []
    monkeypatch.setattr(fedex_client, '_send', lambda *a, **k: calls.append(a))
    dn_id, task_id = _export_dn_ready(client, access_token)
    _use_fedex(client, task_id)

    view = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert view['enabled'] is False and view['can_create'] is False
    assert view['carrier'] == 'fedex' and view['shipment'] is None
    assert [b['code'] for b in view['blockers']] == ['FEDEX_NOT_CONFIGURED']

    response = _create(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16072
    assert _codes(response) == ['FEDEX_NOT_CONFIGURED']
    assert calls == []
    assert _counts(client, dn_id) == {'shipments': 0, 'documents': 0, 'tracking': None}


def test_ready_dn_can_create(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    view = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert view['enabled'] is True
    assert view['blockers'] == [] and view['can_create'] is True
    assert view['delivery_task_id'] == task_id
    assert view['etd_enabled'] is False and view['declared_value_carriage'] is None
    assert fedex.calls == []                    # 只读接口不调 FedEx


def test_blockers_listed_together(client, access_token, fedex):
    customs = _customs(consignee={
        'name': None, 'company': None, 'address_line1': 'Main Street 1', 'address_line2': None,
        'city': 'Springfield', 'state': None, 'postal_code': '12345', 'country': 'US', 'phone': None,
    }, recipient_country='US')
    customs['lines'][0]['hs_code'] = None
    dn_id, task_id = _export_dn_ready(client, access_token, customs=customs)
    _use_fedex(client, task_id, code='yamato')
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).tracking_number = 'MANUAL-1'
        company = get_company()
        company.legal_name_en = None
        db.session.commit()

    response = _create(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16072
    codes = _codes(response)
    for expected in ('CARRIER_NOT_FEDEX', 'TRACKING_NUMBER_EXISTS', 'HS_CODE_MISSING',
                     'EXPORTER_PROFILE_INCOMPLETE', 'RECIPIENT_ADDRESS_INVALID', 'RECIPIENT_NAME_MISSING',
                     'RECIPIENT_PHONE_MISSING'):
        assert expected in codes, (expected, codes)
    hs = next(b for b in response.get_json()['details']['blockers'] if b['code'] == 'HS_CODE_MISSING')
    assert hs['goods_code'] == 'G001' and hs['message']
    assert fedex.calls == []


def test_declared_value_capped_to_packed_goods_value(client, access_token, fedex):
    # 部分打包：已打包货值 = 1200 × 2 + 21000 × 2 = 44400 < 申告价额 50000 → 压到 44400（FedEx 拒收高于货值的）
    dn_id, _task_id = _ready(client, access_token, customs=_customs(declared_value_carriage=50000),
                             packed={'G001': 2, 'G002': 2})
    preview = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert preview['can_create'] is True
    assert [w['code'] for w in preview['warnings']] == ['DECLARED_VALUE_CAPPED']

    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['warnings'] == [{
        'code': 'DECLARED_VALUE_CAPPED',
        'message': data['warnings'][0]['message'],
        'requested': 50000, 'applied': 44400,
    }]
    assert '44400' in data['warnings'][0]['message']
    assert data['shipment']['declared_value'] == 44400 and data['declared_value_carriage'] == 50000
    shipment = fedex.ship_request()['requestedShipment']
    assert [p['declaredValue']['amount'] for p in shipment['requestedPackageLineItems']] == [22200, 22200]
    assert shipment['totalDeclaredValue'] == {'amount': 44400, 'currency': 'JYE'}
    # 建单后 GET 不再重复提示
    assert client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()['warnings'] == []


def test_declared_value_within_goods_value_unchanged(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token, customs=_customs(declared_value_carriage=45600))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201 and response.get_json()['warnings'] == []
    assert response.get_json()['shipment']['declared_value'] == 45600


def test_blockers_status_packages_task_and_domestic(client, access_token, fedex):
    _prepare(client)
    domestic = _create_dn(client, access_token, customs=None, order_number='ORD-DOM').get_json()['id']
    response = _create(client, access_token, domestic)
    assert _codes(response) == ['NOT_EXPORT']

    dn_id = _create_dn(client, access_token, order_number='ORD-EXP-2').get_json()['id']
    codes = _codes(_create(client, access_token, dn_id))
    for expected in ('NOT_PACKED', 'DELIVERY_TASK_MISSING', 'PACKAGES_MISSING'):
        assert expected in codes, (expected, codes)

    client.application.config['FEDEX_LABEL_THERMAL_IMAGE_TYPE'] = 'GIF'
    assert 'FEDEX_CONFIG_INVALID' in _codes(_create(client, access_token, dn_id))
    assert fedex.calls == []


# ---------------------------------------------------------------------------
# 建单：请求体
# ---------------------------------------------------------------------------

def test_request_body_fields(client, access_token, fedex):
    customs = _customs(declared_value_carriage=10001, insurance_charge=300)
    dn_id, task_id = _ready(client, access_token, customs=customs, packed={'G001': 2, 'G002': 2})
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()

    body = fedex.ship_request()
    assert body['labelResponseOptions'] == 'LABEL'
    assert body['accountNumber'] == {'value': ACCOUNT}
    shipment = body['requestedShipment']
    assert shipment['serviceType'] == 'INTERNATIONAL_ECONOMY'
    assert shipment['pickupType'] == 'USE_SCHEDULED_PICKUP'
    assert shipment['packagingType'] == 'YOUR_PACKAGING'
    assert len(shipment['shipDatestamp']) == 10
    assert shipment['shippingChargesPayment'] == {
        'paymentType': 'SENDER', 'payor': {'responsibleParty': {'accountNumber': {'value': ACCOUNT}}}}
    assert shipment['labelSpecification'] == {      # 默认 A4：激光打印机，上半页面单
        'imageType': 'PDF', 'labelStockType': 'PAPER_85X11_TOP_HALF_LABEL', 'labelFormatType': 'COMMON2D'}
    assert 'shipmentSpecialServices' not in shipment            # ETD 关闭

    shipper = shipment['shipper']
    assert shipper['contact']['companyName'] == 'Example Trading Co., Ltd.'
    assert shipper['contact']['personName'] == 'Hanako Example'
    assert shipper['contact']['phoneNumber'] == '123456789'
    assert shipper['address'] == {'streetLines': ['1-2-3 Example', 'Minato-ku'], 'city': 'Tokyo',
                                  'postalCode': '1050000', 'countryCode': 'JP'}
    assert shipper['tins'] == [{'number': '1234567890123', 'tinType': 'BUSINESS_NATIONAL'}]

    recipient = shipment['recipients'][0]
    assert recipient['contact'] == {'phoneNumber': '49300000000', 'personName': 'Max Mustermann',
                                    'companyName': 'Example GmbH'}
    assert recipient['address'] == {'streetLines': ['Hauptstrasse 1'], 'city': 'Berlin',
                                    'postalCode': '10115', 'countryCode': 'DE'}
    assert recipient['tins'] == [{'number': 'DE123456789', 'tinType': 'BUSINESS_UNION'}]   # EORI

    packages = shipment['requestedPackageLineItems']
    assert shipment['totalPackageCount'] == 2
    assert [p['sequenceNumber'] for p in packages] == [1, 2]
    assert packages[0]['weight'] == {'units': 'KG', 'value': 3.25}
    assert packages[0]['dimensions'] == {'length': 40, 'width': 30, 'height': 25, 'units': 'CM'}
    assert [p['declaredValue'] for p in packages] == [{'amount': 5001, 'currency': 'JYE'},
                                                       {'amount': 5000, 'currency': 'JYE'}]
    assert sum(p['declaredValue']['amount'] for p in packages) == 10001
    assert shipment['totalDeclaredValue'] == {'amount': 10001, 'currency': 'JYE'}
    assert packages[0]['customerReferences'] == [
        {'customerReferenceType': 'CUSTOMER_REFERENCE', 'value': 'ORD-EXPORT-1'},
        {'customerReferenceType': 'INVOICE_NUMBER', 'value': 'INV-TEST-001'},
    ]
    assert shipment['totalWeight'] == 14.4      # 6.5 kg → 磅

    clearance = shipment['customsClearanceDetail']
    assert clearance['dutiesPayment'] == {'paymentType': 'RECIPIENT'}
    assert clearance['isDocumentOnly'] is False
    assert clearance['commercialInvoice'] == {
        'termsOfSale': 'DAP', 'shipmentPurpose': 'SOLD',
        'customerReferences': [{'customerReferenceType': 'INVOICE_NUMBER', 'value': 'INV-TEST-001'}],
        'freightCharge': {'amount': 8200, 'currency': 'JYE'},
    }
    assert clearance['insuranceCharge'] == {'amount': 300, 'currency': 'JYE'}
    commodities = clearance['commodities']
    assert [c['quantity'] for c in commodities] == [2, 2]           # 已打包数量（G001 计划 3、打包 2）
    first = commodities[0]
    assert first['description'] == 'Plastic figure'
    assert first['harmonizedCode'] == '950300'
    assert first['countryOfManufacture'] == 'CN'                    # 商品主数据
    assert first['quantityUnits'] == 'PCS' and first['numberOfPieces'] == 1
    assert first['unitPrice'] == {'amount': 1200, 'currency': 'JYE'}
    assert first['customsValue'] == {'amount': 2400, 'currency': 'JYE'}
    assert first['weight'] == {'units': 'KG', 'value': 2.4}          # 1.2 kg × 2
    assert first['partNumber'] == 'G001'
    assert clearance['totalCustomsValue'] == {'amount': 2400 + 42000, 'currency': 'JYE'}

    raw = json.dumps(body)
    assert SECRET not in raw and API_KEY not in raw


def test_postal_code_key_kept_empty_and_state_for_us(client, access_token, fedex):
    consignee = {'name': 'Chan Tai Man', 'company': None, 'address_line1': 'Flat A, 1/F, 1 Example Road',
                 'address_line2': 'Mong Kok', 'city': 'Kowloon', 'state': None, 'postal_code': None,
                 'country': 'HK', 'phone': '+852 1234 5678'}
    dn_id, _task_id = _ready(client, access_token, customs=_customs(consignee=consignee, recipient_country='HK',
                                                                   recipient_tax_id=None))
    assert _create(client, access_token, dn_id).status_code == 201
    address = fedex.ship_request()['requestedShipment']['recipients'][0]['address']
    assert address['postalCode'] == '' and 'postalCode' in address
    assert address['streetLines'] == ['Flat A, 1/F, 1 Example Road', 'Mong Kok']
    assert 'stateOrProvinceCode' not in address
    assert 'tins' not in fedex.ship_request()['requestedShipment']['recipients'][0]

    us = consignee_address({'address_line1': '1 Main St', 'city': 'Austin', 'state': 'tx',
                            'postal_code': '73301'}, 'US')
    assert us['stateOrProvinceCode'] == 'TX'


def test_commodity_weight_allocated_from_gross_when_unit_weight_missing(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    with client.application.app_context():
        get_goods().weight = None                  # G001 没有单件重量
        db.session.commit()
    assert _create(client, access_token, dn_id).status_code == 201
    weights = [c['weight']['value'] for c in fedex.ship_request()['requestedShipment']
               ['customsClearanceDetail']['commodities']]
    # G002：1.2 × 2 = 2.4；G001 分摊 总毛重 6.5 − 2.4 = 4.1
    assert weights == [4.1, 2.4]


# ---------------------------------------------------------------------------
# 建单：写库
# ---------------------------------------------------------------------------

def test_create_saves_tracking_label_and_bumps_ci(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    first = _issue(client, access_token, dn_id).get_json()
    assert first['version'] == 1

    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['can_create'] is False and [b['code'] for b in data['blockers']] == ['SHIPMENT_EXISTS']
    assert data['alerts'][0]['code'] == 'SHIP.RECIPIENT.POSTALCITY.MISMATCH'
    shipment = data['shipment']
    assert shipment['tracking_number'] == '794600000001'
    assert shipment['package_tracking_numbers'] == ['794600000001', '794600000002']
    assert shipment['status'] == 'active' and shipment['carrier'] == 'fedex'
    assert shipment['service_type'] == 'INTERNATIONAL_ECONOMY'
    assert shipment['net_charge'] == 12345 and shipment['currency'] == 'JPY'   # 实际费率类型，JYE → JPY
    assert shipment['ship_date'] == '2026-09-30' and shipment['package_count'] == 2
    assert shipment['transaction_id'] == 'tx-ship-0001'
    assert shipment['created_by'] is not None and shipment['cancelled_at'] is None

    # 运单号存到发货任务；CI / PL 带 AWB 升版本
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).tracking_number == '794600000001'
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert [d['version'] for d in view['current_documents']] == [2, 2]
    assert view['documents_outdated'] is False
    ci_id = view['current_documents'][0]['id']
    ci = client.get(f'/dn/{dn_id}/customs-documents/{ci_id}/file', headers=_h(access_token))
    assert b'794600000001' in _pdf_streams(ci.data)

    # 面单：两箱合成一个 PDF，存 dn_documents（shipping_label）
    label = client.get(shipment['label_download_path'].replace('/warehouse', ''), headers=_h(access_token))
    assert label.status_code == 200 and label.mimetype == 'application/pdf'
    assert _pdf_pages(label.data) == 4                  # 第 1 箱 3 页（主面单 + 2 页 AWB COPY）+ 第 2 箱 1 页
    assert label.headers['Content-Disposition'].startswith('inline')
    assert label.headers['X-Content-SHA256'] == hashlib.sha256(label.data).hexdigest()
    assert shipment['label_format'] == 'A4' and shipment['image_type'] == 'PDF'
    assert shipment['label_stock_type'] == 'PAPER_85X11_TOP_HALF_LABEL'
    assert shipment['label_file_name'] == 'LABEL_794600000001.pdf'
    assert shipment['label_content_type'] == 'application/pdf'
    assert [(p['package_sequence'], p['content_type'], p['pages'], p['archived']) for p in shipment['label_parts']] \
        == [(1, 'LABEL', 3, True), (2, 'LABEL', 1, True)]
    assert data['default_label_format'] == 'A4'
    assert data['label_formats'] == {
        'A4': {'image_type': 'PDF', 'stock_type': 'PAPER_85X11_TOP_HALF_LABEL'},
        'THERMAL': {'image_type': 'PDF', 'stock_type': 'STOCK_4X6'},
    }
    labels = client.get(f'/dn/{dn_id}/customs-documents/?doc_type=shipping_label',
                        headers=_h(access_token)).get_json()
    assert len(labels) == 1 and labels[0]['id'] == shipment['label_document_id']
    assert labels[0]['document_number'] == '794600000001' and labels[0]['version'] == 1
    assert labels[0]['status'] == 'issued' and labels[0]['file_name'] == 'LABEL_794600000001.pdf'

    # 已有有效运单：不能再建
    again = _create(client, access_token, dn_id)
    assert again.status_code == 409 and _codes(again) == ['SHIPMENT_EXISTS']
    assert len(fedex.of('/ship/v1/shipments')) == 1

    # 面单不参与发货拦截；照常发货
    shipped = _ship(client, access_token, task_id)
    assert shipped.status_code == 200, shipped.get_json()
    assert shipped.get_json()['tracking_number'] == '794600000001'


def test_create_issues_documents_first_when_missing(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    assert client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()['current_documents'] == []
    assert _create(client, access_token, dn_id).status_code == 201
    with client.application.app_context():
        docs = DNDocument.query.filter_by(dn_id=dn_id).order_by(DNDocument.id).all()
        summary = [(d.doc_type, d.version, d.status) for d in docs]
    assert summary == [
        ('commercial_invoice', 1, 'void'), ('packing_list', 1, 'void'),      # 建单前签发
        ('commercial_invoice', 2, 'issued'), ('packing_list', 2, 'issued'),  # 带 AWB 升版本
        ('shipping_label', 1, 'issued'),
    ]


def test_etd_uploads_current_ci_and_references_it(client, access_token, fedex):
    client.application.config['FEDEX_ETD_ENABLED'] = True
    dn_id, _task_id = _ready(client, access_token)
    ci_meta = _issue(client, access_token, dn_id).get_json()['documents'][0]
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['etd_enabled'] is True
    assert response.get_json()['shipment']['etd_document_id'] == '090493e181586308'

    upload = fedex.of('/sandbox/documents/v1/etds/upload')[0]
    assert upload['url'] == 'https://documentapitest.prod.fedex.com/sandbox/documents/v1/etds/upload'
    assert upload['headers']['Authorization'] == 'Bearer token-1'
    document = json.loads(upload['data']['document'])
    assert document == {
        'workflowName': 'ETDPreshipment', 'carrierCode': 'FDXE', 'name': ci_meta['file_name'],
        'contentType': 'application/pdf',
        'meta': {'shipDocumentType': 'COMMERCIAL_INVOICE', 'originCountryCode': 'JP',
                 'destinationCountryCode': 'DE'},
    }
    name, content, mime = upload['files']['attachment']
    assert hashlib.sha256(content).hexdigest() == ci_meta['sha256'] and mime == 'application/pdf'

    special = fedex.ship_request()['requestedShipment']['shipmentSpecialServices']
    assert special == {
        'specialServiceTypes': ['ELECTRONIC_TRADE_DOCUMENTS'],
        'etdDetail': {'attachedDocuments': [{
            'documentType': 'COMMERCIAL_INVOICE', 'documentReference': 'INV-TEST-001',
            'description': 'Commercial Invoice', 'documentId': '090493e181586308'}]},
    }
    # 上传在建单之前
    paths = [c['path'] for c in fedex.calls]
    assert paths.index('/sandbox/documents/v1/etds/upload') < paths.index('/ship/v1/shipments')


def test_duties_paid_by_sender_has_payor(client, access_token, fedex):
    client.application.config['FEDEX_DUTIES_PAYMENT_TYPE'] = 'SENDER'
    dn_id, _task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    duties = fedex.ship_request()['requestedShipment']['customsClearanceDetail']['dutiesPayment']
    assert duties == {'paymentType': 'SENDER',
                      'payor': {'responsibleParty': {'accountNumber': {'value': ACCOUNT}}}}


# ---------------------------------------------------------------------------
# FedEx 错误：记录 failed（确定没建）/ unknown（结果不明）
# ---------------------------------------------------------------------------

def test_fedex_rejection_records_failed(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/ship/v1/shipments', (400, {
        'transactionId': 'tx-err-1',
        'errors': [{'code': 'SHIPMENT.USER.UNAUTHORIZED', 'message': 'Requested account is not authorized.'}],
    }))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502
    data = response.get_json()
    assert data['code'] == 16073
    assert 'SHIPMENT.USER.UNAUTHORIZED' in data['message']
    assert data['details']['errors'] == [{'code': 'SHIPMENT.USER.UNAUTHORIZED',
                                          'message': 'Requested account is not authorized.'}]
    assert data['details']['transaction_id'] == 'tx-err-1' and data['details']['http_status'] == 400
    assert data['details']['maybe_processed'] is False and data['details']['unresolved'] is None
    assert SECRET not in response.get_data(as_text=True) and API_KEY not in response.get_data(as_text=True)

    # 确定没有运单：记录 failed（rejected）留痕；建单前签发的 CI / PL 随 pending 记录已提交，照常有效
    records = _records(client, dn_id)
    assert [(r['status'], r['reason'], r['tracking_number'], r['transaction_id']) for r in records] == [
        ('failed', 'rejected', None, 'tx-err-1')]
    assert 'SHIPMENT.USER.UNAUTHORIZED' in records[0]['error_message']
    assert _counts(client, dn_id) == {'shipments': 1, 'documents': 2, 'tracking': None}
    view = _get(client, access_token, dn_id)
    assert view['can_create'] is True and view['unresolved'] is None and view['can_dismiss'] is False
    assert view['shipment'] is None

    # 不挡重试
    fedex.set('/ship/v1/shipments', (200, _ship_body()))
    assert _create(client, access_token, dn_id).status_code == 201


def test_fedex_permission_error_flagged(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/ship/v1/shipments', (403, {'transactionId': 'tx-403', 'errors': [
        {'code': 'FORBIDDEN.ERROR', 'message': 'We could not authorize your credentials.'}]}))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['details']['permission_denied'] is True
    assert _states(client, dn_id) == [('failed', 'rejected', None)]


def test_fedex_timeout_records_unknown_and_blocks_retry(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    response = _create(client, access_token, dn_id)
    assert response.status_code == 504
    data = response.get_json()
    assert data['code'] == 16074 and 'dismiss' in data['message']
    assert data['details']['maybe_processed'] is True
    unresolved = data['details']['unresolved']
    assert set(unresolved) == UNRESOLVED_KEYS
    assert (unresolved['status'], unresolved['reason'], unresolved['tracking_number']) == ('unknown', 'timeout', None)
    assert unresolved['created_at'] and unresolved['updated_at']
    assert fedex.of('/ship/v1/shipments/cancel') == []
    assert _states(client, dn_id) == [('unknown', 'timeout', None)]
    assert _counts(client, dn_id)['tracking'] is None

    view = _get(client, access_token, dn_id)
    assert view['unresolved'] == unresolved
    assert view['can_create'] is False and view['can_dismiss'] is True and view['blockers'] == []
    assert view['shipment'] is None

    # 结果不明时重试：不再调 FedEx，409 16079
    del fedex.errors['/ship/v1/shipments']
    again = _create(client, access_token, dn_id)
    assert again.status_code == 409 and again.get_json()['code'] == 16079
    assert again.get_json()['details']['unresolved']['id'] == unresolved['id']
    assert len(fedex.of('/ship/v1/shipments')) == 1


@pytest.mark.parametrize('failure, reason, transaction_id', [
    ('connection_lost', 'connection_error', None),
    ('http_500', 'server_error', 'tx-500'),
    ('http_503_html', 'server_error', None),
    ('non_json_200', 'bad_response', None),
    ('no_tracking_200', 'bad_response', 'tx-odd'),
    ('broken_body', 'connection_error', None),
])
def test_unclear_results_recorded_unknown(client, access_token, fedex, failure, reason, transaction_id):
    dn_id, _task_id = _ready(client, access_token)
    path = '/ship/v1/shipments'
    if failure == 'connection_lost':
        fedex.errors[path] = requests.ConnectionError(
            ProtocolError('Connection aborted.', RemoteDisconnected('Remote end closed connection')))
    elif failure == 'http_500':
        fedex.set(path, (500, {'transactionId': 'tx-500', 'errors': [
            {'code': 'INTERNAL.SERVER.ERROR', 'message': 'We encountered an unexpected error.'}]}))
    elif failure == 'http_503_html':
        fedex.set(path, (503, '<html>Service Unavailable</html>'))
    elif failure == 'non_json_200':
        fedex.set(path, (200, '<html>OK</html>'))
    elif failure == 'no_tracking_200':
        fedex.set(path, (200, {'transactionId': 'tx-odd', 'output': {'transactionShipments': []}}))
    else:
        fedex.errors[path] = requests.exceptions.ChunkedEncodingError('Connection broken: IncompleteRead')

    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    details = response.get_json()['details']
    assert details['maybe_processed'] is True
    assert (details['unresolved']['status'], details['unresolved']['reason']) == ('unknown', reason)
    assert details['unresolved']['transaction_id'] == transaction_id
    assert _states(client, dn_id) == [('unknown', reason, None)]
    assert fedex.of('/ship/v1/shipments/cancel') == []
    assert _create(client, access_token, dn_id).get_json()['code'] == 16079


@pytest.mark.parametrize('failure, status_code', [
    ('connect_timeout', 504),
    ('refused', 502),
    ('token_down', 502),
])
def test_requests_not_sent_record_failed(client, access_token, fedex, failure, status_code):
    dn_id, _task_id = _ready(client, access_token)
    if failure == 'connect_timeout':
        fedex.errors['/ship/v1/shipments'] = requests.ConnectTimeout('connect timed out')
    elif failure == 'refused':
        fedex.errors['/ship/v1/shipments'] = requests.ConnectionError(MaxRetryError(
            None, '/ship/v1/shipments', NewConnectionError(None, 'Failed to establish a new connection')))
    else:
        fedex.set('/oauth/token', (503, {'errors': [{'code': 'SERVICE.UNAVAILABLE.ERROR', 'message': 'down'}]}))
    response = _create(client, access_token, dn_id)
    assert response.status_code == status_code
    assert response.get_json()['details']['maybe_processed'] is False
    assert response.get_json()['details']['unresolved'] is None
    assert _states(client, dn_id) == [('failed', 'not_sent', None)]
    assert _get(client, access_token, dn_id)['can_create'] is True


def test_etd_upload_error_records_failed(client, access_token, fedex):
    client.application.config['FEDEX_ETD_ENABLED'] = True
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/sandbox/documents/v1/etds/upload', (400, {'errors': [{'code': '1001', 'message': 'bad file'}]}))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['details']['action'] == 'etd_upload'
    assert response.get_json()['details']['unresolved'] is None
    assert fedex.of('/ship/v1/shipments') == []
    assert _states(client, dn_id) == [('failed', 'etd_upload_failed', None)]
    assert _counts(client, dn_id)['documents'] == 2          # 建单前签发的 CI / PL
    # ETD 上传超时也不会有运单：failed
    fedex.errors['/sandbox/documents/v1/etds/upload'] = requests.ReadTimeout('read timed out')
    assert _create(client, access_token, dn_id).status_code == 504
    assert _states(client, dn_id)[-1] == ('failed', 'etd_upload_failed', None)
    assert _get(client, access_token, dn_id)['can_create'] is True


def test_pending_record_committed_before_fedex_is_called(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)
    seen = {}

    def spy(method, url, **kwargs):
        if urlparse(url).path == '/ship/v1/shipments':
            # 调 FedEx 时没有开着的事务（DN 行锁已释放），pending 记录已落库
            seen['in_transaction'] = db.session().in_transaction()
            seen['records'] = [(r.status, r.tracking_number, r.sender_country)
                               for r in DNCarrierShipment.query.filter_by(dn_id=dn_id)]
            db.session.rollback()
        return fedex(method, url, **kwargs)
    monkeypatch.setattr(fedex_client, '_send', spy)

    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    assert seen == {'in_transaction': False, 'records': [('pending', None, 'JP')]}
    records = _records(client, dn_id)
    assert [(r['status'], r['reason'], r['tracking_number'], r['sender_country']) for r in records] == [
        ('active', None, '794600000001', 'JP')]
    assert response.get_json()['unresolved'] is None and response.get_json()['can_dismiss'] is False


def test_db_failure_after_create_cancels_shipment(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)

    def broken(*args, **kwargs):
        raise RuntimeError('disk full')
    monkeypatch.setattr(CarrierShipmentService, '_store_label', staticmethod(broken))

    response = _create(client, access_token, dn_id)
    assert response.status_code == 500
    cancels = fedex.of('/ship/v1/shipments/cancel')
    assert len(cancels) == 1
    assert cancels[0]['method'] == 'PUT'
    assert cancels[0]['json'] == {'accountNumber': {'value': ACCOUNT}, 'senderCountryCode': 'JP',
                                  'deletionControl': 'DELETE_ALL_PACKAGES', 'trackingNumber': '794600000001'}
    # 写库回滚了，但补偿结果独立提交留痕：cancelled（compensated），带运单号
    records = _records(client, dn_id)
    assert [(r['status'], r['reason'], r['tracking_number'], r['transaction_id'], r['cancel_transaction_id'])
            for r in records] == [('cancelled', 'compensated', '794600000001', 'tx-ship-0001', 'tx-cancel-0001')]
    assert _counts(client, dn_id) == {'shipments': 1, 'documents': 2, 'tracking': None}
    view = _get(client, access_token, dn_id)
    assert view['can_create'] is True and view['unresolved'] is None
    assert (view['shipment']['status'], view['shipment']['reason']) == ('cancelled', 'compensated')


@pytest.mark.parametrize('cancel_response', [
    (500, {'transactionId': 'tx-c-500', 'errors': [{'code': 'INTERNAL.SERVER.ERROR', 'message': 'try later'}]}),
    (200, {'transactionId': 'tx-c-no', 'output': {'cancelledShipment': False,
                                                  'alerts': [{'code': 'X', 'message': 'Not cancellable'}]}}),
    (200, {'transactionId': 'tx-c-none', 'output': {}}),          # 没有明确说取消了：不确定
])
def test_compensation_failure_records_unknown_with_tracking(client, access_token, fedex, monkeypatch,
                                                            cancel_response):
    dn_id, task_id = _ready(client, access_token)
    monkeypatch.setattr(CarrierShipmentService, '_store_label',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full'))))
    fedex.set('/ship/v1/shipments/cancel', cancel_response)
    assert _create(client, access_token, dn_id).status_code == 500

    records = _records(client, dn_id)
    assert [(r['status'], r['reason'], r['tracking_number'], r['transaction_id']) for r in records] == [
        ('unknown', 'compensation_failed', '794600000001', 'tx-ship-0001')]
    assert records[0]['error_message']
    view = _get(client, access_token, dn_id)
    assert view['unresolved']['tracking_number'] == '794600000001' and view['can_dismiss'] is True
    assert _counts(client, dn_id)['tracking'] is None
    labels = client.get(f'/dn/{dn_id}/customs-documents/?doc_type=shipping_label', headers=_h(access_token))
    assert labels.get_json() == []
    # 运单可能还在 FedEx 上：不能重建、不能手工存号码
    assert _create(client, access_token, dn_id).get_json()['code'] == 16079
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': '794600000001'},
                          headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16079


def test_compensation_accepts_already_cancelled(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)
    monkeypatch.setattr(CarrierShipmentService, '_store_label',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full'))))
    fedex.set('/ship/v1/shipments/cancel', (404, {'transactionId': 'tx-c-404', 'errors': [
        {'code': 'TRACKING.TRACKINGNUMBER.NOTFOUND', 'message': 'Tracking number not found.'}]}))
    assert _create(client, access_token, dn_id).status_code == 500
    assert _states(client, dn_id) == [('cancelled', 'compensated', '794600000001')]


def test_unparseable_response_with_tracking_is_cancelled(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)

    def broken(body):
        raise ValueError('unexpected document structure')
    monkeypatch.setattr(carrier_services, 'parse_ship_response', broken)
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    assert response.get_json()['details']['unresolved'] is None
    assert 'cancelled' in response.get_json()['message']
    assert fedex.of('/ship/v1/shipments/cancel')[0]['json']['trackingNumber'] == '794600000001'
    assert _states(client, dn_id) == [('cancelled', 'compensated', '794600000001')]


@pytest.mark.parametrize('etd, expected', [(False, ('unknown', 'interrupted')), (True, ('failed', 'interrupted'))])
def test_worker_interrupted_during_fedex_call(client, access_token, fedex, etd, expected):
    # gunicorn 超时杀 worker 抛 SystemExit：except Exception 接不住，也要把记录落下来
    client.application.config['FEDEX_ETD_ENABLED'] = etd
    dn_id, _task_id = _ready(client, access_token)
    path = '/sandbox/documents/v1/etds/upload' if etd else '/ship/v1/shipments'
    fedex.errors[path] = SystemExit(1)
    with pytest.raises(SystemExit):
        _create(client, access_token, dn_id)
    assert [(r['status'], r['reason']) for r in _records(client, dn_id)] == [expected]


def test_dismiss_unknown_record(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    unresolved = _create(client, access_token, dn_id).get_json()['details']['unresolved']

    for body in ({}, {'confirm': False}, {'confirm': 'true'}, []):
        response = _dismiss(client, access_token, dn_id, body)
        assert response.status_code == 400 and response.get_json()['field'] == 'confirm', body
    assert _states(client, dn_id) == [('unknown', 'timeout', None)]

    response = _dismiss(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['unresolved'] is None and data['can_dismiss'] is False and data['can_create'] is True
    records = _records(client, dn_id)
    assert (records[0]['id'], records[0]['status'], records[0]['reason']) == (unresolved['id'], 'dismissed',
                                                                               'timeout')
    assert records[0]['dismissed_by'] is not None and records[0]['dismissed_at'] is not None

    again = _dismiss(client, access_token, dn_id)
    assert again.status_code == 409 and again.get_json()['code'] == 16075
    assert again.get_json()['details']['unresolved'] is None

    # 确认作废后可以重新建单
    del fedex.errors['/ship/v1/shipments']
    assert _create(client, access_token, dn_id).status_code == 201
    assert [r['status'] for r in _records(client, dn_id)] == ['dismissed', 'active']


def test_stale_pending_becomes_dismissable(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    with client.application.app_context():
        now = _datetime.now()
        record = DNCarrierShipment(dn_id=dn_id, carrier='fedex', status='pending', sender_country='JP',
                                   created_at=now, updated_at=now)
        db.session.add(record)
        db.session.commit()
        record_id = record.id

    def set_age(seconds):
        with client.application.app_context():
            db.session.get(DNCarrierShipment, record_id).created_at = _datetime.now() - _timedelta(seconds=seconds)
            db.session.commit()

    # 进行中：不能建单、不能确认作废、不能存运单号
    view = _get(client, access_token, dn_id)
    assert (view['unresolved']['status'], view['unresolved']['reason']) == ('pending', None)
    assert view['can_create'] is False and view['can_dismiss'] is False
    response = _create(client, access_token, dn_id)
    assert response.get_json()['code'] == 16079 and 'in progress' in response.get_json()['message']
    response = _dismiss(client, access_token, dn_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16075
    assert response.get_json()['details']['unresolved']['status'] == 'pending'
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'MANUAL-1'},
                          headers=_h(access_token))
    assert response.get_json()['code'] == 16079

    # FEDEX_PENDING_STALE_MINUTES = 1，但不短于 建单时限 90 + 补偿 15 + 余量 60 = 165 秒
    client.application.config['FEDEX_PENDING_STALE_MINUTES'] = 1
    set_age(150)
    assert _get(client, access_token, dn_id)['unresolved']['status'] == 'pending'
    set_age(170)
    view = _get(client, access_token, dn_id)
    assert (view['unresolved']['status'], view['unresolved']['reason']) == ('unknown', 'stale')
    assert view['can_dismiss'] is True
    assert _dismiss(client, access_token, dn_id).status_code == 200
    assert _states(client, dn_id) == [('dismissed', 'stale', None)]
    assert _create(client, access_token, dn_id).status_code == 201


def test_unresolved_locks_tracking_customs_packages_and_tasks(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    assert _create(client, access_token, dn_id).status_code == 504

    # 结果不明时存 / 改运单号 → 16079（防止操作员又在 FedEx 上手工建一张）；清空 / 不带号码照常
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'MANUAL-1'},
                          headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    assert response.get_json()['details']['unresolved']['status'] == 'unknown'
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': 'MANUAL-1'}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': None, 'remark': 'x'},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': None}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()

    # 报关数据 / 箱子（可能已随运单提交）→ 16076
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076
    details = response.get_json()['details']
    assert details['reason'] == 'CARRIER_SHIPMENT_OPEN' and details['carrier_shipment']['status'] == 'unknown'
    changed = [dict(PACKAGES[0], gross_weight_kg=4.5), PACKAGES[1]]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076

    # 发货任务不能新建 / 删除 → 16078
    response = client.post('/delivery/', json=_task_body(client, dn_id), headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details']['status'] == 'unknown'
    response = client.delete(f'/delivery/{task_id}', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078

    # 完成发货时手工带运单号 → 16079（不能绕过「结果不明」直接发走）；任务仍是进行中
    response = _ship(client, access_token, task_id, tracking_number='MANUAL-2')
    assert response.status_code == 409 and response.get_json()['code'] == 16079
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).status == 'in_progress'

    # 确认作废后都放开
    assert _dismiss(client, access_token, dn_id).status_code == 200
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'MANUAL-1'},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()


def _task_body(client, dn_id, **extra):
    with client.application.app_context():
        recipient_id = get_recipient().id
    body = {'dn_id': dn_id, 'recipient_id': recipient_id, 'shipping_address': 'Hauptstrasse 1, 10115 Berlin',
            'expected_shipping_date': _datetime.now().date().isoformat()}
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

def test_cancel_flow(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    created = _create(client, access_token, dn_id).get_json()['shipment']

    response = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['shipment']['status'] == 'cancelled'
    assert data['shipment']['cancelled_at'] and data['shipment']['cancelled_by']
    assert data['can_create'] is True and data['blockers'] == []

    cancel = fedex.of('/ship/v1/shipments/cancel')[0]
    assert cancel['method'] == 'PUT' and cancel['json']['trackingNumber'] == '794600000001'
    assert cancel['json']['deletionControl'] == 'DELETE_ALL_PACKAGES'

    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).tracking_number is None
        label = db.session.get(DNDocument, created['label_document_id'])
        assert label.status == 'void' and label.void_reason == 'shipment_cancelled'
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id).one()
        assert record.cancel_transaction_id == 'tx-cancel-0001'

    # CI / PL 去掉 AWB 重新签发
    view = client.get(f'/dn/{dn_id}/customs', headers=_h(access_token)).get_json()
    assert [d['version'] for d in view['current_documents']] == [3, 3]
    ci = client.get(f"/dn/{dn_id}/customs-documents/{view['current_documents'][0]['id']}/file",
                    headers=_h(access_token))
    assert b'794600000001' not in _pdf_streams(ci.data)

    again = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert again.status_code == 409 and again.get_json()['code'] == 16075

    # 取消后可以重新建（面单版本 2）
    fedex.set('/ship/v1/shipments', (200, _ship_body(tracking='794600000009', packages=2)))
    recreated = _create(client, access_token, dn_id)
    assert recreated.status_code == 201, recreated.get_json()
    assert recreated.get_json()['shipment']['tracking_number'] == '794600000009'
    labels = client.get(f'/dn/{dn_id}/customs-documents/?doc_type=shipping_label',
                        headers=_h(access_token)).get_json()
    assert sorted((d['version'], d['status']) for d in labels) == [(1, 'void'), (2, 'issued')]


def test_cancel_refused_by_fedex_keeps_everything(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    fedex.set('/ship/v1/shipments/cancel', (400, {'transactionId': 'tx-c-err', 'errors': [
        {'code': 'SHIPMENT.CANCEL.NOTALLOWED', 'message': 'Shipment already picked up.'}]}))
    response = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert response.status_code == 502 and response.get_json()['code'] == 16073
    assert 'Shipment already picked up.' in response.get_json()['message']

    fedex.set('/ship/v1/shipments/cancel', (200, {'transactionId': 'tx-c2', 'output': {
        'cancelledShipment': False, 'alerts': [{'code': 'X', 'message': 'Not cancellable'}]}}))
    response = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert response.status_code == 502 and 'Not cancellable' in response.get_json()['message']

    with client.application.app_context():
        assert DNCarrierShipment.query.filter_by(dn_id=dn_id).one().status == 'active'
        assert db.session.get(DeliveryTask, task_id).tracking_number == '794600000001'


def test_cancel_not_allowed_after_shipping(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    assert _ship(client, access_token, task_id).status_code == 200
    response = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16065
    assert fedex.of('/ship/v1/shipments/cancel') == []


def test_packages_locked_while_shipment_active(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    same = client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(access_token))
    assert same.status_code == 200
    changed = [dict(PACKAGES[0], gross_weight_kg=4.5), PACKAGES[1]]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076

    assert client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token)).status_code == 200
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# token 缓存 / 权限
# ---------------------------------------------------------------------------

def test_token_cached_and_refreshed_on_401(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    token_calls = fedex.of('/oauth/token')
    assert len(token_calls) == 1                                  # 建单 + 取消共用一个 token
    assert token_calls[0]['data'] == {'grant_type': 'client_credentials', 'client_id': API_KEY,
                                      'client_secret': SECRET}

    # token 被提前作废（401）：换一次 token 重试
    fedex.set('/oauth/token', (200, {'access_token': 'token-2', 'expires_in': 3599}))
    fedex.set('/ship/v1/shipments', (401, {'errors': [{'code': 'NOT.AUTHORIZED.ERROR', 'message': 'expired'}]}),
              (200, _ship_body(tracking='794600000010')))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    ships = fedex.of('/ship/v1/shipments')
    assert [c['headers']['Authorization'] for c in ships[-2:]] == ['Bearer token-1', 'Bearer token-2']
    assert len(fedex.of('/oauth/token')) == 2


def test_permissions_and_cross_company(client, access_token, access_operator_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    for method, url in (('get', f'/dn/{dn_id}/carrier-shipment'), ('post', f'/dn/{dn_id}/carrier-shipment'),
                        ('post', f'/dn/{dn_id}/carrier-shipment/cancel'),
                        ('post', f'/dn/{dn_id}/carrier-shipment/dismiss')):
        response = getattr(client, method)(url, headers=_h(access_operator_token))
        assert response.status_code == 403, (method, url)
    headers_b, _warehouse_b = _make_company_b(client, company_admin=True)
    for method, url in (('get', f'/dn/{dn_id}/carrier-shipment'), ('post', f'/dn/{dn_id}/carrier-shipment'),
                        ('post', f'/dn/{dn_id}/carrier-shipment/dismiss')):
        assert getattr(client, method)(url, headers=headers_b).status_code == 403
    assert fedex.calls == []

    # delivery_edit + dn_read 的员工可以建单
    with client.application.app_context():
        role = Role(name='shipper_role', description='shipper', is_active=True)
        for name in ('delivery_edit', 'dn_read'):
            permission = Permission.query.filter_by(name=name).first() or Permission(name=name, description=name)
            role.permissions.append(permission)
        staff = Staff(user_name='shipper', email='shipper@example.com', is_active=True,
                      company_id=get_company().id, created_by=get_admin_user().id)
        staff.set_password('password')
        staff.roles.append(role)
        staff.warehouses.append(get_warehouse())
        db.session.add_all([role, staff])
        db.session.commit()
        token = create_access_token(identity=staff)
        warehouse_id = get_warehouse().id
    headers = {'Authorization': f'Bearer {token}', 'X-WAREHOUSE-ID': str(warehouse_id)}
    assert client.get(f'/dn/{dn_id}/carrier-shipment', headers=headers).status_code == 200
    response = client.post(f'/dn/{dn_id}/carrier-shipment', headers=headers)
    assert response.status_code == 201, response.get_json()


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def test_address_and_value_helpers():
    assert split_address_text('1-2-3 Example, Minato-ku, Tokyo 105-0000, Japan', 'JP', None, 'Japan') == {
        'streetLines': ['1-2-3 Example', 'Minato-ku'], 'city': 'Tokyo', 'postalCode': '1050000',
        'countryCode': 'JP'}
    # 仓库邮编优先、粘在末段的国家名去掉、长地址折行
    result = split_address_text('Unit 5, 1234-5 Very Long Industrial Park Road Name, Example-machi\n'
                                'Sample-gun, Fukuoka 8100000 Japan', 'JP', '810-0000', 'Japan')
    assert result['city'] == 'Fukuoka' and result['postalCode'] == '8100000'
    assert all(len(line) <= 35 for line in result['streetLines']) and len(result['streetLines']) <= 3
    with pytest.raises(AddressError):
        split_address_text('Tokyo 105-0000', 'JP')
    with pytest.raises(AddressError):
        wrap_lines(['x' * 36])
    with pytest.raises(AddressError):
        wrap_lines(['word ' * 30])
    with pytest.raises(AddressError):
        consignee_address({'address_line1': '1 Main St', 'city': 'Austin', 'state': 'Texas'}, 'US')

    assert split_evenly(10001, 2) == [5001, 5000]
    assert split_evenly(10, 3) == [4, 3, 3] and sum(split_evenly(99999, 7)) == 99999
    assert tin_type_for('eori') == 'BUSINESS_UNION' and tin_type_for('CPF') == 'PERSONAL_NATIONAL'
    assert tin_type_for(None) == 'BUSINESS_NATIONAL'
    assert fedex_currency('JPY') == 'JYE' and fedex_currency('EUR') == 'EUR'
    assert iso_currency('JYE') == 'JPY' and iso_currency('JPY') == 'JPY'

    weights = allocate_commodity_weights(
        [{'quantity': 2, 'unit_weight_kg': 1.2}, {'quantity': 1, 'unit_weight_kg': None},
         {'quantity': 3, 'unit_weight_kg': None}], Decimal('6.4'))
    assert weights[0] == Decimal('2.4') and weights[1] == Decimal('1') and weights[2] == Decimal('3')
    # 已知净重超过毛重：按数量占比分摊毛重
    weights = allocate_commodity_weights(
        [{'quantity': 1, 'unit_weight_kg': 5}, {'quantity': 1, 'unit_weight_kg': None}], Decimal('4'))
    assert weights[1] == Decimal('2')


def _doc(content, content_type='LABEL', doc_type='PDF', source='package', sequence=1):
    return {'source': source, 'package_sequence': sequence, 'tracking_number': None,
            'content_type': content_type, 'doc_type': doc_type, 'copies': 1, 'content': content, 'url': None}


def test_label_archive_png_and_skips():
    from PIL import Image
    images = []
    for index, color in enumerate(('white', 'black'), start=1):
        buf = io.BytesIO()
        Image.new('RGB', (812, 1218), color).save(buf, format='PNG')
        images.append(_doc(buf.getvalue(), doc_type='PNG', sequence=index))
    images.append(_doc(None, content_type='COMMERCIAL_INVOICE', source='shipment', sequence=None))
    archive = build_label_archive(images, 'PNG')
    assert archive['extension'] == 'pdf' and archive['content_type'] == 'application/pdf'
    assert archive['content'].startswith(b'%PDF-') and _pdf_pages(archive['content']) == 2
    assert [(p['pages'], p['archived']) for p in archive['parts']] == [(1, True), (1, True), (None, False)]
    with pytest.raises(ValueError):
        build_label_archive([], 'PDF')
    with pytest.raises(ValueError):
        build_label_archive([_doc(b'^XA^XZ', doc_type='ZPLII')], 'PDF')     # 类型不对：一份都存不进


def test_thermal_label_format_uses_4x6_stock(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    response = client.post(f'/dn/{dn_id}/carrier-shipment', json={'label_format': 'thermal'},
                           headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    assert fedex.ship_request()['requestedShipment']['labelSpecification'] == {
        'imageType': 'PDF', 'labelStockType': 'STOCK_4X6', 'labelFormatType': 'COMMON2D'}
    shipment = response.get_json()['shipment']
    assert (shipment['label_format'], shipment['image_type'], shipment['label_stock_type']) == \
        ('THERMAL', 'PDF', 'STOCK_4X6')


def test_invalid_label_format_rejected(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    for value in ('LETTER', 5):
        response = client.post(f'/dn/{dn_id}/carrier-shipment', json={'label_format': value},
                               headers=_h(access_token))
        assert response.status_code == 400 and response.get_json()['code'] == 16077
    assert fedex.calls == []


def test_label_documents_ordered_label_auxiliary_other(client, access_token, fedex):
    extra = [
        {'contentType': 'COMMERCIAL_INVOICE', 'docType': 'PDF',
         'encodedLabel': base64.b64encode(_label_pdf_pages('INVOICE')).decode()},
        {'contentType': 'AUXILIARY', 'docType': 'PDF',
         'encodedLabel': base64.b64encode(_label_pdf_pages('AUX')).decode()},
        {'contentType': 'MERGED_LABEL_DOCUMENTS', 'docType': 'PDF', 'url': 'https://example.invalid/merged'},
    ]
    fedex.set('/ship/v1/shipments', (200, _ship_body(shipment_documents=extra)))
    dn_id, _task_id = _ready(client, access_token)
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    shipment = response.get_json()['shipment']
    assert [(p['source'], p['content_type'], p['pages'], p['archived']) for p in shipment['label_parts']] == [
        ('package', 'LABEL', 3, True), ('package', 'LABEL', 1, True),
        ('shipment', 'AUXILIARY', 1, True),
        ('shipment', 'COMMERCIAL_INVOICE', 1, True), ('shipment', 'MERGED_LABEL_DOCUMENTS', None, False),
    ]
    label = client.get(shipment['label_download_path'].replace('/warehouse', ''), headers=_h(access_token))
    from pypdf import PdfReader
    pages = [page.extract_text().strip() for page in PdfReader(io.BytesIO(label.data)).pages]
    assert pages == ['LABEL 1', 'AWB COPY 1', 'AWB COPY 2', 'LABEL 2', 'AUX', 'INVOICE']


def test_zpl_labels_stored_raw(client, access_token, fedex):
    client.application.config['FEDEX_LABEL_THERMAL_IMAGE_TYPE'] = 'ZPLII'
    zpl = b'^XA^FDLABEL^FS^XZ\n^XA^FDAWB COPY^FS^PQ2\n^XZ\n'
    fedex.set('/ship/v1/shipments', (200, _ship_body(packages=2, image=zpl)))
    dn_id, _task_id = _ready(client, access_token)
    response = client.post(f'/dn/{dn_id}/carrier-shipment', json={'label_format': 'THERMAL'},
                           headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    assert fedex.ship_request()['requestedShipment']['labelSpecification']['imageType'] == 'ZPLII'
    shipment = response.get_json()['shipment']
    assert shipment['image_type'] == 'ZPLII' and shipment['label_file_name'] == 'LABEL_794600000001.zpl'
    assert shipment['label_content_type'] == 'application/octet-stream'
    assert [p['pages'] for p in shipment['label_parts']] == [2, 2]
    label = client.get(shipment['label_download_path'].replace('/warehouse', ''), headers=_h(access_token))
    assert label.status_code == 200 and label.mimetype == 'application/octet-stream'
    assert label.headers['Content-Disposition'] == 'attachment; filename="LABEL_794600000001.zpl"'
    assert label.data == zpl + zpl                      # 各箱原始指令按顺序拼接


@pytest.mark.parametrize('zip_code', [None, '600-0000', '6000000'])
def test_real_warehouse_address_split(zip_code):
    # 线上仓库的英文地址：末段国名去掉、城市段里的邮编剥掉、一段一行、日本邮编 7 位数字
    address = split_address_text('4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN', 'JP', zip_code, 'Japan')
    assert address == {'streetLines': ['4-5-6 Sample-cho', 'Chuo-ku'], 'city': 'Osaka',
                       'postalCode': '6000000', 'countryCode': 'JP'}


def test_warehouse_address_used_as_shipper(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.address_en = '4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN'
        warehouse.zip_code = '600-0000'
        warehouse.country_code = 'JP'
        db.session.commit()
    assert _create(client, access_token, dn_id).status_code == 201
    assert fedex.ship_request()['requestedShipment']['shipper']['address'] == {
        'streetLines': ['4-5-6 Sample-cho', 'Chuo-ku'], 'city': 'Osaka', 'postalCode': '6000000',
        'countryCode': 'JP'}


def test_tracking_number_locked_to_active_shipment(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201

    # 保存运单号：别的号码 / 清空 → 409 16078；同一号码（空白不同）照常
    for value in ('999999999999', None, ''):
        response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': value},
                              headers=_h(access_token))
        assert response.status_code == 409 and response.get_json()['code'] == 16078, value
        assert response.get_json()['details']['tracking_number'] == '794600000001'
    same = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': '7946 0000 0001'},
                      headers=_h(access_token))
    assert same.status_code == 200, same.get_json()

    # 修改发货任务：别的号码 409；空值视为不改
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': 'OTHER-1'}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': None, 'remark': 'x'},
                          headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['tracking_number'] == '7946 0000 0001'

    # 完成发货：别的号码 409；相同或不传照常
    response = _ship(client, access_token, task_id, tracking_number='OTHER-2')
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).status == 'in_progress'
    # 上面存成了带空格的写法：与 CI 上印的 AWB 不再一字不差 → 单证过期，完成发货 409 16069
    response = client.put(f'/delivery/{task_id}/complete/', json={'tracking_number': '794600000001'},
                          headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16069
    assert response.get_json()['details'] == {'missing_documents': [], 'outdated': True}
    # 存回与 CI 一致的号码后照常
    same = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': '794600000001'},
                      headers=_h(access_token))
    assert same.status_code == 200, same.get_json()
    response = client.put(f'/delivery/{task_id}/complete/', json={'tracking_number': '794600000001'},
                          headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == '794600000001'


def test_complete_without_tracking_keeps_auto_number(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    response = _ship(client, access_token, task_id, tracking_number='')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['tracking_number'] == '794600000001'


def test_manual_tracking_allowed_after_cancel(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    assert client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token)).status_code == 200
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'MANUAL-9'},
                          headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['tracking_number'] == 'MANUAL-9'


# ---------------------------------------------------------------------------
# 公司白名单 / DDP / 发件国 / 已取消 / 时间预算 / 发货任务与报关锁定
# ---------------------------------------------------------------------------

def test_company_allow_list(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    company_id = _company_id(client)
    for value, codes, enabled in ((None, ['FEDEX_COMPANY_NOT_ALLOWED'], False),
                                  ('', ['FEDEX_COMPANY_NOT_ALLOWED'], False),
                                  (f'{company_id + 1000}', ['FEDEX_COMPANY_NOT_ALLOWED'], False),
                                  (f'abc, {company_id}', ['FEDEX_CONFIG_INVALID'], True)):
        client.application.config['FEDEX_ALLOWED_COMPANY_IDS'] = value
        view = _get(client, access_token, dn_id)
        assert [b['code'] for b in view['blockers']] == codes, value
        assert view['can_create'] is False and view['enabled'] is enabled, value
        response = _create(client, access_token, dn_id)
        assert response.status_code == 409 and _codes(response) == codes
    assert fedex.calls == []

    # 已建的运单：公司后来被移出名单也能取消
    client.application.config['FEDEX_ALLOWED_COMPANY_IDS'] = f'{company_id + 1000},{company_id}'
    assert _get(client, access_token, dn_id)['enabled'] is True
    assert _create(client, access_token, dn_id).status_code == 201
    client.application.config['FEDEX_ALLOWED_COMPANY_IDS'] = ''
    assert _cancel(client, access_token, dn_id).status_code == 200


def test_ddp_requires_sender_duties(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token, customs=_customs(incoterm='DDP'))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 409 and _codes(response) == ['INCOTERM_DUTIES_MISMATCH']
    blocker = response.get_json()['details']['blockers'][0]
    assert blocker['field'] == 'incoterm' and 'FEDEX_DUTIES_PAYMENT_TYPE' in blocker['message']
    assert fedex.calls == []
    client.application.config['FEDEX_DUTIES_PAYMENT_TYPE'] = 'SENDER'
    assert _create(client, access_token, dn_id).status_code == 201
    assert fedex.ship_request()['requestedShipment']['customsClearanceDetail']['commercialInvoice'][
        'termsOfSale'] == 'DDP'


def test_cancel_uses_sender_country_stored_at_creation(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    created = _create(client, access_token, dn_id).get_json()['shipment']
    assert created['sender_country'] == 'JP'
    # 建单后仓库地址改成别的国家：取消仍用建单时的发件国
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.address_en = 'Unit 1, 2 Example Road, Kowloon'
        warehouse.country_code = 'HK'
        db.session.commit()
    assert _cancel(client, access_token, dn_id).status_code == 200
    assert fedex.of('/ship/v1/shipments/cancel')[0]['json']['senderCountryCode'] == 'JP'


def test_cancel_without_stored_sender_country_uses_same_rule_as_create(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    _create(client, access_token, dn_id)
    with client.application.app_context():
        DNCarrierShipment.query.filter_by(dn_id=dn_id).one().sender_country = None     # 迁移前的记录
        company = get_company()
        company.country_code = 'JP'
        warehouse = get_warehouse()
        warehouse.address_en = None             # 没有仓库英文地址 → 用公司的国家（与建单同一口径）
        warehouse.country_code = 'HK'
        db.session.commit()
    assert _cancel(client, access_token, dn_id).status_code == 200
    assert fedex.of('/ship/v1/shipments/cancel')[0]['json']['senderCountryCode'] == 'JP'


@pytest.mark.parametrize('second_response', [
    (400, {'transactionId': 'tx-c-2', 'errors': [
        {'code': 'SHIPMENT.ALREADY.CANCELLED', 'message': 'Shipment has already been cancelled.'}]}),
    (404, {'transactionId': 'tx-c-2', 'errors': [
        {'code': 'TRACKING.TRACKINGNUMBER.NOTFOUND', 'message': 'Tracking number not found.'}]}),
    (200, {'transactionId': 'tx-c-2', 'output': {'cancelledShipment': False, 'alerts': [
        {'code': '8159', 'message': 'Shipment Delete was requested for a tracking number already in a deleted '
                                    'state.'}]}}),
])
def test_cancel_retry_after_local_failure(client, access_token, fedex, monkeypatch, second_response):
    dn_id, task_id = _ready(client, access_token)
    created = _create(client, access_token, dn_id).get_json()['shipment']

    # FedEx 取消成功，本地写库失败 → 回滚，记录仍 active
    original = CustomsService.issue_documents
    monkeypatch.setattr(CustomsService, 'issue_documents',
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db down'))))
    assert _cancel(client, access_token, dn_id).status_code == 500
    assert _states(client, dn_id) == [('active', None, '794600000001')]
    monkeypatch.setattr(CustomsService, 'issue_documents', staticmethod(original))

    # 再点取消：FedEx 回「已取消 / 查无此运单」→ 视为取消成功
    fedex.set('/ship/v1/shipments/cancel', second_response)
    response = _cancel(client, access_token, dn_id)
    assert response.status_code == 200, response.get_json()
    shipment = response.get_json()['shipment']
    assert (shipment['status'], shipment['reason']) == ('cancelled', 'already_cancelled')
    assert response.get_json()['can_create'] is True
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).tracking_number is None
        assert db.session.get(DNDocument, created['label_document_id']).status == 'void'
        assert DNCarrierShipment.query.filter_by(dn_id=dn_id).one().cancel_transaction_id == 'tx-c-2'


def test_already_cancelled_codes():
    matches = carrier_services._already_cancelled
    for code in ('SHIPMENT.ALREADY.CANCELLED', 'TRACKING.TRACKINGNUMBER.NOTFOUND', 'SHIPMENT.NOT.FOUND',
                 'TRACKINGNUMBER.DOES.NOT.EXIST', 'ALREADY.DELETED', '8159'):
        assert matches([{'code': code}]), code
    for code in ('SHIPMENT.CANCEL.NOTALLOWED', 'ACCOUNT.NUMBER.NOTFOUND', 'NOT.AUTHORIZED.ERROR', 'X', None, ''):
        assert not matches([{'code': code, 'message': 'shipment already cancelled'}]), code
    assert not matches(None) and not matches(['8159'])


def test_create_budget_limits_request_timeouts(client, access_token, fedex, monkeypatch):
    dn_id, _task_id = _ready(client, access_token)
    client.application.config.update(FEDEX_CREATE_BUDGET_SECONDS=20, FEDEX_CONNECT_TIMEOUT_SECONDS=5,
                                     FEDEX_TIMEOUT_SECONDS=30)
    now = [1000.0]
    step = [12]
    monkeypatch.setattr(fedex_client, '_clock', lambda: now[0])

    def slow(method, url, **kwargs):
        response = fedex(method, url, **kwargs)
        now[0] += step[0]
        return response
    monkeypatch.setattr(fedex_client, '_send', slow)

    # 每个请求耗 12 秒：token 请求剩 20 秒 → (5, 15)；建单请求只剩 8 秒 → (4, 4)
    assert _create(client, access_token, dn_id).status_code == 201
    assert fedex.of('/oauth/token')[0]['timeout'] == (5.0, 15.0)
    assert fedex.of('/ship/v1/shipments')[0]['timeout'] == (4.0, 4.0)
    assert _cancel(client, access_token, dn_id).status_code == 200

    # 每个请求耗 16 秒：token 之后只剩 4 秒（< 5）→ 建单请求不发，failed（budget_exhausted），504
    fedex_client.clear_token_cache()
    step[0] = 16
    response = _create(client, access_token, dn_id)
    assert response.status_code == 504 and response.get_json()['code'] == 16074
    assert response.get_json()['details']['budget_exhausted'] is True
    assert response.get_json()['details']['maybe_processed'] is False
    assert len(fedex.of('/ship/v1/shipments')) == 1
    assert _states(client, dn_id)[-1] == ('failed', 'budget_exhausted', None)


def test_fedex_client_error_classification(client, fedex):
    """结果不明（maybe_processed）与确定失败的划分"""
    path = '/ship/v1/shipments'
    cases = [
        (lambda: fedex.errors.__setitem__(path, requests.ReadTimeout('read')), True, True),
        (lambda: fedex.errors.__setitem__(path, requests.ConnectTimeout('connect')), False, True),
        (lambda: fedex.errors.__setitem__(path, requests.ConnectionError(MaxRetryError(
            None, path, NewConnectionError(None, 'refused')))), False, False),
        (lambda: fedex.errors.__setitem__(path, requests.ConnectionError(
            ProtocolError('Connection aborted.', RemoteDisconnected('closed')))), True, False),
        (lambda: fedex.errors.__setitem__(path, requests.exceptions.ChunkedEncodingError('broken')), True, False),
        (lambda: fedex.set(path, (500, {'errors': [{'code': 'INTERNAL.SERVER.ERROR', 'message': 'x'}]})), True, False),
        (lambda: fedex.set(path, (422, {'errors': [{'code': 'SHIPMENT.VALIDATION', 'message': 'x'}]})), False, False),
        (lambda: fedex.set(path, (200, 'not json')), True, False),
    ]
    with client.application.app_context():
        for index, (arrange, maybe_processed, timeout) in enumerate(cases):
            fedex.errors.clear()
            arrange()
            with pytest.raises(fedex_client.FedexError) as caught:
                fedex_client.create_shipment({})
            assert (caught.value.maybe_processed, caught.value.timeout) == (maybe_processed, timeout), index
            assert caught.value.what == fedex_client.CREATE_SHIPMENT

        # token 请求失败：业务请求没发，结果确定
        fedex.errors.clear()
        fedex_client.clear_token_cache()
        fedex.set('/oauth/token', (500, {'errors': [{'code': 'INTERNAL.SERVER.ERROR', 'message': 'x'}]}))
        with pytest.raises(fedex_client.FedexError) as caught:
            fedex_client.create_shipment({})
        assert caught.value.maybe_processed is False and caught.value.what == fedex_client.TOKEN_REQUEST

        # 时间预算不够：不发请求
        calls = len(fedex.calls)
        with pytest.raises(fedex_client.FedexError) as caught:
            fedex_client.create_shipment({}, deadline=fedex_client.clock() + 3)
        assert caught.value.budget_exhausted is True and caught.value.maybe_processed is False
        assert len(fedex.calls) == calls


def test_delivery_tasks_and_customs_locked_while_shipment_active(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201

    # 新建发货任务会成为「当前任务」绕过运单号锁定 → 16078；删除也不行
    response = client.post('/delivery/', json=_task_body(client, dn_id), headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert response.get_json()['details'] == {'tracking_number': '794600000001', 'carrier': 'fedex',
                                              'status': 'active'}
    response = client.post('/delivery/', json=_task_body(client, dn_id, tracking_number='OTHER-1'),
                           headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    response = client.delete(f'/delivery/{task_id}', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16078

    # 报关数据已随运单提交 → 16076（与改箱子同码）
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16076
    assert response.get_json()['details']['carrier_shipment']['tracking_number'] == '794600000001'

    # 取消后都放开
    assert _cancel(client, access_token, dn_id).status_code == 200
    response = client.put(f'/dn/{dn_id}/customs', json=_customs(freight_charge=9000), headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    response = client.post('/delivery/', json=_task_body(client, dn_id), headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
