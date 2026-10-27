from copy import deepcopy
import pytest
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign


def draft():
    return {'floors': [{'id': 'main-1', 'name': 'Main 1F', 'width': 30, 'height': 20,
                       'objects': [{'id': 'rack-a', 'type': 'rack', 'name': 'A-01', 'x': 1, 'y': 2,
                                    'width': 1.2, 'height': .4, 'rotation': 0, 'location_ids': []}]}]}


def test_layout_draft_publish_history_conflict(client, access_token):
    with client.application.app_context():
        wid = get_warehouse().id
    url = f'/warehouse/{wid}/layout'
    headers = _h(access_token)
    assert client.get(url, headers=headers).get_json()['draft'] is None
    saved = client.put(url, json={'revision': 0, 'layout': draft()}, headers=headers)
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['revision'] == 1
    assert client.put(url, json={'revision': 0, 'layout': draft()}, headers=headers).status_code == 409
    published = client.post(url + '/publish', json={'revision': 1}, headers=headers)
    assert published.status_code == 200, published.get_json()
    assert published.get_json()['published_version'] == 1
    newer = draft()
    newer['floors'][0]['name'] = 'Edited draft'
    assert client.put(url, json={'revision': 2, 'layout': newer}, headers=headers).status_code == 200
    historic = client.get(url + '/versions/1', headers=headers).get_json()
    assert historic['layout']['floors'][0]['name'] == 'Main 1F'
    assert client.post(url + '/publish', json={'revision': 2}, headers=headers).status_code == 409
    assert len(client.get(url + '/versions', headers=headers).get_json()['items']) == 1


@pytest.mark.parametrize('field,value', [('x', -1), ('width', 50), ('rotation', 45), ('height', True), ('location_ids', [999999]), ('type', 'script'), ('target_id', 'missing')])
def test_layout_validation(client, access_token, field, value):
    with client.application.app_context():
        wid = get_warehouse().id
    layout = draft()
    layout['floors'][0]['objects'][0][field] = value
    result = client.put(f'/warehouse/{wid}/layout', json={'revision': 0, 'layout': layout}, headers=_h(access_token))
    assert result.status_code == 400, result.get_json()


def test_layout_scope_and_read_only(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        user = _staff('layout_reader', _role('layout_reader', ['warehouse_read']), [warehouse])
        token = create_access_token(identity=user)
    url = f'/warehouse/{wid}/layout'
    assert client.get(url, headers=_h(token)).status_code == 200
    assert client.put(url, json={'revision': 0, 'layout': draft()}, headers=_h(token)).status_code == 403
    assert client.get(url, headers=_h(foreign['token'])).status_code in (403, 404)


def test_layout_rejects_duplicate_and_foreign_location_bindings(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        location = Location.query.filter_by(warehouse_id=wid).first()
        lid = location.id
    layout = draft()
    rack = layout['floors'][0]['objects'][0]
    rack['location_ids'] = [lid, lid]
    url = f'/warehouse/{wid}/layout'
    assert client.put(url, json={'revision': 0, 'layout': layout}, headers=_h(access_token)).status_code == 400
    rack['location_ids'] = [foreign['location_id']]
    assert client.put(url, json={'revision': 0, 'layout': layout}, headers=_h(access_token)).status_code == 400
    unchanged = client.get(url, headers=_h(access_token)).get_json()
    assert unchanged['revision'] == 0 and unchanged['draft'] is None
    rack['location_ids'] = [lid]
    assert client.put(url, json={'revision': 0, 'layout': layout}, headers=_h(access_token)).status_code == 200
