from extensions.error import BadRequestException, NotFoundException
from warehouse.asn.services import ASNService
from .helpers import *
import pytest

# -------------------- 视图层测试用例 --------------------

def test_create_asn(client, access_token):
    """测试创建 ASN (POST /asn/)"""
    with client.application.app_context():
        warehouse = get_warehouse()
        supplier = get_supplier()
        carrier = get_carrier()
        goods = get_goods()
        assert supplier is not None
        assert carrier is not None
        assert goods is not None

    response = client.post(
        '/asn/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'warehouse_id': warehouse.id,
            'supplier_id': supplier.id,
            'carrier_id': carrier.id,
            'asn_type': 'inbound',
            'status': 'pending',
            'remark': 'Test ASN creation',
            'details': [
                {
                    "asn_id": 999,
                    "goods_id": goods.id,
                    "quantity": 10,
                    "actual_quantity": 0,
                    "remark": "Sample detail"
                }
            ]
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert len(data['details']) == 1


def test_get_asns(client, access_token):
    """测试获取 ASN 列表 (GET /asn/)"""
    response = client.get(
        '/asn/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_asn_detail(client, access_token):
    """测试获取单个 ASN (GET /asn/<asn_id>)"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None

    response = client.get(
        f'/asn/{asn.id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == asn.id


def test_update_asn(client, access_token):
    """测试更新 ASN (PUT /asn/<asn_id>)"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None
        asn_id = asn.id

    response = client.put(
        f'/asn/{asn_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'asn_type': 'return_from_customer',
            'status': 'received',
            'remark': 'Updated remark'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'received'


def test_create_asn_detail(client, access_token):
    """测试创建 ASNDetail (POST /asn/<asn_id>/details/)"""
    with client.application.app_context():
        asn = get_asn()
        goods = get_goods()
        assert asn is not None
        assert goods is not None

    response = client.post(
        f'/asn/{asn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "asn_id": asn.id,
            "goods_id": goods.id,
            "quantity": 5,
            "actual_quantity": 2,
            "damage_quantity": 1,
            "remark": "New detail item"
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['quantity'] == 5
    assert data['damage_quantity'] == 1


def test_get_details_list(client, access_token):
    """测试获取 ASN 下所有 ASNDetail (GET /asn/<asn_id>/details/)"""
    with client.application.app_context():
        asn = get_asn()

    response = client.get(
        f'/asn/{asn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_update_asn_detail(client, access_token):
    """测试更新 ASNDetail (PUT /asn/<asn_id>/details/<detail_id>)"""
    with client.application.app_context():
        asn = get_asn()
        detail = get_asn_detail_by_asn_id(asn.id)
        assert detail is not None

    response = client.put(
        f'/asn/{asn.id}/details/{detail.id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'quantity': 99,
            'actual_quantity': 50,
            'remark': 'Updated detail remark'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['quantity'] == 99
    assert data['actual_quantity'] == 50
    assert data['remark'] == 'Updated detail remark'


def test_delete_asn_detail(client, access_token):
    """测试删除 ASNDetail (DELETE /asn/<asn_id>/details/<detail_id>)"""
    with client.application.app_context():
        asn = get_asn()
        detail = get_asn_detail_by_asn_id(asn.id)
        detail_id = detail.id

    response = client.delete(
        f'/asn/{asn.id}/details/{detail_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "ASNDetail deleted successfully"

    with client.application.app_context():
        deleted = get_asn_detail_by_id(detail_id)
        assert deleted is None


def test_delete_asn(client, access_token):
    """测试删除 ASN (DELETE /asn/<asn_id>)"""
    with client.application.app_context():
        asn = get_asn_by_id(2)
        asn_id = asn.id

    response = client.delete(
        f'/asn/{asn_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "ASN deleted successfully"

    with client.application.app_context():
        deleted_asn = get_asn_by_id(asn_id)
        assert deleted_asn is None


def test_receive_asn_success(client, access_token):
    """测试接收有效的 ASN"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None
        original_status = asn.status

        response = client.put(
            f'/asn/{asn.id}/receive/',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()

        assert data['status'] == 'received'
        with client.application.app_context():
            updated_asn = get_asn_by_id(asn.id)
            assert updated_asn.status == 'received'
            assert updated_asn.status != original_status


def test_receive_asn_not_found(client, access_token):
    """测试接收不存在的 ASN"""
    invalid_asn_id = 99999
    response = client.put(
        f'/asn/{invalid_asn_id}/receive/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data.get('code') == 13001
    assert "not found" in data.get('message', '').lower()


# -------------------- 服务层测试用例 --------------------

def test_asn_service__update_asn_status(client):
    """测试更新 ASN 状态"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None

        updated_asn = ASNService._update_asn_status(asn, "received")
        assert updated_asn.status == "received"


def test_asn_service_sync_details(client):
    """测试同步 ASN 明细"""
    with client.application.app_context():
        asn = get_asn()
        original_count = len(asn.details)
        user = get_operator_user()
        existing_detail = asn.details[0]
        new_goods_id = 2

        # 测试混合操作
        ASNService.sync_asn_details(asn.id, [
            {
                "id": existing_detail.id,
                "goods_id": existing_detail.goods_id,
                "quantity": 200,
                "remark": "Updated"
            },
            {
                "goods_id": new_goods_id,
                "quantity": 100,
                "remark": "New Item"
            }
        ], created_by=user.id)

        # 验证总数变化
        assert len(asn.details) == 2
        
        # 验证更新记录
        updated_detail = db.session.get(ASNDetail, existing_detail.id)
        assert updated_detail.quantity == 200
        assert updated_detail.remark == "Updated"
        
        # 验证新增记录
        new_detail = ASNDetail.query.filter_by(goods_id=new_goods_id).first()
        assert new_detail is not None
        assert new_detail.quantity == 100

        # 测试删除操作
        ASNService.sync_asn_details(asn.id, [
            {"goods_id": new_goods_id, "quantity": 50}
        ], created_by=user.id)
        assert len(asn.details) == 1
        assert db.session.get(ASNDetail, existing_detail.id) is None

        # 测试无效状态操作
        ASNService._update_asn_status(asn, 'received')
        with pytest.raises(BadRequestException) as excinfo:
            ASNService.sync_asn_details(asn.id, [], created_by=user.id)
        assert 16006 == excinfo.value.biz_code


def test_asn_service_update_asn_detail(client):
    """测试更新单个 ASN 明细"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None
        detail = get_asn_detail_by_asn_id(asn.id)
        assert detail is not None

        old_qty = detail.quantity
        old_remark = detail.remark

        updated_detail = ASNService.update_asn_detail(asn.id, detail.id, {
            "quantity": 999,
            "remark": "Service updated note"
        })
        assert updated_detail.id == detail.id
        assert updated_detail.quantity == 999
        assert updated_detail.remark == "Service updated note"
        assert updated_detail.quantity != old_qty
        assert updated_detail.remark != old_remark


def test_asn_service_receive_asn(client):
    """测试接收 ASN"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None

        updated_asn = ASNService.receive_asn(asn.id)
        assert updated_asn.status == "received"


def test_asn_service_complete_asn(client):
    """测试完成 ASN"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None

        asn.status = "received"
        db.session.commit()

        updated_asn = ASNService.complete_asn(asn.id)
        assert updated_asn.status == "completed"


def test_close_asn_success_pending(client):
    """测试关闭待处理的 ASN"""
    with client.application.app_context():
        asn = get_asn()
        asn.status = 'pending'
        db.session.commit()

        closed_asn = ASNService.close_asn(asn.id)
        assert closed_asn.status == 'closed'


@pytest.mark.parametrize("status", ['received', 'completed', 'closed'])
def test_close_asn_failure_non_pending(client, status):
    """测试关闭非待处理的 ASN"""
    with client.application.app_context():
        asn = get_asn()
        asn.status = status
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            ASNService.close_asn(asn.id)
        assert "Cannot close a non-pending ASN" in str(excinfo.value)


def test_close_asn_not_found(client):
    """测试关闭不存在的 ASN"""
    invalid_asn_id = 99999
    with client.application.app_context():
        with pytest.raises(NotFoundException) as exc_info:
            ASNService.close_asn(invalid_asn_id)
        assert "not found" in str(exc_info.value).lower()
        assert exc_info.value.biz_code == 13001


def test_asn_service__update_asn_status_invalid_status(client):
    """测试更新无效的 ASN 状态"""
    with client.application.app_context():
        asn = get_asn()
        assert asn is not None

        with pytest.raises(BadRequestException) as excinfo:
            ASNService._update_asn_status(asn, "invalid_status")
        assert "Invalid status" in str(excinfo.value)


def test_asn_service_update_asn_detail_invalid_detail(client):
    """测试更新不存在的 ASN 明细"""
    with client.application.app_context():
        asn = get_asn()
        with pytest.raises(NotFoundException) as excinfo:
            ASNService.update_asn_detail(asn.id, -999, {"quantity": 999})
        assert "not found" in str(excinfo.value).lower()
        assert excinfo.value.biz_code == 13001