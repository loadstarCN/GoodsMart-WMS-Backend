"""承运商的对接方式（doc/shipping-flow.md §2.1，warehouse/carrier/methods.py）

- GET / PUT /carrier/<id>（和列表）带 waybill_method / pickup_method / pickup_schedule / pickup_contact /
  tracking_method / tracking_url_template / auto_sign_days，只读的 available_methods
- api 只有在线对接启用、且插件声明了对应能力时可选；选了不可选的 → 400 16135（field 指明哪一项）；没改的值不重新校验
- 设置在线对接时跟上：新建 / 重新启用后默认值改成 api；删掉对接退回默认值
- 查询页：承运商的 tracking_url_template，没填用插件内置的
"""
from .helpers import *
from .test_customs import _h
from .test_carrier_shipment import _set_fedex

from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.methods import tracking_url


def _carrier_id(client):
    with client.application.app_context():
        return get_carrier().id


def _get(client, token, carrier_id):
    response = client.get(f'/carrier/{carrier_id}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _put(client, token, carrier_id, body):
    return client.put(f'/carrier/{carrier_id}', json=body, headers=_h(token))


def test_defaults_and_available_methods(client, access_token):
    carrier_id = _carrier_id(client)
    data = _get(client, access_token, carrier_id)
    assert (data['waybill_method'], data['pickup_method'], data['tracking_method']) == ('manual', 'none', 'link')
    assert data['pickup_schedule'] is None and data['auto_sign_days'] is None
    assert data['tracking_url_template'] is None and data['pickup_contact'] is None
    assert data['available_methods'] == {'waybill': ['manual'], 'pickup': ['manual', 'scheduled', 'dropoff', 'none'],
                                         'tracking': ['link', 'none']}
    items = client.get('/carrier/?page=1&per_page=10', headers=_h(access_token)).get_json()['items']
    row = next(i for i in items if i['id'] == carrier_id)
    assert row['waybill_method'] == 'manual' and row['available_methods']['waybill'] == ['manual']


def test_save_methods_and_settings(client, access_token):
    carrier_id = _carrier_id(client)
    body = {
        'waybill_method': 'manual', 'pickup_method': 'scheduled', 'tracking_method': 'link',
        'pickup_schedule': {'weekdays': [5, 1, 3, 3], 'ready_time': '14:00', 'close_time': '17:30'},
        'pickup_contact': '0120-00-0000 / https://example.com/pickup',
        'tracking_url_template': 'https://track.example.com/?no={tracking_number}',
        'auto_sign_days': '7',
    }
    response = _put(client, access_token, carrier_id, body)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['pickup_method'] == 'scheduled'
    assert data['pickup_schedule'] == {'weekdays': [1, 3, 5], 'ready_time': '14:00', 'close_time': '17:30'}
    assert data['auto_sign_days'] == 7
    with client.application.app_context():
        assert tracking_url(get_carrier(), 'AB 12/3') == 'https://track.example.com/?no=AB%2012%2F3'

    # 清空可空的设置
    response = _put(client, access_token, carrier_id, {'auto_sign_days': None, 'tracking_url_template': '',
                                                       'pickup_schedule': None})
    assert response.status_code == 200
    data = response.get_json()
    assert data['auto_sign_days'] is None and data['tracking_url_template'] is None
    assert data['pickup_schedule'] is None and data['pickup_method'] == 'scheduled'


@pytest.mark.parametrize('body, field', [
    ({'waybill_method': 'api'}, 'waybill_method'),          # 没有在线对接
    ({'pickup_method': 'api'}, 'pickup_method'),
    ({'tracking_method': 'api'}, 'tracking_method'),
    ({'waybill_method': 'file'}, 'waybill_method'),         # 第二期
    ({'waybill_method': 'fax'}, 'waybill_method'),
    ({'pickup_method': None}, 'pickup_method'),
    ({'tracking_method': 3}, 'tracking_method'),
    ({'auto_sign_days': 0}, 'auto_sign_days'),
    ({'auto_sign_days': 91}, 'auto_sign_days'),
    ({'auto_sign_days': True}, 'auto_sign_days'),
    ({'auto_sign_days': 1.5}, 'auto_sign_days'),
    ({'tracking_url_template': 'https://example.com/track'}, 'tracking_url_template'),
    ({'tracking_url_template': 'javascript:{tracking_number}'}, 'tracking_url_template'),
    ({'pickup_contact': 'x' * 256}, 'pickup_contact'),
    ({'pickup_schedule': {'weekdays': [], 'ready_time': '09:00', 'close_time': '12:00'}}, 'pickup_schedule.weekdays'),
    ({'pickup_schedule': {'weekdays': [8], 'ready_time': '09:00', 'close_time': '12:00'}}, 'pickup_schedule.weekdays'),
    ({'pickup_schedule': {'weekdays': [1], 'ready_time': '9am', 'close_time': '12:00'}},
     'pickup_schedule.ready_time'),
    ({'pickup_schedule': {'weekdays': [1], 'ready_time': '13:00', 'close_time': '12:00'}},
     'pickup_schedule.close_time'),
    ({'pickup_schedule': 'daily'}, 'pickup_schedule'),
])
def test_invalid_settings(client, access_token, body, field):
    response = _put(client, access_token, _carrier_id(client), body)
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16135 and response.get_json()['field'] == field


def test_create_rejects_api_without_integration(client, access_token):
    response = client.post('/carrier/', json={'name': 'New Carrier', 'company_id': 1, 'waybill_method': 'api'},
                           headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['field'] == 'waybill_method'
    response = client.post('/carrier/', json={'name': 'New Carrier', 'company_id': 1, 'pickup_method': 'manual',
                                              'auto_sign_days': 14}, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['pickup_method'] == 'manual' and response.get_json()['auto_sign_days'] == 14


def test_integration_adopts_and_releases_methods(client, access_token):
    carrier_id = _carrier_id(client)
    _set_fedex(client)
    data = _get(client, access_token, carrier_id)
    assert (data['waybill_method'], data['pickup_method'], data['tracking_method']) == ('api', 'api', 'api')
    assert data['available_methods']['waybill'] == ['api', 'manual']
    assert data['available_methods']['pickup'][0] == 'api' and data['available_methods']['tracking'][0] == 'api'

    # 改回手工：之后再保存对接设置（没有重新启用）不会又改成 api
    response = _put(client, access_token, carrier_id, {'pickup_method': 'scheduled', 'waybill_method': 'manual'})
    assert response.status_code == 200, response.get_json()
    _set_fedex(client, service_type='INTERNATIONAL_PRIORITY')
    data = _get(client, access_token, carrier_id)
    assert (data['waybill_method'], data['pickup_method'], data['tracking_method']) == ('manual', 'scheduled', 'api')

    # 对接停用：api 不再可选，但没改的 api 值保存其它资料时不报错
    _set_fedex(client, enabled=False)
    data = _get(client, access_token, carrier_id)
    assert 'api' not in data['available_methods']['tracking']
    response = _put(client, access_token, carrier_id, {'phone': '000', 'tracking_method': 'api'})
    assert response.status_code == 200, response.get_json()
    response = _put(client, access_token, carrier_id, {'waybill_method': 'api'})
    assert response.status_code == 400 and response.get_json()['field'] == 'waybill_method'

    # 删掉对接：api 退回默认值
    with client.application.app_context():
        CarrierIntegrationService.save(get_carrier(), {'adapter': None}, None)
    data = _get(client, access_token, carrier_id)
    assert (data['waybill_method'], data['pickup_method'], data['tracking_method']) == ('manual', 'scheduled', 'link')


def test_tracking_url_falls_back_to_plugin(client, access_token):
    _set_fedex(client)
    with client.application.app_context():
        carrier = get_carrier()
        assert tracking_url(carrier, '794600000001') == 'https://www.fedex.com/fedextrack/?trknbr=794600000001'
        assert tracking_url(carrier, None) is None
        carrier.tracking_url_template = 'https://example.com/t/{tracking_number}'
        db.session.commit()
        assert tracking_url(get_carrier(), '7946') == 'https://example.com/t/7946'
