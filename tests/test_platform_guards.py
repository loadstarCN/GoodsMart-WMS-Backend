"""权限 / 平台 / 主数据守卫（2026-10-04 审计 🟡：B-69 B-70 B-73 B-74 B-75 B-76 B-77 B-84 B-101）

- B-69  PUT /goods/<id>/origin-country 要过仓库范围，响应只有 id / code / origin_country；
        PUT /goods/<id> 的 storage_records 按调用方仓库过滤
- B-101 GET / PUT /goods/<id> 过滤库位记录不再给 dynamic + delete-orphan 关系赋值（不发 DELETE）
- B-70  商品数值有限性 / 范围校验（JSON + CSV，16100）；CSV 内重复编码 16101；
        IntegrityError → 409 44005、DataError → 400 16102，不回显 SQL
- B-73  商品描述 nh3 白名单清洗（JSON 新建 / 修改、CSV 导入）+ 存量清洗脚本
- B-74  API Key 随公司停用 / 过期失效（11007），绑定用户停用直接 401（11006），不退化成公司级 Key
- B-75  只有 warehouse_edit / api_keys_edit 的员工不能借仓库负责人或公司级 Key 扩大仓库范围（16104 / 16105）
- B-76  payment 全部端点按 DN 仓库校验范围，列表按仓库过滤
- B-77  停用角色不再授予权限 / 角色身份；不能分配停用角色（16103），已持有的可保留
- B-84  删除被引用的主数据 → 409 44004（details.entity / references）
"""
from .helpers import *  # noqa: F401,F403  先导入：helpers 里的 `import datetime` 会覆盖同名对象

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DataError, IntegrityError

from warehouse.payment.models import Payment


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _h(token, warehouse_id=None):
    headers = {'Authorization': f'Bearer {token}'}
    if warehouse_id is not None:
        headers['X-WAREHOUSE-ID'] = str(warehouse_id)
    return headers


def _role(name, perms, active=True):
    role = Role(name=name, description=name, is_active=active)
    db.session.add(role)
    for p in perms:
        perm = Permission.query.filter_by(name=p).first()
        if perm is None:
            perm = Permission(name=p, description=p)
            db.session.add(perm)
        role.permissions.append(perm)
    db.session.commit()
    return role


def _staff(name, role, warehouses, company=None):
    company = company or get_company()
    staff = Staff(user_name=name, email=f'{name}@t.com', is_active=True, company_id=company.id,
                  created_by=get_admin_user().id)
    staff.set_password('password')
    if role is not None:
        staff.roles.append(role)
    for w in warehouses:
        staff.warehouses.append(w)
    db.session.add(staff)
    db.session.commit()
    return staff


def _api_key(company_id, permissions, user_id=None):
    plain = 'k-' + uuid.uuid4().hex
    key = APIKey(key=hash_api_key(plain), key_prefix=plain[:8], system_name='guard-' + uuid.uuid4().hex[:6],
                 permissions=list(permissions), user_id=user_id)
    key.company_id = company_id
    key.is_active = True
    db.session.add(key)
    db.session.commit()
    return plain


def _wh_a():
    return Warehouse.query.filter_by(name='Warehouse A').first()


def _wh_b():
    return Warehouse.query.filter_by(name='Warehouse Test').first()


def _stock_in_b(goods, quantity=777):
    """在仓库 B 放一个库位和这件商品的库存（库存记录 + 库位记录）"""
    wh_b = _wh_b()
    loc_b = Location(warehouse_id=wh_b.id, code='B-LOC-' + uuid.uuid4().hex[:4], description='b',
                     location_type='standard', created_by=1)
    db.session.add(loc_b)
    db.session.commit()
    db.session.add(Inventory(goods_id=goods.id, warehouse_id=wh_b.id, total_stock=quantity,
                             onhand_stock=quantity, damage_stock=0, return_stock=0, dn_stock=0, asn_stock=0))
    db.session.add(GoodsLocation(goods_id=goods.id, location_id=loc_b.id, quantity=quantity))
    db.session.commit()
    return loc_b


def _upload_csv(client, token, text, company_id=1, overwrite=None):
    data = {'file': (io.BytesIO(text.encode('utf-8')), 'goods.csv'), 'company_id': str(company_id)}
    if overwrite:
        data['overwrite'] = overwrite
    return client.post('/goods/bulk_upload', headers=_h(token), data=data, content_type='multipart/form-data')


@pytest.fixture
def sql_log(client):
    """记录请求期间发出的 SQL（检查有没有误发 DELETE）"""
    statements = []
    with client.application.app_context():
        engine = db.engine

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', _listener)
    yield statements
    event.remove(engine, 'before_cursor_execute', _listener)


# ===========================================================================
# B-69 / B-101  商品详情 / 修改 / 原产国的仓库范围
# ===========================================================================

