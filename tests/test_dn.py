from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from warehouse.dn.services import DNService

from warehouse.inventory.services import InventoryService
from .helpers import *

# -------------------- 以下为测试各个接口 (views.py) 的用例 --------------------

def test_create_dn(client, access_token):
    """
    测试创建 DN (POST /dn/)
    """
    with client.application.app_context():
        warehouse = Warehouse.query.first()
        carrier = Carrier.query.first()
        recipient = Recipient.query.first()
        goods = Goods.query.first()
        assert carrier is not None
        assert recipient is not None
        assert goods is not None

    response = client.post(
        '/dn/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'recipient_id': recipient.id,
            'warehouse_id': warehouse.id,
            'shipping_address': '456 Another Street',
            'expected_shipping_date': '2025-01-01',
            'carrier_id': carrier.id,
            'dn_type': 'damage_to_supplier',
            'status': 'pending',
            'remark': 'Test DN creation',
            'details': [
                {
                    "dn_id": 999,  # 这里填什么无所谓，后端会用 path 中的 dn_id
                    "goods_id": goods.id,
                    "quantity": 10,
                    "picked_quantity": 0,
                    "remark": "Sample detail"
                }
            ]
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['dn_type'] == 'damage_to_supplier'
    assert len(data['details']) == 1


def test_create_dn_rejects_quantity_above_available_stock(client):
    """An integration cannot create a DN that makes available stock negative."""
    with client.application.app_context():
        warehouse = Warehouse.query.first()
        recipient = Recipient.query.first()
        goods = Goods.query.first()
        inventory = Inventory.query.filter_by(
            goods_id=goods.id, warehouse_id=warehouse.id
        ).first()
        inventory.onhand_stock = 1
        inventory.locked_stock = 0
        inventory.dn_stock = 0
        db.session.commit()

        with pytest.raises(BadRequestException, match="Insufficient available stock"):
            DNService.create_dn({
                'recipient_id': recipient.id,
                'warehouse_id': warehouse.id,
                'shipping_address': 'test',
                'expected_shipping_date': '2026-08-13',
                'dn_type': 'shipping',
                'details': [{'goods_id': goods.id, 'quantity': 2}],
            }, created_by_id=get_operator_user().id)


def test_create_dn_resolves_legacy_carrier_name_from_code(client):
    """carrier_code works with existing rows whose code column was never backfilled."""
    with client.application.app_context():
        warehouse = Warehouse.query.first()
        recipient = Recipient.query.first()
        goods = Goods.query.first()
        carrier = Carrier.query.first()
        carrier.name = 'ヤマト'
        carrier.code = None
        inventory = Inventory.query.filter_by(
            goods_id=goods.id, warehouse_id=warehouse.id
        ).first()
        inventory.onhand_stock = 10
        inventory.locked_stock = 0
        inventory.dn_stock = 0
        db.session.commit()

        dn = DNService.create_dn({
            'recipient_id': recipient.id,
            'warehouse_id': warehouse.id,
            'company_id': carrier.company_id,
            'shipping_address': 'test',
            'expected_shipping_date': '2026-08-13',
            'carrier_code': 'yamato',
            'dn_type': 'shipping',
            'details': [{'goods_id': goods.id, 'quantity': 1}],
        }, created_by_id=get_operator_user().id)

        assert dn.carrier_id == carrier.id


def test_progress_dn_rejects_when_physical_stock_is_insufficient(client):
    """A legacy oversized DN cannot enter picking even if it already exists."""
    with client.application.app_context():
        dn = get_dn()
        detail = dn.details[0]
        inventory = Inventory.query.filter_by(
            goods_id=detail.goods_id, warehouse_id=dn.warehouse_id
        ).first()
        inventory.onhand_stock = max(detail.quantity - 1, 0)
        inventory.locked_stock = 0
        dn.status = 'pending'
        db.session.commit()

        with pytest.raises(BadRequestException, match="Insufficient physical stock"):
            DNService.progress_dn(dn.id)


def test_get_dns(client, access_token):
    """
    测试获取 DN 列表 (GET /dn/)
    """
    response = client.get(
        '/dn/?page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    # data 中通常包含 items, total, page, per_page, ...
    assert 'items' in data
    assert data['total'] >= 1  # 预期至少存在一个 DN


def test_get_dn_detail(client, access_token):
    """
    测试获取单个 DN (GET /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

    response = client.get(
        f'/dn/{dn.id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['id'] == dn.id
    assert data['status'] == dn.status


def test_update_dn(client, access_token):
    """
    测试更新 DN (PUT /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None
        dn_id = dn.id

    response = client.put(
        f'/dn/{dn_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'shipping_address': '999 Updated Street',
            'status': 'picked',
            'is_active': False,
            'created_by': 999,
            'remark': 'Updated DN remark'
        }
    )
    # B-12：status / is_active / created_by 只能经流程端点变更，PUT 里的值被忽略
    assert response.status_code == 200
    data = response.get_json()
    assert data['shipping_address'] == '999 Updated Street'
    assert data['status'] == 'pending'
    assert data['is_active'] is True
    assert data['created_by'] != 999
    assert data['remark'] == 'Updated DN remark'


def test_update_dn_status_closed_is_ignored(client, access_token):
    """B-12：PUT /dn/<id> {"status": "closed"} 不能绕过状态机关闭单据、释放预占。"""
    with client.application.app_context():
        dn = get_dn()
        dn_id = dn.id
        goods_id = dn.details[0].goods_id
        reserved_before = get_inventory_by_goods_id_and_warehouse_id(goods_id, dn.warehouse_id).dn_stock

    response = client.put(
        f'/dn/{dn_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={'status': 'closed'}
    )
    assert response.status_code == 200
    assert response.get_json()['status'] == 'pending'

    with client.application.app_context():
        dn = get_dn_by_id(dn_id)
        assert dn.status == 'pending'
        assert dn.closed_at is None
        assert get_inventory_by_goods_id_and_warehouse_id(goods_id, dn.warehouse_id).dn_stock == reserved_before


def test_create_dn_ignores_status_and_quantity_fields(client, access_token):
    """B-12：创建时传入的 status / is_active / picked_quantity 等一律忽略。"""
    with client.application.app_context():
        warehouse = Warehouse.query.first()
        recipient = Recipient.query.first()
        goods = Goods.query.filter_by(code='G002').first()

    response = client.post(
        '/dn/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'recipient_id': recipient.id,
            'warehouse_id': warehouse.id,
            'shipping_address': 'x',
            'expected_shipping_date': '2026-01-01',
            'status': 'completed',
            'is_active': False,
            'details': [{'goods_id': goods.id, 'quantity': 3,
                         'picked_quantity': 3, 'packed_quantity': 3, 'delivered_quantity': 3}],
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['is_active'] is True
    assert data['details'][0]['picked_quantity'] == 0
    assert data['details'][0]['packed_quantity'] == 0
    assert data['details'][0]['delivered_quantity'] == 0


@pytest.mark.parametrize('body', [
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x'},            # 缺 expected_shipping_date / details
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': {'goods_id': 1}},        # details 不是 list
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': []},                     # details 为空
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': [{'goods_id': 1, 'quantity': 0}]},   # 数量非正
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': [{'goods_id': 1, 'quantity': 'five'}]}, # 数量非整数
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': [{'goods_id': 1, 'quantity': 1.5}]},   # 数量非整数
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': [{'goods_id': 1, 'quantity': True}]},  # bool 不是数量
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'details': [{'quantity': 5}]},      # 缺 goods_id
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': '2026-01-01', 'dn_type': 'bogus',
     'details': [{'goods_id': 1, 'quantity': 1}]},                              # 枚举非法
    {'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
     'expected_shipping_date': 'not-a-date', 'details': [{'goods_id': 1, 'quantity': 1}]},   # 日期非法
])
def test_create_dn_invalid_body_returns_400(client, access_token, body):
    """B-18：缺字段 / 类型不对 / 非正数 → 400 而非 500"""
    response = client.post('/dn/', headers={'Authorization': f'Bearer {access_token}'}, json=body)
    assert response.status_code == 400, response.get_json()


def test_create_dn_rejects_duplicate_goods(client, access_token):
    with client.application.app_context():
        goods = Goods.query.first()
    response = client.post('/dn/', headers={'Authorization': f'Bearer {access_token}'}, json={
        'recipient_id': 1, 'warehouse_id': 1, 'shipping_address': 'x',
        'expected_shipping_date': '2026-01-01',
        'details': [{'goods_id': goods.id, 'quantity': 1}, {'goods_id': goods.id, 'quantity': 1}],
    })
    assert response.status_code == 400
    assert response.get_json()['code'] == 16025


def test_create_dn_detail(client, access_token):
    """
    测试创建 DNDetail (POST /dn/<dn_id>/details/)
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 同一商品在一张 DN 上只能有一行，种子 DN 已含 G001，这里用 G002
        goods = Goods.query.filter_by(code='G002').first()
        assert dn is not None
        assert goods is not None

    response = client.post(
        f'/dn/{dn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            "dn_id": dn.id,  # 实际后端会用 path 的 dn_id
            "goods_id": goods.id,
            "quantity": 5,
            "picked_quantity": 2,
            "remark": "New detail item"
        }
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data['quantity'] == 5
    assert data['picked_quantity'] == 0  # B-12：统计字段不接受客户端赋值


def test_create_dn_detail_rejects_duplicate_goods_and_bad_quantity(client, access_token):
    with client.application.app_context():
        dn = DN.query.first()
        existing_goods_id = dn.details[0].goods_id
        other_goods = Goods.query.filter_by(code='G002').first()
    headers = {'Authorization': f'Bearer {access_token}'}

    response = client.post(f'/dn/{dn.id}/details/', headers=headers,
                           json={'goods_id': existing_goods_id, 'quantity': 1})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16025

    for bad_quantity in (0, -1, 'five', 1.5, True, None):
        response = client.post(f'/dn/{dn.id}/details/', headers=headers,
                               json={'goods_id': other_goods.id, 'quantity': bad_quantity})
        assert response.status_code == 400
        assert response.get_json()['code'] == 16033


def test_create_dn_detail_rejects_quantity_above_available(client, access_token):
    """B-15：新增明细不得超过（排除本单预占后的）可用量。"""
    with client.application.app_context():
        dn = DN.query.first()
        goods = Goods.query.filter_by(code='G002').first()
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods.id, dn.warehouse_id)
        inventory.onhand_stock = 10
        inventory.locked_stock = 0
        inventory.dn_stock = 0
        dn_id, goods_id = dn.id, goods.id
        db.session.commit()

    response = client.post(f'/dn/{dn_id}/details/', headers={'Authorization': f'Bearer {access_token}'},
                           json={'goods_id': goods_id, 'quantity': 11})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16032


def test_get_dn_details_list(client, access_token):
    """
    测试获取 DN 下所有 DNDetail (GET /dn/<dn_id>/details/)
    """
    with client.application.app_context():
        dn = DN.query.first()

    response = client.get(
        f'/dn/{dn.id}/details/',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1  # 应该至少有一条明细


def test_update_dn_detail(client, access_token):
    """
    测试更新 DNDetail (PUT /dn/<dn_id>/details/<detail_id>)
    """
    with client.application.app_context():
        dn = DN.query.first()
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert detail is not None
        # 种子库存不够 99，先把在库量抬高；本用例只验证字段更新
        inventory = get_inventory_by_goods_id_and_warehouse_id(detail.goods_id, dn.warehouse_id)
        inventory.onhand_stock = 1000
        dn_id, detail_id = dn.id, detail.id
        db.session.commit()

    response = client.put(
        f'/dn/{dn_id}/details/{detail_id}',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'quantity': 99,
            'picked_quantity': 50,
            'remark': 'Updated detail remark'
        }
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['quantity'] == 99
    assert data['picked_quantity'] == 0  # B-12：picked_quantity 由拣货流程计算，PUT 里的值忽略
    assert data['remark'] == 'Updated detail remark'


def test_update_dn_detail_rejects_quantity_above_available(client, access_token):
    """B-15：改量时可用量 = onhand - locked - 其它单据预占（本单自身预占已加回）。"""
    with client.application.app_context():
        dn = get_dn()
        detail = dn.details[0]
        goods_id = detail.goods_id
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, dn.warehouse_id)
        own_reserved = sum(d.quantity for d in dn.details if d.goods_id == goods_id)
        other_lines = own_reserved - detail.quantity
        inventory.locked_stock = 0
        # 其它 DN 预占 = dn_stock - 本单预占；让本行最多只能改到 other_available
        inventory.onhand_stock = inventory.dn_stock - own_reserved + other_lines + 15
        db.session.commit()
        detail_id = detail.id
        dn_id = dn.id

    headers = {'Authorization': f'Bearer {access_token}'}
    # 恰好等于可用量：允许
    response = client.put(f'/dn/{dn_id}/details/{detail_id}', headers=headers, json={'quantity': 15})
    assert response.status_code == 200, response.get_json()
    # 超出 1：拒绝
    response = client.put(f'/dn/{dn_id}/details/{detail_id}', headers=headers, json={'quantity': 16})
    assert response.status_code == 400
    assert response.get_json()['code'] == 16032


def test_delete_dn_detail(client, access_token):
    """
    测试删除 DNDetail (DELETE /dn/<dn_id>/details/<detail_id>)
    """
    with client.application.app_context():
        dn = get_dn_by_id(2)
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        detail_id = detail.id

    response = client.delete(
        f'/dn/{dn.id}/details/{detail_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "DNDetail deleted successfully"

    # 再次查询数据库，确认已删除
    with client.application.app_context():
        deleted = db.session.get(DNDetail, detail_id)
        assert deleted is None


def test_delete_dn(client, access_token):
    """
    测试删除 DN (DELETE /dn/<dn_id>)
    """
    with client.application.app_context():
        dn = get_dn_by_id(2)
        dn_id = dn.id

    response = client.delete(
        f'/dn/{dn_id}',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data['message'] == "DN deleted successfully"

    with client.application.app_context():
        deleted_dn = db.session.get(DN, dn_id)
        assert deleted_dn is None


# -------------------- 以下为直接测试服务层 (services.py) 的用例 --------------------

def test_dn_service__update_dn_status(client):
    """
    测试通过 Service 更新 DN 状态
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

        updated_dn = DNService._update_dn_status(dn, "picked")
        assert updated_dn.status == "picked"

def test_sync_dn_details(client):
    """测试DN明细同步全流程"""
    with client.application.app_context():
        dn = get_dn()
        user = get_operator_user()
        existing_detail = dn.details[0]
        new_goods_id = 2
        # 本用例只验证同步机制，把在库量抬高避免撞可用量校验（可用量校验见 test_sync_dn_details_rejects_over_available）
        inventory = get_inventory_by_goods_id_and_warehouse_id(existing_detail.goods_id, dn.warehouse_id)
        inventory.onhand_stock = 1000
        db.session.commit()

        # 测试混合操作（更新+新增+删除）
        new_data = [
            {  # 更新记录
                "id": existing_detail.id,
                "goods_id": existing_detail.goods_id,
                "quantity": 200,
                "remark": "Updated"
            },
            {  # 新增记录
                "goods_id": new_goods_id,
                "quantity": 30,
                "picked_quantity": 5
            }
        ]
        
        # 执行同步
        DNService.sync_dn_details(dn, new_data, user.id)

        # 验证总数变化
        assert len(dn.details) == 2
        # 验证更新记录
        updated_detail = DNService.get_dn_detail(dn.id, existing_detail.id)
        assert updated_detail.quantity == 200
        assert updated_detail.remark == "Updated"
        # 验证新增记录
        new_detail = DNDetail.query.filter_by(goods_id=new_goods_id).first()
        assert new_detail is not None
        assert new_detail.quantity == 30

        # 测试删除操作
        DNService.sync_dn_details(dn.id, [
            {"goods_id": new_goods_id, "quantity": 50}  # 只保留新记录
        ],created_by=user.id)
        assert len(dn.details) == 1
        assert db.session.get(DNDetail,existing_detail.id) is None

        # 测试无效状态操作
        DNService._update_dn_status(dn, 'picked')
        with pytest.raises(BadRequestException) as excinfo:
            DNService.sync_dn_details(dn.id, [],created_by=user.id)

def test_sync_dn_details_rejects_over_available(client):
    """B-15：sync 时每个商品变更后的计划量都要过「排除本单预占后的可用量」校验。"""
    with client.application.app_context():
        dn = get_dn()
        user = get_operator_user()
        goods_id = dn.details[0].goods_id
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, dn.warehouse_id)
        own_reserved = sum(d.quantity for d in dn.details if d.goods_id == goods_id)
        inventory.locked_stock = 0
        inventory.onhand_stock = inventory.dn_stock - own_reserved + 50   # 本单可用 50
        db.session.commit()

        # 50 恰好可用
        DNService.sync_dn_details(dn.id, [{'goods_id': goods_id, 'quantity': 50}], created_by=user.id)
        assert sum(d.quantity for d in get_dn_by_id(dn.id).details) == 50

        with pytest.raises(BadRequestException) as excinfo:
            DNService.sync_dn_details(dn.id, [{'goods_id': goods_id, 'quantity': 51}], created_by=user.id)
        assert excinfo.value.biz_code == 16032

        with pytest.raises(BadRequestException) as excinfo:
            DNService.sync_dn_details(dn.id, [{'goods_id': goods_id, 'quantity': 0}], created_by=user.id)
        assert excinfo.value.biz_code == 16033


