"""全局搜索 GET /warehouse/search（顶栏搜索框 / 搜索结果页）

- 各类型命中：DN（编号 / 订单号 / 运单号 / 收货方 / 明细商品）、ASN（编号 / 订单号 / 运单号 / 供应商 / 明细商品）、
  作业任务（SORT- / PICK- / PACK- / DEL- / CC- / ADJ- 及关联单号、明细商品）、商品、库位、收货方
- 带前缀的完整单号 / 完整运单号：exact，排在组内与全部结果最前（exact_matches）；纯数字同时试编号与运单号
- 运单号去空白 / 连字符比较，高亮位置换算回显示文本
- 权限：没有查看权限的类型不搜、不出现在 types / counts / groups；一类都没有 → 空结果（200）；API Key 按 key 的权限
- 公司 / 仓库隔离（scope=warehouse / all）、LIKE 转义、q 过短 / 过长、types 与单类型分页 / 列表列
"""
import uuid

from .helpers import *
from warehouse.dn.models import DNCarrierShipment
from warehouse.search.services import SEARCH_TYPES


def _h(token=None, wh=None, key=None):
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if key:
        headers['X-API-KEY'] = key
    if wh is not None:
        headers['X-WAREHOUSE-ID'] = str(wh)
    return headers


def _search(client, headers, status=200, **params):
    response = client.get('/search', headers=headers, query_string=params)
    assert response.status_code == status, response.get_json()
    return response.get_json()


def _group(result, type_):
    return next((group for group in result['groups'] if group['type'] == type_), None)


def _ids(result, type_):
    group = _group(result, type_)
    return [item['id'] for item in group['items']] if group else []


def _item(result, type_, record_id):
    return next(item for item in _group(result, type_)['items'] if item['id'] == record_id)


def _highlighted(item, field='subtitle'):
    """高亮在显示文本里圈出的片段（测试数据都是 BMP 字符，UTF-16 下标 = Python 下标）"""
    if field.startswith('columns.'):
        text = item['columns'][field.split('.', 1)[1]]
    else:
        text = item[field]
    return [text[h['start']:h['end']] for h in item['highlights'] if h['field'] == field]


def _role(name, permissions):
    role = Role(name=name, description=name, is_active=True)
    db.session.add(role)
    for permission_name in permissions:
        permission = Permission.query.filter_by(name=permission_name).first()
        if permission is None:
            permission = Permission(name=permission_name, description=permission_name)
            db.session.add(permission)
        role.permissions.append(permission)
    db.session.commit()
    return role


def _staff_token(user_name, company_id, role, warehouses):
    staff = Staff(user_name=user_name, email=f'{user_name}@test.com', is_active=True,
                  company_id=company_id, created_by=get_admin_user().id)
    staff.set_password('password')
    staff.roles.append(role)
    for warehouse in warehouses:
        staff.warehouses.append(warehouse)
    db.session.add(staff)
    db.session.commit()
    return create_access_token(identity=staff)


def _api_key(company_id, permissions):
    plain = uuid.uuid4().hex + uuid.uuid4().hex
    key = APIKey(key=hash_api_key(plain), key_prefix=plain[:8], system_name='sys-' + plain[:6],
                 permissions=permissions)
    key.company_id = company_id
    key.is_active = True
    db.session.add(key)
    db.session.commit()
    return plain


def _new_dn(warehouse_id, **fields):
    dn = DN(recipient_id=get_recipient().id, warehouse_id=warehouse_id, shipping_address='1 Street',
            expected_shipping_date=db.func.current_date(), dn_type='shipping', status='pending',
            created_by=get_admin_user().id, **fields)
    db.session.add(dn)
    db.session.commit()
    return dn.id


def _new_goods(code, name, company_id=None, **fields):
    goods = Goods(code=code, name=name, company_id=company_id or get_company().id, unit='pcs', is_active=True,
                  created_by=get_admin_user().id, **fields)
    db.session.add(goods)
    db.session.commit()
    return goods.id


