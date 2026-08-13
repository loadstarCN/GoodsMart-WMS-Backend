from extensions.error import BadRequestException
from warehouse.inventory.services import InventoryService
from .helpers import *


def test_get_inventories(client, access_token):
    """测试分页获取 Inventory 列表 (GET /inventory/)"""
    # 测试基础分页参数
    response = client.get('/inventory/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert 'total' in data
    assert 'pages' in data


def test_filter_by_goods_id(client, access_token):
    """测试按 goods_id 过滤"""

    with client.application.app_context():
        goods = get_goods()
        # 过滤指定 goods_id
        response = client.get(f'/inventory/?goods_id={goods.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        data = response.get_json()
        assert len(data['items']) == 1
        assert data['items'][0]['goods_id'] == goods.id


def test_filter_by_warehouse_id(client, access_token):
    """测试按 warehouse_id 过滤"""
    with client.application.app_context():
        # 过滤指定 warehouse_id
        warehouse = get_warehouse()
        inventories = Inventory.query.filter_by(warehouse_id=warehouse.id).all()
        response = client.get(f'/inventory/?warehouse_id={warehouse.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        data = response.get_json()
        assert len(data['items']) == len(inventories)
        assert data['items'][0]['warehouse_id'] == warehouse.id


def test_filter_by_goods_codes(client, access_token):
    """测试按 goods_codes 多值过滤"""
    with client.application.app_context():
        # 创建测试数据
        warehouse = get_warehouse()
        inventories = Inventory.query.filter_by(warehouse_id=warehouse.id).all()
        goods_codes = [inventory.goods.code for inventory in inventories]
        goods_codes_str = ','.join(goods_codes)

        # 多值过滤（逗号分隔）
        response = client.get(f'/inventory/?goods_codes={goods_codes_str}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        data = response.get_json()
        assert len(data['items']) == len(inventories)
        codes = [item['goods']['code'] for item in data['items']]
        assert codes.sort() == goods_codes.sort()


def test_filter_by_stock_thresholds(client, access_token):
    """测试高低库存阈值过滤"""
    with client.application.app_context():
        # 创建测试数据
        inventory = get_inventory()
        threshould = inventory.low_stock_threshold

        low_inventories = Inventory.query.filter(Inventory.low_stock_threshold <= threshould).all()
        high_inventories = Inventory.query.filter(Inventory.high_stock_threshold >= threshould).all()

        # 测试低库存阈值过滤
        response = client.get(f'/inventory/?low_stock_threshold={threshould}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        data = response.get_json()
        assert len(data['items']) == len(low_inventories)  # 两个记录都满足 low_stock_threshold <= 10

        # 测试高库存阈值过滤
        response = client.get(f'/inventory/?high_stock_threshold={threshould}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        data = response.get_json()
        assert len(data['items']) == len(high_inventories)
        assert data['items'][0]['high_stock_threshold'] >= threshould


def test_combined_filters(client, access_token):
    """测试组合过滤条件"""
    with client.application.app_context():
        # 创建符合多个条件的测试数据
        inventory = get_inventory()

        # 组合过滤
        response = client.get(
            f'/inventory/?goods_id={inventory.goods_id}&warehouse_id={inventory.warehouse_id}&goods_codes={inventory.goods.code}&low_stock_threshold={inventory.low_stock_threshold}&high_stock_threshold={inventory.high_stock_threshold}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        data = response.get_json()
        assert len(data['items']) == 1
        assert data['items'][0]['goods_id'] == inventory.goods_id


def test_get_inventory_details(client, access_token):
    """测试根据 goods_id 和 warehouse_id 获取单个 Inventory (GET /inventory/<goods_id>/<warehouse_id>)"""
    with client.application.app_context():
        inventory = get_inventory()
        url = f'/inventory/goods/{inventory.goods_id}/warehouse/{inventory.warehouse_id}'
        response = client.get(url, headers={
            'Authorization': f'Bearer {access_token}',
            'X-Warehouse-ID': inventory.warehouse_id
        })

        assert response.status_code == 200
        data = response.get_json()
        assert data['goods_id'] == inventory.goods_id
        assert data['warehouse_id'] == inventory.warehouse_id
        assert data['total_stock'] == inventory.total_stock


# ------------------------- 以下为测试 InventoryService 的方法 -------------------------


def test_inventory_service_lock_inventory(client):
    """
    测试锁定库存：onhand_stock 减少，locked_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 初始库存
        initial_onhand_stock = inventory.onhand_stock
        initial_locked_stock = inventory.locked_stock

        InventoryService.lock_inventory(goods_id, warehouse_id, 2)
        assert inventory.locked_stock == initial_locked_stock + 2


def test_inventory_service_unlock_inventory(client):
    """
    测试解锁库存：locked_stock 减少，onhand_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 确保有足够的 locked_stock
        InventoryService.lock_inventory(goods_id, warehouse_id, 2)

        initial_locked_stock = inventory.locked_stock
        initial_onhand_stock = inventory.onhand_stock

        InventoryService.unlock_inventory(goods_id, warehouse_id, 1)
        assert inventory.locked_stock == initial_locked_stock - 1


def test_inventory_service_asn_completed(client):
    """
    测试分拣完成：需要先保证有足够的 received_stock。
    逻辑：received_stock 减少，sorted_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 确保有足够的 asn_stock -> received_stock
        InventoryService.update_and_calculate_asn_stock(goods_id, warehouse_id)
        InventoryService.asn_received(goods_id, warehouse_id, 30)  # ASN->received

        initial_received_stock = inventory.received_stock
        initial_sort_stock = inventory.sorted_stock

        InventoryService.asn_completed(goods_id, warehouse_id, 10, 10)
        assert inventory.received_stock == initial_received_stock - 10
        assert inventory.sorted_stock == initial_sort_stock + 10


def test_inventory_service_putaway_completed(client):
    """
    测试上架完成：sorted_stock 减少，onhand_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        InventoryService.update_and_calculate_asn_stock(goods_id, warehouse_id)
        InventoryService.asn_received(goods_id, warehouse_id, 30)
        InventoryService.asn_completed(goods_id, warehouse_id, 20, 20)

        initial_sort_stock = inventory.sorted_stock

        InventoryService.putaway_completed(goods_id, warehouse_id, 10)
        assert inventory.sorted_stock == initial_sort_stock - 10



def test_inventory_service_picking_completed(client):
    """
    测试拣货完成：dn_stock 减少，picked_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 先保证有足够的 dn_stock
        InventoryService.update_and_calculate_dn_stock(goods_id, warehouse_id)

        initial_dn_stock = inventory.dn_stock
        initial_picked_stock = inventory.picked_stock

        InventoryService.dn_picked(goods_id, warehouse_id, 2, 2)
        assert inventory.dn_stock == initial_dn_stock - 2
        assert inventory.picked_stock == initial_picked_stock + 2


def test_partial_pick_releases_unpicked_dn_reservation(client):
    """A short pick moves the real quantity and releases the remaining reservation."""
    with client.application.app_context():
        inventory = get_inventory()
        inventory.dn_stock = 2
        initial_picked_stock = inventory.picked_stock

        InventoryService.dn_picked(
            inventory.goods_id, inventory.warehouse_id, quantity=2, picked_quantity=1
        )

        assert inventory.dn_stock == 0
        assert inventory.picked_stock == initial_picked_stock + 1


def test_inventory_service_packing_completed(client):
    """
    测试包装完成：picked_stock 减少，packed_stock 增加
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 先保证有足够的 picked_stock
        InventoryService.update_and_calculate_dn_stock(goods_id, warehouse_id)
        InventoryService.dn_picked(goods_id, warehouse_id, 10, 10)

        initial_picked_stock = inventory.picked_stock
        initial_packed_stock = inventory.packed_stock

        InventoryService.dn_packed(goods_id, warehouse_id, 5)
        assert inventory.packed_stock == initial_packed_stock + 5
        assert inventory.picked_stock == initial_picked_stock - 5


def test_inventory_service_delivery_completed(client):
    """
    测试发货完成：packed_stock 减少，delivered_stock 增加，total_stock 减少
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 确保有足够的 packed_stock
        InventoryService.update_and_calculate_dn_stock(goods_id, warehouse_id)
        InventoryService.dn_picked(goods_id, warehouse_id, 10, 10)
        InventoryService.dn_packed(goods_id, warehouse_id, 10)

        initial_packed_stock = inventory.packed_stock
        initial_delivered_stock = inventory.delivered_stock
        initial_total_stock = inventory.total_stock

        InventoryService.dn_delivered(goods_id, warehouse_id, 5)
        assert inventory.packed_stock == initial_packed_stock - 5
        assert inventory.delivered_stock == initial_delivered_stock + 5
        assert inventory.total_stock == initial_total_stock - 5


def test_inventory_service_delivery_signed(client):
    """
    测试签收确认：delivered_stock 减少
    """
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 先保证有足够的 delivered_stock
        InventoryService.update_and_calculate_dn_stock(goods_id, warehouse_id)
        InventoryService.dn_picked(goods_id, warehouse_id, 10, 10)
        InventoryService.dn_packed(goods_id, warehouse_id, 10)
        InventoryService.dn_delivered(goods_id, warehouse_id, 10)

        initial_delivered_stock = inventory.delivered_stock

        InventoryService.dn_completed(goods_id, warehouse_id, 5)
        assert inventory.delivered_stock == initial_delivered_stock - 5


def test_set_low_stock_threshold_with_disable(client):
    """测试设置低库存阈值支持禁用"""
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 设置低库存阈值
        InventoryService.set_low_stock_threshold(goods_id, warehouse_id, 20)
        assert inventory.low_stock_threshold == 20

        # 设置为 -1 表示禁用
        InventoryService.set_low_stock_threshold(goods_id, warehouse_id, -1)
        assert inventory.low_stock_threshold == -1

        # 尝试设置负数（非 -1），应该抛出异常
        with pytest.raises(BadRequestException, match="Low stock threshold must be non-negative or -1 to disable."):
            InventoryService.set_low_stock_threshold(goods_id, warehouse_id, -5)


def test_set_high_stock_threshold(client):
    """测试设置高库存阈值"""
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 设置低库存阈值
        InventoryService.set_low_stock_threshold(goods_id, warehouse_id, 20)

        # 设置新的高库存阈值
        InventoryService.set_high_stock_threshold(goods_id, warehouse_id, 1200)
        assert inventory.high_stock_threshold == 1200

        # 尝试设置负数，应该抛出异常
        with pytest.raises(BadRequestException, match="High stock threshold must be non-negative or -1 to disable."):
            InventoryService.set_high_stock_threshold(goods_id, warehouse_id, -10)

        # 设置为 -1 表示禁用高库存阈值
        InventoryService.set_high_stock_threshold(goods_id, warehouse_id, -1)
        assert inventory.high_stock_threshold == -1

        # 先设为一个合理值，再设比低库存阈值小或等于的值，应该抛出异常
        InventoryService.set_high_stock_threshold(goods_id, warehouse_id, 25)  # 正确值测试
        with pytest.raises(BadRequestException, match="High stock threshold must be greater than low stock threshold."):
            InventoryService.set_high_stock_threshold(goods_id, warehouse_id, 15)

        # 确保阈值未被改变
        assert inventory.high_stock_threshold == 25


def test_check_stock_thresholds(client):
    """测试检查库存阈值"""
    with client.application.app_context():
        inventory = get_inventory()
        goods_id = inventory.goods_id
        warehouse_id = inventory.warehouse_id

        # 设置库存和阈值
        inventory.onhand_stock = 50
        inventory.dn_stock = 10
        db.session.commit()
        InventoryService.set_low_stock_threshold(goods_id, warehouse_id, 30)
        InventoryService.set_high_stock_threshold(goods_id, warehouse_id, 100)

        # 检查库存状态
        status = InventoryService.check_stock_thresholds(goods_id, warehouse_id)
        print(status)
        assert status["is_below_low_threshold"] is False
        assert status["is_above_high_threshold"] is False

        # 模拟低于低库存阈值的情况
        inventory.onhand_stock = 20
        db.session.commit()
        status = InventoryService.check_stock_thresholds(goods_id, warehouse_id)
        assert status["is_below_low_threshold"] is True
        assert status["is_above_high_threshold"] is False

        # 模拟高于高库存阈值的情况
        inventory.onhand_stock = 150
        db.session.commit()
        status = InventoryService.check_stock_thresholds(goods_id, warehouse_id)
        assert status["is_below_low_threshold"] is False
        assert status["is_above_high_threshold"] is True

        # 设置高库存阈值为 -1 (禁用高库存检查)
        InventoryService.set_high_stock_threshold(goods_id, warehouse_id, -1)
        status = InventoryService.check_stock_thresholds(goods_id, warehouse_id)
        assert status["is_below_low_threshold"] is False
        assert status["is_above_high_threshold"] is False


def test_update_and_calculate_dn_stock_counts_in_progress(client):
    """
    回归(Bug A)：dn_stock 重聚合必须把 in_progress 的 DN 预扣计入。

    旧实现只统计 status == 'pending'，导致 DN 一进入拣货(in_progress)，
    后续任何重聚合(如别的 DN 增删改/关闭)都会把它的预扣清掉，
    最终在 picking_dn → dn_picked 时 dn_stock < picked_quantity 误报 15008(DN库存不足)。
    """
    with client.application.app_context():
        # 取一个处于 in_progress 的 DN，及其中一个商品
        dn_ip = DN.query.filter_by(status='in_progress', is_active=True).first()
        assert dn_ip is not None, "fixture 需包含 in_progress 的 DN"
        gid = dn_ip.details[0].goods_id
        wid = dn_ip.warehouse_id

        def planned_sum(status):
            total = 0
            for d in DN.query.filter_by(warehouse_id=wid, is_active=True, status=status).all():
                total += sum(det.quantity for det in d.details if det.goods_id == gid)
            return total

        pending_q = planned_sum('pending')
        inprogress_q = planned_sum('in_progress')
        assert inprogress_q > 0, "该商品需存在于 in_progress 的 DN 中才能验证此回归"

        InventoryService.update_and_calculate_dn_stock(gid, wid)
        inv = Inventory.query.filter_by(goods_id=gid, warehouse_id=wid).first()

        # 关键：dn_stock 必须同时包含 pending 与 in_progress 的预扣，而不只是 pending
        assert inv.dn_stock == pending_q + inprogress_q
        assert inv.dn_stock > pending_q


def test_dn_stock_recalculation_releases_short_pick_difference(client):
    """Completed picking must not re-reserve the quantity that was not found."""
    with client.application.app_context():
        dn = DN.query.filter_by(status='in_progress', is_active=True).first()
        detail = dn.details[0]
        detail.quantity = 2
        detail.picked_quantity = 1
        dn.status = 'picked'

        # Isolate this goods/warehouse from other active reservations.
        for other in DN.query.filter(
            DN.id != dn.id,
            DN.warehouse_id == dn.warehouse_id,
            DN.is_active.is_(True),
        ).all():
            for other_detail in other.details:
                if other_detail.goods_id == detail.goods_id:
                    other.is_active = False
                    break
        db.session.flush()

        InventoryService.update_and_calculate_dn_stock(
            detail.goods_id, dn.warehouse_id
        )
        inventory = Inventory.query.filter_by(
            goods_id=detail.goods_id, warehouse_id=dn.warehouse_id
        ).first()
        assert inventory.dn_stock == 0
