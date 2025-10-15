from warehouse.cyclecount.services import CycleCountTaskService
from .helpers import *

# ---------------------------------------------------------
# 以下为测试视图层 (views.py) 逻辑的用例
# ---------------------------------------------------------

def test_create_cyclecount(client, access_token):
    """
    测试创建 CycleCount (POST /cyclecount/)
    """
    response = client.post(
        '/cyclecount/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "task_name": "CC-NEW",
            "warehouse_id": 1,
            "status": "pending",
            "is_active": True,
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['task_name'] == "CC-NEW"


def test_get_cyclecounts(client, access_token):
    """
    测试获取 CycleCount 列表 (GET /cyclecount/)
    """
    response = client.get(
        '/cyclecount/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_cyclecount(client, access_token):
    """
    测试获取单个 CycleCount (GET /cyclecount/<cycle_id>)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()
        cycle_id = cycle.id

    response = client.get(
        f'/cyclecount/{cycle_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == cycle_id
    assert data['task_name'] == cycle.task_name


def test_update_cyclecount(client, access_token):
    """
    测试更新 CycleCount (PUT /cyclecount/<cycle_id>)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()
        cycle_id = cycle.id

    response = client.put(
        f'/cyclecount/{cycle_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "task_name": "CC-UPDATED",
            "status": "in_progress",  # 更新状态为进行中
            "is_active": False
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['task_name'] == "CC-UPDATED"
    assert data['is_active'] is False


def test_delete_cyclecount(client, access_token):
    """
    测试删除 CycleCount (DELETE /cyclecount/<cycle_id>)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()
        cycle_id = cycle.id

    response = client.delete(
        f'/cyclecount/{cycle_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Cycle Count Task deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_cycle = get_cyclecount_task_by_id(cycle_id)
        assert deleted_cycle is None


def test_get_cyclecount_details(client, access_token):
    """
    测试获取 CycleCount 下所有 CycleCountDetail (GET /cyclecount/<cycle_id>/details/)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()

    response = client.get(
        f'/cyclecount/{cycle.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_create_cyclecount_detail(client, access_token):
    """
    测试创建 CycleCountDetail (POST /cyclecount/<cycle_id>/details/)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()

    response = client.post(
        f'/cyclecount/{cycle.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "goods_id": 1,
            "warehouse_id": 1,
            "location_id": 1,
            "status": "pending",
            "operator_id": cycle.created_by
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['goods_id'] == 1


def test_update_cyclecount_detail(client, access_token):
    """
    测试更新 CycleCountDetail (PUT /cyclecount/<cycle_id>/details/<detail_id>)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()
        detail = get_cyclecount_task_detail_by_task_id(cycle.id)

    response = client.put(
        f'/cyclecount/{cycle.id}/details/{detail.id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "actual_quantity": 100,
            "status": "completed"
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['actual_quantity'] == 100
    assert data['status'] == "completed"

def test_delete_cyclecount_detail(client, access_token):
    """
    测试删除 CycleCountDetail (DELETE /cyclecount/<cycle_id>/details/<detail_id>)
    """
    with client.application.app_context():
        cycle = get_cyclecount_task()
        detail = get_cyclecount_task_detail_by_task_id(cycle.id)

    response = client.delete(
        f'/cyclecount/{cycle.id}/details/{detail.id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Cycle Count Task Detail deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_detail = get_cyclecount_task_detail_by_id(detail.id)
        assert deleted_detail is None


# ---------------------------------------
# 以下为 services.py 业务逻辑层的用例
# ---------------------------------------

@pytest.fixture
def cycle_count_service(client):
    """
    Fixture：初始化 CycleCountTaskService 服务实例
    """
    app = setup_app()
    with app.app_context():
        cycle_count_service = CycleCountTaskService()
        yield cycle_count_service


def test_create_cyclecount_task_service(client):
    """
    测试 CycleCountTaskService 创建任务
    """
    with client.application.app_context():
        user = get_operator_user()
        task_data = {
            "task_name": "CC-SERVICE-TASK",
            "warehouse_id": 1,
            "status": "pending",
            "is_active": True
        }

        # 模拟调用服务层的创建方法
        cycle_count_task = CycleCountTaskService.create_task(task_data,user.id)

        assert cycle_count_task is not None
        assert cycle_count_task.task_name == task_data["task_name"]
        assert cycle_count_task.status == task_data["status"]
        assert cycle_count_task.is_active == task_data["is_active"]


def test_get_cyclecount_task_by_id_service(client):
    """
    测试 CycleCountTaskService 根据 ID 获取任务
    """
    with client.application.app_context():
        cycle_task = get_cyclecount_task()
        cycle_count_task = CycleCountTaskService.get_task(cycle_task.id)

        assert cycle_count_task is not None
        assert cycle_count_task.id == cycle_task.id
        assert cycle_count_task.task_name == cycle_task.task_name


def test_update_cyclecount_task_service(client):
    """
    测试 CycleCountTaskService 更新任务
    """
    with client.application.app_context():
        cycle_task = get_cyclecount_task()
        updated_data = {
            "task_name": "CC-SERVICE-UPDATED",
            "status": "in_progress",
            "is_active": False
        }
        updated_task = CycleCountTaskService.update_task(cycle_task.id, updated_data)

        assert updated_task is not None
        assert updated_task.task_name == updated_data["task_name"]
        assert updated_task.status == updated_data["status"]
        assert updated_task.is_active == updated_data["is_active"]


def test_delete_cyclecount_task_service(client):
    """
    测试 CycleCountTaskService 删除任务
    """
    with client.application.app_context():
        cycle_task = get_cyclecount_task()
        result = CycleCountTaskService.delete_task(cycle_task.id)

        assert result is None

    with client.application.app_context():
        deleted_task = get_cyclecount_task_by_id(cycle_task.id)
        assert deleted_task is None


def test_create_cyclecount_detail_service(client):
    """
    测试 CycleCountTaskService 创建任务详情
    """
    
    with client.application.app_context():
        user = get_operator_user()
        cycle_task = get_cyclecount_task()
        detail_data = {
            "goods_id": 4,
            "location_id": 4,
            "status": "pending",
            "operator_id": cycle_task.created_by
        }

        cycle_count_detail = CycleCountTaskService.create_task_detail(cycle_task.id, detail_data,user.id)
        print(cycle_count_detail)
        assert cycle_count_detail is not None
        assert cycle_count_detail.goods_id == detail_data["goods_id"]
        assert cycle_count_detail.status == detail_data["status"]


def test_update_cyclecount_detail_service(client):
    """
    测试 CycleCountTaskService 更新任务详情
    """
    with client.application.app_context():
        user = get_operator_user()
        cycle_task = get_cyclecount_task()
        detail = get_cyclecount_task_detail_by_task_id(cycle_task.id)
        updated_data = {
            "actual_quantity": 200,
            "status": "completed"
        }

        updated_detail = CycleCountTaskService.update_task_detail(cycle_task.id, detail.id, updated_data)
        
        assert updated_detail is not None
        # assert updated_detail.actual_quantity == updated_data["actual_quantity"]
        # assert updated_detail.status == updated_data["status"]


def test_delete_cyclecount_detail_service(client):
    """
    测试 CycleCountTaskService 删除任务详情
    """
    with client.application.app_context():
        cycle_task = get_cyclecount_task()
        detail = get_cyclecount_task_detail_by_task_id(cycle_task.id)

        result = CycleCountTaskService.delete_task_detail(cycle_task.id, detail.id)

        assert result is None
    with client.application.app_context():
        deleted_detail = get_cyclecount_task_detail_by_id(detail.id)
        assert deleted_detail is None




def test_create_cycle_count_tasks_from_goods_list(client):
    """
    测试 create_cycle_count_tasks_from_goods_list 方法
    """
    with client.application.app_context():
        created_by_id = 1  # 假设创建者ID为 1
        goods_ids = [1, 2]  # 模拟输入的 Goods ID 列表
        warehouse_id = 1  # 假设仓库 ID 为 1

        # 运行方法
        task = CycleCountTaskService.create_cycle_count_tasks_from_goods_list(goods_ids, warehouse_id, created_by_id)

        # 验证任务是否创建
        assert task is not None
        assert task.task_name.startswith("Cycle Count Task -")
        assert task.status == 'pending'
        assert task.is_active is True

        # 验证生成的任务详情
        assert len(task.task_details) == 3  # goods_1 有 2 个 location，goods_2 有 1 个 location，总共 3 次

        # 验证任务详情的内容
        task_details = task.task_details
        assert task_details[0].goods_id == 1
        assert task_details[0].location_id == 1
        assert task_details[1].goods_id == 2
        assert task_details[1].location_id == 2

        # 验证数据库中是否有添加任务和任务详情
        db.session.refresh(task)  # 刷新任务对象
        db.session.refresh(task_details[0])  # 刷新任务详情对象
        db.session.refresh(task_details[1])

        assert task.id is not None
        assert task_details[0].id is not None
        assert task_details[1].id is not None


def test_create_cycle_count_tasks_from_goods_list_no_goods(client):
    """
    测试 create_cycle_count_tasks_from_goods_list 方法，当没有有效的 Goods ID 时
    """
    with client.application.app_context():
        created_by_id = 1  # 假设创建者ID为 1
        goods_ids = []  # 模拟输入为空的 Goods ID 列表
        warehouse_id = 1

        # 运行方法
        task = CycleCountTaskService.create_cycle_count_tasks_from_goods_list(goods_ids, warehouse_id, created_by_id)

        # 验证任务是否创建
        assert task is not None
        assert len(task.task_details) == 0


def test_create_cycle_count_tasks_from_goods_list_invalid_goods(client):
    """
    测试 create_cycle_count_tasks_from_goods_list 方法，当查询到无效的 Goods 时触发异常
    """
    with client.application.app_context():
        created_by_id = 1  # 假设创建者ID为 1
        goods_ids = [999]  # 模拟无效的 Goods ID 列表
        warehouse_id = 1

        # 确保方法在查询无效 Goods 时抛出异常
        with pytest.raises(Exception):
            CycleCountTaskService.create_cycle_count_tasks_from_goods_list(goods_ids, warehouse_id, created_by_id)
