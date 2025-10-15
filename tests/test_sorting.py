from extensions.error import NotFoundException
from .helpers import *
from warehouse.sorting.services import SortingTaskService

# ---------------------------------------------------------
# 测试视图层 (views.py) 逻辑的用例
# ---------------------------------------------------------

def test_create_sorting_task(client, access_token):
    """
    测试创建 Sorting Task (POST /sorting/)
    """
    response = client.post(
        '/sorting/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "asn_id": 1,
            "status": "pending",
            "is_active": True,
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['status'] == "pending"


def test_get_sorting_tasks(client, access_token):
    """
    测试获取 SortingTask 列表 (GET /sorting/)
    """
    response = client.get(
        '/sorting/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_get_sorting_task_detail(client, access_token):
    """
    测试获取单个 SortingTask (GET /sorting/<task_id>)
    """
    with client.application.app_context():
        task = get_sorting_task()
        assert task is not None
        task_id = task.id

    response = client.get(
        f'/sorting/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == task_id
    # 验证其他关键字段
    assert data['status'] == task.status


def test_update_sorting_task(client, access_token):
    """
    测试更新 Sorting Task (PUT /sorting/<task_id>)
    """
    with client.application.app_context():
        task = get_sorting_task()
        assert task is not None
        task_id = task.id

    response = client.put(
        f'/sorting/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "status": "pending",  # 仍为 pending，因此允许更新
            "is_active": False
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    # 验证更新后的关键字段
    assert data['status'] == "pending"
    assert data['is_active'] is False


def test_delete_sorting_task(client, access_token):
    """
    测试删除 Sorting Task (DELETE /sorting/<task_id>)
    """
    with client.application.app_context():
        task = get_sorting_task()
        task_id = task.id

    response = client.delete(
        f'/sorting/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Sorting Task deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_task = get_sorting_task_by_id(task_id)
        assert deleted_task is None


# ---------------------------
# 下面开始测试 TaskDetail 的相关操作
# ---------------------------

def test_get_sorting_task_details_list(client, access_token):
    """
    测试获取 Sorting Task 下所有 SortingTaskDetail (GET /sorting/<task_id>/details/)
    由于Fixture中并未创建 detail，这里需要先创建后再测试获取
    """
    with client.application.app_context():
        operator_id = get_operator_user().id
        task = get_sorting_task()
        # 先把任务从 pending => in_progress
        SortingTaskService.process_task(task.id,operator_id)
        # 再创建一个 batch，获取 batch_id
        
        new_batch = SortingTaskService.create_batch(task.id, {}, operator_id)
        # 再创建detail
        SortingTaskService.create_task_detail(
            task_id=task.id,
            data={
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 5,
                "damage_quantity": 0
            },
            created_by_id=operator_id
        )

        # 调用接口获取
        response = client.get(
            f'/sorting/{task.id}/details/',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1


def test_create_sorting_task_detail(client, access_token):
    """
    测试创建 SortingTaskDetail (POST /sorting/<task_id>/details/)
    要求：Task 必须 in_progress，且需要 batch_id
    """
    with client.application.app_context():
        operator_id = get_operator_user().id
        # 1) 找一个pending Task
        task = get_sorting_task()
        assert task is not None
        # 2) 切换到 in_progress
        SortingTaskService.process_task(task.id,operator_id)
        # 3) 创建 batch 拿到 batch_id
        
        new_batch = SortingTaskService.create_batch(task.id, {}, operator_id)

        # 4) 发起接口请求创建 detail
        response = client.post(
            f'/sorting/{task.id}/details/',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 5,
                "damage_quantity": 1
            }
        )
        assert response.status_code == 201, response.data
        data = response.get_json()
        assert data['batch_id'] == new_batch.id
        assert data['sorted_quantity'] == 5
        assert data['damage_quantity'] == 1


def test_get_sorting_task_detail_item(client, access_token):
    """
    测试获取单个 SortingTaskDetail (GET /sorting/<task_id>/details/<detail_id>)
    """
    with client.application.app_context():
        # 1) 创建新的 in_progress Task + batch + detail
        user = get_operator_user()
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        # pending => in_progress
        SortingTaskService.process_task(new_task.id,user.id)
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            new_task.id,
            {
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 10,
                "damage_quantity": 2
            },
            user.id
        )
        # detail_obj 已创建

        # 接口请求
        response = client.get(
            f'/sorting/{new_task.id}/details/{detail_obj.id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == detail_obj.id
        assert data['batch_id'] == new_batch.id
        assert data['sorted_quantity'] == 10


def test_update_sorting_task_detail(client, access_token):
    """
    测试更新 SortingTaskDetail (PUT /sorting/<task_id>/details/<detail_id>)
    必须 in_progress
    """
    with client.application.app_context():
        user = get_operator_user()
        # 创建新的 Task => in_progress => batch => detail
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            new_task.id,
            {
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 10,
                "damage_quantity": 2
            },
            user.id
        )

        response = client.put(
            f'/sorting/{new_task.id}/details/{detail_obj.id}',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                "sorted_quantity": 99,
                "damage_quantity": 10
            }
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['sorted_quantity'] == 99
        assert data['damage_quantity'] == 10


def test_delete_sorting_task_detail(client, access_token):
    """
    测试删除 SortingTaskDetail (DELETE /sorting/<task_id>/details/<detail_id>)
    现在也必须在 in_progress 状态才能删除
    """
    with client.application.app_context():
        user = get_operator_user()
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)  # => in_progress
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            new_task.id,
            {
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 3,
                "damage_quantity": 0
            },
            user.id
        )
        detail_id = detail_obj.id

        response = client.delete(
            f'/sorting/{new_task.id}/details/{detail_id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == "Sorting Task Detail deleted successfully"

        # 再次查询，验证删除
        with client.application.app_context():
            deleted_detail = get_sorting_task_detail_by_id(detail_id)
            assert deleted_detail is None


# ---------------------------------------------------------
# 以下为直接测试服务层 (services.py) 逻辑的用例
# ---------------------------------------------------------

def test_sorting_task_service_get_task(client):
    """
    测试 get_task 方法
    """
    with client.application.app_context():
        task = get_sorting_task()
        found_task = SortingTaskService.get_task(task.id)
        assert found_task.id == task.id


def test_sorting_task_service_create_task(client):
    """
    测试通过服务层创建一个新的 SortingTask
    """
    with client.application.app_context():
        user = get_operator_user()
        data = {
            "asn_id": 1,
            "status": "pending",
            "is_active": True,
        }
        new_task = SortingTaskService.create_task(data, user.id)
        assert new_task.id is not None
        # 验证其他关键字段
        assert new_task.status == "pending"
        assert new_task.is_active is True


def test_sorting_task_service_update_task(client):
    """
    测试通过服务层更新 SortingTask
    """
    with client.application.app_context():
        task = get_sorting_task()
        assert task is not None

        updated_task = SortingTaskService.update_task(task.id, {
            "status": "pending",  # 允许更新
            "is_active": False
        })
        # 验证更新后的关键字段
        assert updated_task.status == "pending"
        assert updated_task.is_active is False


def test_sorting_task_service_delete_task(client):
    """
    测试通过服务层删除 SortingTask
    """
    with client.application.app_context():
        task = get_sorting_task()
        task_id = task.id
        SortingTaskService.delete_task(task_id)

        deleted_task = get_sorting_task_by_id(task_id)
        assert deleted_task is None


def test_sorting_task_service_get_task_detail(client):
    """
    测试 get_task_detail 方法
    """
    with client.application.app_context():
        # 创建一个 in_progress Task + batch + detail
        user = get_operator_user()
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            new_task.id,
            {
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 5,
                "damage_quantity": 0
            },
            user.id
        )
        found_detail = SortingTaskService.get_task_detail(new_task.id, detail_obj.id)
        assert found_detail.id == detail_obj.id

        # 测试不存在时抛出 NotFound
        with pytest.raises(NotFoundException):
            SortingTaskService.get_task_detail(new_task.id, 999)


def test_sorting_task_service_create_task_detail(client):
    """
    测试通过服务层创建 SortingTaskDetail
    """
    with client.application.app_context():
        user = get_operator_user()
        # 先创建 in_progress Task
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)
        # 创建 batch
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)

        new_detail = SortingTaskService.create_task_detail(
            task_id=new_task.id,
            data={"batch_id": new_batch.id, "goods_id": 1, "sorted_quantity": 10, "damage_quantity": 2},
            created_by_id=user.id
        )
        assert new_detail.id is not None
        assert new_detail.sorted_quantity == 10
        assert new_detail.damage_quantity == 2