# ---------------------------------------------------------------------------------------------
# 单号 / 前缀
# ---------------------------------------------------------------------------------------------

def test_dn_prefix_is_exact_and_listed_first(client, access_token):
    with client.application.app_context():
        picking = get_picking_task()
        dn_id, picking_id = picking.dn_id, picking.id

    for q in (f'DN-{dn_id}', f'dn{dn_id}', f'DN #{dn_id}'):
        result = _search(client, _h(access_token), q=q)
        first = result['groups'][0]
        assert first['type'] == 'dn' and first['items'][0]['id'] == dn_id, q
        item = first['items'][0]
        assert item['exact'] is True and item['matched_field'] == 'number'
        assert item['code'] == f'DN-{dn_id}' and _highlighted(item, 'code') == [f'DN-{dn_id}']
        assert item['url_hint'] == {'page': 'dn', 'id': dn_id}
        assert [x['id'] for x in result['exact_matches']] == [dn_id]
        # 关联单号：拣货任务按所属 DN 命中，但不是 exact
        task = _item(result, 'picking', picking_id)
        assert task['matched_field'] == 'dn_number' and task['exact'] is False
        assert task['title'] == f'DN-{dn_id}' and _highlighted(task, 'title') == [f'DN-{dn_id}']
        assert task['url_hint'] == {'page': 'picking', 'id': picking_id}


def test_task_prefixes_hit_their_own_documents(client, access_token):
    with client.application.app_context():
        ids = {
            'SORT': ('sorting', SortingTask.query.first().id), 'PICK': ('picking', PickingTask.query.first().id),
            'PACK': ('packing', PackingTask.query.first().id), 'DEL': ('delivery', DeliveryTask.query.first().id),
            'CC': ('cyclecount', CycleCountTask.query.first().id), 'ADJ': ('adjustment', Adjustment.query.first().id),
            'ASN': ('asn', ASN.query.order_by(ASN.id.desc()).first().id),
        }
    for prefix, (type_, record_id) in ids.items():
        result = _search(client, _h(access_token), q=f'{prefix}-{record_id}')
        first = result['groups'][0]
        assert first['type'] == type_, prefix
        item = first['items'][0]
        assert item['id'] == record_id and item['exact'] is True and item['matched_field'] == 'number', prefix
        assert item['code'] == f'{prefix}-{record_id}' and item['url_hint'] == {'page': type_, 'id': record_id}
        assert result['exact_matches'][0]['type'] == type_
        # 别的类型的完整单号不算 exact
        assert all(x['type'] == type_ for x in result['exact_matches'])


def test_adjustment_matches_source_cycle_count_only_with_prefix(client, access_token):
    with client.application.app_context():
        adjustment = Adjustment.query.first()
        cyclecount = CycleCountTask.query.first()
        adjustment.cyclecount_id = cyclecount.id
        adjustment.adjustment_reason = 'Count diff'
        db.session.commit()
        adjustment_id, cyclecount_id = adjustment.id, cyclecount.id

    result = _search(client, _h(access_token), q=f'CC-{cyclecount_id}', types='adjustment,cyclecount')
    assert result['groups'][0]['type'] == 'cyclecount'          # 盘点单本身是 exact，排前面
    item = _item(result, 'adjustment', adjustment_id)
    assert item['matched_field'] == 'cyclecount_number' and not item['exact']
    assert item['title'] == 'Count diff' and _highlighted(item) == [f'CC-{cyclecount_id}']

    # 纯数字只比调整单号（同调整单列表）：来源盘点单号不按纯数字找
    with client.application.app_context():
        for _ in range(12):
            db.session.add(CycleCountTask(task_name='x', warehouse_id=get_warehouse().id, status='pending',
                                          created_by=get_admin_user().id))
        db.session.commit()
        adjustment = db.session.get(Adjustment, adjustment_id)
        adjustment.cyclecount_id = CycleCountTask.query.order_by(CycleCountTask.id.desc()).first().id
        db.session.commit()
        source_id = adjustment.cyclecount_id
    assert adjustment_id not in _ids(_search(client, _h(access_token), q=str(source_id), types='adjustment'),
                                     'adjustment')


