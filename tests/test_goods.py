import pytest
from .helpers import *


def test_count_scope_filters_use_lot_dates_rules_and_positive_stock(client, access_company_admin_token):
    from datetime import timedelta
    from warehouse.common import clock
    from warehouse.stock.models import GoodsLot, GoodsLotRules
    from warehouse.stock.service import StockOp, Leg
    with client.application.app_context():
        original = get_goods_location()
        goods, location = original.goods, original.location
        today = clock.local_today(location.warehouse_id)
        db.session.add(GoodsLotRules(goods_id=goods.id, overrides={'warning_days': 30, 'ship_days': 10}))
        ids = []
        for index, (days, quantity) in enumerate(((9, 2), (10, 3), (30, 4), (31, 5), (None, 6), (5, 0))):
            lot = GoodsLot(goods_id=goods.id, lot_no=f'COUNT-{index}',
                           expiry_date=today + timedelta(days=days) if days is not None else None)
            db.session.add(lot)
            db.session.flush()
            if quantity:
                op = StockOp(location.warehouse_id, 'opening')
                op.move(goods.id, quantity, dst=Leg.at(location.id, lot_id=lot.id))
                op.commit()
                row = GoodsLocation.query.filter_by(goods_id=goods.id, location_id=location.id, lot_id=lot.id).one()
            else:
                row = GoodsLocation(goods_id=goods.id, location_id=location.id, lot_id=lot.id, quantity=0)
                db.session.add(row)
                db.session.flush()
            ids.append(row.id)
        db.session.commit()
        warehouse_id, code, name = location.warehouse_id, location.code, goods.name
    headers = {'Authorization': f'Bearer {access_company_admin_token}', 'X-Warehouse-ID': str(warehouse_id)}
    def selected(**query):
        response = client.get('/goods/locations/', headers=headers,
                              query_string={'per_page': 100, 'quantity_min': 1, **query})
        assert response.status_code == 200
        return {row['id'] for row in response.json['items']}
    assert selected(lot_no='COUNT-', expiry_scope='warning') == set(ids[:3])
    assert selected(lot_no='COUNT-', expiry_scope='below_shipping') == {ids[0]}
    assert selected(lot_no='COUNT-4') == {ids[4]}
    assert selected(lot_no='COUNT-%') == set()
    assert selected(lot_no='COUNT-', goods_query=name, location_code=code) == set(ids[:5])
    assert selected(goods_query='NO-SUCH-GOODS') == set()
    assert client.get('/goods/locations/?expiry_scope=invalid', headers=headers).status_code == 400


@pytest.mark.parametrize("permission", ["adjustment_edit", "cycle_count_edit"])
def test_inventory_editor_can_search_stock_only_in_assigned_warehouse(client, permission):
    from .test_global_search import _role, _staff_token
    with client.application.app_context():
        location = get_goods_location()
        warehouse = db.session.get(Warehouse, location.location.warehouse_id)
        expected_id = location.id
        warehouse_id = warehouse.id
        foreign = Warehouse(name='Adjustment inaccessible', company_id=warehouse.company_id,
                            created_by=get_admin_user().id)
        db.session.add(foreign)
        db.session.commit()
        foreign_id = foreign.id
        token = _staff_token('adjustment_only', warehouse.company_id,
                             _role('adjustment_only', [permission]), [warehouse])
    headers = {'Authorization': f'Bearer {token}', 'X-Warehouse-ID': str(warehouse_id)}
    response = client.get('/goods/locations/?per_page=100', headers=headers)
    assert response.status_code == 200
    allowed_ids = {row['id'] for row in response.json['items']}
    assert expected_id in allowed_ids
    assert client.get('/goods/', headers=headers).status_code == 403
    assert client.get('/goods/locations/', headers={**headers, 'X-Warehouse-ID': str(foreign_id)}).status_code == 403
    response = client.get(f'/goods/locations/?warehouse_id={foreign_id}', headers=headers)
    # The selected warehouse header is authoritative over a stale URL parameter.
    assert response.status_code == 200
    assert {row['id'] for row in response.json['items']} == allowed_ids

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
        
        # 仍有库存 / 单据引用：409 44004（B-84，以前撞外键约束兜成 500）
        assert response.status_code == 409
        body = response.get_json()
        assert body['code'] == 44004
        assert body['details']['entity'] == 'goods'
        assert body['details']['references']['inventory'] >= 1
        

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


def test_update_goods_numbers_do_not_mutate_request_json(client, access_token):
    """weight / price 规范化成 Decimal 只发生在副本上：请求 JSON 保持原样，
    after_request 操作日志能序列化请求体（以前 TypeError → 已提交的修改返回 500）"""
    import json
    from decimal import Decimal
    from flask import request
    from system.logs.utils import _capture_request_body
    from warehouse.goods.models import Goods
    from warehouse.goods.services import GoodsService

    body = {'weight': 0.328, 'length': 140, 'width': 130, 'height': 13, 'price': 1760}
    with client.application.app_context():
        goods_id = get_goods().id
    with client.application.test_request_context(json=dict(body)):
        payload = request.get_json()
        GoodsService.update_goods(goods_id, payload)
        # 按 JSON 比（Decimal('1760.00') == 1760 为真，直接 == 看不出被改成了 Decimal）
        assert json.dumps(payload, sort_keys=True) == json.dumps(body, sort_keys=True)
        assert json.loads(_capture_request_body()) == body
        goods = db.session.get(Goods, goods_id)
        assert goods.weight == Decimal('0.328')
        assert (goods.length, goods.width, goods.height) == (140, 130, 13)
        assert goods.price == Decimal('1760')


def test_create_goods_numbers_do_not_mutate_request_json(client, access_token):
    """新建同上：_build_goods 在副本上规范化"""
    import json
    from flask import request
    from warehouse.goods.services import GoodsService

    body = {'code': 'G-DEC-1', 'company_id': 1, 'name': 'Decimal goods', 'weight': 1.5, 'price': 980}
    with client.application.app_context():
        created_by = get_admin_user().id
    with client.application.test_request_context(json=dict(body)):
        payload = request.get_json()
        GoodsService.create_goods(payload, created_by)
        assert json.dumps(payload, sort_keys=True) == json.dumps(body, sort_keys=True)


def test_capture_request_body_tolerates_non_json_values(client):
    """视图就地往请求 JSON 里塞了 Decimal / date：操作日志按字符串记下，不抛异常"""
    import json
    from datetime import date
    from decimal import Decimal
    from flask import request
    from system.logs.utils import _capture_request_body

    with client.application.test_request_context(json={'weight': 0.328}):
        payload = request.get_json()
        payload['weight'] = Decimal('0.328')
        payload['production_date'] = date(2026, 10, 15)
        assert json.loads(_capture_request_body()) == {'weight': '0.328', 'production_date': '2026-10-15'}
