"""
入库线（ASN / Sorting / Putaway / Removal / Transfer / Inventory）的租户隔离与输入校验负向用例。

覆盖审计项：B-04 / B-12 / B-13 / B-17 / B-18 / B-24 / B-33 / B-40 / B-43。
"""
import pytest
from flask_jwt_extended import create_access_token

from warehouse.asn.services import ASNService
from warehouse.sorting.services import SortingTaskService
from .helpers import *


# ---------------------------------------------------------
# 夹具：Company B 的公司管理员 + 该公司的仓库 / 库位 / 商品 / 供应商
# ---------------------------------------------------------

@pytest.fixture
def foreign(client):
    """
    构造"别家公司"的员工：company_admin 角色（持 company_all_access，能过 permission_required），
    但可访问仓库只有 Company B 的仓库，应被归属校验拦下。
    返回各对象的 id 与其 JWT。
    """
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        role = Role.query.filter_by(name='company_admin').first()

        warehouse_b = Warehouse(
            name='Warehouse B', address='B Street', phone='000', zip_code='00000',
            company_id=company_b.id, created_by=admin.id,
        )
        db.session.add(warehouse_b)
        db.session.flush()

        location_b = Location(
            warehouse_id=warehouse_b.id, code='B-LOC001', description='B location',
            location_type='standard', created_by=admin.id,
        )
        # 与 Company A 的 G001 同编码，用来验证 goods_code 只在单据仓库所属公司内解析（B-17）
        goods_b = Goods(
            code='G001', company_id=company_b.id, name='B Goods', unit='pcs',
            is_active=True, created_by=admin.id,
        )
        supplier_b = Supplier(
            name='Supplier B', address='B Street', phone='000', email='supplierB@example.com',
            contact='B', created_by=admin.id, company_id=company_b.id,
        )
        staff_b = Staff(
            user_name='foreign_admin', email='foreign_admin@test.com', is_active=True,
            company_id=company_b.id, created_by=admin.id,
        )
        staff_b.set_password('password')
        staff_b.roles.append(role)
        staff_b.warehouses.append(warehouse_b)
        db.session.add_all([location_b, goods_b, supplier_b, staff_b])
        db.session.commit()

        return {
            'token': create_access_token(identity=staff_b),
            'company_id': company_b.id,
            'warehouse_id': warehouse_b.id,
            'location_id': location_b.id,
            'goods_id': goods_b.id,
            'supplier_id': supplier_b.id,
        }


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _assert_forbidden(response):
    assert response.status_code == 403, response.data
    assert response.get_json()['code'] == 12001


# ---------------------------------------------------------
# B-04：ASN 单据级归属校验
# ---------------------------------------------------------

def test_foreign_staff_cannot_update_or_delete_asn(client, foreign):
    with client.application.app_context():
        asn_id = get_asn().id

    _assert_forbidden(client.put(f'/asn/{asn_id}', headers=_auth(foreign['token']), json={'remark': 'x'}))
    _assert_forbidden(client.delete(f'/asn/{asn_id}', headers=_auth(foreign['token'])))
    _assert_forbidden(client.get(f'/asn/{asn_id}', headers=_auth(foreign['token'])))
    _assert_forbidden(client.put(f'/asn/{asn_id}/receive/', headers=_auth(foreign['token'])))
    _assert_forbidden(client.put(f'/asn/{asn_id}/cancel/', headers=_auth(foreign['token'])))

    with client.application.app_context():
        assert get_asn_by_id(asn_id) is not None


