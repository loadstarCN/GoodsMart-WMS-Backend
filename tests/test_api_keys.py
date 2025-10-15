from .helpers import *

def test_get_api_keys(client, access_token):
    response = client.get('/api-keys/api-keys?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_create_api_key(client, access_token):
    response = client.post('/api-keys/api-keys', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        "user_id": 1,
        "system_name": "New System",
        "permissions": {
            "admin": {
                "actions": ["settings"]
            },
            "tasks": {
                "actions": ["view","execute"]
            }
        }

    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['system_name'] == "New System"

def test_get_api_key_detail(client, access_token):
    with client.application.app_context():
        api_key = get_api_key()
        response = client.get(f'/api-keys/api-keys/{api_key.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == api_key.id
        assert data['system_name'] == api_key.system_name

def test_update_api_key(client, access_token):
    with client.application.app_context():
        api_key = get_api_key()
        response = client.put(f'/api-keys/api-keys/{api_key.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            "system_name": "Updated System",
            "permissions": {
                "module": {"actions": ["read"]}
            },
            "user_id": 1
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['system_name'] == "Updated System"

def test_delete_api_key(client, access_token):
    with client.application.app_context():
        api_key = get_api_key()
        response = client.delete(f'/api-keys/api-keys/{api_key.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        assert response.get_json()['message'] == "API key deleted successfully"
