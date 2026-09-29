from warehouse.inventory.services import InventoryService
from .helpers import *
from warehouse.picking.services import PickingTaskService
from extensions.error import BadRequestException

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
            "status": "completed",
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


def test_create_picking_task_requires_dn_id(client, access_token):
    """B-18：缺 dn_id → 400；DN 不存在 → 404"""
    headers = {'Authorization': f'Bearer {access_token}'}
    assert client.post('/picking/', headers=headers, json={"status": "pending"}).status_code == 400
    assert client.post('/picking/', headers=headers, json={"dn_id": "abc"}).status_code == 400
    assert client.post('/picking/', headers=headers, json={"dn_id": True}).status_code == 400
    assert client.post('/picking/', headers=headers, json={"dn_id": 99999}).status_code == 404


def test_post_picking_detail_endpoint_removed(client, access_token):
    """B-35：明细只能经批次创建，POST /picking/<id>/details/ 已下线（原来必 500）。"""
    with client.application.app_context():
        task_id = get_picking_task().id
    response = client.post(f'/picking/{task_id}/details/',
                           headers={'Authorization': f'Bearer {access_token}'},
                           json={"goods_id": 1, "location_id": 1, "picked_quantity": 1})
    assert response.status_code == 405


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
            "status": "completed",
            "is_active": False
        })
        # B-12：status / is_active 不接受客户端赋值
        assert updated_task.status == "pending"
        assert updated_task.is_active is True


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
        _drop_off_plan_details(task)
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


def _drop_off_plan_details(task):
    """种子拣货任务带了一条不在 DN 明细内的 goods_2 明细；complete 现在会拒绝计划外商品（16042），
    正常完成流程的用例先把它清掉。"""
    planned = {d.goods_id for d in task.dn.details}
    for detail in list(task.task_details):
        if detail.goods_id not in planned:
            db.session.delete(detail)
    db.session.commit()
    db.session.expire(task, ['task_details'])


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
        assert dn.status == "in_progress"

        # 将任务状态设置为 in_progress
        PickingTaskService.process_task(task.id, admin_user.id)
        _drop_off_plan_details(task)

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
        _drop_off_plan_details(task)

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


def test_create_batch_within_planned_succeeds(client):
    """回归(Bug B)：累计已拣量不超过 DN 计划量时，create_batch 正常成功。"""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()                       # dn3：goods_1 计划 40，已拣 10
        PickingTaskService.process_task(task.id, admin_user.id)
        planned_goods_id = task.dn.details[0].goods_id  # 计划内商品(goods_1)
        location = get_location()

        # 10 + 25 = 35 <= 40 → 允许
        batch = PickingTaskService.create_batch(task.id, {
            "details": [
                {"location_id": location.id, "goods_id": planned_goods_id, "picked_quantity": 25},
            ]
        }, admin_user.id)
        assert batch is not None


def test_create_batch_rejects_overpick(client):
    """
    回归(Bug B)：同一计划商品的累计已拣量(现有 + 本次)超过 DN 计划量时必须拒绝(16029)。

    这是「重复/超量提交」污染数据的根因防线：旧实现 create_batch 纯追加、无上限校验，
    断网重试 / token 过期重登 / 连点都会不断累加 picked_quantity，最终撑大到计划量数倍，
    并在 dn_picked 处误报 15008(DN库存不足)、使单据永久无法完成。
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()                       # goods_1 计划 40，已拣 10
        PickingTaskService.process_task(task.id, admin_user.id)
        planned_goods_id = task.dn.details[0].goods_id
        location = get_location()

        # 10 + 35 = 45 > 40 → 拒绝
        with pytest.raises(BadRequestException, match="exceeds planned quantity"):
            PickingTaskService.create_batch(task.id, {
                "details": [
                    {"location_id": location.id, "goods_id": planned_goods_id, "picked_quantity": 35},
                ]
            }, admin_user.id)


def test_create_batch_rejects_quantity_above_location_stock(client):
    """Picking is capped by physical stock in the selected warehouse location."""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        goods_id = task.dn.details[0].goods_id
        location = get_location()
        stock = GoodsLocation.query.filter_by(
            goods_id=goods_id, location_id=location.id
        ).first()
        stock.quantity = 1
        # Isolate this stock boundary from the seeded historical picking details.
        PickingTaskDetail.query.filter_by(picking_task_id=task.id).delete()
        db.session.flush()

        with pytest.raises(BadRequestException, match="Insufficient stock in location"):
            PickingTaskService.create_batch(task.id, {
                'details': [{
                    'location_id': location.id,
                    'goods_id': goods_id,
                    'picked_quantity': 2,
                }],
            }, admin_user.id)


def test_complete_task_rejects_overpicked_data(client):
    """
    回归(Bug B)：对已被重复提交污染(累计已拣 > 计划)的历史单据，
    complete_task 给出明确的 16029，而非在 dn_picked 处误报 15008 或撞数据库约束。
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()                       # goods_1 计划 40，已拣 10
        PickingTaskService.process_task(task.id, admin_user.id)
        planned_goods_id = task.dn.details[0].goods_id
        location = get_location()

        # 绕过 create_batch 直接注入超量明细，模拟历史脏数据：goods_1 再 +35 → 累计 45 > 40
        batch = PickingTaskService.create_batch(task.id, {"remark": "seed"}, admin_user.id)
        db.session.add(PickingTaskDetail(
            picking_task_id=task.id,
            batch_id=batch.id,
            location_id=location.id,
            goods_id=planned_goods_id,
            picked_quantity=35,
            operator_id=admin_user.id,
        ))
        db.session.commit()

        with pytest.raises(BadRequestException, match="exceeds planned quantity"):
            PickingTaskService.complete_task(task.id, admin_user.id)


