from .helpers import *

def test_get_user_logs(client, access_token):
    response = client.get('/logs/', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_log_query_with_filters(client, access_token):
    response = client.get('/logs/?page=1&per_page=10&actor=admin&method=GET&status_code=200', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0
    for item in data['items']:
        assert item['actor'] == 'admin'
        assert item['method'] == 'GET'
        assert item['status_code'] == 200


def test_get_user_log_detail(client, access_token):
    with client.application.app_context():
        log = get_activity_log()
        response = client.get(f'/logs/{log.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == log.id
        assert data['actor'] == log.actor
        assert data['endpoint'] == log.endpoint
        assert data['method'] == log.method
        assert data['status_code'] == log.status_code
