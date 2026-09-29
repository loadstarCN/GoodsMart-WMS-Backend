"""
出库线（dn / picking / packing / delivery）跨租户隔离用例（B-04）。

种子数据里 Company B 没有员工和仓库；这里在 app_context 内为其造一个仓库和员工，
用其 JWT 去碰 Company A 的单据，全部应被归属校验拦成 403。
两种员工画像：
- company_admin 角色：不带 X-WAREHOUSE-ID，可访问范围 = 本公司全部仓库
- 普通操作员：角色显式持有 *_edit / *_delete 权限（能过 permission_required），
  必须带 X-WAREHOUSE-ID（自家仓库），被 check_warehouse_access 拦下
"""
from .helpers import *
from warehouse.picking.services import PickingTaskService

OUTBOUND_PERMISSIONS = (
    'dn_read', 'dn_edit', 'dn_delete',
    'picking_read', 'picking_edit', 'picking_delete',
    'packing_read', 'packing_edit', 'packing_delete',
    'delivery_read', 'delivery_edit', 'delivery_delete',
)


def _make_company_b(client, *, company_admin: bool):
    """返回 (headers, warehouse_b_id)。company_admin=False 时创建带显式出库权限的普通员工并带仓库头。"""
    with client.application.app_context():
        admin = get_admin_user()
        company_b = Company.query.filter_by(name='Company B').first()
        warehouse_b = Warehouse.query.filter_by(company_id=company_b.id).first()
        if warehouse_b is None:
            warehouse_b = Warehouse(name='Warehouse B', address='B', phone='1', zip_code='1',
                                    company_id=company_b.id, created_by=admin.id)
            db.session.add(warehouse_b)
            db.session.flush()

        if company_admin:
            role = Role.query.filter_by(name='company_admin').first()
            user_name = 'company_b_admin'
        else:
            role = Role(name='outbound_operator_b', description='Company B outbound operator', is_active=True)
            db.session.add(role)
            for name in OUTBOUND_PERMISSIONS:
                perm = Permission.query.filter_by(name=name).first()
                if perm is None:
                    perm = Permission(name=name, description=name)
                    db.session.add(perm)
                role.permissions.append(perm)
            user_name = 'company_b_operator'

        staff = Staff(user_name=user_name, email=f'{user_name}@b.com', is_active=True,
                      company_id=company_b.id, created_by=admin.id)
        staff.set_password('password')
        staff.roles.append(role)
        staff.warehouses.append(warehouse_b)
        db.session.add(staff)
        db.session.commit()

        token = create_access_token(identity=staff)
        warehouse_b_id = warehouse_b.id

    headers = {'Authorization': f'Bearer {token}'}
    if not company_admin:
        headers['X-WAREHOUSE-ID'] = str(warehouse_b_id)
    return headers, warehouse_b_id


def _company_a_fixtures(client):
    """Company A 的种子单据 id 集合（拣货任务置为 in_progress 并建一个批次，便于测批次端点）"""
    with client.application.app_context():
        operator = get_operator_user()
        dn = get_dn()                                       # dn1: pending
        dn_detail_id = dn.details[0].id
        picking = get_picking_task()                        # dn3
        PickingTaskService.process_task(picking.id, operator.id)
        batch = PickingTaskService.create_batch(picking.id, {"remark": "seed"}, operator.id)
        picking_detail_id = picking.task_details[0].id
        packing = get_packing_task()
        packing_detail_id = packing.task_details[0].id
        delivery = get_delivery_task()
        return {
            'dn_id': dn.id, 'dn_detail_id': dn_detail_id, 'warehouse_a_id': dn.warehouse_id,
            'picking_id': picking.id, 'picking_detail_id': picking_detail_id, 'picking_batch_id': batch.id,
            'packing_id': packing.id, 'packing_detail_id': packing_detail_id,
            'delivery_id': delivery.id,
            'goods_id': dn.details[0].goods_id, 'location_id': get_location().id,
            'recipient_id': dn.recipient_id,
        }