def test_update_task_detail_rejects_location_or_goods_change(client, access_token):
    """B-14：拣货明细 PUT 不能改 location_id / goods_id（否则可以挪到别仓库位，complete 时跨仓扣库存）。"""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        detail = task.task_details[0]
        other_location = Location.query.filter(Location.id != detail.location_id).first()
        task_id, detail_id = task.id, detail.id
        original_location_id, original_goods_id = detail.location_id, detail.goods_id
        other_goods_id = 2 if original_goods_id != 2 else 1

    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.put(f'/picking/{task_id}/details/{detail_id}', headers=headers,
                          json={"location_id": other_location.id})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16049

    response = client.put(f'/picking/{task_id}/details/{detail_id}', headers=headers,
                          json={"goods_id": other_goods_id})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16049

    # 回传未变化的 location_id / goods_id（前端整对象回传）是允许的
    response = client.put(f'/picking/{task_id}/details/{detail_id}', headers=headers,
                          json={"location_id": original_location_id, "goods_id": original_goods_id})
    assert response.status_code == 200

    with client.application.app_context():
        refreshed = get_picking_task_detail_by_id(detail_id)
        assert refreshed.location_id == original_location_id
        assert refreshed.goods_id == original_goods_id


def test_update_task_detail_quantity_is_bounded(client):
    """B-14：明细改量要过计划量上限与库位库存校验，且必须是正整数。"""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()                       # goods_1 计划 40，已拣 10
        PickingTaskService.process_task(task.id, admin_user.id)
        planned_goods_id = task.dn.details[0].goods_id
        detail = next(d for d in task.task_details if d.goods_id == planned_goods_id)

        # 10 → 40：恰好等于计划量，允许
        PickingTaskService.update_task_detail(task.id, detail.id, {"picked_quantity": 40})
        assert detail.picked_quantity == 40

        # 41 > 计划 40 → 16029
        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.update_task_detail(task.id, detail.id, {"picked_quantity": 41})
        assert excinfo.value.biz_code == 16029

        # 库位库存 1，改到 2 → 16036
        PickingTaskService.update_task_detail(task.id, detail.id, {"picked_quantity": 1})
        stock = GoodsLocation.query.filter_by(goods_id=detail.goods_id, location_id=detail.location_id).first()
        stock.quantity = 1
        db.session.flush()
        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.update_task_detail(task.id, detail.id, {"picked_quantity": 2})
        assert excinfo.value.biz_code == 16036

        for bad in (0, -3, "five", 1.5, True):
            with pytest.raises(BadRequestException) as excinfo:
                PickingTaskService.update_task_detail(task.id, detail.id, {"picked_quantity": bad})
            assert excinfo.value.biz_code == 16033


def test_complete_task_rejects_location_in_other_warehouse(client):
    """B-14：complete 前复核全部明细的库位必须属于 DN 仓库。"""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        _drop_off_plan_details(task)
        other_warehouse = Warehouse.query.filter(Warehouse.id != task.dn.warehouse_id).first()
        foreign_location = Location(warehouse_id=other_warehouse.id, code='FOREIGN', description='x',
                                    location_type='standard', created_by=admin_user.id)
        db.session.add(foreign_location)
        db.session.flush()
        db.session.add(GoodsLocation(goods_id=task.dn.details[0].goods_id,
                                     location_id=foreign_location.id, quantity=100))
        # 直接把一条明细挪到别仓库位，模拟被绕过校验的历史脏数据
        detail = task.task_details[0]
        detail.location_id = foreign_location.id
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.complete_task(task.id, admin_user.id)
        assert excinfo.value.biz_code == 16054
        assert get_picking_task_by_id(task.id).status == 'in_progress'


