from extensions.error import BadRequestException
from warehouse.delivery.services import DeliveryTaskService
from .helpers import *


# ---------------------------------------------------------
# 以下为测试视图层 (views.py) 逻辑的用例
# ---------------------------------------------------------

def test_create_task_task(client, access_token):
    """
    测试创建 Delivery Task (POST /delivery/)
    """
    response = client.post(
        '/delivery/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "dn_id": 1,
            "recipient_id": 1,
            "carrier_id": 1,
            "shipping_address": "New Delivery Address",
            "status": "pending",
            "expected_shipping_date": "2022-01-01",
            "transportation_mode": "express"
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['shipping_address'] == "New Delivery Address"


def test_get_delivery_tasks(client, access_token):
    """
    测试获取 DeliveryTask 列表 (GET /delivery/)
    """
    response = client.get(
        '/delivery/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1


def test_update_delivery_task(client, access_token):
    """
    测试更新 DeliveryTask (PUT /delivery/<task_id>)
    """
    with client.application.app_context():
        task = get_delivery_task()
        assert task is not None
        task_id = task.id

    response = client.put(
        f'/delivery/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "tracking_number": "DT-UPDATED",
            "status": "signed",
            "is_active": False,
            "dn_id": 2,
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['tracking_number'] == "DT-UPDATED"
    # B-12 / B-40：status / is_active / dn_id 不能经 PUT 改
    assert data['status'] == "pending"
    assert data['is_active'] is True
    assert data['dn_id'] == task.dn_id


@pytest.mark.parametrize('body', [
    {"dn_id": 1, "recipient_id": 1, "shipping_address": "x"},                       # 缺 expected_shipping_date
    {"recipient_id": 1, "shipping_address": "x", "expected_shipping_date": "2026-01-01"},  # 缺 dn_id
    {"dn_id": 1, "recipient_id": 1, "shipping_address": "x",
     "expected_shipping_date": "2026-01-01", "transportation_mode": "teleport"},     # 枚举非法
    {"dn_id": 1, "recipient_id": 1, "shipping_address": "x",
     "expected_shipping_date": "2026-01-01", "shipping_cost": -1},                  # 运费为负
    {"dn_id": 1, "recipient_id": 1, "shipping_address": "x",
     "expected_shipping_date": "2026-01-01", "actual_shipping_date": "2025-12-31"}, # 实际早于计划（撞 CHECK 约束）
    {"dn_id": 1, "recipient_id": 1, "shipping_address": "x", "expected_shipping_date": "01/01/2026"},  # 日期格式
])
def test_create_delivery_invalid_body_returns_400(client, access_token, body):
    """B-18：缺字段 / 枚举非法 / 数值非法 → 400 而非 500"""
    response = client.post('/delivery/', headers={'Authorization': f'Bearer {access_token}'}, json=body)
    assert response.status_code == 400, response.get_json()


def test_create_delivery_rejects_recipient_of_other_company(client, access_token):
    """B-04：收货人 / 承运商必须与 DN 仓库同公司 → 403"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        recipient_b = Recipient(name='R-B-DELIVERY', address='b', zip_code='1', phone='1', email='rbd@b.com',
                                contact='c', country='us', created_by=admin.id, company_id=company_b.id)
        db.session.add(recipient_b)
        db.session.commit()
        recipient_b_id = recipient_b.id

    response = client.post('/delivery/', headers={'Authorization': f'Bearer {access_token}'}, json={
        "dn_id": 1, "recipient_id": recipient_b_id, "shipping_address": "x",
        "expected_shipping_date": "2026-01-01",
    })
    assert response.status_code == 403
    assert response.get_json()['code'] == 12001


def test_delete_delivery_task(client, access_token):
    """
    测试删除 DeliveryTask (DELETE /delivery/<task_id>)
    """
    with client.application.app_context():
        task = get_delivery_task()
        task_id = task.id

    response = client.delete(
        f'/delivery/{task_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "Delivery deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted_task = db.session.get(DeliveryTask, task_id)
        assert deleted_task is None


# ---------------------------------------------------------
# 以下为直接测试服务层 (services.py) 逻辑的用例
# ---------------------------------------------------------

def test_delivery_task_service_create_task(client):
    """
    测试通过服务层创建一个新的 DeliveryTask
    """
    with client.application.app_context():
        user = get_operator_user()
        dn = get_dn()
        recipient = get_recipient()
        carrier = get_carrier()
        data = {
            "dn_id": dn.id,
            "tracking_number": "DT-SERVICE-NEW",
            "recipient_id": recipient.id,
            "carrier_id": carrier.id,
            "shipping_address": "New Address",
            "status": "pending",
            "expected_shipping_date": "2022-01-01",
            "transportation_mode": "express"

        }
        task = DeliveryTaskService.create_task(data,user.id)
        assert task is not None
        assert task.tracking_number == "DT-SERVICE-NEW"


def test_delivery_task_service_update_delivery(client):
    """
    测试通过服务层更新 DeliveryTask
    """
    with client.application.app_context():
        task = get_delivery_task()
        user = get_operator_user()

        original_dn_id = task.dn_id
        updated_data = {
            "status": "completed",
            "dn_id": original_dn_id + 1,
            "remark": "service updated",
        }
        updated_task = DeliveryTaskService.update_task(task.id, updated_data)
        assert updated_task is not None
        # B-12 / B-40：status 只经流程端点变更，dn_id 不可改
        assert updated_task.status == "pending"
        assert updated_task.dn_id == original_dn_id
        assert updated_task.remark == "service updated"


def test_create_task_with_invalid_data(client):
    """
    测试通过服务层创建 DeliveryTask 时，传入无效数据的情况
    """
    with client.application.app_context():
        user = get_operator_user()
        dn = get_dn()

        # 创建任务时缺少必要字段，例如收货人
        invalid_data = {
            "dn_id": dn.id,
            "tracking_number": "DT-SERVICE-INVALID",
            "carrier_id": 1,  # 收货人缺失

            "shipping_address": "Invalid Address",
            "status": "pending",
        }

        # B-18：缺必填字段 → 400（BadRequestException），而不是 KeyError 变 500
        with pytest.raises(BadRequestException) as excinfo:
            DeliveryTaskService.create_task(invalid_data,user.id)
        assert excinfo.value.biz_code == 40000


def test_update_delivery_with_invalid_status(client):
    """
    测试通过服务层更新 DeliveryTask 时，使用无效的状态
    """
    with client.application.app_context():
        task = get_delivery_task()
        user = get_operator_user()
        task.status = "completed"
        db.session.commit()

        # 尝试更新为一个不存在的状态
        updated_data = {
            "status": "invalid_status",  # 无效状态
        }

        # 预期会抛出错误，状态不被允许
        with pytest.raises(BadRequestException) as excinfo:
            DeliveryTaskService.update_task(task.id, updated_data)

        assert 16023 == excinfo.value.biz_code