def _assert_all_forbidden(client, headers, ids):
    """对 Company A 的出库线单据做全部写 / 读操作，逐条断言 403（或 POST 的 403）"""
    dn, did = ids['dn_id'], ids['dn_detail_id']
    pk, pkd, pkb = ids['picking_id'], ids['picking_detail_id'], ids['picking_batch_id']
    pa, pad = ids['packing_id'], ids['packing_detail_id']
    dl = ids['delivery_id']
    calls = [
        # DN 本体 / 明细 / 流程
        ('GET', f'/dn/{dn}', None),
        ('PUT', f'/dn/{dn}', {'remark': 'hacked'}),
        ('DELETE', f'/dn/{dn}', None),
        ('GET', f'/dn/{dn}/details/', None),
        ('POST', f'/dn/{dn}/details/', {'goods_id': ids['goods_id'], 'quantity': 1}),
        ('GET', f'/dn/{dn}/details/{did}', None),
        ('PUT', f'/dn/{dn}/details/{did}', {'quantity': 1}),
        ('DELETE', f'/dn/{dn}/details/{did}', None),
        ('PUT', f'/dn/{dn}/progress/', None),
        ('PUT', f'/dn/{dn}/close/', None),
        ('PUT', f'/dn/{dn}/cancel/', None),
        # 拣货任务 / 明细 / 批次 / 流程
        ('GET', f'/picking/{pk}', None),
        ('PUT', f'/picking/{pk}', {'remark': 'x'}),
        ('DELETE', f'/picking/{pk}', None),
        ('GET', f'/picking/{pk}/details/', None),
        ('GET', f'/picking/{pk}/details/{pkd}', None),
        ('PUT', f'/picking/{pk}/details/{pkd}', {'picked_quantity': 1}),
        ('DELETE', f'/picking/{pk}/details/{pkd}', None),
        ('GET', f'/picking/{pk}/batches/', None),
        ('POST', f'/picking/{pk}/batches/', {'details': [
            {'goods_id': ids['goods_id'], 'location_id': ids['location_id'], 'picked_quantity': 1}]}),
        ('GET', f'/picking/{pk}/batches/{pkb}', None),
        ('PUT', f'/picking/{pk}/batches/{pkb}', {'remark': 'x'}),
        ('DELETE', f'/picking/{pk}/batches/{pkb}', None),
        ('PUT', f'/picking/{pk}/process/', None),
        ('PUT', f'/picking/{pk}/complete/', None),
        # 打包任务 / 明细 / 批次 / 流程
        ('GET', f'/packing/{pa}', None),
        ('PUT', f'/packing/{pa}', {'remark': 'x'}),
        ('DELETE', f'/packing/{pa}', None),
        ('GET', f'/packing/{pa}/details/', None),
        ('GET', f'/packing/{pa}/details/{pad}', None),
        ('PUT', f'/packing/{pa}/details/{pad}', {'packed_quantity': 1}),
        ('DELETE', f'/packing/{pa}/details/{pad}', None),
        ('GET', f'/packing/{pa}/batches/', None),
        ('POST', f'/packing/{pa}/batches/', {'details': [{'goods_id': ids['goods_id'], 'packed_quantity': 1}]}),
        ('PUT', f'/packing/{pa}/batches/1', {'remark': 'x'}),
        ('DELETE', f'/packing/{pa}/batches/1', None),
        ('PUT', f'/packing/{pa}/process/', None),
        ('PUT', f'/packing/{pa}/complete/', None),
        # 发货任务 / 流程
        ('GET', f'/delivery/{dl}', None),
        ('PUT', f'/delivery/{dl}', {'remark': 'x'}),
        ('DELETE', f'/delivery/{dl}', None),
        ('PUT', f'/delivery/{dl}/process/', None),
        ('PUT', f'/delivery/{dl}/complete/', {'tracking_number': 'x', 'shipping_cost': 1}),
        ('PUT', f'/delivery/{dl}/sign/', {'signed_at': '2026-01-01T00:00:00'}),
        # POST 主单：把 Company A 的 DN 当外键
        ('POST', '/picking/', {'dn_id': dn}),
        ('POST', '/packing/', {'dn_id': dn}),
        ('POST', '/delivery/', {'dn_id': dn, 'recipient_id': ids['recipient_id'],
                                'shipping_address': 'x', 'expected_shipping_date': '2026-01-01'}),
    ]
    for method, url, body in calls:
        response = client.open(url, method=method, headers=headers, json=body)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code} {response.get_json()}"
        assert response.get_json()['code'] == 12001, f"{method} {url}"


def test_company_b_admin_is_forbidden_on_company_a_outbound(client):
    ids = _company_a_fixtures(client)
    headers, _ = _make_company_b(client, company_admin=True)
    _assert_all_forbidden(client, headers, ids)


def test_company_b_operator_with_explicit_permissions_is_forbidden(client):
    ids = _company_a_fixtures(client)
    headers, _ = _make_company_b(client, company_admin=False)
    _assert_all_forbidden(client, headers, ids)


def test_company_b_cannot_create_dn_in_company_a_warehouse(client):
    """POST /dn/ 的 warehouse_id 必须在调用方可访问范围内，否则不能把预占打到别家仓库。"""
    ids = _company_a_fixtures(client)
    for company_admin in (True, False):
        headers, warehouse_b_id = _make_company_b(client, company_admin=company_admin)
        with client.application.app_context():
            goods_id = ids['goods_id']
            warehouse_a_id = ids['warehouse_a_id']
            reserved_before = get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_a_id).dn_stock

        response = client.post('/dn/', headers=headers, json={
            'recipient_id': ids['recipient_id'], 'warehouse_id': warehouse_a_id,
            'shipping_address': 'x', 'expected_shipping_date': '2026-01-01',
            'details': [{'goods_id': goods_id, 'quantity': 1}],
        })
        assert response.status_code == 403, response.get_json()
        assert response.get_json()['code'] == 12001

        with client.application.app_context():
            assert get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_a_id).dn_stock == reserved_before
            # 清掉本轮造的员工，第二轮用另一画像
            Staff.query.filter(Staff.company_id == 2).delete()
            db.session.commit()


