from .helpers import *


def test_get_removal_records(client, access_token):
    """
    测试分页获取下架记录列表 (GET /removal/)
    """
    response = client.get('/removal/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    # data 中通常包含 items, total, page, per_page, ...
    assert 'items' in data
    assert data['total'] >= 1  # 预期至少存在一个 RemovalRecord


def test_create_removal_record(client, access_token):
    """
    测试创建新的下架记录 (POST /removal/)
    """
    with client.application.app_context():
        goods = get_goods()
        location = get_location()
        operator = get_operator_user()

        inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id,location.warehouse_id)
        InventoryService.asn_received(goods.id, location.warehouse_id, 100)
        InventoryService.asn_completed(goods.id, location.warehouse_id, 50,50)

        old_total_stock = inventory.total_stock
        old_onhand_stock = inventory.onhand_stock


        response = client.post('/removal/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            "goods_id": goods.id,
            "location_id": location.id,
            "quantity": 25,
            "operator_id": operator.id,
            "reason": "return",
            "remark": "New removal record from test"
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['goods_id'] == goods.id
        assert data['quantity'] == 25
        assert data['reason'] == "return"
        assert data['remark'] == "New removal record from test"

        new_inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id,location.warehouse_id)
        new_total_stock = new_inventory.total_stock
        new_onhand_stock = new_inventory.onhand_stock

        assert new_total_stock == old_total_stock
        assert new_onhand_stock == old_onhand_stock - 25


def test_get_removal_record_details(client, access_token):
    """
    测试根据 record_id 获取单条下架记录 (GET /removal/<record_id>)
    """
    with client.application.app_context():
        record = get_removal_record()
        response = client.get(f'/removal/{record.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == record.id
        assert data['goods_id'] == record.goods_id
        assert data['quantity'] == record.quantity
        assert data['reason'] == record.reason


# def test_update_removal_record(client, access_token):
#     """
#     测试更新下架记录 (PUT /removal/<record_id>)
#     """
#     with client.application.app_context():
#         record = get_removal_record()
#         goods = get_goods()
#         location = get_location()
#         operator = get_operator_user()

#     response = client.put(f'/removal/{record.id}', headers={
#         'Authorization': f'Bearer {access_token}'
#     }, json={
#         "goods_id": goods.id,
#         "location_id": location.id,
#         "quantity": 9,
#         "operator_id": operator.id,
#         "reason": "transfer",
#         "remark": "Updated removal info"
#     })
#     assert response.status_code == 200
#     data = response.get_json()
#     assert data['id'] == record.id
#     assert data['quantity'] == 9
#     assert data['reason'] == "transfer"
#     assert data['remark'] == "Updated removal info"



# def test_delete_removal_record(client, access_token):
#     """
#     测试删除下架记录 (DELETE /removal/<record_id>)
#     """
#     with client.application.app_context():
#         record = get_removal_record()
#         record_id = record.id

#     response = client.delete(f'/removal/{record_id}', headers={
#         'Authorization': f'Bearer {access_token}'
#     })
#     assert response.status_code == 200
#     data = response.get_json()
#     assert data['message'] == "Removal record deleted successfully"

#     # 再次检查数据库确保已删除
#     with client.application.app_context():
#         deleted_record = get_removal_record_by_id(record_id)
#         assert deleted_record is None

# def test_delete_removal_record_by_user_without_permission(client, access_operator_token):
#     """
#     测试没有权限的用户删除下架记录 (DELETE /removal/<record_id>)
#     预期返回 403 Forbidden，并且数据库中的记录不会被删除。
#     """
#     with client.application.app_context():
#         record = get_removal_record()
#         record_id = record.id

#     response = client.delete(
#         f'/removal/{record_id}',
#         headers={'Authorization': f'Bearer {access_operator_token}'}
#     )
#     assert response.status_code == 403
#     data = response.get_json()
#     assert data['message'] == "Permission denied"

#     # 再次检查数据库确保记录未被删除
#     with client.application.app_context():
#         still_existing_record = get_removal_record_by_id(record_id)
#         assert still_existing_record is not None

def test_bulk_create_removal_records(client, access_token):
    """
    测试批量创建下架记录 (POST /removal/bulk)
    预期接收一个列表，每个元素符合 removal_record_input_model 定义的 JSON 对象
    """
    with client.application.app_context():
        goods = get_goods()
        location = get_location()
        operator = get_operator_user()

        # 构造批量创建的数据列表（示例中创建两条记录）
        bulk_payload = [
            {
                "goods_id": goods.id,
                "location_id": location.id,
                "quantity": 30,
                "operator_id": operator.id,
                "reason": "damaged",
                "remark": "Bulk removal record 1"
            },
            {
                "goods_id": goods.id,
                "location_id": location.id,
                "quantity": 5,
                "operator_id": operator.id,
                "reason": "expired",
                "remark": "Bulk removal record 2"
            }
        ]

        response = client.post(
            '/removal/bulk',
            headers={'Authorization': f'Bearer {access_token}'},
            json=bulk_payload
        )
        assert response.status_code == 201
        data = response.get_json()
        # 返回的数据应该为列表，且包含至少两条记录
        assert isinstance(data, list)
        assert len(data) >= 2

        # 验证第一条记录
        record1 = data[0]
        assert record1['goods_id'] == goods.id
        assert record1['location_id'] == location.id
        assert record1['quantity'] == 30
        assert record1['reason'] == "damaged"
        assert record1['remark'] == "Bulk removal record 1"

        # 验证第二条记录
        record2 = data[1]
        assert record2['goods_id'] == goods.id
        assert record2['location_id'] == location.id
        assert record2['quantity'] == 5
        assert record2['reason'] == "expired"
        assert record2['remark'] == "Bulk removal record 2"