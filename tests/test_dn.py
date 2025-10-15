from extensions.error import BadRequestException, NotFoundException
from warehouse.dn.services import DNService

from warehouse.inventory.services import InventoryService
from .helpers import *

# -------------------- 以下为测试各个接口 (views.py) 的用例 --------------------

def test_create_dn(client, access_token):
    """
    测试创建 DN (POST /dn/)
    """
    with client.application.app_context():
        warehouse = Warehouse.query.first()
        carrier = Carrier.query.first()
        recipient = Recipient.query.first()
        goods = Goods.query.first()
        assert carrier is not None
        assert recipient is not None
        assert goods is not None

    response = client.post(
        '/dn/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'recipient_id': recipient.id,
            'warehouse_id': warehouse.id,
            'shipping_address': '456 Another Street',
            'expected_shipping_date': '2025-01-01',
            'carrier_id': carrier.id,
            'dn_type': 'damage_to_supplier',
            'status': 'pending',
            'remark': 'Test DN creation',
            'details': [
                {
                    "dn_id": 999,  # 这里填什么无所谓，后端会用 path 中的 dn_id
                    "goods_id": goods.id,
                    "quantity": 10,
                    "picked_quantity": 0,
                    "remark": "Sample detail"
                }
            ]
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['dn_type'] == 'damage_to_supplier'
    assert len(data['details']) == 1


def test_get_dns(client, access_token):
    """
    测试获取 DN 列表 (GET /dn/)
    """
    response = client.get(
        '/dn/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    # data 中通常包含 items, total, page, per_page, ...
    assert 'items' in data
    assert data['total'] >= 1  # 预期至少存在一个 DN


def test_get_dn_detail(client, access_token):
    """
    测试获取单个 DN (GET /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

    response = client.get(
        f'/dn/{dn.id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == dn.id
    assert data['status'] == dn.status


def test_update_dn(client, access_token):
    """
    测试更新 DN (PUT /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None
        dn_id = dn.id

    response = client.put(
        f'/dn/{dn_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'shipping_address': '999 Updated Street',
            'status': 'picked',
            'remark': 'Updated DN remark'
        }
    )
    # 如果 DN 原本是 pending，更新为 picking 应该没问题
    # 若非 pending，需要根据视图层的逻辑返回 400
    # 这里假设能成功更新
    assert response.status_code == 200
    data = response.get_json()
    assert data['shipping_address'] == '999 Updated Street'
    assert data['status'] == 'picked'
    assert data['remark'] == 'Updated DN remark'


def test_create_dn_detail(client, access_token):
    """
    测试创建 DNDetail (POST /dn/<dn_id>/details/)
    """
    with client.application.app_context():
        dn = DN.query.first()
        goods = Goods.query.first()
        assert dn is not None
        assert goods is not None

    response = client.post(
        f'/dn/{dn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "dn_id": dn.id,  # 实际后端会用 path 的 dn_id
            "goods_id": goods.id,
            "quantity": 5,
            "picked_quantity": 2,
            "remark": "New detail item"
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['quantity'] == 5


def test_get_dn_details_list(client, access_token):
    """
    测试获取 DN 下所有 DNDetail (GET /dn/<dn_id>/details/)
    """
    with client.application.app_context():
        dn = DN.query.first()

    response = client.get(
        f'/dn/{dn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1  # 应该至少有一条明细


def test_update_dn_detail(client, access_token):
    """
    测试更新 DNDetail (PUT /dn/<dn_id>/details/<detail_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert detail is not None

    response = client.put(
        f'/dn/{dn.id}/details/{detail.id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'quantity': 99,
            'picked_quantity': 50,
            'remark': 'Updated detail remark'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['quantity'] == 99
    assert data['picked_quantity'] == 50
    assert data['remark'] == 'Updated detail remark'


def test_delete_dn_detail(client, access_token):
    """
    测试删除 DNDetail (DELETE /dn/<dn_id>/details/<detail_id>)
    """
    with client.application.app_context():
        dn = get_dn_by_id(2)
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        detail_id = detail.id

    response = client.delete(
        f'/dn/{dn.id}/details/{detail_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "DNDetail deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted = db.session.get(DNDetail, detail_id)
        assert deleted is None


def test_delete_dn(client, access_token):
    """
    测试删除 DN (DELETE /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = get_dn_by_id(2)
        dn_id = dn.id

    response = client.delete(
        f'/dn/{dn_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "DN deleted successfully"

    with client.application.app_context():
        deleted_dn = db.session.get(DN, dn_id)
        assert deleted_dn is None


# -------------------- 以下为直接测试服务层 (services.py) 的用例 --------------------

def test_dn_service__update_dn_status(client):
    """
    测试通过 Service 更新 DN 状态
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

        updated_dn = DNService._update_dn_status(dn, "picked")
        assert updated_dn.status == "picked"

def test_sync_dn_details(client):
    """测试DN明细同步全流程"""
    with client.application.app_context():
        dn = get_dn()
        user = get_operator_user()
        existing_detail = dn.details[0]
        new_goods_id = 2

        # 测试混合操作（更新+新增+删除）
        new_data = [
            {  # 更新记录
                "id": existing_detail.id,
                "goods_id": existing_detail.goods_id,
                "quantity": 200,
                "remark": "Updated"
            },
            {  # 新增记录
                "goods_id": new_goods_id,
                "quantity": 30,
                "picked_quantity": 5
            }
        ]
        
        # 执行同步
        DNService.sync_dn_details(dn, new_data, user.id)

        # 验证总数变化
        assert len(dn.details) == 2
        # 验证更新记录
        updated_detail = DNService.get_dn_detail(dn.id, existing_detail.id)
        assert updated_detail.quantity == 200
        assert updated_detail.remark == "Updated"
        # 验证新增记录
        new_detail = DNDetail.query.filter_by(goods_id=new_goods_id).first()
        assert new_detail is not None
        assert new_detail.quantity == 30

        # 测试删除操作
        DNService.sync_dn_details(dn.id, [
            {"goods_id": new_goods_id, "quantity": 50}  # 只保留新记录
        ],created_by=user.id)
        assert len(dn.details) == 1
        assert db.session.get(DNDetail,existing_detail.id) is None

        # 测试无效状态操作
        DNService._update_dn_status(dn, 'picked')
        with pytest.raises(BadRequestException) as excinfo:
            DNService.sync_dn_details(dn.id, [],created_by=user.id)

def test_duplicate_goods_validation(client):
    """测试商品重复校验"""
    with client.application.app_context():
        dn = get_dn()
        user = get_operator_user()
        with pytest.raises(BadRequestException) as e:
            DNService.sync_dn_details(dn, [
                {"goods_id": 1, "quantity": 10},
                {"goods_id": 1, "quantity": 20}
            ],created_by=user.id)
        assert "Duplicate goods_id: 1" in str(e.value)

def test_dn_service_update_dn_detail(client):
    """
    测试通过 Service 更新单个 DNDetail
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert detail is not None

        old_qty = detail.quantity
        old_remark = detail.remark

        updated_detail = DNService.update_dn_detail(dn.id, detail.id, {
            "quantity": 999,
            "remark": "Service updated note"
        })
        assert updated_detail.id == detail.id
        assert updated_detail.quantity == 999
        assert updated_detail.remark == "Service updated note"
        assert updated_detail.quantity != old_qty
        assert updated_detail.remark != old_remark

def test_dn_service_pick_dn(client):
    """
    测试通过 Service 将 DN 标记为 'picked'
    """
    with client.application.app_context():
        dn = DN.query.first()
        dn.status = "in_progress"  # 假设当前状态为 in_progress
        db.session.commit()

        updated_dn = DNService.picking_dn(dn.id)
        assert updated_dn.status == "picked"

def test_dn_service_packing_dn(client):
    """
    测试通过 Service 将 DN 标记为 'packed'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 picked 状态，才能 pack
        dn.status = "picked"
        db.session.commit()

        updated_dn = DNService.packing_dn(dn.id)
        assert updated_dn.status == "packed"

def test_dn_service_delivery_dn(client):
    """
    测试通过 Service 将 DN 标记为 'delivered'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 packed 状态，才能 delivery
        dn.status = "packed"
        db.session.commit()

        updated_dn = DNService.delivery_dn(dn.id)
        assert updated_dn.status == "delivered"


def test_dn_service_complete_dn(client):
    """
    测试通过 Service 将 DN 标记为 'completed'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 delivered 状态，才能 complete
        dn.status = "delivered"
        db.session.commit()

        updated_dn = DNService.complete_dn(dn.id)
        assert updated_dn.status == "completed"

def test_close_dn_success_pending(client):
    """
    测试当 DN 状态为 pending 时，调用 close_dn 后状态更新为 closed
    """
    with client.application.app_context():
        dn = get_dn()
        # 确保 DN 状态为 pending
        dn.status = 'pending'
        db.session.commit()

        closed_dn = DNService.close_dn(dn.id)
        assert closed_dn.status == 'closed', "DN 状态应更新为 closed"


# -------------------- 一些负面用例 / 异常场景 --------------------

@pytest.mark.parametrize("status", ['picked', 'packed', 'delivered', 'completed','closed'])
def test_close_dn_failure_not_pending(client, status):
    """
    测试当 DN 状态不为 pending 时，调用 close_dn 应抛出 ValueError 异常
    """
    with client.application.app_context():
        dn = get_dn()
        dn.status = status
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            DNService.close_dn(dn.id)
        assert 16022 == excinfo.value.biz_code

def test_close_dn_not_found(client):
    """
    测试传入一个不存在的 DN id 时，close_dn 应抛出 NotFound 异常
    """
    invalid_dn_id = 99999  # 假设此 id 不存在
    with client.application.app_context():
        with pytest.raises(NotFoundException) as excinfo:
            DNService.close_dn(invalid_dn_id)

def test_dn_service__update_dn_status_invalid_status(client):
    """
    测试更新 DN 状态时，若传入无效的 status 应抛出 ValueError
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

        with pytest.raises(BadRequestException) as excinfo:
            DNService._update_dn_status(dn, "invalid_status")
        assert "Invalid DN status" in str(excinfo.value)


def test_dn_service_update_dn_detail_invalid_detail(client):
    """
    测试对不存在的 detail_id 更新 DNDetail，应抛出 NotFound
    """
    with client.application.app_context():
        dn = DN.query.first()
        with pytest.raises(NotFoundException) as excinfo:
            DNService.update_dn_detail(dn.id, -999, {"quantity": 999})
        assert "404" in str(excinfo.value) or "Not Found" in str(excinfo.value)


def test_dn_service_update_dn_detail_success(client):
    """
    测试当更新数据均符合约束时，update_dn_detail 可以成功
    """
    with client.application.app_context():
        dn = DN.query.first()
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert dn is not None
        assert detail is not None

        # 设置初始值
        detail.quantity = 10
        detail.picked_quantity = 5
        db.session.commit()

        updated_detail = DNService.update_dn_detail(
            dn.id,
            detail.id,
            {
                "quantity": 20,
                "picked_quantity": 10,
                "remark": "All constraints satisfied"
            }
        )
        assert updated_detail.quantity == 20
        assert updated_detail.picked_quantity == 10
        assert updated_detail.remark == "All constraints satisfied"