def test_goods_code_never_falls_back_to_company_1(client):
    """B-17：按 goods_code 解析商品时用单据仓库所属公司，不能回落到 company 1。"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        warehouse_b = Warehouse(name='WH-B', address='b', phone='1', zip_code='1',
                                company_id=company_b.id, created_by=admin.id)
        recipient_b = Recipient(name='R-B', address='b', zip_code='1', phone='1', email='rb@b.com',
                                contact='c', country='us', created_by=admin.id, company_id=company_b.id)
        db.session.add_all([warehouse_b, recipient_b])
        db.session.commit()

        # G001 属于 Company A：在 Company B 的仓库上按 code 找不到 → 16030，而不是解析成 A 的商品
        with pytest.raises(BadRequestException) as excinfo:
            DNService.create_dn({
                'recipient_id': recipient_b.id,
                'warehouse_id': warehouse_b.id,
                'shipping_address': 'x',
                'expected_shipping_date': '2026-01-01',
                'details': [{'goods_code': 'G001', 'quantity': 1}],
            }, created_by_id=admin.id)
        assert excinfo.value.biz_code == 16030


def test_create_dn_rejects_company_id_mismatching_warehouse(client):
    """B-17：请求体显式给出的 company_id 与仓库所属公司不一致 → 403。"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        with pytest.raises(ForbiddenException) as excinfo:
            DNService.create_dn({
                'recipient_id': get_recipient().id,
                'warehouse_id': get_warehouse().id,
                'company_id': company_b.id,
                'shipping_address': 'x',
                'expected_shipping_date': '2026-01-01',
                'details': [{'goods_id': get_goods().id, 'quantity': 1}],
            }, created_by_id=admin.id)
        assert excinfo.value.biz_code == 12001


