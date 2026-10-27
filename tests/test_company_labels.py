from copy import deepcopy
import pytest
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.company.labels import DEFAULT_TEMPLATE


def test_company_label_defaults_save_and_conflict(client, access_token):
    with client.application.app_context():
        cid = get_warehouse().company_id
    url = f'/company/{cid}/product-label'
    headers = _h(access_token)
    original = client.get(url, headers=headers).get_json()
    assert original['revision'] == 0 and original['template']['width_mm'] == 60
    template = dict(DEFAULT_TEMPLATE, width_mm=70, height_mm=50, name_font_pt=10)
    saved = client.put(url, json={'revision': 0, 'template': template}, headers=headers)
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['revision'] == 1
    assert client.get(url, headers=headers).get_json()['template'] == template
    assert client.put(url, json={'revision': 0, 'template': DEFAULT_TEMPLATE}, headers=headers).status_code == 409
    assert client.get(url, headers=headers).get_json()['template'] == template


def test_company_label_read_permissions_and_tenant_isolation(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        cid = warehouse.company_id
        user = _staff('label_reader', _role('label_reader', ['goods_read']), [warehouse])
        token = create_access_token(identity=user)
        denied = _staff('label_denied', _role('label_denied', ['inventory_read']), [warehouse])
        denied_token = create_access_token(identity=denied)
    url = f'/company/{cid}/product-label'
    assert client.get(url, headers=_h(token)).status_code == 200
    assert client.get(url, headers=_h(denied_token)).status_code == 403
    assert client.put(url, json={'revision': 0, 'template': DEFAULT_TEMPLATE}, headers=_h(token)).status_code == 403
    assert client.get(url, headers=_h(foreign['token'])).status_code == 403
    assert client.put(url, json={'revision': 0, 'template': DEFAULT_TEMPLATE}, headers=_h(foreign['token'])).status_code == 403
    other = client.get(f"/company/{foreign['company_id']}/product-label", headers=_h(foreign['token']))
    assert other.status_code == 200 and other.get_json()['revision'] == 0


@pytest.mark.parametrize('patch', [dict(width_mm=True), dict(height_mm=0), dict(width_mm=151),
    dict(name_font_pt=30), dict(margin_mm=-1), dict(show_name='false'), dict(barcode_format='html'),
    dict(height_mm=25, margin_mm=8, name_font_pt=14), dict(extra='unknown')])
def test_company_label_invalid_template_is_atomic(client, access_token, patch):
    with client.application.app_context():
        cid = get_warehouse().company_id
    url = f'/company/{cid}/product-label'
    template = deepcopy(DEFAULT_TEMPLATE)
    template.update(patch)
    assert client.put(url, json={'revision': 0, 'template': template}, headers=_h(access_token)).status_code == 400
    result = client.get(url, headers=_h(access_token)).get_json()
    assert result['revision'] == 0 and result['template'] == DEFAULT_TEMPLATE
