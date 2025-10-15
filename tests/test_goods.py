import pytest
from .helpers import *

def test_get_goods(client, access_token):
    response = client.get('/goods/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_get_goods_by_goods_codes(client, access_token):
    # 从测试数据中取出一条商品，获取其 code 作为过滤依据
    with client.application.app_context():
        goods = get_goods()
        code = goods.code
        # 将 code 传入 goods_codes 参数（逗号分隔字符串，但解析后会变成列表）
        response = client.get(f'/goods/?page=1&per_page=10&goods_codes={code}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        # 检查返回的商品中，其 code 均应包含在传入的 goods_codes 列表中
        for item in data['items']:
            assert item['code'] == code

def test_create_goods(client, access_company_admin_token):
    response = client.post('/goods/', headers={
        'Authorization': f'Bearer {access_company_admin_token}'
    }, json={
        'code': 'G003',
        'company_id': 1,
        'category_id': 1,
        'name': 'New Goods',
        'description': 'New goods description',
        'unit': 'pcs',
        'weight': 1.5,
        'length': 12.0,
        'width': 6.0,
        'height': 3.0,
        'manufacturer': 'New Manufacturer',
        'is_active': True
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['code'] == 'G003'
    assert data['name'] == 'New Goods'


def test_get_goods_details(client, access_company_admin_token):
    with client.application.app_context():
        goods = get_goods()
        response = client.get(f'/goods/{goods.id}', headers={
            'Authorization': f'Bearer {access_company_admin_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == goods.id
        assert data['name'] == goods.name


def test_update_goods(client, access_token):
    with client.application.app_context():
        goods = get_goods()
        response = client.put(f'/goods/{goods.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Updated Goods',
            'description': 'Updated description',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Goods'
        assert data['is_active'] is False


def test_delete_goods_dependency_error(client, access_token):
    """测试删除货物时的依赖错误"""
    with client.application.app_context():
        goods = get_goods()
        
        # 发送删除请求
        response = client.delete(
            f'/goods/{goods.id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        
        # 验证响应状态码
        assert response.status_code == 500  # 内部服务器错误
        

def test_get_goods_locations(client, access_token):
    response = client.get('/goods/locations/', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_goods_location(client, access_token):
    with client.application.app_context():
        goods = get_goods()
        location = get_location_by_id(2)
        response = client.post('/goods/locations/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'goods_id': goods.id,
            'location_id': location.id,
            'quantity': 50
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['goods_id'] == goods.id
        assert data['quantity'] == 50


def test_update_goods_location(client, access_token):
    with client.application.app_context():
        goods_location = get_goods_location()
        response = client.put(f'/goods/locations/{goods_location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'quantity': 200
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['quantity'] == 200


def test_delete_goods_location(client, access_token):
    with client.application.app_context():
        goods_location = get_goods_location()
        response = client.delete(f'/goods/locations/{goods_location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'GoodsLocation deleted successfully'

        # Ensure the goods location is deleted
        deleted_goods_location = get_goods_location_by_id(goods_location.id)
        assert deleted_goods_location is None
