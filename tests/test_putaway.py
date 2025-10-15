from sqlalchemy import func

from warehouse.inventory.services import InventoryService
from .helpers import *

def test_get_putaway_records(client, access_token):
    """
    测试分页获取上架记录列表 (GET /putaway/)
    """
    response = client.get('/putaway/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    # data 中通常包含 items, total, page, per_page, ...
    assert 'items' in data
    assert data['total'] >= 1  # 预期至少存在一个 PutawayRecord


def test_create_putaway_record(client, access_token):
    """
    测试创建新的上架记录 (POST /putaway/)
    """
    with client.application.app_context():
        # 获取 goods_id, location_id 以及 operator_id
        goods = get_goods()
        location = get_location()
        operator = get_operator_user()

        inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id,location.warehouse_id)
        InventoryService.asn_received(goods.id, location.warehouse_id, 100)
        InventoryService.asn_completed(goods.id, location.warehouse_id, 50, 50)

        old_total_stock = inventory.total_stock
        old_onhand_stock = inventory.onhand_stock
        old_sorted_stock = inventory.sorted_stock

        response = client.post('/putaway/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            "goods_id": goods.id,
            "location_id": location.id,
            "warehouse_id": location.warehouse_id,
            "quantity": 50,
            "operator_id": operator.id,
            "remark": "New putaway record from test"
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['goods_id'] == goods.id
        assert data['quantity'] == 50
        assert data['remark'] == "New putaway record from test"

        new_inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id,location.warehouse_id)
        new_total_stock = new_inventory.total_stock
        new_onhand_stock = new_inventory.onhand_stock
        new_sorted_stock = new_inventory.sorted_stock

        assert new_total_stock == old_total_stock
        assert new_onhand_stock == old_onhand_stock + 50
        assert new_sorted_stock == old_sorted_stock - 50


def test_get_putaway_record_details(client, access_token):
    """
    测试根据 record_id 获取单条上架记录 (GET /putaway/<record_id>)
    """
    with client.application.app_context():
        record = get_putaway_record()
        response = client.get(f'/putaway/{record.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == record.id
        assert data['goods_id'] == record.goods_id
        assert data['quantity'] == record.quantity


# def test_update_putaway_record(client, access_token):
#     """
#     测试更新上架记录 (PUT /putaway/<record_id>)
#     """
#     with client.application.app_context():
#         record = get_putaway_record()
#         goods = get_goods()
#         location = get_location()
#         operator = get_operator_user()

#     response = client.put(f'/putaway/{record.id}', headers={
#         'Authorization': f'Bearer {access_token}'
#     }, json={
#         "goods_id": goods.id,
#         "location_id": location.id,
#         "quantity": 999,
#         "operator_id": operator.id,
#         "remark": "Updated putaway info"
#     })
#     assert response.status_code == 200
#     data = response.get_json()
#     assert data['id'] == record.id
#     assert data['quantity'] == 999
#     assert data['remark'] == "Updated putaway info"


# def test_delete_putaway_record(client, access_token):
#     """
#     测试删除上架记录 (DELETE /putaway/<record_id>)
#     """
#     with client.application.app_context():
#         record = get_putaway_record()
#         record_id = record.id

#     response = client.delete(f'/putaway/{record_id}', headers={
#         'Authorization': f'Bearer {access_token}'
#     })
#     assert response.status_code == 200
#     data = response.get_json()
#     assert data['message'] == "Putaway record deleted successfully"

#     # 再次检查数据库确保已删除
#     with client.application.app_context():
#         deleted_record = get_putaway_record_by_id(record_id)
#         assert deleted_record is None

# def test_delete_putaway_record_by_user_without_permission(client, access_operator_token):
#     """
#     测试普通操作员删除上架记录 (无权限)
#     """
#     with client.application.app_context():
#         record = get_putaway_record()
#         record_id = record.id

#     response = client.delete(f'/putaway/{record_id}', headers={
#         'Authorization': f'Bearer {access_operator_token}'
#     })
#     assert response.status_code == 403
#     data = response.get_json()
#     assert data['message'] == "Permission denied"
#     # 再次检查数据库确保未删除
#     with client.application.app_context():
#         deleted_record = get_putaway_record_by_id(record_id)
#         assert deleted_record is not None

def test_bulk_create_putaway_record(client, access_token):
    """
    测试批量创建上架记录 (POST /putaway/bulk)
    """
    with client.application.app_context():
        goods = get_goods()
        location = get_location()
        operator = get_admin_user()
        putaway = get_putaway_record()

        InventoryService.asn_received(goods.id, location.warehouse_id, 1000)
        InventoryService.asn_completed(goods.id, location.warehouse_id, 1000, 1000)

        # 构造批量创建的数据列表（示例创建两条记录）
        bulk_payload = [
            {
                "goods_id": goods.id,
                "location_id": location.id,
                "quantity": 100,
                "remark": "Bulk putaway record 1"
            },
            {
                "goods_id": goods.id,
                "location_id": location.id,
                "quantity": 150,
                "remark": "Bulk putaway record 2"
            }
        ]

        

        response = client.post(
            '/putaway/bulk',
            headers={'Authorization': f'Bearer {access_token}'},
            json=bulk_payload
        )
        assert response.status_code == 201
        data = response.get_json()
        # 返回的数据应该是一个列表
        assert isinstance(data, list)
        assert len(data) >= 2

        
        # 从数据库查询新创建的记录
        records = PutawayRecord.query.filter(
            PutawayRecord.goods_id == goods.id,
            PutawayRecord.location_id == location.id,
            PutawayRecord.id > putaway.id  # 时间范围过滤
        ).order_by(PutawayRecord.id.asc()).all()

        # 验证记录数量
        assert len(records) == 2, "应准确创建2条记录"

        # 验证基础字段
        expected_data = [
            (100, "Bulk putaway record 1"),
            (150, "Bulk putaway record 2")
        ]
        
        for idx, record in enumerate(records):
            # 验证核心业务字段
            assert record.quantity == expected_data[idx][0], f"记录{idx}数量不符"
            assert record.remark == expected_data[idx][1], f"记录{idx}备注不符"
            
            # 验证自动生成的字段
            assert record.operator_id == operator.id, "操作员ID应自动关联"
            
            # 验证外键约束
            assert record.goods == goods, "商品关联关系错误"
            assert record.location == location, "库位关联关系错误"