@pytest.fixture
def sorter(client):
    """只绑仓库 A、只有 sorting_edit + goods_read 的员工；商品 ONLY-B 只在仓库 B 有货"""
    with client.application.app_context():
        role = _role('sorter', ['sorting_edit', 'goods_read'])
        staff = _staff('sorter', role, [_wh_a()])
        goods = Goods(code='ONLY-B', company_id=get_company().id, name='Only in B', unit='pcs', weight=2.5,
                      created_by=1, is_active=True)
        db.session.add(goods)
        db.session.commit()
        _stock_in_b(goods)
        return {
            'token': create_access_token(identity=staff),
            'only_b': goods.id,
            'in_a': Goods.query.filter_by(code='G001').first().id,
            'wh_a': _wh_a().id,
            'wh_b': _wh_b().id,
        }


def test_origin_country_requires_warehouse_scope(client, sorter):
    h = _h(sorter['token'], sorter['wh_a'])
    # 只在仓库 B 有货：与 GET 一致 → 404，且原产国没被改
    response = client.put(f"/goods/{sorter['only_b']}/origin-country", headers=h, json={'origin_country': 'CN'})
    assert response.status_code == 404, response.get_json()
    assert response.get_json()['code'] == 13003
    with client.application.app_context():
        assert db.session.get(Goods, sorter['only_b']).origin_country is None

    # 不带 X-WAREHOUSE-ID：非公司管理员员工 → 400 14003
    response = client.put(f"/goods/{sorter['only_b']}/origin-country", headers=_h(sorter['token']),
                          json={'origin_country': 'JP'})
    assert response.status_code == 400
    assert response.get_json()['code'] == 14003

    # 带别人的仓库 → 403
    response = client.put(f"/goods/{sorter['in_a']}/origin-country", headers=_h(sorter['token'], sorter['wh_b']),
                          json={'origin_country': 'JP'})
    assert response.status_code == 403


def test_origin_country_response_is_slim(client, sorter):
    response = client.put(f"/goods/{sorter['in_a']}/origin-country", headers=_h(sorter['token'], sorter['wh_a']),
                          json={'origin_country': 'cn'})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body == {'id': sorter['in_a'], 'code': 'G001', 'origin_country': 'CN'}


def test_put_goods_filters_storage_records_and_keeps_other_warehouse_rows(client, sql_log):
    with client.application.app_context():
        role = _role('goods_editor', ['goods_edit', 'goods_read'])
        staff = _staff('goods_editor', role, [_wh_a()])
        goods = Goods.query.filter_by(code='G001').first()
        loc_b = _stock_in_b(goods, quantity=55)
        token = create_access_token(identity=staff)
        goods_id, wh_a_id, loc_b_id = goods.id, _wh_a().id, loc_b.id

    sql_log.clear()
    response = client.put(f'/goods/{goods_id}', headers=_h(token, wh_a_id), json={'name': 'Renamed by A'})
    assert response.status_code == 200, response.get_json()
    records = response.get_json()['storage_records']
    assert records and {r['warehouse_id'] for r in records} == {wh_a_id}

    # 生产上 PUT 之后审计日志会 commit：过滤时若给关系赋值，仓库 B 的库位记录就被真删了（B-101）。
    # 测试 app 没挂审计日志，所以直接查请求期间有没有发 DELETE
    assert not [s for s in sql_log if s.lstrip().upper().startswith('DELETE')]
    with client.application.app_context():
        assert GoodsLocation.query.filter_by(goods_id=goods_id, location_id=loc_b_id).count() == 1

    # 平台管理员看得到两个仓库
    with client.application.app_context():
        admin_token = create_access_token(identity=get_admin_user())
    response = client.put(f'/goods/{goods_id}', headers=_h(admin_token), json={'name': 'Renamed by admin'})
    assert response.status_code == 200
    assert len({r['warehouse_id'] for r in response.get_json()['storage_records']}) == 2


def test_get_goods_filters_without_touching_relationship(client, sql_log):
    with client.application.app_context():
        role = _role('goods_reader', ['goods_read'])
        staff = _staff('goods_reader', role, [_wh_a()])
        goods = Goods.query.filter_by(code='G001').first()
        loc_b = _stock_in_b(goods, quantity=9)
        token = create_access_token(identity=staff)
        goods_id, wh_a_id, loc_b_id = goods.id, _wh_a().id, loc_b.id

    sql_log.clear()
    response = client.get(f'/goods/{goods_id}', headers=_h(token, wh_a_id))
    assert response.status_code == 200, response.get_json()
    assert {r['warehouse_id'] for r in response.get_json()['storage_records']} == {wh_a_id}
    # 以前给 storage_records 赋值，序列化时 autoflush 对仓库 B 的库位记录发 DELETE（PG 上会锁行）
    assert not [s for s in sql_log if s.lstrip().upper().startswith('DELETE')]
    assert not [s for s in sql_log if s.lstrip().upper().startswith('UPDATE GOODS_LOCATIONS')]
    with client.application.app_context():
        assert GoodsLocation.query.filter_by(goods_id=goods_id, location_id=loc_b_id).count() == 1


