"""承运商在线对接设置（carrier_integrations）：插件列表、保存 / 读取（密钥加密、不回显）、校验、测试连接、
有未结束运单时不能删除 / 换插件、旧版 FEDEX_* 环境变量导入命令。

FedEx 一律 mock（沿用 test_carrier_shipment 的 fedex fixture），不联网。
"""
from .helpers import *
from .test_customs import _h
from .test_carrier_shipment import (  # noqa: F401  fedex 是 fixture
    ACCOUNT, API_KEY, SECRET, fedex, _cancel, _create, _ready,
)

from warehouse.carrier.commands import carrier_cli
from warehouse.carrier.integrations.fedex import client as fedex_client
from warehouse.carrier.models import CarrierIntegration


def _carrier_id(client):
    with client.application.app_context():
        return get_carrier().id


def _put(client, token, carrier_id, body):
    return client.put(f'/carrier/{carrier_id}/integration', json=body, headers=_h(token))


def _fedex_body(**settings):
    return {'adapter': 'fedex', 'enabled': True,
            'settings': {'environment': 'sandbox', 'account_number': ACCOUNT, **settings},
            'secrets': {'api_key': API_KEY, 'secret_key': SECRET}}


def test_available_integrations(client, access_token):
    response = client.get('/carrier/integrations', headers=_h(access_token))
    assert response.status_code == 200
    fedex_info = next(i for i in response.get_json() if i['code'] == 'fedex')
    assert fedex_info['name'] == 'FedEx' and fedex_info['portal_name'] == 'FedEx Ship Manager'
    assert fedex_info['label_formats'] == ['A4', 'THERMAL']
    fields = {f['key']: f for f in fedex_info['fields']}
    assert fields['api_key']['kind'] == 'secret' and fields['api_key']['required'] is True
    assert fields['service_type']['kind'] == 'select' and fields['service_type']['default'] == 'INTERNATIONAL_ECONOMY'
    assert fields['etd_enabled']['kind'] == 'boolean'
    assert set(fields['environment']['label']) == {'en-US', 'zh-CN', 'ja-JP'}


def test_save_and_read_without_secrets(client, access_token):
    carrier_id = _carrier_id(client)
    assert client.get(f'/carrier/{carrier_id}/integration', headers=_h(access_token)).get_json() is None

    response = _put(client, access_token, carrier_id, _fedex_body(service_type='INTERNATIONAL_PRIORITY'))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['adapter'] == 'fedex' and data['name'] == 'FedEx' and data['enabled'] is True
    assert data['secrets'] == {'api_key': True, 'secret_key': True}
    assert data['ready'] is True and data['problems'] == []
    assert data['settings']['service_type'] == 'INTERNATIONAL_PRIORITY'
    assert data['settings']['pickup_type'] == 'CONTACT_FEDEX_TO_SCHEDULE'       # 没传的用默认
    assert API_KEY not in response.get_data(as_text=True) and SECRET not in response.get_data(as_text=True)

    with client.application.app_context():
        stored = CarrierIntegration.query.filter_by(carrier_id=carrier_id).one()
        assert stored.secrets and API_KEY not in stored.secrets and SECRET not in stored.secrets

    # 承运商列表 / 详情带对接概要
    listed = client.get('/carrier/?page=1&per_page=50', headers=_h(access_token)).get_json()
    item = next(c for c in listed['items'] if c['id'] == carrier_id)
    assert item['integration'] == {'adapter': 'fedex', 'name': 'FedEx', 'enabled': True}

    # 不传密钥 = 保持原值
    response = _put(client, access_token, carrier_id, {**_fedex_body(), 'secrets': {}, 'enabled': False})
    assert response.status_code == 200
    assert response.get_json()['secrets'] == {'api_key': True, 'secret_key': True}
    assert response.get_json()['enabled'] is False
    with client.application.app_context():
        from warehouse.carrier.integration_services import CarrierIntegrationService
        snapshot = CarrierIntegrationService.for_carrier(get_carrier())
        assert snapshot['secrets'] == {'api_key': API_KEY, 'secret_key': SECRET}


def test_invalid_settings_rejected(client, access_token):
    carrier_id = _carrier_id(client)
    cases = [
        ({'adapter': 'dhl'}, 'adapter'),
        (_fedex_body(service_type='SAME_DAY'), 'settings.service_type'),
        (_fedex_body(etd_enabled='yes'), 'settings.etd_enabled'),
        ({**_fedex_body(), 'enabled': 'yes'}, 'enabled'),
        ({**_fedex_body(), 'secrets': {'api_key': 123}}, 'secrets.api_key'),
    ]
    for body, field in cases:
        response = _put(client, access_token, carrier_id, body)
        assert response.status_code == 400 and response.get_json()['code'] == 16124, body
        assert response.get_json()['field'] == field
    with client.application.app_context():
        assert CarrierIntegration.query.count() == 0


