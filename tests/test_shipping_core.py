"""发货核心（doc/shipping-flow.md §2.2 / §3 / §4）：国内 / 跨境（route）、关税同盟、插件声明（scopes /
supports_customs / tracks_any_number / tracking_url_template）、国内件建运单不碰报关、箱子不要求报关、
SCOPE_NOT_SUPPORTED / CUSTOMS_DATA_MISSING / CUSTOMS_NOT_SUPPORTED、按运单记录判断的发货守卫（16079 / 16091）。

国内件用测试插件 TestPost（只接国内件、不带报关；注册到插件表里），FedEx 一律 mock。
"""
from .helpers import *
from .test_customs import PACKAGES, _create_dn, _export_dn_ready, _force_packed, _h, _prepare, _ship
from .test_carrier_shipment import _create, fedex  # noqa: F401  fedex 是 fixture

import pytest

from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.integrations import base as carrier_base, registry
from warehouse.carrier.integrations.base import CarrierAdapter, CarrierError
from warehouse.carrier.integrations.fedex.adapter import FedexAdapter
from warehouse.common.customs_unions import same_customs_union, union_of
from warehouse.dn.models import DNCarrierShipment, DNDocument
from warehouse.shipping.route import scope_of, shipping_route


class TestPost(CarrierAdapter):
    """测试用的国内承运商插件：只接国内件、不带报关；建单 / 取消 / 跟踪的调用记在 calls 里"""
    __test__ = False
    code = 'testpost'
    name = 'TestPost'
    portal_name = 'TestPost Online'
    scopes = ('domestic',)
    supports_customs = False
    supports_tracking = True
    tracks_any_number = False
    tracking_url_template = 'https://track.testpost.example/?no={tracking_number}'
    calls = []
    fail = None

    def create_shipment(self, shipment, label, *, deadline, document_ref=None):
        TestPost.calls.append(('create', shipment))
        if TestPost.fail is not None:
            raise TestPost.fail
        return {'tracking_number': 'TP000001', 'package_tracking_numbers': ['TP000001'], 'service_type': 'STANDARD',
                'ship_date': None, 'net_charge': None, 'currency': None, 'documents': ['label'],
                'transaction_id': 'tp-tx-1', 'alerts': []}

    def label_archive(self, documents, label):
        return {'content': b'%PDF-1.4 testpost label', 'extension': 'pdf', 'parts': []}

    def cancel_shipment(self, tracking_number, *, sender_country, deadline):
        TestPost.calls.append(('cancel', tracking_number))
        return {'result': 'confirmed', 'reason': None, 'transaction_id': 'tp-tx-c'}

    def track(self, tracking_numbers, *, deadline):
        TestPost.calls.append(('track', list(tracking_numbers)))
        return {n: {'status': 'label_created', 'code': 'OC', 'status_text': 'Label created', 'picked_up_at': None,
                    'delivered_at': None, 'estimated_delivery': None, 'events': [], 'error': None}
                for n in tracking_numbers}


@pytest.fixture
def testpost(client, monkeypatch):
    """注册 TestPost 插件，另建一个承运商「TestPost」并设好对接 → 承运商 id"""
    monkeypatch.setitem(registry(), 'testpost', TestPost)
    TestPost.calls = []
    TestPost.fail = None
    with client.application.app_context():
        base = get_carrier()
        carrier = Carrier(name='TestPost', code='testpost', company_id=base.company_id, created_by=base.created_by)
        db.session.add(carrier)
        db.session.commit()
        CarrierIntegrationService.save(carrier, {'adapter': 'testpost', 'enabled': True, 'settings': {}}, None)
        return carrier.id


def _domestic_ready(client, token, carrier_id=None, order_number='ORD-DOM-1'):
    """国内件（JP → JP，没有报关快照）：已打包、已录箱子，发货任务的承运商 = carrier_id → (dn_id, task_id)"""
    _prepare(client)
    with client.application.app_context():
        get_recipient().country = 'JP'
        db.session.commit()
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': PACKAGES}, headers=_h(token))
    assert response.status_code == 200, response.get_json()
    if carrier_id is not None:
        with client.application.app_context():
            db.session.get(DeliveryTask, task_id).carrier_id = carrier_id
            db.session.commit()
    return dn_id, task_id


def _blocker_codes(response):
    return [b['code'] for b in response.get_json()['details']['blockers']]