# ===========================================================================
# B-70  数值校验 / CSV 重复编码 / 数据库异常映射
# ===========================================================================

@pytest.mark.parametrize('raw_body, field', [
    ('{"weight": NaN}', 'weight'),
    ('{"weight": Infinity}', 'weight'),
    ('{"weight": -Infinity}', 'weight'),
    ('{"weight": "NaN"}', 'weight'),
    ('{"weight": 0}', 'weight'),
    ('{"weight": -1.5}', 'weight'),
    ('{"weight": 100000}', 'weight'),
    ('{"weight": 1e300}', 'weight'),
    ('{"weight": "abc"}', 'weight'),
    ('{"weight": true}', 'weight'),
    ('{"length": -5}', 'length'),
    ('{"length": 0}', 'length'),
    ('{"width": 2147483648}', 'width'),
    ('{"height": NaN}', 'height'),
    ('{"price": NaN}', 'price'),
    ('{"price": -1}', 'price'),
    ('{"discount_price": 1e20}', 'discount_price'),
])
def test_json_goods_numbers_rejected(client, access_token, raw_body, field):
    with client.application.app_context():
        goods = Goods.query.filter_by(code='G001').first()
        goods_id, before = goods.id, (goods.weight, goods.length, goods.width, goods.height, goods.price)

    response = client.put(f'/goods/{goods_id}', headers={**_h(access_token), 'Content-Type': 'application/json'},
                          data=raw_body)
    assert response.status_code == 400, response.get_data(as_text=True)
    body = response.get_json()
    assert body['code'] == 16100
    assert body['field'] == field and body['details'] == {'field': field}
    with client.application.app_context():
        goods = db.session.get(Goods, goods_id)
        assert (goods.weight, goods.length, goods.width, goods.height, goods.price) == before

    # 新建同样拦
    response = client.post('/goods/', headers={**_h(access_token), 'Content-Type': 'application/json'},
                           data=raw_body[:-1] + ', "code": "NUM-X", "name": "x", "company_id": 1}')
    assert response.status_code == 400 and response.get_json()['code'] == 16100
    with client.application.app_context():
        assert Goods.query.filter_by(code='NUM-X').count() == 0


def test_json_goods_numbers_normalized(client, access_token):
    with client.application.app_context():
        goods_id = Goods.query.filter_by(code='G001').first().id
    response = client.put(f'/goods/{goods_id}', headers=_h(access_token), json={
        'weight': '99999.999', 'length': 10.6, 'width': '7', 'height': 1, 'price': 0,
    })
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['weight'] == pytest.approx(99999.999)
    assert (data['length'], data['width'], data['height']) == (11, 7, 1)

    # 重量按 3 位小数四舍五入；空串 / null = 清空
    response = client.put(f'/goods/{goods_id}', headers=_h(access_token), json={'weight': 1.2345, 'length': ''})
    assert response.status_code == 200
    assert response.get_json()['weight'] == pytest.approx(1.235)
    assert response.get_json()['length'] is None


@pytest.mark.parametrize('weight, length, field', [
    ('NaN', '', 'weight'),
    ('Infinity', '', 'weight'),
    ('-2.5', '', 'weight'),
    ('0', '', 'weight'),
    ('', '-100', 'length'),
    ('', 'inf', 'length'),
    ('', '99999999999', 'length'),
])
def test_csv_goods_numbers_rejected_with_row(client, access_company_admin_token, weight, length, field):
    csv_text = f'code,name,weight,length\nOK1,Fine,1.5,10\nWEIRD1,Weird,{weight},{length}\n'
    response = _upload_csv(client, access_company_admin_token, csv_text)
    assert response.status_code == 400, response.get_json()
    body = response.get_json()
    assert body['code'] == 16100
    assert body['details'] == {'field': field, 'row': 3}
    assert body['message'].startswith('Row 3:')
    with client.application.app_context():
        assert Goods.query.filter(Goods.code.in_(['OK1', 'WEIRD1'])).count() == 0


def test_csv_duplicate_code_reports_rows(client, access_company_admin_token):
    csv_text = 'code,name\nDUP0,Zero\nDUP1,One\nDUP1,Two\n'
    response = _upload_csv(client, access_company_admin_token, csv_text)
    assert response.status_code == 400, response.get_json()
    body = response.get_json()
    assert body['code'] == 16101
    assert body['details'] == {'code': 'DUP1', 'rows': [3, 4]}
    with client.application.app_context():
        assert Goods.query.filter(Goods.code.in_(['DUP0', 'DUP1'])).count() == 0


def test_csv_valid_numbers_still_import(client, access_company_admin_token):
    csv_text = 'code,name,weight,length,width,height,price\nNEWOK,Ok,0.25,120,80,45,1000\n'
    response = _upload_csv(client, access_company_admin_token, csv_text)
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        goods = Goods.query.filter_by(code='NEWOK').first()
        assert float(goods.weight) == pytest.approx(0.25)
        assert (goods.length, goods.width, goods.height) == (120, 80, 45)