def test_missing_required_settings_reported(client, access_token):
    carrier_id = _carrier_id(client)
    response = _put(client, access_token, carrier_id, {'adapter': 'fedex', 'settings': {}})
    assert response.status_code == 200
    data = response.get_json()
    assert data['ready'] is False and data['secrets'] == {'api_key': False, 'secret_key': False}
    assert data['problems'][0]['code'] == 'CARRIER_NOT_CONFIGURED'
    assert set(data['problems'][0]['fields']) == {'account_number', 'api_key', 'secret_key'}


def test_connection_test(client, access_token, fedex):
    carrier_id = _carrier_id(client)
    response = client.post(f'/carrier/{carrier_id}/integration/test', json={}, headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['ok'] is True
    assert fedex.of('/oauth/token')[-1]['data']['client_id'] == API_KEY

    # 用表单里还没保存的值测；密钥留空用已保存的
    fedex_client.clear_token_cache()
    fedex.set('/oauth/token', (401, {'errors': [{'code': 'NOT.AUTHORIZED.ERROR', 'message': 'bad key'}]}))
    body = {**_fedex_body(), 'secrets': {'api_key': 'typed-key'}}
    response = client.post(f'/carrier/{carrier_id}/integration/test', json=body, headers=_h(access_token))
    data = response.get_json()
    assert response.status_code == 200 and data['ok'] is False and data['permission_denied'] is True
    assert data['errors'][0]['code'] == 'NOT.AUTHORIZED.ERROR'
    sent = fedex.of('/oauth/token')[-1]['data']
    assert sent['client_id'] == 'typed-key' and sent['client_secret'] == SECRET
    # 测试不保存
    with client.application.app_context():
        from warehouse.carrier.integration_services import CarrierIntegrationService
        assert CarrierIntegrationService.for_carrier(get_carrier())['secrets']['api_key'] == API_KEY


def test_cannot_remove_while_shipments_open(client, access_token, fedex):
    dn_id, _task_id = _ready(client, access_token)
    assert _create(client, access_token, dn_id).status_code == 201
    carrier_id = _carrier_id(client)

    response = client.delete(f'/carrier/{carrier_id}/integration', headers=_h(access_token))
    assert response.status_code == 409 and response.get_json()['code'] == 16125
    assert response.get_json()['details']['shipments'][0]['tracking_number'] == '794600000001'
    # 换密钥 / 停用照常（取消运单仍用这套设置）
    assert _put(client, access_token, carrier_id, {**_fedex_body(), 'enabled': False}).status_code == 200

    assert _cancel(client, access_token, dn_id).status_code == 200
    assert client.delete(f'/carrier/{carrier_id}/integration', headers=_h(access_token)).status_code == 204
    assert client.get(f'/carrier/{carrier_id}/integration', headers=_h(access_token)).get_json() is None


def test_import_fedex_env_command(client, monkeypatch):
    with client.application.app_context():
        carrier = get_carrier()
        carrier.code = 'FedEx'
        company_id = carrier.company_id
        db.session.commit()
    for name, value in {'FEDEX_API_KEY': API_KEY, 'FEDEX_SECRET_KEY': SECRET, 'FEDEX_ACCOUNT_NUMBER': ACCOUNT,
                        'FEDEX_API_BASE': 'https://apis.fedex.com', 'FEDEX_ALLOWED_COMPANY_IDS': f'{company_id}',
                        'FEDEX_DEFAULT_LABEL_FORMAT': 'thermal', 'FEDEX_SERVICE_TYPE': 'GROUND_SOMETHING',
                        'FEDEX_ETD_ENABLED': 'False'}.items():
        monkeypatch.setenv(name, value)
    runner = client.application.test_cli_runner()

    result = runner.invoke(carrier_cli, ['import-fedex-env', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert 'GROUND_SOMETHING' in result.output and 'Dry run' in result.output
    with client.application.app_context():
        assert CarrierIntegration.query.count() == 0

    result = runner.invoke(carrier_cli, ['import-fedex-env'])
    assert result.exit_code == 0, result.output
    with client.application.app_context():
        from warehouse.carrier.integration_services import CarrierIntegrationService
        snapshot = CarrierIntegrationService.for_carrier(get_carrier())
        assert snapshot['adapter'] == 'fedex' and snapshot['enabled'] is True
        assert snapshot['settings'] == {'environment': 'production', 'account_number': ACCOUNT,
                                        'etd_enabled': False, 'default_label_format': 'THERMAL'}
        assert snapshot['secrets'] == {'api_key': API_KEY, 'secret_key': SECRET}

    # 已有对接：不覆盖
    result = runner.invoke(carrier_cli, ['import-fedex-env'])
    assert result.exit_code == 0 and 'skipped' in result.output
