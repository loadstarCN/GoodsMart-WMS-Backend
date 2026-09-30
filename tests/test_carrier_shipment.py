"""海外 DN 在 FedEx 自动建运单（Ship API + ETD）：前置条件、请求体、写库、错误不写库、补偿取消、取消流程。

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
from decimal import Decimal
from urllib.parse import urlparse

import pytest
import requests

from warehouse.delivery.services import DeliveryTaskService
from warehouse.dn import fedex_client
from warehouse.dn.carrier_services import CarrierShipmentService
from warehouse.dn.customs_services import CustomsService
from warehouse.dn.fedex_shipment import (
    AddressError, allocate_commodity_weights, consignee_address, fedex_currency, iso_currency, merge_labels,
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


def _ship_body(tracking='794600000001', packages=2, image=b'PDF'):
    pieces = []
    for seq in range(1, packages + 1):
        content = _label_pdf(f'LABEL {seq}') if image == b'PDF' else image
        pieces.append({
            'trackingNumber': tracking if seq == 1 else f'{tracking[:-1]}{seq}',
            'masterTrackingNumber': tracking,
            'packageSequenceNumber': seq,
            'packageDocuments': [{'contentType': 'LABEL', 'docType': 'PDF', 'copiesToPrint': 1,
                                  'encodedLabel': base64.b64encode(content).decode()}],
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
    client.application.config.update(
        FEDEX_API_BASE='https://apis-sandbox.fedex.com',
        FEDEX_API_KEY=API_KEY, FEDEX_SECRET_KEY=SECRET, FEDEX_ACCOUNT_NUMBER=ACCOUNT,
        FEDEX_ETD_ENABLED=False, FEDEX_DUTIES_PAYMENT_TYPE='RECIPIENT', FEDEX_LABEL_IMAGE_TYPE='PDF',
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


def test_declared_value_above_customs_value_blocked(client, access_token, fedex):
    # 已打包货值 = 1200 × 3 + 21000 × 2 = 45600；FedEx 不收申告价额高于货值的运单
    dn_id, _task_id = _ready(client, access_token, customs=_customs(declared_value_carriage=45601))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 409 and _codes(response) == ['DECLARED_VALUE_EXCEEDS_CUSTOMS_VALUE']
    assert fedex.calls == []


def test_blockers_status_packages_task_and_domestic(client, access_token, fedex):
    _prepare(client)
    domestic = _create_dn(client, access_token, customs=None, order_number='ORD-DOM').get_json()['id']
    response = _create(client, access_token, domestic)
    assert _codes(response) == ['NOT_EXPORT']

    dn_id = _create_dn(client, access_token, order_number='ORD-EXP-2').get_json()['id']
    codes = _codes(_create(client, access_token, dn_id))
    for expected in ('NOT_PACKED', 'DELIVERY_TASK_MISSING', 'PACKAGES_MISSING'):
        assert expected in codes, (expected, codes)

    with client.application.app_context():
        client.application.config['FEDEX_LABEL_IMAGE_TYPE'] = 'ZPLII'
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
    assert shipment['labelSpecification'] == {
        'imageType': 'PDF', 'labelStockType': 'PAPER_4X6', 'labelFormatType': 'COMMON2D'}
    assert 'shipmentSpecialServices' not in shipment            # ETD 关闭

    shipper = shipment['shipper']
    assert shipper['contact']['companyName'] == 'Example Trading Co., Ltd.'
    assert shipper['contact']['personName'] == 'Hanako Example'
    assert shipper['contact']['phoneNumber'] == '123456789'
    assert shipper['address'] == {'streetLines': ['1-2-3 Example, Minato-ku'], 'city': 'Tokyo',
                                  'postalCode': '105-0000', 'countryCode': 'JP'}
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
    assert _pdf_pages(label.data) == 2
    assert label.headers['X-Content-SHA256'] == hashlib.sha256(label.data).hexdigest()
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
# FedEx 错误：不写库
# ---------------------------------------------------------------------------

def test_fedex_error_writes_nothing(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/ship/v1/shipments', (400, {
        'transactionId': 'tx-err-1',
        'errors': [{'code': 'SHIPMENT.USER.UNAUTHORIZED', 'message': 'Requested account is not authorized.'}],
    }))
    before = _counts(client, dn_id)
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502
    data = response.get_json()
    assert data['code'] == 16073
    assert 'SHIPMENT.USER.UNAUTHORIZED' in data['message']
    assert data['details']['errors'] == [{'code': 'SHIPMENT.USER.UNAUTHORIZED',
                                          'message': 'Requested account is not authorized.'}]
    assert data['details']['transaction_id'] == 'tx-err-1' and data['details']['http_status'] == 400
    assert SECRET not in response.get_data(as_text=True) and API_KEY not in response.get_data(as_text=True)
    # 建单前签发的 CI / PL 也随事务回滚
    assert _counts(client, dn_id) == before == {'shipments': 0, 'documents': 0, 'tracking': None}


def test_fedex_permission_error_flagged(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/ship/v1/shipments', (403, {'transactionId': 'tx-403', 'errors': [
        {'code': 'FORBIDDEN.ERROR', 'message': 'We could not authorize your credentials.'}]}))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['details']['permission_denied'] is True
    assert _counts(client, dn_id)['shipments'] == 0


def test_fedex_timeout_returns_504(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    fedex.errors['/ship/v1/shipments'] = requests.ReadTimeout('read timed out')
    response = _create(client, access_token, dn_id)
    assert response.status_code == 504
    assert response.get_json()['code'] == 16074
    assert response.get_json()['details']['maybe_processed'] is True
    assert _counts(client, dn_id) == {'shipments': 0, 'documents': 0, 'tracking': None}
    assert fedex.of('/ship/v1/shipments/cancel') == []


def test_etd_upload_error_writes_nothing(client, access_token, fedex):
    client.application.config['FEDEX_ETD_ENABLED'] = True
    dn_id, _task_id = _ready(client, access_token)
    fedex.set('/sandbox/documents/v1/etds/upload', (400, {'errors': [{'code': '1001', 'message': 'bad file'}]}))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 502 and response.get_json()['details']['action'] == 'etd_upload'
    assert fedex.of('/ship/v1/shipments') == []
    assert _counts(client, dn_id)['documents'] == 0


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
    assert _counts(client, dn_id) == {'shipments': 0, 'documents': 0, 'tracking': None}


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
                        ('post', f'/dn/{dn_id}/carrier-shipment/cancel')):
        response = getattr(client, method)(url, headers=_h(access_operator_token))
        assert response.status_code == 403, (method, url)
    headers_b, _warehouse_b = _make_company_b(client, company_admin=True)
    for method, url in (('get', f'/dn/{dn_id}/carrier-shipment'), ('post', f'/dn/{dn_id}/carrier-shipment')):
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
        'streetLines': ['1-2-3 Example, Minato-ku'], 'city': 'Tokyo', 'postalCode': '105-0000',
        'countryCode': 'JP'}
    # 仓库邮编优先、粘在末段的国家名去掉、长地址折行
    result = split_address_text('Unit 5, 1234-5 Very Long Industrial Park Road Name, Example-machi\n'
                                'Sample-gun, Fukuoka 8100000 Japan', 'JP', '810-0000', 'Japan')
    assert result['city'] == 'Fukuoka' and result['postalCode'] == '810-0000'
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


def test_merge_png_labels_into_pdf():
    from PIL import Image
    images = []
    for color in ('white', 'black'):
        buf = io.BytesIO()
        Image.new('RGB', (812, 1218), color).save(buf, format='PNG')
        images.append({'sequence': len(images) + 1, 'content': buf.getvalue(), 'doc_type': 'PNG'})
    pdf = merge_labels(images, 'PNG')
    assert pdf.startswith(b'%PDF-') and _pdf_pages(pdf) == 2
    with pytest.raises(ValueError):
        merge_labels([], 'PDF')