def test_integrity_error_maps_to_409_without_sql(client, access_token):
    with client.application.app_context():
        g2 = Goods.query.filter_by(code='G002').first().id
    # 同公司编码重复：撞 uix_code_company
    response = client.put(f'/goods/{g2}', headers=_h(access_token), json={'code': 'G001'})
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert (body['code'], body['message']) == (44005, 'The data conflicts with existing records')
    # 开发环境也只带异常类型名，不带 SQL / 约束名
    assert set(body) <= {'status', 'code', 'message', 'debug'}
    assert body.get('debug', {}) in ({}, {'exception': 'IntegrityError'})
    text = response.get_data(as_text=True).lower()
    for leak in ('unique', 'constraint', 'goods.code', 'update goods', 'uix_code_company'):
        assert leak not in text
    # 会话已回滚，后续请求正常
    response = client.get(f'/goods/{g2}', headers=_h(access_token))
    assert response.status_code == 200 and response.get_json()['code'] == 'G002'


def test_db_errors_never_leak_sql(client):
    app = client.application

    def _raise_data_error():
        raise DataError('UPDATE goods SET weight=%(w)s', {'w': 'Infinity'}, Exception('numeric field overflow'))

    def _raise_integrity_error():
        raise IntegrityError('DELETE FROM suppliers WHERE id=1', {}, Exception('violates foreign key constraint'))

    app.add_url_rule('/__guard/data-error', 'guard_data_error', _raise_data_error)
    app.add_url_rule('/__guard/integrity-error', 'guard_integrity_error', _raise_integrity_error)

    response = client.get('/__guard/data-error')
    assert response.status_code == 400
    assert response.get_json()['code'] == 16102
    assert response.get_json()['message'] == 'Invalid data value'
    response = client.get('/__guard/integrity-error')
    assert response.status_code == 409
    assert response.get_json()['code'] == 44005
    for path in ('/__guard/data-error', '/__guard/integrity-error'):
        text = client.get(path).get_data(as_text=True)
        for leak in ('UPDATE', 'DELETE', 'overflow', 'foreign key', 'suppliers', 'Infinity'):
            assert leak not in text


# ===========================================================================
# B-73  商品描述 nh3 清洗
# ===========================================================================

DIRTY = ('<p onclick="x()">Hi <b>there</b><script>alert(1)</script>'
         '<img src="x" onerror="alert(2)"><a href="javascript:alert(3)">l</a>'
         '<span style="color: red; position: fixed">c</span><iframe src="https://evil"></iframe></p>')


def _assert_clean(html):
    lowered = html.lower()
    for bad in ('<script', 'onerror', 'onclick', 'javascript:', '<iframe', 'position'):
        assert bad not in lowered, html
    assert '<b>there</b>' in html and 'color:red' in html.replace(' ', '')


def test_description_sanitized_on_create_and_update(client, access_token):
    response = client.post('/goods/', headers=_h(access_token), json={
        'code': 'XSS-1', 'name': 'x', 'company_id': 1, 'description': DIRTY})
    assert response.status_code == 201, response.get_json()
    _assert_clean(response.get_json()['description'])
    goods_id = response.get_json()['id']

    response = client.put(f'/goods/{goods_id}', headers=_h(access_token), json={'description': DIRTY})
    assert response.status_code == 200
    _assert_clean(response.get_json()['description'])

    # 纯文本原样保留（不把 & 转成 &amp;）；不传 description 不动
    response = client.put(f'/goods/{goods_id}', headers=_h(access_token), json={'description': 'A & B > C'})
    assert response.get_json()['description'] == 'A & B > C'
    response = client.put(f'/goods/{goods_id}', headers=_h(access_token), json={'name': 'renamed'})
    assert response.get_json()['description'] == 'A & B > C'


@pytest.mark.parametrize('overwrite', [None, 'override', 'append'])
def test_description_sanitized_on_csv_import(client, access_company_admin_token, overwrite):
    if overwrite == 'append':
        with client.application.app_context():
            goods = Goods.query.filter_by(code='G001').first()
            goods.description = None
            db.session.commit()
    code = 'G001' if overwrite else 'XSS-CSV'
    csv_text = 'code,name,description\n' + f'{code},x,"{DIRTY.replace(chr(34), chr(34) * 2)}"\n'
    response = _upload_csv(client, access_company_admin_token, csv_text, overwrite=overwrite)
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        _assert_clean(Goods.query.filter_by(code=code).first().description)


def test_sanitize_script_dry_run_then_apply(client):
    from scripts.sanitize_goods_description import sanitize_existing_descriptions
    with client.application.app_context():
        goods = Goods.query.filter_by(code='G001').first()
        goods.description = DIRTY
        Goods.query.filter_by(code='G002').first().description = 'plain & text'
        db.session.commit()
        goods_id = goods.id

        scanned, changed = sanitize_existing_descriptions(apply=False)
        assert changed == [goods_id] and scanned == 1
        assert db.session.get(Goods, goods_id).description == DIRTY  # dry-run 不写库

        scanned, changed = sanitize_existing_descriptions(apply=True, batch_size=1)
        assert changed == [goods_id]
        _assert_clean(db.session.get(Goods, goods_id).description)
        assert Goods.query.filter_by(code='G002').first().description == 'plain & text'

        assert sanitize_existing_descriptions(apply=False)[1] == []  # 幂等