def test_foreign_staff_cannot_touch_asn_details(client, foreign):
    with client.application.app_context():
        asn = get_asn()
        asn_id = asn.id
        detail_id = get_asn_detail_by_asn_id(asn_id).id
        goods_id = get_goods().id

    headers = _auth(foreign['token'])
    _assert_forbidden(client.get(f'/asn/{asn_id}/details/', headers=headers))
    _assert_forbidden(client.post(f'/asn/{asn_id}/details/', headers=headers, json={'goods_id': goods_id, 'quantity': 1}))
    _assert_forbidden(client.get(f'/asn/{asn_id}/details/{detail_id}', headers=headers))
    _assert_forbidden(client.put(f'/asn/{asn_id}/details/{detail_id}', headers=headers, json={'quantity': 1}))
    _assert_forbidden(client.delete(f'/asn/{asn_id}/details/{detail_id}', headers=headers))

    with client.application.app_context():
        assert get_asn_detail_by_id(detail_id) is not None


def test_foreign_staff_cannot_create_asn_in_other_warehouse(client, foreign):
    """POST /asn/ 的 body.warehouse_id 必须在调用方可访问范围内"""
    with client.application.app_context():
        warehouse_a = get_warehouse().id
        supplier_a = get_supplier().id

    response = client.post('/asn/', headers=_auth(foreign['token']), json={
        'warehouse_id': warehouse_a, 'supplier_id': supplier_a, 'details': [],
    })
    _assert_forbidden(response)


def test_create_asn_rejects_partner_from_other_company(client, access_company_admin_token, foreign):
    """supplier / carrier 必须与单据仓库同公司"""
    with client.application.app_context():
        warehouse_a = get_warehouse().id

    response = client.post('/asn/', headers=_auth(access_company_admin_token), json={
        'warehouse_id': warehouse_a, 'supplier_id': foreign['supplier_id'], 'details': [],
    })
    _assert_forbidden(response)


def test_asn_detail_rejects_goods_from_other_company(client, access_token, foreign):
    """明细里的商品必须属于单据仓库所在公司（平台管理员也不例外）"""
    with client.application.app_context():
        asn = get_asn()
        asn_id = asn.id
        warehouse_a = asn.warehouse_id
        supplier_a = asn.supplier_id

    _assert_forbidden(client.post(f'/asn/{asn_id}/details/', headers=_auth(access_token),
                                  json={'goods_id': foreign['goods_id'], 'quantity': 3}))
    _assert_forbidden(client.post('/asn/', headers=_auth(access_token), json={
        'warehouse_id': warehouse_a, 'supplier_id': supplier_a,
        'details': [{'goods_id': foreign['goods_id'], 'quantity': 3}],
    }))


def test_foreign_staff_cannot_create_asn_without_warehouse(client, foreign):
    response = client.post('/asn/', headers=_auth(foreign['token']), json={'supplier_id': 1})
    assert response.status_code == 400
    assert response.get_json()['code'] == 40000


# ---------------------------------------------------------
# B-12：状态 / 过程量不接受客户端输入
# ---------------------------------------------------------

