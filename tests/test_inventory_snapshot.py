from unittest.mock import patch, MagicMock
from .helpers import *
from warehouse.inventory_snapshot.models import InventorySnapshot


# ---------------------------------------------------------
# POST /task/inventory_snapshot — 触发快照任务
# ---------------------------------------------------------

def test_trigger_inventory_snapshot_returns_200(client, access_token):
    """POST /task/inventory_snapshot 应返回 200 及 message"""
    response = client.post(
        '/task/inventory_snapshot',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert 'message' in data


def test_trigger_inventory_snapshot_creates_records(client, access_token):
    """触发任务后数据库中应生成快照记录"""
    with client.application.app_context():
        count_before = InventorySnapshot.query.count()

    client.post(
        '/task/inventory_snapshot',
        headers={'Authorization': f'Bearer {access_token}'}
    )

    with client.application.app_context():
        count_after = InventorySnapshot.query.count()

    assert count_after > count_before


def test_trigger_inventory_snapshot_quantity_correct(client, access_token):
    """快照数量与实际 GoodsLocation 库存一致"""
    client.post(
        '/task/inventory_snapshot',
        headers={'Authorization': f'Bearer {access_token}'}
    )

    with client.application.app_context():
        goods_location = get_goods_location()
        warehouse = get_warehouse()
        snapshot = InventorySnapshot.query.filter_by(
            goods_id=goods_location.goods_id,
            location_id=goods_location.location_id,
            warehouse_id=warehouse.id
        ).first()
        assert snapshot is not None
        assert snapshot.quantity == goods_location.quantity


def test_trigger_inventory_snapshot_multiple_times(client, access_token):
    """多次触发快照任务应追加记录（历史快照保留）"""
    client.post('/task/inventory_snapshot',
                headers={'Authorization': f'Bearer {access_token}'})

    with client.application.app_context():
        count_first = InventorySnapshot.query.count()

    client.post('/task/inventory_snapshot',
                headers={'Authorization': f'Bearer {access_token}'})

    with client.application.app_context():
        count_second = InventorySnapshot.query.count()

    assert count_second == count_first * 2


def test_trigger_inventory_snapshot_unauthorized(client, access_operator_token):
    """operator 权限不足时应返回 403"""
    response = client.post(
        '/task/inventory_snapshot',
        headers={'Authorization': f'Bearer {access_operator_token}'}
    )
    assert response.status_code == 403


def test_trigger_inventory_snapshot_no_token(client):
    """未携带 token 应被拦截"""
    response = client.post('/task/inventory_snapshot')
    assert response.status_code in (401, 403)