# ===========================================================================
# B-74  API Key 随公司 / 绑定用户失效
# ===========================================================================

@pytest.mark.parametrize('mode', ['expired', 'inactive'])
def test_api_key_rejected_when_company_unavailable(client, mode):
    with client.application.app_context():
        company = get_company()
        plain = _api_key(company.id, ['supplier_read'])
    assert client.get('/supplier/', headers={'X-API-KEY': plain}).status_code == 200

    with client.application.app_context():
        company = get_company()
        if mode == 'expired':
            company.expired_at = datetime.now(timezone.utc) - timedelta(days=1)
        else:
            company.is_active = False
        db.session.commit()
    response = client.get('/supplier/', headers={'X-API-KEY': plain})
    assert response.status_code == 401
    assert response.get_json()['code'] == 11007


def test_api_key_rejected_when_bound_user_deactivated(client):
    """绑定只管仓库 A 的员工的 Key：员工停用后直接 401，不能退化成全公司 Key"""
    with client.application.app_context():
        op = User.query.filter_by(user_name='operator').first()
        plain = _api_key(get_company().id, ['inventory_read'], user_id=op.id)
        op_id, wh_a_id, wh_b_id = op.id, _wh_a().id, _wh_b().id
    kh = {'X-API-KEY': plain}
    assert client.get('/inventory/', headers={**kh, 'X-WAREHOUSE-ID': str(wh_a_id)}).status_code == 200
    assert client.get('/inventory/', headers={**kh, 'X-WAREHOUSE-ID': str(wh_b_id)}).status_code == 403

    with client.application.app_context():
        db.session.get(User, op_id).is_active = False
        db.session.commit()
    for wh in (wh_a_id, wh_b_id):
        response = client.get('/inventory/', headers={**kh, 'X-WAREHOUSE-ID': str(wh)})
        assert response.status_code == 401
        assert response.get_json()['code'] == 11006


def test_api_key_without_company_rejected_when_bound_staff_company_expired(client):
    with client.application.app_context():
        op = User.query.filter_by(user_name='operator').first()
        plain = _api_key(None, ['inventory_read'], user_id=op.id)
        get_company().expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        wh_a_id = _wh_a().id
    response = client.get('/inventory/', headers={'X-API-KEY': plain, 'X-WAREHOUSE-ID': str(wh_a_id)})
    assert response.status_code == 401
    assert response.get_json()['code'] == 11007


def test_platform_key_still_works(client):
    response = client.get('/supplier/', headers={'X-API-KEY': TEST_API_KEY_PLAIN})
    assert response.status_code == 200


# ===========================================================================
# B-75  仓库 / API Key 写操作不能扩大自己的仓库范围
# ===========================================================================

@pytest.fixture
def wh_manager(client):
    """只绑仓库 A、有 warehouse_edit / warehouse_delete 的员工"""
    with client.application.app_context():
        role = _role('wh_mgr', ['warehouse_read', 'warehouse_edit', 'warehouse_delete', 'inventory_read'])
        staff = _staff('mgr1', role, [_wh_a()])
        return {'token': create_access_token(identity=staff), 'id': staff.id,
                'wh_a': _wh_a().id, 'wh_b': _wh_b().id}


def test_warehouse_edit_holder_cannot_take_other_warehouse(client, wh_manager):
    h = _h(wh_manager['token'])
    # 别的仓库：不在自己范围 → 403 12001，负责人没变
    response = client.put(f"/warehouse/{wh_manager['wh_b']}", headers=h, json={'manager_id': wh_manager['id']})
    assert response.status_code == 403 and response.get_json()['code'] == 12001
    # 自己的仓库也不能改负责人
    response = client.put(f"/warehouse/{wh_manager['wh_a']}", headers=h, json={'manager_id': wh_manager['id']})
    assert response.status_code == 403 and response.get_json()['code'] == 16104
    with client.application.app_context():
        assert db.session.get(Warehouse, wh_manager['wh_b']).manager_id is None
        assert db.session.get(Warehouse, wh_manager['wh_a']).manager_id is None
    response = client.get('/inventory/', headers={**h, 'X-WAREHOUSE-ID': str(wh_manager['wh_b'])})
    assert response.status_code == 403


def test_warehouse_edit_holder_can_edit_own_warehouse(client, wh_manager):
    """Web 编辑页会把整条记录连同（未变的）manager_id 一起提交"""
    h = _h(wh_manager['token'])
    response = client.get(f"/warehouse/{wh_manager['wh_a']}", headers=h)
    payload = {**response.get_json(), 'name': 'Renamed A'}
    response = client.put(f"/warehouse/{wh_manager['wh_a']}", headers=h, json=payload)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['name'] == 'Renamed A'
    # 停用 / 启用自己的仓库（列表页开关）
    for active in (False, True):
        response = client.put(f"/warehouse/{wh_manager['wh_a']}", headers=h, json={'is_active': active})
        assert response.status_code == 200 and response.get_json()['is_active'] is active


