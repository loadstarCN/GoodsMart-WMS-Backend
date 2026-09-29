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


def test_create_adjustment_ignores_status_and_created_by(client, access_token):
    """B-12：创建时 status / created_by / is_active 不接受客户端输入"""
    with client.application.app_context():
        admin_id = get_admin_user().id
    response = client.post(
        '/adjustment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={"warehouse_id": 1, "status": "approved", "is_active": False, "created_by": 999}
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['is_active'] is True
    assert data['created_by'] == admin_id


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
                "status": "approved",   # B-12：status / is_active 不接受客户端输入，应被忽略
                "is_active": False
            }
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['adjustment_reason'] == "ADJ-UPDATED"
        assert data['status'] == "pending"
        assert data['is_active'] is True


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
    测试 AdjustmentService 审批调整记录（审批人必须不是创建人）
    """
    with client.application.app_context():
        approver = get_company_admin_user()
        adjustment = get_adjustment()  # 获取第一个调整记录（由 admin 创建）
        assert adjustment.created_by != approver.id
        result = AdjustmentService.approve_adjustment(adjustment.id, approver.id)

        assert result is not None
        assert result.status == "approved"
        assert result.approved_by == approver.id


def test_approve_adjustment_creator_cannot_self_approve(client, access_token):
    """B-34：创建人不能审批自己的调整单 → 403"""
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id
        assert adjustment.created_by == get_admin_user().id

    response = client.put(
        f'/adjustment/{adjustment_id}/approve/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 403
    assert response.get_json()['code'] == 16040


def test_approve_adjustment_by_other_user(client, access_company_admin_token):
    """非创建人且有权限（company_all_access）可以审批"""
    with client.application.app_context():
        adjustment_id = get_adjustment().id

    response = client.put(
        f'/adjustment/{adjustment_id}/approve/',
        headers={'Authorization': f'Bearer {access_company_admin_token}'}
    )
    assert response.status_code == 200
    assert response.get_json()['status'] == 'approved'


def test_adjustment_details_frozen_after_approval(client, access_token, access_company_admin_token):
    """B-34：approved 之后明细增删改一律 409"""
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id
        detail_id = adjustment.details[0].id
        goods_id = adjustment.details[0].goods_id
        location_id = adjustment.details[0].location_id

    approve = client.put(f'/adjustment/{adjustment_id}/approve/',
                         headers={'Authorization': f'Bearer {access_company_admin_token}'})
    assert approve.status_code == 200

    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.post(f'/adjustment/{adjustment_id}/details/', headers=headers, json={
        'goods_id': goods_id, 'location_id': location_id,
        'system_quantity': 1, 'actual_quantity': 3, 'adjustment_quantity': 2
    })
    assert response.status_code == 409
    assert response.get_json()['code'] == 16039

    response = client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=headers,
                          json={'actual_quantity': 99})
    assert response.status_code == 409

    response = client.delete(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=headers)
    assert response.status_code == 409


def test_adjustment_detail_quantity_mismatch(client, access_token):
    """B-18：adjustment_quantity ≠ actual − system → 400（而不是撞 CHECK 变 500）"""
    with client.application.app_context():
        adjustment = get_adjustment()
        adjustment_id = adjustment.id
        detail = adjustment.details[0]
        detail_id, goods_id, location_id = detail.id, detail.goods_id, detail.location_id

    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.post(f'/adjustment/{adjustment_id}/details/', headers=headers, json={
        'goods_id': goods_id, 'location_id': location_id,
        'system_quantity': 10, 'actual_quantity': 15, 'adjustment_quantity': 3
    })
    assert response.status_code == 400
    assert response.get_json()['code'] == 16038

    # 负数实际数量 / 非整数也 400
    response = client.post(f'/adjustment/{adjustment_id}/details/', headers=headers, json={
        'goods_id': goods_id, 'location_id': location_id,
        'system_quantity': 10, 'actual_quantity': -1
    })
    assert response.status_code == 400
    response = client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=headers,
                          json={'actual_quantity': 'many'})
    assert response.status_code == 400

    # 缺省 adjustment_quantity 时自动计算；一致时正常
    response = client.post(f'/adjustment/{adjustment_id}/details/', headers=headers, json={
        'goods_id': goods_id, 'location_id': location_id,
        'system_quantity': 10, 'actual_quantity': 15
    })
    assert response.status_code == 201
    assert response.get_json()['adjustment_quantity'] == 5

    response = client.put(f'/adjustment/{adjustment_id}/details/{detail_id}', headers=headers,
                          json={'actual_quantity': 12, 'adjustment_quantity': 2})
    assert response.status_code == 200
    assert response.get_json()['adjustment_quantity'] == 2


def test_create_adjustment_from_cyclecount_requires_completed(client, access_token):
    """B-34：盘点任务未 completed 不能生成调整单 → 400"""
    with client.application.app_context():
        cyclecount_id = get_cyclecount_task().id

    response = client.post(
        f'/adjustment/create_adjustment_by_cyclecount/{cyclecount_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 400
    assert response.get_json()['code'] == 16041


def test_create_adjustment_from_completed_cyclecount(client, access_token):
    """盘点 pending → in_progress → 保存实盘 → completed 后可生成调整单，明细数量自洽"""
    from warehouse.cyclecount.services import CycleCountTaskService
    with client.application.app_context():
        admin_id = get_admin_user().id
        task = get_cyclecount_task()
        task_id = task.id
        CycleCountTaskService.process_task(task_id, admin_id)
        details = [{'id': d.id, 'actual_quantity': (d.system_quantity or 0) + 3} for d in task.task_details]
        CycleCountTaskService.batch_save_task_details(task_id, details, admin_id)
        CycleCountTaskService.complete_task(task_id, admin_id)

    response = client.post(
        f'/adjustment/create_adjustment_by_cyclecount/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['status'] == 'pending'
    assert len(data['details']) >= 1
    for d in data['details']:
        assert d['adjustment_quantity'] == d['actual_quantity'] - d['system_quantity'] == 3


def test_adjustment_stats_use_adjustment_service(client, access_token):
    """B-25：stats 改调 Adjustment 自己的服务，状态序列为 pending/approved/completed"""
    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.get('/adjustment/monthly-stats?months=3', headers=headers)
    assert response.status_code == 200
    assert [s['name'] for s in response.get_json()] == ['Pending', 'Approved', 'Completed']
    assert response.get_json()[0]['data'][-1] >= 1   # helpers 里有一条本月 pending 的调整单

    response = client.get('/adjustment/status-overview-stats', headers=headers)
    assert response.status_code == 200
    assert [s['name'] for s in response.get_json()] == ['pending', 'approved', 'completed']