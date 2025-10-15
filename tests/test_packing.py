from warehouse.packing.services import PackingTaskService
from .helpers import *


# ---------------------------------------------------------
# 以下为测试视图层 (views.py) 逻辑的用例
# ---------------------------------------------------------

def test_create_packing_task(client, access_token):
    """
    测试创建 Packing Task (POST /packing/)
    """
    response = client.post(
        '/packing/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "dn_id": 1,
            "status": "pending",
            "is_active": True,
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['status'] == "pending"


def test_get_packing_tasks(client, access_token):
    """
    测试获取 PackingTask 列表 (GET /packing/)
    """
    response = client.get(
        '/packing/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_packing_task_detail(client, access_token):
    """
    测试获取单个 PackingTask (GET /packing/<task_id>)
    """
    with client.application.app_context():
        task = get_packing_task()
        assert task is not None
        task_id = task.id

    response = client.get(
        f'/packing/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == task_id


def test_update_packing_task(client, access_token):
    """
    测试更新 PackingTask (PUT /packing/<task_id>)
    """
    with client.application.app_context():
        task = get_packing_task()
        assert task is not None
        task_id = task.id

    response = client.put(
        f'/packing/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "status": "in_progress",
            "is_active": False
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == "in_progress"
    assert data['is_active'] is False


def test_delete_packing_task(client, access_token):
    """
    测试删除 PackingTask (DELETE /packing/<task_id>)
    """
    with client.application.app_context():
        task = get_packing_task()
        task_id = task.id

    response = client.delete(
        f'/packing/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Packing Task deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_task = get_packing_task_by_id(task_id)
        assert deleted_task is None


# ---------------------------------------------------------
# 以下为直接测试服务层 (services.py) 逻辑的用例
# ---------------------------------------------------------

def test_packing_task_service_get_task(client):
    """
    测试 get_task 方法
    """
    with client.application.app_context():
        task = get_packing_task()
        found_task = PackingTaskService.get_task(task.id)
        assert found_task.id == task.id


def test_packing_task_service_create_task(client):
    """
    测试通过服务层创建一个新的 PackingTask
    """
    with client.application.app_context():
        user = get_operator_user()
        dn =get_dn()
        data = {
            "dn_id": dn.id,
            "status": "pending",
            "is_active": True,
        }
        new_task = PackingTaskService.create_task(data, user.id)
        assert new_task.id is not None
        # 检查其他关键字段
        assert new_task.status == "pending"
        assert new_task.is_active is True


def test_packing_task_service_update_task(client):
    """
    测试通过服务层更新 PackingTask
    """
    with client.application.app_context():
        task = get_packing_task()
        assert task is not None

        updated_task = PackingTaskService.update_task(task.id, {
            "status": "in_progress",  # 允许更新
            "is_active": False
        })
        assert updated_task.status == "in_progress"
        assert updated_task.is_active is False


def test_packing_task_service_delete_task(client):
    """
    测试通过服务层删除 PackingTask
    """
    with client.application.app_context():
        task = get_packing_task()
        task_id = task.id
        PackingTaskService.delete_task(task_id)

        deleted_task = get_packing_task_by_id(task_id)
        assert deleted_task is None