def test_warehouse_create_delete_require_company_admin(client, wh_manager, access_company_admin_token):
    h = _h(wh_manager['token'])
    response = client.post('/warehouse/', headers=h, json={'name': 'Sneaky', 'company_id': 1})
    assert response.status_code == 403 and response.get_json()['code'] == 16104
    response = client.delete(f"/warehouse/{wh_manager['wh_a']}", headers=h)
    assert response.status_code == 403 and response.get_json()['code'] == 16104
    with client.application.app_context():
        assert Warehouse.query.filter_by(name='Sneaky').count() == 0

    # 公司管理员照常：建仓库、指定负责人、删除没被引用的仓库
    ca = _h(access_company_admin_token)
    response = client.post('/warehouse/', headers=ca, json={'name': 'New WH', 'manager_id': wh_manager['id']})
    assert response.status_code == 201, response.get_json()
    new_id = response.get_json()['id']
    response = client.put(f"/warehouse/{wh_manager['wh_b']}", headers=ca, json={'manager_id': wh_manager['id']})
    assert response.status_code == 200
    response = client.delete(f'/warehouse/{new_id}', headers=ca)
    assert response.status_code == 200, response.get_json()


@pytest.fixture
def integrator(client):
    """只绑仓库 A、有 api_keys_edit / api_keys_delete 的员工"""
    with client.application.app_context():
        role = _role('integrator', ['api_keys_read', 'api_keys_edit', 'api_keys_delete', 'inventory_read'])
        staff = _staff('integ1', role, [_wh_a()])
        company_key = APIKey(key=hash_api_key('company-wide-' + uuid.uuid4().hex), system_name='admin-made',
                             permissions=['inventory_read'])
        company_key.company_id = get_company().id
        db.session.add(company_key)
        db.session.commit()
        return {'token': create_access_token(identity=staff), 'id': staff.id, 'company_key': company_key.id,
                'operator': User.query.filter_by(user_name='operator').first().id,
                'wh_a': _wh_a().id, 'wh_b': _wh_b().id}


def test_api_key_holder_key_is_bound_to_self(client, integrator):
    h = _h(integrator['token'])
    response = client.post('/api-keys/api-keys', headers=h, json={'system_name': 'x', 'permissions': ['inventory_read']})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['user_id'] == integrator['id']  # 不传 user_id → 自动绑定自己
    plain = response.get_json()['key']
    kh = {'X-API-KEY': plain}
    assert client.get('/inventory/', headers={**kh, 'X-WAREHOUSE-ID': str(integrator['wh_a'])}).status_code == 200
    assert client.get('/inventory/', headers={**kh, 'X-WAREHOUSE-ID': str(integrator['wh_b'])}).status_code == 403

    # 绑别人 → 16105；权限超出自己 → 12006
    response = client.post('/api-keys/api-keys', headers=h, json={
        'system_name': 'y', 'permissions': ['inventory_read'], 'user_id': integrator['operator']})
    assert response.status_code == 403 and response.get_json()['code'] == 16105
    response = client.post('/api-keys/api-keys', headers=h, json={'system_name': 'z', 'permissions': ['dn_edit']})
    assert response.status_code == 403 and response.get_json()['code'] == 12006


def test_api_key_holder_cannot_touch_company_wide_keys(client, integrator):
    h = _h(integrator['token'])
    key_id = integrator['company_key']
    response = client.put(f'/api-keys/api-keys/{key_id}', headers=h, json={'is_active': False})
    assert response.status_code == 403 and response.get_json()['code'] == 16105
    response = client.delete(f'/api-keys/api-keys/{key_id}', headers=h)
    assert response.status_code == 403 and response.get_json()['code'] == 16105
    with client.application.app_context():
        key = db.session.get(APIKey, key_id)
        assert key is not None and key.is_active is True

    # 自己的 Key：可改可删，但不能解绑 / 改绑
    own = client.post('/api-keys/api-keys', headers=h, json={'system_name': 'own', 'permissions': []}).get_json()
    response = client.put(f"/api-keys/api-keys/{own['id']}", headers=h, json={'user_id': None})
    assert response.status_code == 403 and response.get_json()['code'] == 16105
    response = client.put(f"/api-keys/api-keys/{own['id']}", headers=h,
                          json={'system_name': 'own2', 'permissions': ['inventory_read']})
    assert response.status_code == 200 and response.get_json()['user_id'] == integrator['id']
    response = client.delete(f"/api-keys/api-keys/{own['id']}", headers=h)
    assert response.status_code == 200