def test_create_asn_ignores_status_and_process_quantities(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        supplier = get_supplier()
        goods = get_goods()

    response = client.post('/asn/', headers=_auth(access_token), json={
        'warehouse_id': warehouse.id, 'supplier_id': supplier.id,
        'status': 'completed', 'is_active': False, 'created_by': 999, 'api_key_id': 1,
        'details': [{'goods_id': goods.id, 'quantity': 10,
                     'actual_quantity': 10, 'sorted_quantity': 10, 'damage_quantity': 10}],
    })
    assert response.status_code == 201, response.data
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['is_active'] is True
    assert data['details'][0]['actual_quantity'] == 0
    assert data['details'][0]['sorted_quantity'] == 0
    assert data['details'][0]['damage_quantity'] == 0

    with client.application.app_context():
        asn = get_asn_by_id(data['id'])
        assert asn.created_by == get_admin_user().id
        assert asn.api_key_id is None


def test_update_asn_ignores_status(client, access_token):
    with client.application.app_context():
        asn_id = get_asn().id

    response = client.put(f'/asn/{asn_id}', headers=_auth(access_token),
                          json={'status': 'completed', 'is_active': False, 'remark': 'kept'})
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['is_active'] is True
    assert data['remark'] == 'kept'


def test_create_sorting_task_ignores_status(client, access_token):
    with client.application.app_context():
        asn_id = get_asn().id

    response = client.post('/sorting/', headers=_auth(access_token),
                           json={'asn_id': asn_id, 'status': 'completed', 'is_active': False})
    assert response.status_code == 201, response.data
    data = response.get_json()
    assert data['status'] == 'pending'
    assert data['is_active'] is True


# ---------------------------------------------------------
# B-17：goods_code 只在单据仓库所属公司内解析，不再回落到公司 1
# ---------------------------------------------------------

def test_goods_code_resolves_within_warehouse_company(client, access_token, foreign):
    """同编码 G001 在 A / B 两家公司各有一件商品；平台管理员往仓库 B 建单必须解析到 B 的商品"""
    with client.application.app_context():
        goods_a = get_goods()
        assert goods_a.code == 'G001'
        goods_a_id = goods_a.id

    response = client.post('/asn/', headers=_auth(access_token), json={
        'warehouse_id': foreign['warehouse_id'], 'supplier_id': foreign['supplier_id'],
        'details': [{'goods_code': 'G001', 'quantity': 2}],
    })
    assert response.status_code == 201, response.data
    assert response.get_json()['details'][0]['goods_id'] == foreign['goods_id']

    # body 显式给出的 company_id 与仓库所属公司不一致 → 403
    with client.application.app_context():
        company_a_id = get_company().id
    assert company_a_id != foreign['company_id']
    response = client.post('/asn/', headers=_auth(access_token), json={
        'warehouse_id': foreign['warehouse_id'], 'supplier_id': foreign['supplier_id'],
        'company_id': company_a_id,
        'details': [{'goods_code': 'G001', 'quantity': 2}],
    })
    _assert_forbidden(response)

    # 公司 A 的管理员用 goods_code 建单则解析到 A 的商品
    with client.application.app_context():
        warehouse_a = get_warehouse().id
        supplier_a = get_supplier().id
        token_a = create_access_token(identity=get_company_admin_user())
    response = client.post('/asn/', headers=_auth(token_a), json={
        'warehouse_id': warehouse_a, 'supplier_id': supplier_a,
        'details': [{'goods_code': 'G001', 'quantity': 2}],
    })
    assert response.status_code == 201, response.data
    assert response.get_json()['details'][0]['goods_id'] == goods_a_id


# ---------------------------------------------------------
# B-04 / B-13：Sorting 归属与批次超量
# ---------------------------------------------------------

def test_foreign_staff_cannot_touch_sorting_task(client, foreign):
    with client.application.app_context():
        task = get_sorting_task()
        task_id = task.id
        asn_id = task.asn_id

    headers = _auth(foreign['token'])
    _assert_forbidden(client.get(f'/sorting/{task_id}', headers=headers))
    _assert_forbidden(client.put(f'/sorting/{task_id}', headers=headers, json={}))
    _assert_forbidden(client.delete(f'/sorting/{task_id}', headers=headers))
    _assert_forbidden(client.put(f'/sorting/{task_id}/process/', headers=headers))
    _assert_forbidden(client.get(f'/sorting/{task_id}/batches/', headers=headers))
    _assert_forbidden(client.post(f'/sorting/{task_id}/batches/', headers=headers, json={}))
    _assert_forbidden(client.get(f'/sorting/{task_id}/details/', headers=headers))
    # body 里引用别家仓库的 ASN 也不行
    _assert_forbidden(client.post('/sorting/', headers=headers, json={'asn_id': asn_id}))

    with client.application.app_context():
        assert get_sorting_task_by_id(task_id) is not None


def _start_sorting_task(client):
    """把夹具里的分拣任务推进到 in_progress，返回 (task_id, asn 明细 goods_id → 计划量)"""
    with client.application.app_context():
        task = get_sorting_task()
        SortingTaskService.process_task(task.id, get_operator_user().id)
        planned = {d.goods_id: d.quantity for d in task.asn.details}
        return task.id, planned


def test_sorting_batch_rejects_goods_outside_asn(client, access_token, foreign):
    task_id, _ = _start_sorting_task(client)
    response = client.post(f'/sorting/{task_id}/batches/', headers=_auth(access_token), json={
        'details': [{'goods_id': foreign['goods_id'], 'sorted_quantity': 1, 'damage_quantity': 0}],
    })
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16057


def test_sorting_batch_rejects_over_quantity_and_duplicate_submit(client, access_token):
    task_id, planned = _start_sorting_task(client)
    goods_id, quantity = next(iter(planned.items()))
    headers = _auth(access_token)

    # 单批直接超量
    response = client.post(f'/sorting/{task_id}/batches/', headers=headers, json={
        'details': [{'goods_id': goods_id, 'sorted_quantity': quantity, 'damage_quantity': 1}],
    })
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16062

    # 第一次提交超过一半 → 通过；同样的批次再提交一次（重复提交）→ 累计超量被拒
    payload = {'details': [{'goods_id': goods_id, 'sorted_quantity': quantity // 2 + 1, 'damage_quantity': 0}]}
    assert client.post(f'/sorting/{task_id}/batches/', headers=headers, json=payload).status_code == 201
    response = client.post(f'/sorting/{task_id}/batches/', headers=headers, json=payload)
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16062

    with client.application.app_context():
        task = get_sorting_task_by_id(task_id)
        total = sum(d.sorted_quantity + d.damage_quantity for d in task.task_details if d.goods_id == goods_id)
        assert total == quantity // 2 + 1


def test_sorting_complete_revalidates_quantity(client, access_token):
    task_id, planned = _start_sorting_task(client)
    goods_id, quantity = next(iter(planned.items()))

    with client.application.app_context():
        operator_id = get_operator_user().id
        batch = SortingTaskService.create_batch(task_id, {
            'details': [{'goods_id': goods_id, 'sorted_quantity': 1, 'damage_quantity': 0}],
        }, operator_id)
        # 绕过接口把明细改成超量，complete 时必须再验一次
        batch.details[0].sorted_quantity = quantity + 1
        db.session.commit()

    response = client.put(f'/sorting/{task_id}/complete/', headers=_auth(access_token))
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16062

    with client.application.app_context():
        assert get_sorting_task_by_id(task_id).status == 'in_progress'


def test_sorting_detail_requires_batch_of_same_task(client, access_token):
    task_id, planned = _start_sorting_task(client)
    goods_id = next(iter(planned))

    with client.application.app_context():
        user = get_operator_user()
        other_task = SortingTaskService.create_task({'asn_id': get_asn().id}, user.id)
        SortingTaskService.process_task(other_task.id, user.id)
        other_batch_id = SortingTaskService.create_batch(other_task.id, {}, user.id).id

    response = client.post(f'/sorting/{task_id}/details/', headers=_auth(access_token),
                           json={'batch_id': other_batch_id, 'goods_id': goods_id, 'sorted_quantity': 1})
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16058


# ---------------------------------------------------------
# B-18：数量必须是正整数 / bulk 必须是非空列表
# ---------------------------------------------------------

@pytest.mark.parametrize('quantity', [-5, 0, 2.5, 'ten', True, None])
def test_putaway_rejects_invalid_quantity(client, access_token, quantity):
    with client.application.app_context():
        goods_id = get_goods().id
        location_id = get_location().id

    response = client.post('/putaway/', headers=_auth(access_token),
                           json={'goods_id': goods_id, 'location_id': location_id, 'quantity': quantity})
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] in (16033, 40000)


@pytest.mark.parametrize('quantity', [-1, 1.5, 'abc'])
def test_removal_rejects_invalid_quantity(client, access_token, quantity):
    with client.application.app_context():
        goods_id = get_goods().id
        location_id = get_location().id

    response = client.post('/removal/', headers=_auth(access_token),
                           json={'goods_id': goods_id, 'location_id': location_id, 'quantity': quantity, 'reason': 'damage'})
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16033


def test_removal_missing_reason_returns_400(client, access_token):
    with client.application.app_context():
        goods_id = get_goods().id
        location_id = get_location().id

    response = client.post('/removal/', headers=_auth(access_token),
                           json={'goods_id': goods_id, 'location_id': location_id, 'quantity': 1})
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 40000


@pytest.mark.parametrize('quantity', [-2, 0.5, 'x'])
def test_transfer_rejects_invalid_quantity(client, access_token, quantity):
    with client.application.app_context():
        goods_id = get_goods().id

    response = client.post('/transfer/', headers=_auth(access_token), json={
        'goods_id': goods_id, 'from_location_id': 1, 'to_location_id': 2, 'quantity': quantity,
    })
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16033


@pytest.mark.parametrize('path', ['/putaway/bulk', '/removal/bulk', '/transfer/bulk'])
@pytest.mark.parametrize('payload', [{}, [], {'goods_id': 1}, [1, 2]])
def test_bulk_rejects_non_list_payload(client, access_token, path, payload):
    response = client.post(path, headers=_auth(access_token), json=payload)
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16015


def test_bulk_rejects_too_many_items(client, access_token):
    with client.application.app_context():
        goods_id = get_goods().id
        location_id = get_location().id
    payload = [{'goods_id': goods_id, 'location_id': location_id, 'quantity': 1}] * 501
    response = client.post('/putaway/bulk', headers=_auth(access_token), json=payload)
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16015


def test_sorting_batch_rejects_negative_quantity(client, access_token):
    task_id, planned = _start_sorting_task(client)
    goods_id = next(iter(planned))
    response = client.post(f'/sorting/{task_id}/batches/', headers=_auth(access_token), json={
        'details': [{'goods_id': goods_id, 'sorted_quantity': -1, 'damage_quantity': 0}],
    })
    assert response.status_code == 400, response.data
    assert response.get_json()['code'] == 16033


def test_asn_detail_rejects_non_positive_quantity(client, access_token):
    with client.application.app_context():
        asn_id = get_asn().id
        goods_id = get_goods().id
    for quantity in (0, -3, 1.5, 'four'):
        response = client.post(f'/asn/{asn_id}/details/', headers=_auth(access_token),
                               json={'goods_id': goods_id, 'quantity': quantity})
        assert response.status_code == 400, response.data
        assert response.get_json()['code'] == 16033


# ---------------------------------------------------------
# B-33：ASN 取消释放库存
# ---------------------------------------------------------

def _inventory_snapshot(goods_id, warehouse_id):
    inv = get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_id)
    return inv.asn_stock, inv.received_stock


def test_cancel_pending_asn_releases_asn_stock(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        asn_id, warehouse_id = asn.id, asn.warehouse_id
        goods_id = asn.details[0].goods_id
        planned = asn.details[0].quantity
        asn_stock_before, _ = _inventory_snapshot(goods_id, warehouse_id)
        assert asn_stock_before >= planned

    response = client.put(f'/asn/{asn_id}/cancel/', headers=_auth(access_token))
    assert response.status_code == 200, response.data
    assert response.get_json()['status'] == 'closed'

    with client.application.app_context():
        asn_stock_after, _ = _inventory_snapshot(goods_id, warehouse_id)
        assert asn_stock_after == asn_stock_before - planned


def test_cancel_received_asn_rolls_back_received_stock(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        asn_id, warehouse_id = asn.id, asn.warehouse_id
        goods_id = asn.details[0].goods_id
        planned = asn.details[0].quantity
        asn_stock_before, received_before = _inventory_snapshot(goods_id, warehouse_id)

        ASNService.receive_asn(asn_id)
        asn_stock_received, received_after = _inventory_snapshot(goods_id, warehouse_id)
        assert received_after == received_before + planned
        assert asn_stock_received == asn_stock_before - planned

    response = client.put(f'/asn/{asn_id}/cancel/', headers=_auth(access_token))
    assert response.status_code == 200, response.data
    assert response.get_json()['status'] == 'closed'

    with client.application.app_context():
        asn_stock_final, received_final = _inventory_snapshot(goods_id, warehouse_id)
        assert received_final == received_before
        assert asn_stock_final == asn_stock_received  # 已关闭的单据不再计入 asn_stock
        assert get_asn_by_id(asn_id).status == 'closed'
        tasks = SortingTask.query.filter_by(asn_id=asn_id).all()
        assert tasks and all(not t.is_active for t in tasks)


def test_cancel_received_asn_with_sorting_progress_conflicts(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        asn_id = asn.id
        goods_id = asn.details[0].goods_id
        ASNService.receive_asn(asn_id)
        user = get_operator_user()
        task = SortingTask.query.filter_by(asn_id=asn_id, is_active=True).first()
        SortingTaskService.process_task(task.id, user.id)
        SortingTaskService.create_batch(task.id, {
            'details': [{'goods_id': goods_id, 'sorted_quantity': 1, 'damage_quantity': 0}],
        }, user.id)

    response = client.put(f'/asn/{asn_id}/cancel/', headers=_auth(access_token))
    assert response.status_code == 409, response.data
    assert response.get_json()['code'] == 16060

    with client.application.app_context():
        assert get_asn_by_id(asn_id).status == 'received'


@pytest.mark.parametrize('status', ['completed', 'closed'])
def test_cancel_finished_asn_conflicts(client, access_token, status):
    with client.application.app_context():
        asn = get_asn()
        asn_id = asn.id
        asn.status = status
        db.session.commit()

    response = client.put(f'/asn/{asn_id}/cancel/', headers=_auth(access_token))
    assert response.status_code == 409, response.data
    assert response.get_json()['code'] == 16059


# ---------------------------------------------------------
# B-43 / B-24：只读接口的仓库归属
# ---------------------------------------------------------

def test_foreign_staff_cannot_read_records_of_other_warehouse(client, foreign):
    with client.application.app_context():
        putaway_id = get_putaway_record().id
        removal_id = get_removal_record().id
        transfer_id = get_transfer_record().id
        inventory = get_inventory()
        goods_id, warehouse_id = inventory.goods_id, inventory.warehouse_id

    headers = _auth(foreign['token'])
    _assert_forbidden(client.get(f'/putaway/{putaway_id}', headers=headers))
    _assert_forbidden(client.get(f'/removal/{removal_id}', headers=headers))
    _assert_forbidden(client.get(f'/transfer/{transfer_id}', headers=headers))
    _assert_forbidden(client.get(f'/inventory/goods/{goods_id}/warehouse/{warehouse_id}', headers=headers))


def test_own_company_admin_can_read_records(client, access_company_admin_token):
    with client.application.app_context():
        putaway_id = get_putaway_record().id
        removal_id = get_removal_record().id
        transfer_id = get_transfer_record().id

    headers = _auth(access_company_admin_token)
    assert client.get(f'/putaway/{putaway_id}', headers=headers).status_code == 200
    assert client.get(f'/removal/{removal_id}', headers=headers).status_code == 200
    assert client.get(f'/transfer/{transfer_id}', headers=headers).status_code == 200


# ---------------------------------------------------------
# B-19：移库必须在同一仓库内
# ---------------------------------------------------------

def test_transfer_across_warehouses_is_rejected(client, access_token, foreign):
    with client.application.app_context():
        goods_id = get_goods().id
        from_location_id = get_location().id

    response = client.post('/transfer/', headers=_auth(access_token), json={
        'goods_id': goods_id, 'from_location_id': from_location_id,
        'to_location_id': foreign['location_id'], 'quantity': 1,
    })
    _assert_forbidden(response)