def test_picking_rejects_goods_not_in_dn(client, access_token):
    """
    拣货明细的商品必须在 DN 明细内（16042）：批次创建 / 明细改量当场拒绝，
    complete 前对全部明细再验一次（种子任务自带一条计划外 goods_2 明细，正好当脏数据）。
    拣货下架走 picking_removed 不进 sorted_stock，计划外商品一旦下架会从 total_stock 里消失。
    """
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()                       # dn3 只有 goods_1
        PickingTaskService.process_task(task.id, admin_user.id)
        planned = {d.goods_id for d in task.dn.details}
        off_plan_detail = next(d for d in task.task_details if d.goods_id not in planned)
        off_plan_goods_id = off_plan_detail.goods_id
        location = get_location()
        task_id, off_plan_detail_id, location_id = task.id, off_plan_detail.id, location.id

        # 批次创建：计划外商品 → 16042
        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.create_batch(task.id, {"details": [
                {"location_id": location.id, "goods_id": off_plan_goods_id, "picked_quantity": 1},
            ]}, admin_user.id)
        assert excinfo.value.biz_code == 16042

        # complete：历史脏数据（计划外明细）→ 16042，任务保持 in_progress
        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.complete_task(task.id, admin_user.id)
        assert excinfo.value.biz_code == 16042
        assert get_picking_task_by_id(task.id).status == 'in_progress'

    # 明细改量：计划外明细 → 16042（走 API）
    response = client.put(f'/picking/{task_id}/details/{off_plan_detail_id}',
                          headers={'Authorization': f'Bearer {access_token}'},
                          json={"picked_quantity": 1})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16042

    # 批次创建走 API 同样 16042
    response = client.post(f'/picking/{task_id}/batches/',
                           headers={'Authorization': f'Bearer {access_token}'},
                           json={"details": [{"location_id": location_id, "goods_id": off_plan_goods_id,
                                              "picked_quantity": 1}]})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16042


def test_create_batch_rejects_location_in_other_warehouse(client):
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        other_warehouse = Warehouse.query.filter(Warehouse.id != task.dn.warehouse_id).first()
        foreign_location = Location(warehouse_id=other_warehouse.id, code='FOREIGN2', description='x',
                                    location_type='standard', created_by=admin_user.id)
        db.session.add(foreign_location)
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            PickingTaskService.create_batch(task.id, {"details": [
                {"location_id": foreign_location.id, "goods_id": task.dn.details[0].goods_id, "picked_quantity": 1},
            ]}, admin_user.id)
        assert excinfo.value.biz_code == 16054


@pytest.mark.parametrize('body', [
    {"details": {"goods_id": 1}},                                                     # details 不是 list
    {"details": ["x"]},                                                               # 元素不是对象
    {"details": [{"location_id": 1, "goods_id": 1}]},                                 # 缺 picked_quantity
    {"details": [{"location_id": 1, "goods_id": 1, "picked_quantity": 0}]},           # 非正数
    {"details": [{"location_id": 1, "goods_id": 1, "picked_quantity": "two"}]},       # 非整数
    {"details": [{"location_id": 1, "goods_id": 1, "picked_quantity": True}]},        # bool 不是数量
    {"details": [{"goods_id": 1, "picked_quantity": 1}]},                             # 缺 location_id
    {"details": [{"location_id": 1, "picked_quantity": 1}]},                          # 缺 goods_id
    {"operation_time": "yesterday"},                                                  # 时间格式非法
])
def test_create_batch_invalid_body_returns_400(client, access_token, body):
    """B-18：批次明细缺字段 / 非正数 / 类型不对 → 400 而非 500"""
    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        task_id = task.id
    response = client.post(f'/picking/{task_id}/batches/',
                           headers={'Authorization': f'Bearer {access_token}'}, json=body)
    assert response.status_code == 400, response.get_json()


def test_location_stock_lock_query_has_no_outer_join(client):
    """
    回归：_assert_location_stock 的 SELECT ... FOR UPDATE 行锁查询不得携带
    eager join（lazy='joined'）产生的 LEFT OUTER JOIN。

    PostgreSQL 对 outer join 的可空侧加 FOR UPDATE 会抛
    FeatureNotSupported("FOR UPDATE cannot be applied to the nullable side
    of an outer join")，导致保存拣货批次直接 500。SQLite 会忽略 FOR UPDATE
    子句、无法复现该报错，故通过捕获实际下发的 SQL 来断言查询形态。
    """
    from sqlalchemy import event

    with client.application.app_context():
        admin_user = get_operator_user()
        task = get_picking_task()
        PickingTaskService.process_task(task.id, admin_user.id)
        planned_goods_id = task.dn.details[0].goods_id
        location = get_location()

        captured = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        event.listen(db.engine, "before_cursor_execute", _capture)
        try:
            PickingTaskService.create_batch(task.id, {
                "details": [
                    {"location_id": location.id, "goods_id": planned_goods_id, "picked_quantity": 1},
                ]
            }, admin_user.id)
        finally:
            event.remove(db.engine, "before_cursor_execute", _capture)

        lock_lookups = [
            s for s in captured
            if s.lstrip().upper().startswith("SELECT") and "FROM goods_locations" in s
        ]
        assert lock_lookups, "expected _assert_location_stock to query goods_locations"
        for statement in lock_lookups:
            assert "LEFT OUTER JOIN" not in statement.upper(), (
                "goods_locations 行锁查询携带了 eager join 的 LEFT OUTER JOIN，"
                "会在 PostgreSQL 上触发 FeatureNotSupported：\n" + statement
            )
