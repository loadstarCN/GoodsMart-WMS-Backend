from .helpers import *
from warehouse.payment.models import Payment


# ---------------------------------------------------------
# 视图层测试 (views.py)
# ---------------------------------------------------------

def test_get_payments_empty(client, access_token):
    """GET /payment/ 初始状态应返回空列表"""
    response = client.get(
        '/payment/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] == 0


def test_create_payment(client, access_token):
    """POST /payment/ 正常创建支付记录"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    response = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '1500.00',
            'currency': 'JPY',
            'payment_method': 'bank_transfer',
            'status': 'pending',
            'remark': 'Test payment'
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['payment_method'] == 'bank_transfer'
    assert float(data['amount']) == 1500.00


def test_get_payments_after_create(client, access_token):
    """创建后 GET /payment/ 应返回 1 条记录"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '500.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    response = client.get(
        '/payment/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] >= 1


def test_get_payment_by_id(client, access_token):
    """GET /payment/<id> 获取单条支付记录"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '800.00',
            'currency': 'JPY',
            'payment_method': 'online_payment',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    response = client.get(
        f'/payment/{payment_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    assert response.get_json()['id'] == payment_id


def test_get_payment_not_found(client, access_token):
    """GET /payment/99999 不存在应返回 404"""
    response = client.get(
        '/payment/99999',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 404


def test_update_payment(client, access_token):
    """PUT /payment/<id> 正常更新支付记录"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '1000.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    response = client.put(
        f'/payment/{payment_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'amount': '2000.00',
            'remark': 'Updated remark'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert float(data['amount']) == 2000.00
    assert data['remark'] == 'Updated remark'


def test_process_payment(client, access_token):
    """PUT /payment/<id>/process/ 将支付状态更新为 paid"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '3000.00',
            'currency': 'JPY',
            'payment_method': 'bank_transfer',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    response = client.put(
        f'/payment/{payment_id}/process/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'paid'
    assert data['payment_time'] is not None


def test_process_payment_not_pending(client, access_token):
    """PUT /payment/<id>/process/ 非 pending 状态应返回 400"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '500.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    # 先处理一次，变为 paid
    client.put(f'/payment/{payment_id}/process/', headers={'Authorization': f'Bearer {access_token}'})

    # 再次处理应报错
    response = client.put(
        f'/payment/{payment_id}/process/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 400


def test_cancel_payment(client, access_token):
    """PUT /payment/<id>/cancel/ 将支付状态更新为 canceled"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '200.00',
            'currency': 'JPY',
            'payment_method': 'other',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    response = client.put(
        f'/payment/{payment_id}/cancel/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    assert response.get_json()['status'] == 'canceled'


def test_cancel_payment_not_pending(client, access_token):
    """PUT /payment/<id>/cancel/ 非 pending 状态应返回 400"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '100.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    # 先取消
    client.put(f'/payment/{payment_id}/cancel/', headers={'Authorization': f'Bearer {access_token}'})

    # 再次取消应报错
    response = client.put(
        f'/payment/{payment_id}/cancel/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 400


def test_delete_payment_pending(client, access_token):
    """DELETE /payment/<id> pending 状态可以删除"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '100.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    response = client.delete(
        f'/payment/{payment_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    assert response.get_json()['message'] == 'Payment deleted successfully'


def test_delete_payment_non_pending(client, access_token):
    """DELETE /payment/<id> 非 pending 状态不允许删除"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    create_res = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '100.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    payment_id = create_res.get_json()['id']

    # 先处理，变为 paid
    client.put(f'/payment/{payment_id}/process/', headers={'Authorization': f'Bearer {access_token}'})

    response = client.delete(
        f'/payment/{payment_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 400


def test_get_payments_filter_by_status(client, access_token):
    """GET /payment/?status=pending 过滤状态"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    # 创建 pending 支付
    client.post('/payment/', headers={'Authorization': f'Bearer {access_token}'}, json={
        'delivery_id': delivery.id, 'carrier_id': carrier.id,
        'amount': '100.00', 'currency': 'JPY', 'payment_method': 'cash', 'status': 'pending'
    })

    response = client.get(
        '/payment/?status=pending&page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] >= 1
    for item in data['items']:
        assert item['status'] == 'pending'


def test_create_payment_unauthorized(client, access_operator_token):
    """权限不足时 POST /payment/ 应返回 403"""
    with client.application.app_context():
        delivery = get_delivery_task()
        carrier = get_carrier()

    response = client.post(
        '/payment/',
        headers={'Authorization': f'Bearer {access_operator_token}'},
        json={
            'delivery_id': delivery.id,
            'carrier_id': carrier.id,
            'amount': '100.00',
            'currency': 'JPY',
            'payment_method': 'cash',
            'status': 'pending',
        }
    )
    assert response.status_code == 403