def test_plain_number_tries_number_and_tracking(client, access_token):
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        new_ids = [_new_dn(warehouse_id) for _ in range(10)]
        target = next(i for i in new_ids if i >= 10)
        tracked = get_dn().id
        db.session.add(DNCarrierShipment(dn_id=tracked, source='manual', status='active',
                                         tracking_number=str(target)))
        db.session.commit()

    result = _search(client, _h(access_token), q=str(target), types='dn')
    group = _group(result, 'dn')
    # 完整运单号是 exact，排在编号命中的前面
    assert [item['id'] for item in group['items'][:2]] == [tracked, target]
    assert group['items'][0]['matched_field'] == 'tracking_number' and group['items'][0]['exact'] is True
    assert group['items'][1]['matched_field'] == 'number' and group['items'][1]['exact'] is False
    assert _highlighted(group['items'][1], 'code') == [str(target)]       # 纯数字只圈编号部分


# ---------------------------------------------------------------------------------------------
# DN / ASN 的各字段
# ---------------------------------------------------------------------------------------------

def test_tracking_number_ignores_separators_and_maps_highlight_back(client, access_token):
    with client.application.app_context():
        task = DeliveryTask.query.filter_by(tracking_number='DT-TEST-001').one()
        task.tracking_number = 'AB 7946-0000-01'
        db.session.commit()
        dn_id = task.dn_id

    result = _search(client, _h(access_token), q='ab7946000001')
    item = _item(result, 'dn', dn_id)
    assert item['exact'] is True and item['matched_field'] == 'tracking_number'
    assert result['groups'][0]['type'] == 'dn' and result['exact_matches'][0]['id'] == dn_id
    assert _highlighted(item) == ['AB 7946-0000-01']

    item = _item(_search(client, _h(access_token), q='946000'), 'dn', dn_id)   # 6 位以上：一段也行
    assert item['exact'] is False and item['matched_field'] == 'tracking_number'
    assert _highlighted(item) == ['946-000']

    assert dn_id not in _ids(_search(client, _h(access_token), q='AB79'), 'dn')   # 太短只按完整号码比

    # 单类型：列表列里的运单号也带高亮
    result = _search(client, _h(access_token), q='7946 0000 01', types='dn')
    item = _item(result, 'dn', dn_id)
    assert item['columns']['tracking_number'] == 'AB 7946-0000-01'
    assert _highlighted(item, 'columns.tracking_number') == ['7946-0000-01']


def test_dn_order_number_recipient_and_detail_goods(client, access_token):
    with client.application.app_context():
        dn = get_dn()
        dn.order_number = 'ORD-5566'
        db.session.commit()
        dn_id, dn_count = dn.id, DN.query.count()

    item = _item(_search(client, _h(access_token), q='ord-55'), 'dn', dn_id)
    assert item['matched_field'] == 'order_number' and _highlighted(item) == ['ORD-55']

    result = _search(client, _h(access_token), q='recipient a', types='dn', per_page=50)
    assert _group(result, 'dn')['total'] == dn_count
    item = _item(result, 'dn', dn_id)
    assert item['matched_field'] == 'recipient_name' and _highlighted(item, 'title') == ['Recipient A']

    # 明细商品：编码前缀 / 名称包含，副标题带命中的商品
    item = _item(_search(client, _h(access_token), q='g00', types='dn'), 'dn', dn_id)
    assert item['matched_field'] == 'goods_code'
    assert 'G001 Sample Goods' in item['subtitle'] and _highlighted(item) == ['G00']
    item = _item(_search(client, _h(access_token), q='ample go', types='dn'), 'dn', dn_id)
    assert item['matched_field'] == 'goods_name' and _highlighted(item) == ['ample Go']