def test_company_admin_still_manages_any_key(client, integrator, access_company_admin_token):
    ca = _h(access_company_admin_token)
    response = client.post('/api-keys/api-keys', headers=ca, json={'system_name': 'cw', 'permissions': ['inventory_read']})
    assert response.status_code == 201 and response.get_json()['user_id'] is None
    response = client.put(f"/api-keys/api-keys/{integrator['company_key']}", headers=ca, json={'is_active': False})
    assert response.status_code == 200


# ===========================================================================
# B-76  payment 的仓库范围
# ===========================================================================

def test_payment_endpoints_enforce_warehouse_scope(client, access_token):
    with client.application.app_context():
        delivery_id, carrier_id = get_delivery_task().id, get_carrier().id
    response = client.post('/payment/', headers=_h(access_token), json={
        'delivery_id': delivery_id, 'carrier_id': carrier_id, 'amount': '100.00', 'payment_method': 'cash'})
    assert response.status_code == 201, response.get_json()
    pid = response.get_json()['id']

    with client.application.app_context():
        role = _role('cashier', ['payment_read', 'payment_edit', 'payment_delete'])
        cashier_b = create_access_token(identity=_staff('cashier_b', role, [_wh_b()]))
        cashier_a = create_access_token(identity=_staff('cashier_a', role, [_wh_a()]))
        wh_a_id, wh_b_id = _wh_a().id, _wh_b().id

    hb = _h(cashier_b, wh_b_id)
    assert client.get(f'/payment/{pid}', headers=hb).status_code == 403
    assert client.get('/payment/', headers=hb).get_json()['total'] == 0
    assert client.put(f'/payment/{pid}', headers=hb, json={'remark': 'x'}).status_code == 403
    assert client.put(f'/payment/{pid}/process/', headers=hb).status_code == 403
    assert client.put(f'/payment/{pid}/cancel/', headers=hb).status_code == 403
    assert client.delete(f'/payment/{pid}', headers=hb).status_code == 403
    response = client.post('/payment/', headers=hb, json={
        'delivery_id': delivery_id, 'carrier_id': carrier_id, 'amount': '1.00', 'payment_method': 'cash'})
    assert response.status_code == 403
    # 不带仓库：非公司管理员 → 400 14003
    assert client.get('/payment/', headers=_h(cashier_b)).status_code == 400
    with client.application.app_context():
        payment = db.session.get(Payment, pid)
        assert payment.status == 'pending' and Payment.query.count() == 1

    ha = _h(cashier_a, wh_a_id)
    assert client.get(f'/payment/{pid}', headers=ha).status_code == 200
    assert client.get('/payment/', headers=ha).get_json()['total'] == 1
    response = client.put(f'/payment/{pid}/process/', headers=ha)
    assert response.status_code == 200 and response.get_json()['status'] == 'paid'


# ===========================================================================
# B-77  停用角色
# ===========================================================================

def test_inactive_role_grants_nothing(client, access_token, access_company_admin_token):
    with client.application.app_context():
        role_id = Role.query.filter_by(name='company_admin').first().id
    assert client.get('/supplier/', headers=_h(access_company_admin_token)).status_code == 200

    response = client.put(f'/user/roles/{role_id}', headers=_h(access_token), json={'is_active': False})
    assert response.status_code == 200 and response.get_json()['is_active'] is False

    # 权限不再生效（permission_required）
    assert client.get('/supplier/', headers=_h(access_company_admin_token)).status_code == 403
    # 也不再算 company_admin，权限集合里没有 company_all_access
    with client.application.app_context():
        user = User.query.filter_by(user_name='company_admin').first()
        assert user.has_role('company_admin') is False
        assert 'company_all_access' not in user.active_permission_names()
        assert [r.name for r in user.roles] == ['company_admin']  # 分配关系本身还在
    # 登录返回的角色只含生效中的
    response = client.post('/user/login', json={'account': 'company_admin', 'password': 'password'})
    assert response.status_code == 200 and 'company_admin' not in response.get_json()['roles']
    # 重新启用即恢复
    client.put(f'/user/roles/{role_id}', headers=_h(access_token), json={'is_active': True})
    assert client.get('/supplier/', headers=_h(access_company_admin_token)).status_code == 200


def test_inactive_company_admin_role_loses_company_wide_warehouse_scope(client, access_token):
    with client.application.app_context():
        role = Role.query.filter_by(name='company_admin').first()
        # 另给一个启用中的角色带 inventory_read，停用 company_admin 后仍能过 permission_required，
        # 这样看到的是仓库范围的变化
        extra = _role('inv_reader', ['inventory_read'])
        ca = User.query.filter_by(user_name='company_admin').first()
        ca.roles.append(extra)
        db.session.commit()
        token = create_access_token(identity=ca)
        role_id, wh_b_id = role.id, _wh_b().id
    # 公司管理员：不带仓库也能看全公司库存，也能看没分配给自己的仓库 B
    assert client.get('/inventory/', headers=_h(token)).status_code == 200
    assert client.get('/inventory/', headers=_h(token, wh_b_id)).status_code == 200

    client.put(f'/user/roles/{role_id}', headers=_h(access_token), json={'is_active': False})
    # 停用后按普通员工处理：必须带仓库，且只能是分配给自己的仓库（A）
    assert client.get('/inventory/', headers=_h(token)).status_code == 400
    assert client.get('/inventory/', headers=_h(token, wh_b_id)).status_code == 403