def test_sorting_task_service_update_task_detail(client):
    """
    测试通过服务层更新 SortingTaskDetail
    """
    with client.application.app_context():
        user = get_operator_user()
        # 创建 in_progress Task + batch + detail
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            task_id=new_task.id,
            data={
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 10,
                "damage_quantity": 2
            },
            created_by_id=user.id
        )

        # 更新
        updated_detail = SortingTaskService.update_task_detail(
            new_task.id,
            detail_obj.id,
            {"sorted_quantity": 999, "damage_quantity": 99}
        )
        assert updated_detail.sorted_quantity == 999
        assert updated_detail.damage_quantity == 99


def test_sorting_task_service_delete_task_detail(client):
    """
    测试通过服务层删除 SortingTaskDetail
    要求 in_progress 状态
    """
    with client.application.app_context():
        user = get_operator_user()
        new_task = SortingTaskService.create_task({"asn_id": 1}, user.id)
        SortingTaskService.process_task(new_task.id,user.id)
        new_batch = SortingTaskService.create_batch(new_task.id, {}, user.id)
        detail_obj = SortingTaskService.create_task_detail(
            new_task.id,
            {
                "batch_id": new_batch.id,
                "goods_id": 1,
                "sorted_quantity": 3,
                "damage_quantity": 0
            },
            user.id
        )
        detail_id = detail_obj.id

        SortingTaskService.delete_task_detail(new_task.id, detail_id)
        assert get_sorting_task_detail_by_id(detail_id) is None