def test_goods_in_details_hit_documents_and_tasks(client, access_token):
    result = _search(client, _h(access_token), q='Sample Goods')
    for type_ in ('dn', 'asn', 'sorting', 'picking', 'packing', 'delivery', 'cyclecount', 'adjustment'):
        group = _group(result, type_)
        assert group['total'] >= 1, type_
        item = group['items'][0]
        assert item['matched_field'] == 'goods_name', type_
        assert 'Sample Goods' in item['subtitle'] and _highlighted(item) == ['Sample Goods'], type_
    assert _group(result, 'goods')['items'][0]['matched_field'] == 'name'


def test_asn_order_number_tracking_and_supplier(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        asn.order_number = 'PO-778899'
        asn.tracking_number = 'JP 1234-5678-90'
        db.session.commit()
        asn_id = asn.id

    item = _item(_search(client, _h(access_token), q='po-7788'), 'asn', asn_id)
    assert item['matched_field'] == 'order_number' and _highlighted(item) == ['PO-7788']

    result = _search(client, _h(access_token), q='jp1234567890')
    item = _item(result, 'asn', asn_id)
    assert item['exact'] is True and item['matched_field'] == 'tracking_number'
    assert result['groups'][0]['type'] == 'asn' and _highlighted(item) == ['JP 1234-5678-90']

    item = _item(_search(client, _h(access_token), q='supplier a'), 'asn', asn_id)
    assert item['matched_field'] == 'supplier_name' and _highlighted(item, 'title') == ['Supplier A']


# ---------------------------------------------------------------------------------------------
# 主数据
# ---------------------------------------------------------------------------------------------

def test_goods_code_exact_then_prefix_then_name(client, access_token):
    with client.application.app_context():
        exact = _new_goods('ABC', 'Plain')
        prefix = _new_goods('ABC-1', 'Plain')
        by_name = _new_goods('ZZ-9', 'Widget abc')
        _new_goods('XABC', 'Other')            # 编码只认相同 / 前缀，包含不算

    result = _search(client, _h(access_token), q='abc', types='goods')
    group = _group(result, 'goods')
    assert [item['id'] for item in group['items']] == [exact, prefix, by_name]
    assert [item['matched_field'] for item in group['items']] == ['goods_code', 'goods_code', 'name']
    assert _highlighted(group['items'][0], 'code') == ['ABC']
    assert _highlighted(group['items'][1], 'code') == ['ABC']
    assert _highlighted(group['items'][2], 'title') == ['abc']
    # 编码完全相同（大小写不敏感）= exact：排在全部结果前面、也在 exact_matches 里；前缀 / 名称不是
    assert [item['exact'] for item in group['items']] == [True, False, False]
    assert [(item['type'], item['id']) for item in result['exact_matches']] == [('goods', exact)]
    result = _search(client, _h(access_token), q='ABC-1')
    assert result['groups'][0]['type'] == 'goods' and result['exact_matches'][0]['id'] == prefix
    # 1 个字符：只比完全相同，同样 exact
    with client.application.app_context():
        single = _new_goods('Q', 'Single')
    item = _item(_search(client, _h(access_token), q='q', types='goods'), 'goods', single)
    assert item['exact'] is True and item['matched_goods'] is None


def test_location_code(client, access_token):
    with client.application.app_context():
        ids = {location.code: location.id for location in Location.query.all()}

    result = _search(client, _h(access_token), q='loc00', types='location')
    assert sorted(_ids(result, 'location')) == sorted(ids.values())
    item = _item(_search(client, _h(access_token), q='C002'), 'location', ids['LOC002'])
    assert item['matched_field'] == 'location_code' and _highlighted(item, 'code') == ['C002']
    assert item['url_hint'] == {'page': 'location', 'id': ids['LOC002']}


def test_recipient_returns_only_name_and_city_level(client, access_token):
    with client.application.app_context():
        recipient = get_recipient()
        recipient.organization = 'Acme Trading'
        recipient.locality = 'Shibuya'
        recipient.admin_area = 'Tokyo'
        db.session.commit()
        recipient_id = recipient.id

    for params in ({}, {'types': 'recipient'}):
        response = client.get('/search', headers=_h(access_token), query_string={'q': 'acme', **params})
        assert response.status_code == 200
        item = _item(response.get_json(), 'recipient', recipient_id)
        assert item['matched_field'] == 'organization'
        assert item['title'] == 'Recipient A' and item['subtitle'] == 'Shibuya · Tokyo · us'
        assert item['code'] is None and item['url_hint'] == {'page': 'recipient', 'id': recipient_id}
        body = response.get_data(as_text=True)
        for secret in ('123 Recipient Street', 'recipientA@example.com', '123456789', 'Acme Trading', 'John Doe'):
            assert secret not in body, secret
    assert set(item['columns']) == {'name', 'locality', 'admin_area', 'country'}


# ---------------------------------------------------------------------------------------------
# 权限 / 隔离
# ---------------------------------------------------------------------------------------------

def test_types_without_read_permission_are_not_searched(client):
    with client.application.app_context():
        warehouse = get_warehouse()
        token = _staff_token('dn_goods_reader', warehouse.company_id,
                             _role('search_dn_goods', ['dn_read', 'goods_read']), [warehouse])
        nothing = _staff_token('no_reader', warehouse.company_id, _role('search_none', ['dn_edit']), [warehouse])
        dn_id = get_dn().id
        location_key = _api_key(warehouse.company_id, ['location_read'])
        warehouse_id = warehouse.id

    result = _search(client, _h(token, warehouse_id), q='sample')
    assert result['types'] == ['dn', 'goods'] and set(result['counts']) == {'dn', 'goods'}
    assert {group['type'] for group in result['groups']} == {'dn', 'goods'}
    # 指定了没权限的类型：同样不搜
    result = _search(client, _h(token, warehouse_id), q=f'DN-{dn_id}', types='dn,asn,picking')
    assert result['types'] == ['dn'] and _ids(result, 'dn') == [dn_id]
    assert _group(result, 'dn')['items'][0]['columns'] is None      # 请求的是多类：不因权限筛剩一类就变成分页模式

    # 一类都不能看：空结果，不 403
    result = _search(client, _h(nothing, warehouse_id), q='sample')
    assert result['types'] == [] and result['groups'] == [] and result['counts'] == {}

    # API Key 按 key 的权限
    result = _search(client, _h(key=location_key), q='LOC')
    assert result['types'] == ['location'] and _group(result, 'location')['total'] == 3
    result = _search(client, _h(key=TEST_API_KEY_PLAIN), q='sample')         # all_access
    assert result['types'] == list(SEARCH_TYPES)

    _search(client, {}, status=401, q='sample')


def test_company_isolation(client):
    with client.application.app_context():
        company_b = Company.query.filter_by(name='Company B').first()
        warehouse_b = Warehouse(name='Warehouse B', address='B', phone='1', zip_code='1', company_id=company_b.id,
                                created_by=get_admin_user().id)
        db.session.add(warehouse_b)
        db.session.commit()
        own_goods = _new_goods('B-SAMPLE', 'Sample B goods', company_id=company_b.id)
        token = _staff_token('company_b_admin', company_b.id, Role.query.filter_by(name='company_admin').first(),
                             [warehouse_b])
        key = _api_key(company_b.id, ['all_access'])
        dn_a = get_dn().id

    for headers in (_h(token), _h(key=key)):
        result = _search(client, headers, q='sample')
        assert _ids(result, 'goods') == [own_goods]
        assert all(group['total'] == 0 for group in result['groups'] if group['type'] != 'goods'), result['counts']
        result = _search(client, headers, q=f'DN-{dn_a}')
        assert result['counts']['dn'] == 0 and result['exact_matches'] == []
        assert _search(client, headers, q='recipient')['counts']['recipient'] == 0

    # 别家公司的仓库：同列表 403
    with client.application.app_context():
        warehouse_a = get_warehouse().id
    _search(client, _h(token, warehouse_a), status=403, q='sample')


def test_warehouse_scope_current_and_all(client, access_company_admin_token):
    with client.application.app_context():
        warehouse_a, warehouse_t = Warehouse.query.order_by(Warehouse.id).limit(2).all()
        dn_a = get_dn().id
        dn_t = _new_dn(warehouse_t.id, order_number='SCOPE-T')
        _new_dn(warehouse_a.id, order_number='SCOPE-A')
        role = _role('search_dn_reader', ['dn_read'])
        both = _staff_token('reader_both', warehouse_a.company_id, role, [warehouse_a, warehouse_t])
        only_a = _staff_token('reader_a', warehouse_a.company_id, role, [warehouse_a])
        a_id, t_id = warehouse_a.id, warehouse_t.id

    def warehouses(headers, **params):
        result = _search(client, headers, q='scope-', types='dn', **params)
        return sorted(item['warehouse_id'] for item in _group(result, 'dn')['items'])

    assert warehouses(_h(both, a_id)) == [a_id]
    assert warehouses(_h(both, t_id)) == [t_id]
    assert warehouses(_h(both, a_id), scope='all') == [a_id, t_id]
    assert warehouses(_h(only_a, a_id), scope='all') == [a_id]
    assert warehouses(_h(only_a), scope='all') == [a_id]          # scope=all 不需要当前仓库
    # 普通员工 scope=warehouse 必须给仓库（同列表）
    _search(client, _h(only_a), status=400, q='scope-', types='dn')
    # 公司管理员不给仓库 = 全公司仓库（同列表）；scope=all 不看当前仓库
    assert warehouses(_h(access_company_admin_token)) == [a_id, t_id]
    assert warehouses(_h(access_company_admin_token, a_id)) == [a_id]
    assert warehouses(_h(access_company_admin_token, a_id), scope='all') == [a_id, t_id]

    result = _search(client, _h(only_a, a_id), q=f'DN-{dn_t}', scope='all')
    assert result['counts']['dn'] == 0
    assert _ids(_search(client, _h(only_a, a_id), q=f'DN-{dn_a}'), 'dn') == [dn_a]
    result = _search(client, _h(only_a, a_id), status=400, q='x', scope='everywhere')
    assert (result['code'], result['field']) == (40000, 'scope')


# ---------------------------------------------------------------------------------------------
# 关键字处理 / 参数
# ---------------------------------------------------------------------------------------------

def test_like_wildcards_are_escaped(client, access_token):
    with client.application.app_context():
        percent = _new_goods('P-1', '100% Cotton')
        _new_goods('P-2', '1000 Cotton')
        underscore = _new_goods('A_1', 'Plain')
        _new_goods('AB1', 'Plain')
        _new_goods('A-X1', 'Plain!')

    assert _ids(_search(client, _h(access_token), q='100%', types='goods'), 'goods') == [percent]
    assert _ids(_search(client, _h(access_token), q='A_1', types='goods'), 'goods') == [underscore]
    assert _search(client, _h(access_token), q='%%', types='goods')['counts']['goods'] == 0
    assert _search(client, _h(access_token), q='n!', types='goods')['counts']['goods'] == 1


def test_short_query_matches_exact_numbers_and_codes_only(client, access_token):
    with client.application.app_context():
        one_char = _new_goods('Q', 'Q goods')
        dn_id = get_dn().id
        assert dn_id < 10

    result = _search(client, _h(access_token), q='G')
    assert result['counts']['goods'] == 0 and result['counts']['recipient'] == 0   # 不做前缀 / 包含
    assert _ids(_search(client, _h(access_token), q='q'), 'goods') == [one_char]
    result = _search(client, _h(access_token), q=str(dn_id))
    assert _ids(result, 'dn') == [dn_id] and _item(result, 'dn', dn_id)['matched_field'] == 'number'


def test_query_is_trimmed_and_cut(client, access_token):
    result = _search(client, _h(access_token), q='  ' + 'x' * 150 + '  ')
    assert result['q'] == 'x' * 100
    result = _search(client, _h(access_token), q='   ')
    assert result['q'] == '' and all(group['total'] == 0 for group in result['groups'])
    assert set(result['counts']) == set(SEARCH_TYPES) and set(result['counts'].values()) == {0}


def test_types_limit_and_single_type_paging(client, access_token):
    with client.application.app_context():
        created = [_new_goods(f'PG-{i:02d}', f'Paged {i}') for i in range(7)]

    result = _search(client, _h(access_token), q='pg-')
    group = _group(result, 'goods')
    assert group['total'] == 7 and len(group['items']) == 5 and group['per_page'] == 5   # 默认每类 5 条
    assert group['items'][0]['columns'] is None                                         # 多类型不带列表列
    assert set(result['counts']) == set(SEARCH_TYPES)

    result = _search(client, _h(access_token), q='pg-', types='goods', page=2, per_page=3)
    group = result['groups'][0]
    assert result['types'] == ['goods'] and set(result['counts']) == {'goods'}
    assert (group['page'], group['per_page'], group['total'], group['pages']) == (2, 3, 7, 3)
    assert [item['id'] for item in group['items']] == sorted(created, reverse=True)[3:6]
    assert set(group['items'][0]['columns']) >= {'code', 'name', 'brand', 'available_stock_for_sale', 'onhand_stock'}

    group = _search(client, _h(access_token), q='pg-', types='goods', page=9, per_page=3)['groups'][0]
    assert group['items'] == [] and group['total'] == 7

    # 重复给 / 逗号分隔都行；limit 上限 50
    result = _search(client, _h(access_token), q='pg-', types=['goods', 'location,dn'], limit=500)
    assert result['types'] == ['dn', 'goods', 'location'] and _group(result, 'goods')['per_page'] == 50

    result = _search(client, _h(access_token), status=400, q='pg-', types='goods,nope')
    assert (result['code'], result['field'], result['details']['unknown']) == (40000, 'types', ['nope'])

    # 不带尾斜杠 / 带尾斜杠都行
    assert client.get('/search/?q=pg-', headers=_h(access_token)).status_code == 200


def test_single_type_columns(client, access_token):
    with client.application.app_context():
        location = Location.query.filter_by(code='LOC001').one()
        goods = get_goods()
        picking = get_picking_task()
        inventory = Inventory.query.filter_by(goods_id=goods.id, warehouse_id=location.warehouse_id).one()
        expected_stock = (inventory.available_stock_for_sale, inventory.onhand_stock)
        quantities = [gl.quantity for gl in GoodsLocation.query.filter_by(location_id=location.id).all()]
        ids = {'location': location.id, 'goods': goods.id, 'picking': picking.id, 'dn': picking.dn_id,
               'goods_code': goods.code}

    columns = _item(_search(client, _h(access_token), q='LOC001', types='location'), 'location',
                    ids['location'])['columns']
    assert (columns['goods_count'], columns['quantity']) == (len(quantities), sum(quantities))
    assert columns['location_type'] == 'standard' and columns['warehouse_name'] == 'Warehouse A'

    columns = _item(_search(client, _h(access_token), q=ids['goods_code'], types='goods'), 'goods',
                    ids['goods'])['columns']
    assert (columns['available_stock_for_sale'], columns['onhand_stock']) == expected_stock

    item = _item(_search(client, _h(access_token), q=f"PICK-{ids['picking']}", types='picking'), 'picking',
                 ids['picking'])
    assert item['columns']['task_type'] == 'picking' and item['columns']['related_code'] == f"DN-{ids['dn']}"
    assert item['columns']['related_url_hint'] == {'page': 'dn', 'id': ids['dn']}

    item = _item(_search(client, _h(access_token), q=f"DN-{ids['dn']}", types='dn'), 'dn', ids['dn'])
    assert set(item['columns']) >= {'recipient_name', 'carrier_name', 'tracking_number', 'status',
                                    'expected_shipping_date', 'order_number'}
    assert item['columns']['carrier_name'] == 'Test Carrier' and item['columns']['status'] == item['status']


def test_highlight_offsets_are_utf16(client, access_token):
    with client.application.app_context():
        goods_id = _new_goods('EMO-1', '😀抹茶ラテ')

    item = _item(_search(client, _h(access_token), q='抹茶', types='goods'), 'goods', goods_id)
    assert item['highlights'] == [{'field': 'title', 'start': 2, 'end': 4},
                                  {'field': 'columns.name', 'start': 2, 'end': 4}]


# ---------------------------------------------------------------------------------------------
# 发货任务的运单号、明细商品的 matched_goods
# ---------------------------------------------------------------------------------------------

def test_delivery_task_tracking_number_matches_like_the_dn(client, access_token):
    with client.application.app_context():
        task = DeliveryTask.query.filter_by(tracking_number='DT-TEST-001').one()
        task.tracking_number = 'AB 7946-0000-01'
        db.session.commit()
        task_id, dn_id = task.id, task.dn_id

    # 完整号码（去空白 / 连字符）：DN 与发货任务都 exact
    result = _search(client, _h(access_token), q='ab7946000001')
    item = _item(result, 'delivery', task_id)
    assert item['exact'] is True and item['matched_field'] == 'tracking_number'
    assert _highlighted(item) == ['AB 7946-0000-01']
    assert {(x['type'], x['id']) for x in result['exact_matches']} >= {('dn', dn_id), ('delivery', task_id)}
    # 6 位以上可以只输一段；太短只认完整号码
    item = _item(_search(client, _h(access_token), q='946000', types='delivery'), 'delivery', task_id)
    assert item['exact'] is False and item['matched_field'] == 'tracking_number' and _highlighted(item) == ['946-000']
    assert task_id not in _ids(_search(client, _h(access_token), q='AB79', types='delivery'), 'delivery')

    # 任务上还没有运单号：按 DN 的当前运单号（有效的自动 / 手工运单），副标题显示它
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).tracking_number = None
        db.session.add(DNCarrierShipment(dn_id=dn_id, source='manual', status='active', tracking_number='7700 1122 33'))
        db.session.commit()
    item = _item(_search(client, _h(access_token), q='770011223', types='delivery'), 'delivery', task_id)
    assert item['matched_field'] == 'tracking_number' and _highlighted(item) == ['7700 1122 3']
    item = _item(_search(client, _h(access_token), q='7700-1122-33', types='delivery'), 'delivery', task_id)
    assert item['exact'] is True


