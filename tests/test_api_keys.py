from .helpers import *

def test_get_api_keys(client, access_token):
    response = client.get('/api-keys/api-keys?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0
    # 列表不再暴露明文 key / webhook_secret
    for item in data['items']:
        assert 'key' not in item
        assert 'webhook_secret' not in item

def test_create_api_key(client, access_token):
    response = client.post('/api-keys/api-keys', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        "user_id": 1,
        "system_name": "New System",
        "permissions": ["asn_read", "asn_edit", "dn_read"],
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['system_name'] == "New System"
    # 创建响应一次性返回明文；落库的是哈希
    plain = data['key']
    assert len(plain) == 64
    assert data['key_prefix'] == plain[:8]
    with client.application.app_context():
        stored = get_api_key_by_id(data['id'])
        assert stored.key != plain
        assert stored.key == hash_api_key(plain)

    # 明文可用于 X-API-KEY 认证
    response = client.get('/asn/', headers={'X-API-KEY': plain})
    assert response.status_code == 200

    # 详情接口不再返回明文
    response = client.get(f"/api-keys/api-keys/{data['id']}", headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    assert 'key' not in response.get_json()

def test_create_api_key_rejects_non_list_permissions(client, access_token):
    response = client.post('/api-keys/api-keys', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        "system_name": "Bad",
        "permissions": {"admin": {"actions": ["settings"]}},
    })
    assert response.status_code == 400
    assert response.get_json()['code'] == 14010

def test_company_admin_cannot_escalate_via_api_key(client, access_company_admin_token):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}

    # 不能授予平台权限
    response = client.post('/api-keys/api-keys', headers=headers, json={
        "system_name": "Escalate",
        "permissions": ["all_access"],
    })
    assert response.status_code == 403
    assert response.get_json()['code'] == 12006

    response = client.post('/api-keys/api-keys', headers=headers, json={
        "system_name": "Escalate",
        "permissions": ["user_edit"],
    })
    assert response.status_code == 403

    # 不能绑定到平台管理员
    response = client.post('/api-keys/api-keys', headers=headers, json={
        "system_name": "Escalate",
        "permissions": ["asn_read"],
        "user_id": 1,
    })
    assert response.status_code == 403

    # 正常创建：company_id 被强制为自己公司
    response = client.post('/api-keys/api-keys', headers=headers, json={
        "system_name": "Mine",
        "permissions": ["asn_read", "dn_read"],
        "company_id": 999,
    })
    assert response.status_code == 201
    with client.application.app_context():
        assert response.get_json()['company_id'] == get_company_admin_user().company_id

def test_company_admin_cannot_access_other_company_keys(client, access_company_admin_token):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    with client.application.app_context():
        # helpers 里的 key 没有 company_id（平台密钥）
        api_key = get_api_key()
        key_id = api_key.id
    response = client.get(f'/api-keys/api-keys/{key_id}', headers=headers)
    assert response.status_code == 403
    response = client.put(f'/api-keys/api-keys/{key_id}', headers=headers, json={"permissions": ["all_access"]})
    assert response.status_code == 403

    # 列表只看到自己公司的
    response = client.get('/api-keys/api-keys', headers=headers)
    assert response.status_code == 200
    with client.application.app_context():
        company_id = get_company_admin_user().company_id
    for item in response.get_json()['items']:
        assert item['company_id'] == company_id

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
            "permissions": ["asn_read"],
            "user_id": 1
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['system_name'] == "Updated System"
        assert data['permissions'] == ["asn_read"]

def test_delete_api_key(client, access_token):
    with client.application.app_context():
        api_key = get_api_key()
        response = client.delete(f'/api-keys/api-keys/{api_key.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        assert response.get_json()['message'] == "API key deleted successfully"