def test_company_b_cannot_use_company_a_master_data_in_own_warehouse(client):
    """在自家仓库建 DN 也不能引用别家公司的收货人 / 商品。"""
    ids = _company_a_fixtures(client)
    headers, warehouse_b_id = _make_company_b(client, company_admin=True)
    response = client.post('/dn/', headers=headers, json={
        'recipient_id': ids['recipient_id'], 'warehouse_id': warehouse_b_id,
        'shipping_address': 'x', 'expected_shipping_date': '2026-01-01',
        'details': [{'goods_id': ids['goods_id'], 'quantity': 1}],
    })
    assert response.status_code == 403, response.get_json()
    assert response.get_json()['code'] == 12001


def test_api_key_bound_to_company_b_is_forbidden(client):
    """绑定 Company B 的 API Key 同样不能读写 Company A 的 DN。"""
    ids = _company_a_fixtures(client)
    raw_key = 'company-b-outbound-key-000000000000000000000000'
    with client.application.app_context():
        company_b = Company.query.filter_by(name='Company B').first()
        admin = get_admin_user()
        if Warehouse.query.filter_by(company_id=company_b.id).first() is None:
            db.session.add(Warehouse(name='Warehouse B', address='B', phone='1', zip_code='1',
                                     company_id=company_b.id, created_by=admin.id))
        api_key = APIKey(key=hash_api_key(raw_key), key_prefix=raw_key[:8], system_name='CompanyB-ERP',
                         permissions=list(OUTBOUND_PERMISSIONS))
        api_key.company_id = company_b.id     # APIKey.__init__ 不收 company_id，构造后赋值
        db.session.add(api_key)
        db.session.commit()

    headers = {'X-API-KEY': raw_key}
    dn = ids['dn_id']
    for method, url, body in [
        ('GET', f'/dn/{dn}', None),
        ('PUT', f'/dn/{dn}', {'remark': 'hacked'}),
        ('DELETE', f'/dn/{dn}', None),
        ('PUT', f'/dn/{dn}/cancel/', None),
        ('GET', f'/picking/{ids["picking_id"]}', None),
        ('GET', f'/packing/{ids["packing_id"]}', None),
        ('GET', f'/delivery/{ids["delivery_id"]}', None),
    ]:
        response = client.open(url, method=method, headers=headers, json=body)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code} {response.get_json()}"


def test_company_a_admin_keeps_access_to_own_outbound(client, access_company_admin_token):
    """正向对照：Company A 的 company_admin 对自家单据一切正常。"""
    ids = _company_a_fixtures(client)
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    assert client.get(f'/dn/{ids["dn_id"]}', headers=headers).status_code == 200
    assert client.put(f'/dn/{ids["dn_id"]}', headers=headers, json={'remark': 'mine'}).status_code == 200
    assert client.get(f'/picking/{ids["picking_id"]}/batches/', headers=headers).status_code == 200
    assert client.get(f'/packing/{ids["packing_id"]}/details/', headers=headers).status_code == 200
    assert client.get(f'/delivery/{ids["delivery_id"]}', headers=headers).status_code == 200
    assert client.put(f'/dn/{ids["dn_id"]}/cancel/', headers=headers).status_code == 200


def test_outbound_stats_use_module_permissions(client):
    """B-25：stats 端点权限从 sorting_read 改为各模块 *_read。"""
    with client.application.app_context():
        admin = get_admin_user()
        company = get_company()
        role = Role(name='stats_reader', description='x', is_active=True)
        db.session.add(role)
        for name in ('picking_read', 'packing_read', 'delivery_read'):
            perm = Permission.query.filter_by(name=name).first() or Permission(name=name, description=name)
            role.permissions.append(perm)
        staff = Staff(user_name='stats_reader', email='stats@a.com', is_active=True,
                      company_id=company.id, created_by=admin.id)
        staff.set_password('password')
        staff.roles.append(role)
        staff.warehouses.append(get_warehouse())
        db.session.add_all([role, staff])
        db.session.commit()
        token = create_access_token(identity=staff)
        warehouse_id = get_warehouse().id

        sorting_role = Role(name='sorting_only', description='x', is_active=True)
        sorting_role.permissions.append(Permission(name='sorting_read', description='x'))
        sorter = Staff(user_name='sorter', email='sorter@a.com', is_active=True,
                       company_id=company.id, created_by=admin.id)
        sorter.set_password('password')
        sorter.roles.append(sorting_role)
        sorter.warehouses.append(get_warehouse())
        db.session.add_all([sorting_role, sorter])
        db.session.commit()
        sorter_token = create_access_token(identity=sorter)

    for module in ('picking', 'packing', 'delivery'):
        for path in ('monthly-stats', 'status-overview-stats'):
            ok = client.get(f'/{module}/{path}', headers={'Authorization': f'Bearer {token}',
                                                          'X-WAREHOUSE-ID': str(warehouse_id)})
            assert ok.status_code == 200, f"{module}/{path}: {ok.get_json()}"
            denied = client.get(f'/{module}/{path}', headers={'Authorization': f'Bearer {sorter_token}',
                                                              'X-WAREHOUSE-ID': str(warehouse_id)})
            assert denied.status_code == 403, f"{module}/{path} should not accept sorting_read"