def test_inactive_role_cannot_be_assigned_but_can_be_kept(client, access_token):
    with client.application.app_context():
        _role('retired_role', ['operator'], active=False)
        keep = _role('keep_role', ['operator'])
        staff = _staff('keeper', keep, [_wh_a()])
        staff_id, keep_id = staff.id, keep.id
        op_id = User.query.filter_by(user_name='operator').first().id
    h = _h(access_token)

    # 新分配停用角色 → 400 16103（员工 / 平台用户的新建与修改都拦）
    response = client.put(f'/staff/{op_id}', headers=h, json={'roles': ['warehouse_operator', 'retired_role']})
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 16103 and body['field'] == 'roles' and body['details'] == {'roles': ['retired_role']}
    response = client.post('/staff/', headers=h, json={
        'user_name': 'new_s', 'email': 'new_s@t.com', 'password': 'password123', 'company_id': 1,
        'roles': ['retired_role']})
    assert response.status_code == 400 and response.get_json()['code'] == 16103
    response = client.post('/user/users', headers=h, json={
        'user_name': 'new_u', 'email': 'new_u@t.com', 'password': 'password123', 'roles': ['retired_role']})
    assert response.status_code == 400 and response.get_json()['code'] == 16103

    # 已持有的角色被停用后，编辑页原样提交回来 → 照常保存（保留该分配）
    client.put(f'/user/roles/{keep_id}', headers=h, json={'is_active': False})
    response = client.put(f'/staff/{staff_id}', headers=h, json={'roles': ['keep_role'], 'position': 'p'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['roles'] == ['keep_role']


# ===========================================================================
# B-84  删除被引用的主数据
# ===========================================================================

@pytest.mark.parametrize('path, getter, entity, expected_ref', [
    ('/supplier', lambda: get_supplier_by_id(1), 'supplier', 'asn'),
    ('/carrier', lambda: get_carrier(), 'carrier', 'dn'),
    ('/recipient', lambda: get_recipient_by_id(1), 'recipient', 'dn'),
    ('/goods', lambda: Goods.query.filter_by(code='G001').first(), 'goods', 'goods_locations'),
    ('/warehouse', lambda: _wh_a(), 'warehouse', 'locations'),
])
def test_delete_referenced_master_data_is_409(client, access_token, path, getter, entity, expected_ref):
    with client.application.app_context():
        record = getter()
        model, record_id = type(record), record.id
    response = client.delete(f'{path}/{record_id}', headers=_h(access_token))
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body['code'] == 44004
    assert body['details']['entity'] == entity and body['details']['id'] == record_id
    assert body['details']['references'][expected_ref] >= 1
    assert 'deactivate' in body['message']
    with client.application.app_context():
        assert db.session.get(model, record_id) is not None


def test_delete_department_with_staff_is_409(client, access_token):
    with client.application.app_context():
        department = get_department()
        op = Staff.query.filter_by(user_name='operator').first()
        op.department_id = department.id
        db.session.commit()
        dep_id, op_id = department.id, op.id
    response = client.delete(f'/department/{dep_id}', headers=_h(access_token))
    assert response.status_code == 409
    assert response.get_json()['details'] == {'entity': 'department', 'id': dep_id, 'references': {'staff': 1}}
    with client.application.app_context():
        assert db.session.get(Staff, op_id).department_id == dep_id

    # 员工移走后可以删
    with client.application.app_context():
        db.session.get(Staff, op_id).department_id = None
        db.session.commit()
    assert client.delete(f'/department/{dep_id}', headers=_h(access_token)).status_code == 200


def test_delete_warehouse_only_assigned_to_staff_is_allowed(client, access_token):
    """员工的仓库分配不算引用：删除仓库时一并清掉"""
    with client.application.app_context():
        wh = Warehouse(name='Assigned only', address='x', company_id=get_company().id, created_by=1)
        db.session.add(wh)
        db.session.commit()
        op = Staff.query.filter_by(user_name='operator').first()
        op.warehouses.append(wh)
        db.session.commit()
        wh_id, op_id = wh.id, op.id
    response = client.delete(f'/warehouse/{wh_id}', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        assert db.session.get(Warehouse, wh_id) is None
        assert wh_id not in {w.id for w in db.session.get(Staff, op_id).warehouses}


def test_delete_unreferenced_goods_still_works(client, access_token):
    response = client.post('/goods/', headers=_h(access_token), json={'code': 'TMP-DEL', 'name': 'tmp', 'company_id': 1})
    assert response.status_code == 201
    goods_id = response.get_json()['id']
    response = client.delete(f'/goods/{goods_id}', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