def test_create_dn_rejects_master_data_of_other_company(client):
    """B-04：收货人 / 承运商 / 商品必须与仓库同公司，否则 403。"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        recipient_b = Recipient(name='R-B2', address='b', zip_code='1', phone='1', email='rb2@b.com',
                                contact='c', country='us', created_by=admin.id, company_id=company_b.id)
        db.session.add(recipient_b)
        db.session.commit()
        warehouse_a = get_warehouse()
        goods = get_goods()

        with pytest.raises(ForbiddenException) as excinfo:
            DNService.create_dn({
                'recipient_id': recipient_b.id,
                'warehouse_id': warehouse_a.id,
                'shipping_address': 'x',
                'expected_shipping_date': '2026-01-01',
                'details': [{'goods_id': goods.id, 'quantity': 1}],
            }, created_by_id=admin.id)
        assert excinfo.value.biz_code == 12001


def test_cancel_dn_pending_releases_reservation(client, access_token):
    """B-33：pending 取消等同 close，dn_stock 预占释放。"""
    with client.application.app_context():
        dn = get_dn()
        dn_id = dn.id
        goods_id = dn.details[0].goods_id
        own_reserved = sum(d.quantity for d in dn.details if d.goods_id == goods_id)
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, dn.warehouse_id)
        reserved_before = inventory.dn_stock
        warehouse_id = dn.warehouse_id

    response = client.put(f'/dn/{dn_id}/cancel/', headers={'Authorization': f'Bearer {access_token}'})
    assert response.status_code == 200
    assert response.get_json()['status'] == 'closed'

    with client.application.app_context():
        assert get_dn_by_id(dn_id).closed_at is not None
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_id)
        assert inventory.dn_stock == reserved_before - own_reserved


def test_cancel_dn_in_progress_without_picking_batches(client, access_token):
    """B-33：in_progress 且拣货任务无批次 → 允许取消，拣货任务停用、预占释放。"""
    with client.application.app_context():
        dn = get_dn()
        dn_id = dn.id
        goods_id = dn.details[0].goods_id
        own_reserved = sum(d.quantity for d in dn.details if d.goods_id == goods_id)
        warehouse_id = dn.warehouse_id
        DNService.progress_dn(dn.id)           # 创建 pending 拣货任务（无批次）
        task = PickingTask.query.filter_by(dn_id=dn_id, is_active=True).one()
        task_id = task.id
        reserved_before = get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_id).dn_stock

    response = client.put(f'/dn/{dn_id}/cancel/', headers={'Authorization': f'Bearer {access_token}'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'closed'

    with client.application.app_context():
        assert get_picking_task_by_id(task_id).is_active is False
        inventory = get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_id)
        assert inventory.dn_stock == reserved_before - own_reserved


def test_cancel_dn_conflicts_once_picking_started(client, access_token):
    """B-33：拣货任务已有批次 / 明细，或单据已过拣货阶段 → 409。"""
    headers = {'Authorization': f'Bearer {access_token}'}
    with client.application.app_context():
        dn3 = get_dn_by_id(3)                  # 种子：in_progress，拣货任务已有明细
        assert dn3.status == 'in_progress'
        assert PickingTask.query.filter_by(dn_id=3).first().task_details

    response = client.put('/dn/3/cancel/', headers=headers)
    assert response.status_code == 409
    assert response.get_json()['code'] == 16053

    with client.application.app_context():
        dn = get_dn()
        dn.status = 'picked'
        db.session.commit()
        dn_id = dn.id
    response = client.put(f'/dn/{dn_id}/cancel/', headers=headers)
    assert response.status_code == 409
    assert response.get_json()['code'] == 16052


def test_duplicate_goods_validation(client):
    """测试商品重复校验"""
    with client.application.app_context():
        dn = get_dn()
        user = get_operator_user()
        with pytest.raises(BadRequestException) as e:
            DNService.sync_dn_details(dn, [
                {"goods_id": 1, "quantity": 10},
                {"goods_id": 1, "quantity": 20}
            ],created_by=user.id)
        assert "Duplicate goods_id: 1" in str(e.value)

def test_dn_service_update_dn_detail(client):
    """
    测试通过 Service 更新单个 DNDetail
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert detail is not None

        old_qty = detail.quantity
        old_remark = detail.remark
        inventory = get_inventory_by_goods_id_and_warehouse_id(detail.goods_id, dn.warehouse_id)
        inventory.onhand_stock = 5000
        db.session.commit()

        updated_detail = DNService.update_dn_detail(dn.id, detail.id, {
            "quantity": 999,
            "remark": "Service updated note"
        })
        assert updated_detail.id == detail.id
        assert updated_detail.quantity == 999
        assert updated_detail.remark == "Service updated note"
        assert updated_detail.quantity != old_qty
        assert updated_detail.remark != old_remark

