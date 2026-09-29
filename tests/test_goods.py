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


def test_get_goods_locations_by_goods_ids(client, access_token):
    # goods_ids：逗号分隔的批量过滤（APP 列表页/拣货详情一次查多件商品的库位）
    with client.application.app_context():
        goods_location = get_goods_location()
        goods_id = goods_location.goods_id
        response = client.get(
            f'/goods/locations/?page=1&per_page=100&goods_ids={goods_id},999999',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data['items']) > 0
        for item in data['items']:
            assert item['goods_id'] == goods_id

        # 不存在的 id 组合应返回空列表而不是报错
        response = client.get(
            '/goods/locations/?page=1&per_page=100&goods_ids=999998,999999',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        assert response.status_code == 200
        assert response.get_json()['items'] == []


def test_get_goods_location_detail(client, access_token):
    with client.application.app_context():
        goods_location = get_goods_location()
        response = client.get(f'/goods/locations/{goods_location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == goods_location.id
        assert data['quantity'] == goods_location.quantity


def test_goods_location_write_endpoints_removed(client, access_token):
    """B-07：库位库存只能经上架/移库/下架/调整流程变更，直写端点一律 405"""
    with client.application.app_context():
        goods = get_goods()
        location = get_location_by_id(2)
        goods_location = get_goods_location()
        original_quantity = goods_location.quantity
        headers = {'Authorization': f'Bearer {access_token}'}

        response = client.post('/goods/locations/', headers=headers, json={
            'goods_id': goods.id, 'location_id': location.id, 'quantity': 50
        })
        assert response.status_code == 405

        response = client.put(f'/goods/locations/{goods_location.id}', headers=headers, json={'quantity': 200})
        assert response.status_code == 405

        response = client.delete(f'/goods/locations/{goods_location.id}', headers=headers)
        assert response.status_code == 405

        db.session.expire_all()
        assert get_goods_location_by_id(goods_location.id).quantity == original_quantity


def test_update_goods_cannot_change_company(client, access_token):
    """update 忽略 company_id（归属固定）"""
    with client.application.app_context():
        goods = get_goods()
        original_company_id = goods.company_id
        response = client.put(f'/goods/{goods.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={'name': 'Still mine', 'company_id': 2})
        assert response.status_code == 200
        assert response.get_json()['company_id'] == original_company_id


def test_bulk_upload_missing_price_and_bad_number(client, access_token):
    """B-18：CSV 缺 price 列 → 正常导入（price 为空）；价格非数字 → 400"""
    import io
    headers = {'Authorization': f'Bearer {access_token}'}

    csv_ok = "code,name\nB001,Bulk One\n"
    response = client.post('/goods/bulk_upload', headers=headers, data={
        'file': (io.BytesIO(csv_ok.encode('utf-8')), 'goods.csv'),
        'company_id': '1',
    }, content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()

    csv_bad = "code,name,price\nB002,Bulk Two,abc\n"
    response = client.post('/goods/bulk_upload', headers=headers, data={
        'file': (io.BytesIO(csv_bad.encode('utf-8')), 'goods.csv'),
        'company_id': '1',
    }, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()['code'] == 10012