def test_sorting_task_service__update_task_status(client):
    """
    测试 _update_task_status 方法
    """
    with client.application.app_context():
        user = get_operator_user()
        task = get_sorting_task()
        updated_task = SortingTaskService._update_task_status(task, "completed",user.id)
        assert updated_task.status == "completed"


def test_sorting_task_service_create_sorting_task_from_asn(client):
    """
    测试 create_sorting_task_from_asn 方法
    """
    with client.application.app_context():
        # 需要一个已存在的 ASN
        asn = get_asn()
        if not asn:
            pytest.skip("需要至少一个 ASN 才能测试 create_sorting_task_from_asn")

        new_task = SortingTaskService.create_sorting_task_from_asn(asn.id)
        assert new_task.id is not None
        assert new_task.asn_id == asn.id


def test_list_batches_empty(client, access_token):
    """
    初始情况下(无batch), 测试 GET /sorting/<task_id>/batches/
    """
    with client.application.app_context():
        # 1) 找一个 SortingTask, 并把它置为 in_progress
        admin_user = get_operator_user()
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        task_id = task.id

    # 2) 调用接口获取批次列表(此时应为空)
    response = client.get(
        f'/sorting/{task_id}/batches/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    # 你在视图代码中用的是 marshal_list_with(sorting_batch_model)，这里应返回一个list
    assert isinstance(data, list)
    assert len(data) == 0

def test_create_batch(client, access_token):
    """
    测试 POST /sorting/<task_id>/batches/ 
    - 创建新的 batch (不带 details)
    - Task 必须在 in_progress
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        # 拿一个Task, 并置为in_progress
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        task_id = task.id

    # 发起接口请求创建batch
    request_json = {
        "operation_time": "2025-02-01T08:00:00",
        "remark": "Test batch creation"
    }

    response = client.post(
        f'/sorting/{task_id}/batches/',
        headers={'Authorization': f'Bearer {access_token}'},
        json=request_json
    )
    assert response.status_code == 201
    data = response.get_json()
    # 验证返回的 batch 数据结构
    assert data['remark'] == "Test batch creation"
    # operation_time 返回的格式(可能带时区)，可以做一下startswith的判断
    # 或者只验证不是None

def test_create_batch_with_details(client, access_token):
    """
    测试通过 POST /sorting/<task_id>/batches/
    同时创建 batch + details
    注意: details 里要有 asn_detail_id, sorted_quantity 等
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_sorting_task()
        # 切到 in_progress
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        task_id = task.id

    request_json = {
        "operation_time": "2025-02-02T09:00:00",
        "remark": "Batch with details",
        "details": [
            {
                "goods_id": 1,
                "sorted_quantity": 5,
                "damage_quantity": 1
            },
            {
                "goods_id": 2,
                "sorted_quantity": 10,
                "damage_quantity": 0
            }
        ]
    }

    response = client.post(
        f'/sorting/{task_id}/batches/',
        headers={'Authorization': f'Bearer {access_token}'},
        json=request_json
    )
    assert response.status_code == 201, response.data
    data = response.get_json()
    assert data['remark'] == "Batch with details"
    # 若返回不包含 details, 只包含 batch 本身也正常
    # 关键是要验证这条 batch 确实创建成功, detail 也已插入

    with client.application.app_context():
        # 查询数据库验证 details
        created_batch = get_sorting_batch_by_id(data['id'])
        assert created_batch is not None
        assert len(created_batch.details) == 2

def test_get_single_batch(client, access_token):
    """
    测试 GET /sorting/<task_id>/batches/<batch_id>/
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        # 创建 task in_progress + batch
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        batch = SortingTaskService.create_batch(task.id, 
                    {"remark": "Single batch"}, admin_user.id)
        task_id = task.id
        batch_id = batch.id

    response = client.get(
        f'/sorting/{task_id}/batches/{batch_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == batch_id
    assert data['remark'] == "Single batch"

def test_update_batch(client, access_token):
    """
    测试 PUT /sorting/<task_id>/batches/<batch_id>/
    - 只能在 in_progress 时更新
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        batch = SortingTaskService.create_batch(task.id, 
                    {"remark": "old remark"}, admin_user.id)
        task_id = task.id
        batch_id = batch.id

        # 发起更新请求
        response = client.put(
            f'/sorting/{task_id}/batches/{batch_id}',
            headers={'Authorization': f'Bearer {access_token}'},
            json={"remark": "updated remark"}
        )
        assert response.status_code == 200
        data = response.get_json()
        print(data)
        assert data['remark'] == "updated remark"

    # 再查数据库验证
    with client.application.app_context():
        updated_batch = get_sorting_batch_by_id(batch_id)
        assert updated_batch.remark == "updated remark"

def test_delete_batch(client, access_token):
    """
    测试 DELETE /sorting/<task_id>/batches/<batch_id>/
    - 只能在 in_progress 时删除
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, operator_id=admin_user.id)
        batch = SortingTaskService.create_batch(
            task.id, 
            {"remark": "to-be-deleted"}, 
            admin_user.id
        )
        task_id = task.id
        batch_id = batch.id

    response = client.delete(
        f'/sorting/{task_id}/batches/{batch_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Sorting Batch deleted successfully"

    with client.application.app_context():
        deleted_batch = get_sorting_batch_by_id(batch_id)
        assert deleted_batch is None