def test_dn_service_pick_dn(client):
    """
    测试通过 Service 将 DN 标记为 'picked'
    """
    with client.application.app_context():
        dn = DN.query.first()
        dn.status = "in_progress"  # 假设当前状态为 in_progress
        db.session.commit()

        updated_dn = DNService.picking_dn(dn.id)
        assert updated_dn.status == "picked"

def test_dn_service_packing_dn(client):
    """
    测试通过 Service 将 DN 标记为 'packed'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 picked 状态，才能 pack
        dn.status = "picked"
        db.session.commit()

        updated_dn = DNService.packing_dn(dn.id)
        assert updated_dn.status == "packed"

def test_dn_service_delivery_dn(client):
    """
    测试通过 Service 将 DN 标记为 'delivered'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 packed 状态，才能 delivery
        dn.status = "packed"
        db.session.commit()

        updated_dn = DNService.delivery_dn(dn.id)
        assert updated_dn.status == "delivered"


def test_dn_service_complete_dn(client):
    """
    测试通过 Service 将 DN 标记为 'completed'
    """
    with client.application.app_context():
        dn = DN.query.first()
        # 先更新到 delivered 状态，才能 complete
        dn.status = "delivered"
        db.session.commit()

        updated_dn = DNService.complete_dn(dn.id)
        assert updated_dn.status == "completed"