# ---------------------------------------------------------------------------
# route / 关税同盟 / 插件声明
# ---------------------------------------------------------------------------

def test_customs_unions():
    assert union_of('de') == 'EU' and union_of('JP') is None and union_of(None) is None
    assert same_customs_union('DE', 'FR') is True
    assert same_customs_union('DE', 'CH') is False
    assert same_customs_union('JP', 'JP') is False          # 不在登记的同盟里（国内由 international 判断）


def test_route_origin_destination_and_customs(client, access_token):
    _prepare(client)            # 公司国家 JP；仓库没填国家
    with client.application.app_context():
        recipient = get_recipient()
        recipient.country = 'JP'
        db.session.commit()
    domestic = _create_dn(client, access_token, customs=None, order_number='ORD-R1').get_json()['id']
    export = _create_dn(client, access_token, order_number='ORD-R2').get_json()['id']     # 报关快照收件国 DE
    with client.application.app_context():
        route = shipping_route(db.session.get(DN, domestic))
        assert route == {'origin_country': 'JP', 'destination_country': 'JP', 'international': False,
                         'requires_customs': False}
        assert scope_of(route) == 'domestic'
        route = shipping_route(db.session.get(DN, export))
        assert route == {'origin_country': 'JP', 'destination_country': 'DE', 'international': True,
                         'requires_customs': True}

        # 仓库的国家优先于公司的；EU 内部（DE → FR）跨境但不用报关
        warehouse = get_warehouse()
        warehouse.country_code = 'DE'
        get_recipient().country = 'fr'
        db.session.commit()
        route = shipping_route(db.session.get(DN, domestic))
        assert route == {'origin_country': 'DE', 'destination_country': 'FR', 'international': True,
                         'requires_customs': False}
        assert scope_of(route) == 'international'
        # 收件国未知按国内
        get_recipient().country = ''
        db.session.commit()
        route = shipping_route(db.session.get(DN, domestic))
        assert route['international'] is False and route['requires_customs'] is False
        # 有报关快照一律要报关（来源系统给了报关数据）
        warehouse.country_code = None
        db.session.get(DN, export).customs.recipient_country = 'JP'
        db.session.get(DN, export).customs.consignee = None
        db.session.commit()
        route = shipping_route(db.session.get(DN, export))
        assert route['international'] is False and route['requires_customs'] is True
        assert scope_of(route) == 'international'


def test_plugin_declarations(client, access_token):
    info = FedexAdapter.describe()
    assert info['scopes'] == ['international']
    assert info['supports_waybill'] is True and info['supports_customs'] is True
    assert info['tracks_any_number'] is True
    assert info['tracking_url_template'] == 'https://www.fedex.com/fedextrack/?trknbr={tracking_number}'
    base_info = CarrierAdapter.describe()
    assert base_info['scopes'] == ['domestic', 'international'] and base_info['supports_customs'] is False
    assert base_info['tracks_any_number'] is False and base_info['tracking_url_template'] is None
    response = client.get('/carrier/integrations', headers=_h(access_token))
    fedex_info = next(i for i in response.get_json() if i['code'] == 'fedex')
    assert fedex_info['scopes'] == ['international'] and fedex_info['supports_customs'] is True


# ---------------------------------------------------------------------------
# 箱子归发货：国内件也能录，不碰单证
# ---------------------------------------------------------------------------

