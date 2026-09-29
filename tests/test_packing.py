from extensions.error import BadRequestException
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
            "is_active": False,
            "dn_id": 2,
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    # B-12：status / is_active / dn_id 都不能经 PUT 改，状态只经 process / complete 流转
    assert data['status'] == "pending"
    assert data['is_active'] is True
    assert data['dn_id'] == task.dn_id


def test_post_packing_detail_endpoint_removed(client, access_token):
    """B-35：明细只能经批次创建，POST /packing/<id>/details/ 已下线（原来必 500）。"""
    with client.application.app_context():
        task_id = get_packing_task().id
    response = client.post(f'/packing/{task_id}/details/',
                           headers={'Authorization': f'Bearer {access_token}'},
                           json={"goods_id": 1, "packed_quantity": 1})
    assert response.status_code == 405


def _prepare_picked_dn_for_packing(picked_quantity=15):
    """把种子打包任务对应的 DN 置为 picked，goods_1 已拣 picked_quantity，并清掉种子打包明细。"""
    admin_user = get_operator_user()
    task = get_packing_task()
    dn = task.dn
    dn.status = 'picked'
    goods_id = dn.details[0].goods_id
    for d in dn.details:
        d.picked_quantity = 0
    # 表约束 picked_quantity <= quantity，计划量同步抬到已拣量
    dn.details[0].quantity = max(dn.details[0].quantity, picked_quantity)
    dn.details[0].picked_quantity = picked_quantity
    PackingTaskDetail.query.filter_by(packing_task_id=task.id).delete()
    db.session.commit()
    PackingTaskService.process_task(task.id, admin_user.id)
    return task, goods_id, admin_user


def test_create_packing_batch_rejects_goods_not_in_dn(client):
    """B-13：goods_id 不在 DN 明细内 → 16042"""
    with client.application.app_context():
        task, goods_id, admin_user = _prepare_picked_dn_for_packing()
        other_goods_id = 2 if goods_id != 2 else 1
        with pytest.raises(BadRequestException) as excinfo:
            PackingTaskService.create_batch(task.id, {"details": [
                {"goods_id": other_goods_id, "packed_quantity": 1},
            ]}, admin_user.id)
        assert excinfo.value.biz_code == 16042


def test_create_packing_batch_rejects_over_picked_and_duplicate_submit(client, access_token):
    """B-13：累计 packed_quantity 不得超过 DNDetail.picked_quantity；重复提交同一批次被拒。"""
    with client.application.app_context():
        task, goods_id, admin_user = _prepare_picked_dn_for_packing(picked_quantity=15)
        task_id = task.id

    headers = {'Authorization': f'Bearer {access_token}'}
    body = {"details": [{"goods_id": goods_id, "packed_quantity": 10}]}
    # 10 <= 15：允许
    assert client.post(f'/packing/{task_id}/batches/', headers=headers, json=body).status_code == 201
    # 断网重试 / 连点重复提交：10 + 10 > 15 → 拒绝
    response = client.post(f'/packing/{task_id}/batches/', headers=headers, json=body)
    assert response.status_code == 400
    assert response.get_json()['code'] == 16043
    # 再补 5 恰好到 15：允许；再补 1 → 拒绝
    body['details'][0]['packed_quantity'] = 5
    assert client.post(f'/packing/{task_id}/batches/', headers=headers, json=body).status_code == 201
    body['details'][0]['packed_quantity'] = 1
    assert client.post(f'/packing/{task_id}/batches/', headers=headers, json=body).status_code == 400

    with client.application.app_context():
        total = sum(d.packed_quantity for d in get_packing_task_by_id(task_id).task_details)
        assert total == 15


def test_complete_packing_task_rejects_over_packed(client):
    """B-13：complete 时再验一次，历史脏数据给出 16043 而不是撞 packed<=picked 约束 500。"""
    with client.application.app_context():
        task, goods_id, admin_user = _prepare_picked_dn_for_packing(picked_quantity=5)
        batch = PackingTaskService.create_batch(task.id, {"remark": "seed"}, admin_user.id)
        db.session.add(PackingTaskDetail(packing_task_id=task.id, batch_id=batch.id, goods_id=goods_id,
                                         packed_quantity=6, operator_id=admin_user.id))
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            PackingTaskService.complete_task(task.id, admin_user.id)
        assert excinfo.value.biz_code == 16043
        assert get_packing_task_by_id(task.id).status == 'in_progress'


def test_update_packing_detail_whitelist(client):
    """B-12/B-13：明细 PUT 只能改 packed_quantity（受已拣量约束）；goods_id 不可改。"""
    with client.application.app_context():
        task, goods_id, admin_user = _prepare_picked_dn_for_packing(picked_quantity=15)
        batch = PackingTaskService.create_batch(task.id, {"details": [
            {"goods_id": goods_id, "packed_quantity": 10},
        ]}, admin_user.id)
        detail = batch.details[0]

        with pytest.raises(BadRequestException) as excinfo:
            PackingTaskService.update_task_detail(task.id, detail.id, {"goods_id": 2 if goods_id != 2 else 1})
        assert excinfo.value.biz_code == 16049

        PackingTaskService.update_task_detail(task.id, detail.id, {"packed_quantity": 15})
        assert detail.packed_quantity == 15
        with pytest.raises(BadRequestException) as excinfo:
            PackingTaskService.update_task_detail(task.id, detail.id, {"packed_quantity": 16})
        assert excinfo.value.biz_code == 16043


@pytest.mark.parametrize('body', [
    {"details": {"goods_id": 1}},
    {"details": [{"goods_id": 1}]},
    {"details": [{"goods_id": 1, "packed_quantity": 0}]},
    {"details": [{"goods_id": 1, "packed_quantity": "three"}]},
    {"details": [{"goods_id": 1, "packed_quantity": 2.5}]},
    {"details": [{"packed_quantity": 3}]},
])
def test_create_packing_batch_invalid_body_returns_400(client, access_token, body):
    """B-18：批次明细缺字段 / 非正数 / 类型不对 → 400 而非 500"""
    with client.application.app_context():
        task, _, _ = _prepare_picked_dn_for_packing()
        task_id = task.id
    response = client.post(f'/packing/{task_id}/batches/',
                           headers={'Authorization': f'Bearer {access_token}'}, json=body)
    assert response.status_code == 400, response.get_json()


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
            "status": "in_progress",
            "is_active": False
        })
        # B-12：status / is_active 不接受客户端赋值
        assert updated_task.status == "pending"
        assert updated_task.is_active is True


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