def test_close_dn_success_pending(client):
    """
    测试当 DN 状态为 pending 时，调用 close_dn 后状态更新为 closed
    """
    with client.application.app_context():
        dn = get_dn()
        # 确保 DN 状态为 pending
        dn.status = 'pending'
        db.session.commit()

        closed_dn = DNService.close_dn(dn.id)
        assert closed_dn.status == 'closed', "DN 状态应更新为 closed"


# -------------------- 一些负面用例 / 异常场景 --------------------

@pytest.mark.parametrize("status", ['picked', 'packed', 'delivered', 'completed','closed'])
def test_close_dn_failure_not_pending(client, status):
    """
    测试当 DN 状态不为 pending 时，调用 close_dn 应抛出 ValueError 异常
    """
    with client.application.app_context():
        dn = get_dn()
        dn.status = status
        db.session.commit()

        with pytest.raises(BadRequestException) as excinfo:
            DNService.close_dn(dn.id)
        assert 16022 == excinfo.value.biz_code

def test_close_dn_not_found(client):
    """
    测试传入一个不存在的 DN id 时，close_dn 应抛出 NotFound 异常
    """
    invalid_dn_id = 99999  # 假设此 id 不存在
    with client.application.app_context():
        with pytest.raises(NotFoundException) as excinfo:
            DNService.close_dn(invalid_dn_id)