def test_domestic_packages_without_customs(client, access_token):
    dn_id, _task_id = _domestic_ready(client, access_token)
    response = client.get(f'/dn/{dn_id}/packages', headers=_h(access_token))
    assert [p['package_no'] for p in response.get_json()['packages']] == [1, 2]
    changed = [dict(PACKAGES[0], gross_weight_kg=4.5)]
    response = client.put(f'/dn/{dn_id}/packages', json={'packages': changed}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['voided_documents'] == []
    assert [p['gross_weight_kg'] for p in response.get_json()['packages']] == [4.5]


# ---------------------------------------------------------------------------
# 国内件建运单：不碰报关
# ---------------------------------------------------------------------------

def test_domestic_api_waybill_without_customs(client, access_token, testpost):
    dn_id, task_id = _domestic_ready(client, access_token, testpost)
    view = client.get(f'/dn/{dn_id}/carrier-shipment', headers=_h(access_token)).get_json()
    assert view['can_create'] is True, view['blockers']
    assert view['route']['requires_customs'] is False

    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['shipment']['tracking_number'] == 'TP000001'
    assert response.get_json()['shipment']['source'] == 'api'
    kind, shipment = TestPost.calls[0]
    assert kind == 'create'
    assert shipment['route'] == {'origin_country': 'JP', 'destination_country': 'JP', 'international': False,
                                 'requires_customs': False}
    assert shipment['commodities'] is None and shipment['invoice'] is None and shipment['documents'] is None
    # 发件人用本地资料（公司名 / 仓库地址），收件人 = DN 收件人 + 收件地址（一个字符串）
    assert shipment['shipper']['company_name'] == 'Company A'
    assert shipment['shipper']['address_text'] == 'Warehouse Address A'
    assert shipment['shipper']['country_code'] == 'JP'
    assert shipment['recipient']['address_text'] == 'Hauptstrasse 1, 10115 Berlin'
    assert shipment['recipient']['person_name'] == 'John Doe' and shipment['recipient']['country_code'] == 'JP'
    assert [p['declared_value'] for p in shipment['packages']] == [None, None]
    with client.application.app_context():
        docs = {d.doc_type for d in DNDocument.query.filter_by(dn_id=dn_id, status='issued')}
        assert docs == {'shipping_label'}                   # 没有 CI / PL
        assert db.session.get(DeliveryTask, task_id).tracking_number == 'TP000001'

    # 取消照常（只调插件，不碰单证）
    response = client.post(f'/dn/{dn_id}/carrier-shipment/cancel', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert TestPost.calls[-1] == ('cancel', 'TP000001')


def test_scope_and_customs_blockers(client, access_token, testpost, fedex):
    # FedEx（只接跨境）× 国内件 → SCOPE_NOT_SUPPORTED，插件自己的检查不再跑
    dn_id, task_id = _domestic_ready(client, access_token, get_carrier_id(client))
    response = _create(client, access_token, dn_id)
    assert response.status_code == 409 and 'SCOPE_NOT_SUPPORTED' in _blocker_codes(response)

    # TestPost（只接国内、不带报关）× 跨境报关件 → SCOPE_NOT_SUPPORTED + CUSTOMS_NOT_SUPPORTED
    export_id, export_task = _export_dn_ready(client, access_token)
    with client.application.app_context():
        db.session.get(DeliveryTask, export_task).carrier_id = testpost
        db.session.commit()
    codes = _blocker_codes(_create(client, access_token, export_id))
    assert 'SCOPE_NOT_SUPPORTED' in codes and 'CUSTOMS_NOT_SUPPORTED' in codes
    assert TestPost.calls == []

    # 跨境（JP → US）却没有报关快照 → CUSTOMS_DATA_MISSING
    with client.application.app_context():
        get_recipient().country = 'US'
        db.session.commit()
    codes = _blocker_codes(_create(client, access_token, dn_id))
    assert 'CUSTOMS_DATA_MISSING' in codes and 'NOT_EXPORT' not in codes


def get_carrier_id(client):
    with client.application.app_context():
        return get_carrier().id


# ---------------------------------------------------------------------------
# 发货守卫按运单记录判断（不只看报关快照）
# ---------------------------------------------------------------------------

def test_unresolved_domestic_shipment_blocks_shipping(client, access_token, testpost):
    dn_id, task_id = _domestic_ready(client, access_token, testpost)
    TestPost.fail = CarrierError('timed out', step=carrier_base.STEP_CREATE, timeout=True, maybe_processed=True)
    response = _create(client, access_token, dn_id)
    assert response.status_code == 504, response.get_json()
    with client.application.app_context():
        assert DNCarrierShipment.query.filter_by(dn_id=dn_id).one().status == 'unknown'

    response = _ship(client, access_token, task_id)
    assert response.status_code == 409 and response.get_json()['code'] == 16079, response.get_json()

    # 有运单记录的国内 DN 也只认当前发货任务（16091）
    with client.application.app_context():
        first = db.session.get(DeliveryTask, task_id)
        newer = DeliveryTask(dn_id=dn_id, recipient_id=first.recipient_id, shipping_address=first.shipping_address,
                             expected_shipping_date=first.expected_shipping_date, status='pending',
                             created_by=first.created_by)
        db.session.add(newer)
        db.session.commit()
    response = client.put(f'/delivery/{task_id}/tracking', json={'tracking_number': 'X1'}, headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16091