def test_matched_goods_of_documents_and_tasks(client, access_token):
    with client.application.app_context():
        picking = get_picking_task()
        dn = picking.dn
        goods = Goods.query.filter_by(code='G001').first()
        db.session.add(DNDetail(dn_id=dn.id, goods_id=goods.id, quantity=4, created_by=get_admin_user().id))
        db.session.commit()
        quantity = sum(detail.quantity for detail in dn.details if detail.goods_id == goods.id)
        expected = {'goods_id': goods.id, 'code': 'G001', 'name': 'Sample Goods', 'quantity': quantity}
        dn_id, picking_id = dn.id, picking.id

    result = _search(client, _h(access_token), q='g001', types='dn,picking')
    item = _item(result, 'dn', dn_id)
    assert item['matched_field'] == 'goods_code' and item['matched_goods'] == expected
    assert 'G001 Sample Goods' in item['subtitle']                    # 副标题照旧
    assert _item(result, 'picking', picking_id)['matched_goods'] == expected
    # 按名称命中同样给；按单号命中、明细商品不含关键字 → null
    assert _item(_search(client, _h(access_token), q='sample goods', types='dn'), 'dn', dn_id)['matched_goods'][
        'goods_id'] == goods.id
    item = _item(_search(client, _h(access_token), q=f'DN-{dn_id}', types='dn'), 'dn', dn_id)
    assert item['matched_goods'] is None

    # 其余单据 / 任务：都有 matched_goods，数量为整数
    result = _search(client, _h(access_token), q='Sample Goods')
    for type_ in ('asn', 'sorting', 'packing', 'delivery', 'cyclecount', 'adjustment'):
        matched = _group(result, type_)['items'][0]['matched_goods']
        assert matched and matched['name'] == 'Sample Goods' and isinstance(matched['quantity'], int), type_
    assert _group(result, 'goods')['items'][0]['matched_goods'] is None
