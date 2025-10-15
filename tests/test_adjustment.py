import pytest
from warehouse.adjustment.services import AdjustmentService

from .helpers import *


# ---------------------------------------------------------
# Tests for View Layer (adjustment API Routes)
# ---------------------------------------------------------

def test_create_adjustment(client, access_token):
    """
    Test creating an Adjustment (POST /adjustment/)
    """
    response = client.post(
        '/adjustment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "warehouse_id": 1,
            "adjustment_reason": "ADJ-NEW",
            "status": "pending",
            "is_active": True,
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['adjustment_reason'] == "ADJ-NEW"


def test_get_adjustments(client, access_token):
    """
    Test retrieving the list of Adjustments (GET /adjustment/)
    """
    response = client.get(
        '/adjustment/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_adjustment(client, access_token):
    """
    Test retrieving a single Adjustment (GET /adjustment/<adjustment_id>)
    """
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id

        response = client.get(
            f'/adjustment/{adjustment_id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == adjustment_id
        assert data['adjustment_reason'] == adjustment.adjustment_reason


def test_update_adjustment(client, access_token):
    """
    Test updating an Adjustment (PUT /adjustment/<adjustment_id>)
    """
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id

        response = client.put(
            f'/adjustment/{adjustment_id}',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                "adjustment_reason": "ADJ-UPDATED",
                "status": "pending",
                "is_active": False
            }
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['adjustment_reason'] == "ADJ-UPDATED"
        assert data['is_active'] is False


def test_delete_adjustment(client, access_token):
    """
    Test deleting an Adjustment (DELETE /adjustment/<adjustment_id>)
    """
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id

        response = client.delete(
            f'/adjustment/{adjustment_id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == "Adjustment deleted successfully"

        deleted_adjustment = get_adjustment_by_id(adjustment_id)
        assert deleted_adjustment is None

# ---------------------------------------
# 以下为 services.py 业务逻辑层的用例
# ---------------------------------------

@pytest.fixture
def adjustment_service(client):
    """
    Fixture：初始化 AdjustmentService 服务实例
    """
    with client.application.app_context():
        adjustment_service = AdjustmentService()
        yield adjustment_service


def test_create_adjustment_service(client):
    """
    测试 AdjustmentService 创建调整记录
    """
    with client.application.app_context():
        user = get_admin_user()
        adjustment_data = {
            "warehouse_id": 1,
            "adjustment_reason": "库存调整",
        }

        # 模拟调用服务层的创建方法
        adjustment = AdjustmentService.create_adjustment(adjustment_data,user.id)

        assert adjustment is not None
        assert adjustment.adjustment_reason == adjustment_data["adjustment_reason"]
        assert adjustment.created_by == user.id


def test_get_adjustment_by_id_service(client):
    """
    测试 AdjustmentService 根据 ID 获取调整记录
    """
    with client.application.app_context():
        adjustment = get_adjustment()  # 获取第一个调整记录
        fetched_adjustment = AdjustmentService.get_adjustment(adjustment.id)

        assert fetched_adjustment is not None
        assert fetched_adjustment.id == adjustment.id


def test_update_adjustment_service(client):
    """
    测试 AdjustmentService 更新调整记录
    """
    with client.application.app_context():
        
        adjustment = get_adjustment()  # 获取第一个调整记录
        updated_data = {
            "adjustment_reason": "库存补充"
        }

        updated_adjustment = AdjustmentService.update_adjustment(adjustment.id,updated_data)

        assert updated_adjustment is not None
        assert updated_adjustment.adjustment_reason == updated_data["adjustment_reason"]


def test_delete_adjustment_service(client):
    """
    测试 AdjustmentService 删除调整记录
    """
    with client.application.app_context():
        adjustment = get_adjustment()  # 获取第一个调整记录
        result = AdjustmentService.delete_adjustment(adjustment.id)

        assert result is None

    with client.application.app_context():
        deleted_adjustment = get_adjustment_by_id(adjustment.id)
        assert deleted_adjustment is None


def test_approve_adjustment_service(client):
    """
    测试 AdjustmentService 审批调整记录
    """
    with client.application.app_context():
        user = get_admin_user()
        adjustment = get_adjustment()  # 获取第一个调整记录
        result = AdjustmentService.approve_adjustment(adjustment.id,user.id)

        assert result is not None
        assert result.status == "approved"