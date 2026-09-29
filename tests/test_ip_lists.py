from .helpers import *
import time

def test_get_blacklist(client, access_token):
    response = client.get('/ip-lists/blacklist?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_add_blacklist(client, access_token):
    response = client.post('/ip-lists/blacklist', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        "ip_address": "192.168.1.1",
        "reason": "Malicious activity"
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['ip_address'] == "192.168.1.1"

def test_delete_blacklist(client, access_token):
    with client.application.app_context():
        blacklist_entry = IPBlacklist.query.first()
        response = client.delete(f'/ip-lists/blacklist/{blacklist_entry.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        assert response.get_json()['message'] == "Blacklist entry deleted"

def test_blacklist_search(client, access_token):
    response = client.get('/ip-lists/blacklist?page=1&per_page=10&ip_address=192.168.0.1', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert any(item['ip_address'] == "192.168.0.1" for item in data['items'])

def test_get_whitelist(client, access_token):
    response = client.get('/ip-lists/whitelist?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_add_whitelist(client, access_token):
    response = client.post('/ip-lists/whitelist', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        "ip_address": "10.0.0.2",
        "reason": "Internal server"
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['ip_address'] == "10.0.0.2"

def test_delete_whitelist(client, access_token):
    with client.application.app_context():
        whitelist_entry = IPWhitelist.query.first()
        response = client.delete(f'/ip-lists/whitelist/{whitelist_entry.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        assert response.get_json()['message'] == "Whitelist entry deleted"

def test_limiter(client, access_token):
    """Test Limiter - Rate Limiting"""
    # 测试配置默认关闭限流（避免登录等用例互相干扰）。关闭时 init_app 不会注册请求钩子，
    # 所以这里在本测试的 app 上打开配置并重新初始化（此时该 app 尚未处理过请求）
    from extensions import limiter
    app = client.application
    app.config['RATELIMIT_ENABLED'] = True
    limiter.init_app(app)
    try:
        _run_limiter_scenario(client, access_token)
    finally:
        limiter.enabled = False


def _run_limiter_scenario(client, access_token):
    time.sleep(3)  # 等待 3 秒
    # 第一次请求
    response = client.get('/ip-lists/test', headers={
        'Authorization': f'Bearer {access_token}',
        'X-Forwarded-For': '127.0.0.1'  # 模拟客户端 IP
    })
    
    assert response.status_code == 200
    assert response.get_json()['message'] == "Limiter API"

    # 第二次请求（超过限制）
    response = client.get('/ip-lists/test', headers={
        'Authorization': f'Bearer {access_token}',
        'X-Forwarded-For': '127.0.0.1'  # 相同 IP
    })
    
    assert response.status_code == 429  # HTTP 429 Too Many Requests
    assert "1 per 3 second" in response.get_json()['message']

    time.sleep(3)  # 等待 3 秒
    """Test Limiter - Rate Limiting"""
    # 第三次请求
    response = client.get('/ip-lists/test', headers={
        'Authorization': f'Bearer {access_token}',
        'X-Forwarded-For': '127.0.0.1'  # 模拟客户端 IP
    })
    
    assert response.status_code == 200
    assert response.get_json()['message'] == "Limiter API"