def test_dn_service__update_dn_status_invalid_status(client):
    """
    测试更新 DN 状态时，若传入无效的 status 应抛出 ValueError
    """
    with client.application.app_context():
        dn = DN.query.first()
        assert dn is not None

        with pytest.raises(BadRequestException) as excinfo:
            DNService._update_dn_status(dn, "invalid_status")
        assert "Invalid DN status" in str(excinfo.value)


def test_dn_service_update_dn_detail_invalid_detail(client):
    """
    测试对不存在的 detail_id 更新 DNDetail，应抛出 NotFound
    """
    with client.application.app_context():
        dn = DN.query.first()
        with pytest.raises(NotFoundException) as excinfo:
            DNService.update_dn_detail(dn.id, -999, {"quantity": 999})
        assert "404" in str(excinfo.value) or "Not Found" in str(excinfo.value)


def test_dn_service_update_dn_detail_success(client):
    """
    测试当更新数据均符合约束时，update_dn_detail 可以成功
    """
    with client.application.app_context():
        dn = DN.query.first()
        detail = DNDetail.query.filter_by(dn_id=dn.id).first()
        assert dn is not None
        assert detail is not None

        # 设置初始值
        detail.quantity = 10
        detail.picked_quantity = 5
        db.session.commit()

        updated_detail = DNService.update_dn_detail(
            dn.id,
            detail.id,
            {
                "quantity": 20,
                "picked_quantity": 10,
                "remark": "All constraints satisfied"
            }
        )
        assert updated_detail.quantity == 20
        assert updated_detail.picked_quantity == 5   # B-12：picked_quantity 不接受客户端赋值
        assert updated_detail.remark == "All constraints satisfied"

