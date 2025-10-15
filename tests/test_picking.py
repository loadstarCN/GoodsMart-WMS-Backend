from warehouse.inventory.services import InventoryService
from .helpers import *
from warehouse.picking.services import PickingTaskService

# ---------------------------------------------------------
# 以下为测试视图层 (views.py) 逻辑的用例
# ---------------------------------------------------------

def test_create_picking_task(client, access_token):
    """
    测试创建 Picking Task (POST /picking/)
    """
    response = client.post(
        '/picking/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "dn_id": 1,
            "status": "pending",
            "is_active": True,
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    # 检查返回数据中的关键字段
    assert data['status'] == "pending"


def test_get_picking_tasks(client, access_token):
    """
    测试获取 PickingTask 列表 (GET /picking/)
    """
    response = client.get(
        '/picking/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_picking_task_detail(client, access_token):
    """
    测试获取单个 PickingTask (GET /picking/<task_id>)
    """
    with client.application.app_context():
        task = get_picking_task()
        assert task is not None
        task_id = task.id

    response = client.get(
        f'/picking/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == task_id
    assert data['status'] == task.status


def test_update_picking_task(client, access_token):
    """
    测试更新 PickingTask (PUT /picking/<task_id>)
    """
    with client.application.app_context():
        task = get_picking_task()
        assert task is not None
        task_id = task.id

    response = client.put(
        f'/picking/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "status": "pending",
            "is_active": False
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    # 检查更新后的关键字段
    assert data['status'] == "pending"
    assert data['is_active'] is False


def test_delete_picking_task(client, access_token):
    """
    测试删除 PickingTask (DELETE /picking/<task_id>)
    """
    with client.application.app_context():
        task = get_picking_task()
        task_id = task.id

    response = client.delete(
        f'/picking/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Picking Task deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_task = get_picking_task_by_id(task_id)
        assert deleted_task is None


# ---------------------------------------------------------
# 以下为直接测试服务层 (services.py) 逻辑的用例
# ---------------------------------------------------------

def test_picking_task_service_get_task(client):
    """
    测试 get_task 方法
    """
    with client.application.app_context():
        task = get_picking_task()
        found_task = PickingTaskService.get_task(task.id)
        assert found_task.id == task.id


def test_picking_task_service_create_task(client):
    """
    测试通过服务层创建一个新的 PickingTask
    """
    with client.application.app_context():
        user = get_operator_user()
        dn = get_dn()
        data = {
            "dn_id": dn.id,
            "status": "pending",
            "is_active": True,
        }
        new_task = PickingTaskService.create_task(data, user.id)
        assert new_task.id is not None
        # 检查返回数据中的关键字段
        assert new_task.status == "pending"
        assert new_task.is_active is True


def test_picking_task_service_update_task(client):
    """
    测试通过服务层更新 PickingTask
    """
    with client.application.app_context():
        task = get_picking_task()
        assert task is not None

        updated_task = PickingTaskService.update_task(task.id, {
            "status": "pending",  # 允许更新
            "is_active": False
        })
        # 检查更新后的关键字段
        assert updated_task.status == "pending"
        assert updated_task.is_active is False


def test_picking_task_service_delete_task(client):
    """
    测试通过服务层删除 PickingTask
    """
    with client.application.app_context():
        task = get_picking_task()
        task_id = task.id
        PickingTaskService.delete_task(task_id)

        deleted_task = get_picking_task_by_id(task_id)
        assert deleted_task is None


# ---------------------------------------------------------
def test_list_batches_empty(client, access_token):
    """
    初始情况下(无 batch), 测试 GET /picking/<task_id>/batches/
    """
    with client.application.app_context():
        # 找一个PickingTask, 并把它置为 in_progress
        admin_user = get_operator_user()
        task = get_picking_task()
        # 修改状态: pending -> in_progress
        PickingTaskService.process_task(task.id, admin_user.id)

        # 调用接口获取批次列表(此时应为空)
        response = client.get(
            f'/picking/{task.id}/batches/',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        # 你的视图里 marshal_list_with(picking_batch_model)，应返回list
        assert isinstance(data, list)
        assert len(data) == 0

def test_create_batch(client, access_token):
    """
    测试在 in_progress 的 PickingTask 中创建 batch (POST /picking/<task_id>/batches/)
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # 将任务置为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)

        request_json = {
            "operation_time": "2025-02-01T08:00:00",
            "remark": "Test batch creation"
            # 无 details: 只创建 batch
        }
        response = client.post(
            f'/picking/{task.id}/batches/',
            headers={'Authorization': f'Bearer {access_token}'},
            json=request_json
        )
        assert response.status_code == 201
        data = response.get_json()
        assert "id" in data
        assert data['remark'] == "Test batch creation"
        # 如果视图中返回了 operator 或其他字段，也可在此断言

def test_create_batch_with_details(client, access_token):
    """
    测试在 in_progress 的 PickingTask 中创建 batch + details
    POST /picking/<task_id>/batches/
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # 将任务置为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        # 获取location_id / goods_id 以便 detail
        location = get_location()
        goods = get_goods()

        request_json = {
            "operation_time": "2025-02-02T09:00:00",
            "remark": "Batch with details",
            "details": [
                {
                    "location_id": location.id,
                    "goods_id": goods.id,
                    "picked_quantity": 5,
                },
                {
                    "location_id": location.id,
                    "goods_id": goods.id,
                    "picked_quantity": 10,
                }
            ]
        }
        response = client.post(
            f'/picking/{task.id}/batches/',
            headers={'Authorization': f'Bearer {access_token}'},
            json=request_json
        )
        assert response.status_code == 201
        data = response.get_json()
        assert "id" in data
        assert data['remark'] == "Batch with details"

    # 检查数据库里的 batch + detail
    with client.application.app_context():
        created_batch = get_picking_task_batch_by_id(data['id'])
        assert created_batch is not None
        assert len(created_batch.details) == 2

def test_get_single_batch(client, access_token):
    """
    测试 GET /picking/<task_id>/batches/<batch_id>/
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # in_progress
        PickingTaskService.process_task(task.id, admin_user.id)

        # 创建 batch
        batch_data = {"remark": "Single batch"}
        new_batch = PickingTaskService.create_batch(task.id, batch_data, admin_user.id)

        response = client.get(
            f'/picking/{task.id}/batches/{new_batch.id}',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == new_batch.id
        assert data['remark'] == "Single batch"

def test_update_batch(client, access_token):
    """
    测试 PUT /picking/<task_id>/batches/<batch_id>/
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        new_batch = PickingTaskService.create_batch(task.id, {"remark": "old remark"}, admin_user.id)

        response = client.put(
            f'/picking/{task.id}/batches/{new_batch.id}',
            headers={'Authorization': f'Bearer {access_token}'},
            json={"remark": "updated remark"}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['remark'] == "updated remark"

        with client.application.app_context():
            updated_batch = get_picking_task_batch_by_id(new_batch.id)
            assert updated_batch.remark == "updated remark"

def test_delete_batch(client, access_token):
    """
    测试 DELETE /picking/<task_id>/batches/<batch_id>/
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        batch_to_delete = PickingTaskService.create_batch(task.id, {"remark": "to-be-deleted"}, admin_user.id)

        response = client.delete(
            f'/picking/{task.id}/batches/{batch_to_delete.id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == "Picking Batch deleted successfully"

        with client.application.app_context():
            deleted_batch = get_picking_task_batch_by_id(batch_to_delete.id)
            assert deleted_batch is None

def test_picking_task_service_create_batch(client):
    """
    测试服务层 create_batch 方法
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        # 确保 task 为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        batch = PickingTaskService.create_batch(
            task.id,
            {"operation_time": "2025-02-01T08:00:00", "remark": "Service test batch"},
            operator_id=admin_user.id
        )
        assert batch.id is not None
        assert batch.remark == "Service test batch"

def test_picking_task_service_delete_batch(client):
    """
    测试服务层 delete_batch 方法
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        batch = PickingTaskService.create_batch(
            task.id, {}, admin_user.id
        )
        batch_id = batch.id
        PickingTaskService.delete_batch(task.id, batch_id)
        assert not get_picking_task_batch_by_id(batch_id)

def test_picking_task_process_sets_started_at(client, access_token):
    """
    测试 /picking/<task_id>/process/ 接口会更新 started_at 并插入状态日志
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task_by_status("pending")
        task_id = task.id

        response = client.put(
            f'/picking/{task_id}/process/',
            headers={'Authorization': f'Bearer {access_token}'},
            json={}
        )
        assert response.status_code == 200
        data = response.get_json()
        # 校验 started_at 不为 null
        assert data['started_at'] is not None

    # 再次查询数据库，确保 status_logs 中有记录
    with client.application.app_context():
        updated_task = get_picking_task_by_id(task_id)
        assert updated_task.status == 'in_progress'
        assert updated_task.started_at is not None
        assert len(updated_task.status_logs) == 1
        assert updated_task.status_logs[0].new_status == 'in_progress'

def test_picking_task_complete_sets_completed_at(client, access_token):
    """
    测试 /picking/<task_id>/complete/ 接口会更新 completed_at 并插入状态日志
    """
    with client.application.app_context():
        admin_user = get_operator_user()    
        task = get_picking_task_by_status("pending")
        # 先从 pending => in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        task_id = task.id
        response = client.put(
            f'/picking/{task_id}/complete/',
            headers={'Authorization': f'Bearer {access_token}'},
            json={}
        )
        assert response.status_code == 200
        data = response.get_json()
        # 校验 completed_at
        assert data['completed_at'] is not None

    with client.application.app_context():
        updated_task = get_picking_task_by_id(task_id)
        assert updated_task.status == 'completed'
        assert updated_task.completed_at is not None
        assert len(updated_task.status_logs) == 2
        # 第二条状态日志为 new_status='completed'
        assert updated_task.status_logs[-1].new_status == 'completed'


# 新增辅助函数，用于根据 goods_id 和 warehouse_id 获取库存记录
def get_inventory_for(goods_id, warehouse_id):
    from warehouse.inventory.models import Inventory
    return Inventory.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id).first()

def test_complete_task_success(client):
    """
    测试 complete_task 正常完成流程：
    1. 任务必须先处于 in_progress 状态。
    2. 调用 complete_task 后，状态变为 completed，completed_at 不为空，
       并更新 DN 与库存（通过间接检查）。
    """
    with client.application.app_context():
        # 获取一个任务，并将其置为 in_progress
        admin_user = get_operator_user()
        task = get_picking_task_by_status("pending")
        assert task is not None

        dn = task.dn
        dn.status == "in_progress"

        # 将任务状态设置为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)

        # 确保 task_details 存在，便于后续验证库存更新
        assert len(task.task_details) > 0

        # 执行 complete_task
        completed_task = PickingTaskService.complete_task(task.id, admin_user.id)

        # 验证任务状态已变为 completed，并且 completed_at 不为空
        assert completed_task.status == "completed"
        assert completed_task.completed_at is not None

        # 可选：检查 DN 状态更新（依据 DNService 的业务逻辑）
        dn = task.dn
        assert dn.status == "picked"  # 根据实际业务调整

        # 检查库存更新：使用 get_inventory_for 辅助函数
        for detail in task.task_details:
            inventory = get_inventory_for(detail.goods_id, task.dn.warehouse_id)
            # 这里断言库存变化的逻辑依据实际业务，示例中假设库存 onhand_stock 应减少
            assert inventory.onhand_stock <= inventory.total_stock

def test_complete_task_transaction_rollback(client, monkeypatch):
    """
    测试 complete_task 在调用子方法时出错后事务回滚：
    模拟某个子服务方法抛出异常，验证任务状态未变为 completed。
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task_by_status("pending")
        assert task is not None

        # 将任务状态设置为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)

        # 模拟 InventoryService.picking_completed 抛出异常
        def fake_picking_completed(goods_id, warehouse_id, quantity, commit=True):
            raise Exception("Simulated inventory update error")
        monkeypatch.setattr(InventoryService, "dn_picked", fake_picking_completed)

        with pytest.raises(Exception) as exc_info:
            PickingTaskService.complete_task(task.id, admin_user.id)
        assert "Simulated inventory update error" in str(exc_info.value)

        # 验证任务状态未变为 completed（应仍为 in_progress）
        refreshed_task = get_picking_task_by_id(task.id)
        assert refreshed_task.status != "completed"
        assert refreshed_task.status == "in_progress"