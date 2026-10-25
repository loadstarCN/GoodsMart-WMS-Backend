"""破损品上架：待上架·破损（inventory.sorted_damage_stock，doc/库存数值系统说明.md）

- 分拣完成时破损数量同时记进 sorted_stock 与 sorted_damage_stock（不变式 0 ≤ sorted_damage_stock ≤ sorted_stock）
- 上到 standard / return 库位只能用良品部分（sorted_stock − sorted_damage_stock），超出 → 400 15016，
  details {good_pending, damaged_pending}；批量上架整批不落库
- 上到 damaged 库位先扣破损部分，其余扣良品部分；damage_stock = 破损库位上的数量
- 退回待上架的（少打包、DN 取消）都是良品；从 damaged 库位下架、因破损下架的回到待上架·破损
- 库存列表 / 详情、入库流程的上架一步都带 sorted_damage_stock
"""
import pytest

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _bin, _create_asn, _err, _h, _ok, _picked_dn, _task_ids, _world

FIELDS = ('total_stock', 'onhand_stock', 'damage_stock', 'return_stock', 'sorted_stock', 'sorted_damage_stock',
          'picked_stock', 'packed_stock', 'delivered_stock')


def _inv(client, goods_id, warehouse_id):
    with client.application.app_context():
        inventory = db.session.get(Inventory, (goods_id, warehouse_id))
        return {k: getattr(inventory, k) for k in FIELDS}


def _assert_invariants(stock):
    """各字段 ≥ 0；0 ≤ 待上架·破损 ≤ 待上架；total_stock = 各实物分量之和"""
    assert all(stock[k] >= 0 for k in FIELDS), stock
    assert stock['sorted_damage_stock'] <= stock['sorted_stock'], stock
    assert stock['total_stock'] == (stock['onhand_stock'] + stock['damage_stock'] + stock['return_stock']
                                    + stock['sorted_stock'] + stock['picked_stock'] + stock['packed_stock']), stock


def _locations(client, w):
    """Warehouse A 下再建一个破损库位、一个退货库位"""
    with client.application.app_context():
        damaged = Location(warehouse_id=w['wh'], code='DMG-1', location_type='damaged', created_by=w['admin'])
        returned = Location(warehouse_id=w['wh'], code='RET-1', location_type='return', created_by=w['admin'])
        db.session.add_all([damaged, returned])
        db.session.commit()
        return {**w, 'dmg': damaged.id, 'ret': returned.id}


def _sort_in(client, token, w, goods_id, good, damaged):
    """ASN 收货 → 分拣（good 件合格 + damaged 件破损）→ 完成，返回 asn_id"""
    asn_id = _create_asn(client, token, w, [(goods_id, good + damaged)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(token)))
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': goods_id, 'sorted_quantity': good, 'damage_quantity': damaged}]}, headers=_h(token)))
    _ok(client.put(f'/sorting/{task_id}/complete/', headers=_h(token)))
    return asn_id


def _putaway(client, token, goods_id, location_id, quantity):
    return client.post('/putaway/', json={'goods_id': goods_id, 'location_id': location_id, 'quantity': quantity},
                       headers=_h(token))


def test_damaged_goods_only_go_to_damaged_locations(client, access_token):
    w = _locations(client, _world(client))
    g = w['g1']
    asn_id = _sort_in(client, access_token, w, g, good=7, damaged=3)

    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['total_stock']) == (10, 3, 10)
    _assert_invariants(stock)

    # 库存详情 / 列表、入库流程的上架一步都带待上架·破损
    detail = _ok(client.get(f"/inventory/goods/{g}/warehouse/{w['wh']}", headers=_h(access_token)))
    assert (detail['sorted_stock'], detail['sorted_damage_stock']) == (10, 3)
    items = _ok(client.get(f'/inventory/?goods_id={g}', headers=_h(access_token)))['items']
    assert [(i['sorted_stock'], i['sorted_damage_stock']) for i in items] == [(10, 3)]
    putaway_step = next(s for s in _ok(client.get(f'/asn/{asn_id}/flow', headers=_h(access_token)))['steps']
                        if s['key'] == 'putaway')
    assert [(r['goods_id'], r['sorted_stock'], r['sorted_damage_stock']) for r in putaway_step['summary']['goods']] \
        == [(g, 10, 3)]

    # 普通库位上架超出良品数 → 400 15016（英文 message，details 带良品 / 破损待上架数），库存与库位都不动
    body = _err(_putaway(client, access_token, g, w['std1'], 8), 400, 15016)
    assert body['details']['good_pending'] == 7 and body['details']['damaged_pending'] == 3
    assert body['message'].isascii()
    _err(client.post('/putaway/bulk', json=[
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 5},
        {'goods_id': g, 'location_id': w['std2'], 'quantity': 5},
    ], headers=_h(access_token)), 400, 15016)
    assert _inv(client, g, w['wh']) == stock
    assert (_bin(client, g, w['std1']), _bin(client, g, w['std2'])) == (0, 0)

    # 良品上普通库位
    _ok(_putaway(client, access_token, g, w['std1'], 7))
    stock = _inv(client, g, w['wh'])
    assert (stock['onhand_stock'], stock['sorted_stock'], stock['sorted_damage_stock']) == (7, 3, 3)
    _assert_invariants(stock)

    # 剩下的都是破损：退货库位也不行
    body = _err(_putaway(client, access_token, g, w['ret'], 1), 400, 15016)
    assert (body['details']['good_pending'], body['details']['damaged_pending']) == (0, 3)

    # 破损库位上架成功：damage_stock = 破损库位上的数量
    _ok(_putaway(client, access_token, g, w['dmg'], 3))
    stock = _inv(client, g, w['wh'])
    assert stock == {'total_stock': 10, 'onhand_stock': 7, 'damage_stock': 3, 'return_stock': 0, 'sorted_stock': 0,
                     'sorted_damage_stock': 0, 'picked_stock': 0, 'packed_stock': 0, 'delivered_stock': 0}
    assert _bin(client, g, w['dmg']) == 3

    # 待上架总量不够仍是 15007
    _err(_putaway(client, access_token, g, w['dmg'], 1), 400, 15007)


def test_damaged_location_takes_damaged_part_first(client, access_token):
    """上到破损库位：先扣待上架·破损，其余扣良品部分（良品也可以上破损库位）"""
    w = _locations(client, _world(client))
    g = w['g1']
    _sort_in(client, access_token, w, g, good=7, damaged=3)

    _ok(_putaway(client, access_token, g, w['dmg'], 5))     # 3 件破损 + 2 件良品
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['damage_stock']) == (5, 0, 5)
    _assert_invariants(stock)

    _ok(_putaway(client, access_token, g, w['ret'], 2))     # 剩下全是良品：退货 / 普通库位都可以
    _ok(_putaway(client, access_token, g, w['std1'], 3))
    stock = _inv(client, g, w['wh'])
    assert (stock['onhand_stock'], stock['return_stock'], stock['damage_stock'], stock['sorted_stock']) == (3, 2, 5, 0)
    _assert_invariants(stock)


def test_goods_returned_to_putaway_are_good(client, access_token):
    """少打包退回、DN 取消退回待上架的都是良品：不动待上架·破损，可以上普通库位"""
    w = _locations(client, _world(client))
    g = w['g1']
    _sort_in(client, access_token, w, g, good=10, damaged=2)
    _ok(_putaway(client, access_token, g, w['std1'], 10))
    assert _inv(client, g, w['wh'])['sorted_damage_stock'] == 2

    # 拣 5 打 3：2 件良品退回待上架
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': g, 'packed_quantity': 3}]},
                    headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['packed_stock']) == (4, 2, 3)
    _assert_invariants(stock)

    # 已拣（未打包）的 DN 取消：2 件良品退回待上架
    dn2, _packing2 = _picked_dn(client, access_token, w, [(g, 2)], [(g, w['std1'], 2)])
    _ok(client.put(f'/dn/{dn2}/cancel/', headers=_h(access_token)))
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['picked_stock']) == (6, 2, 0)
    _assert_invariants(stock)

    # 良品 4 件能上普通库位，第 5 件就超了
    body = _err(_putaway(client, access_token, g, w['std1'], 5), 400, 15016)
    assert (body['details']['good_pending'], body['details']['damaged_pending']) == (4, 2)
    _ok(_putaway(client, access_token, g, w['std1'], 4))
    _ok(_putaway(client, access_token, g, w['dmg'], 2))
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['damage_stock']) == (0, 0, 2)
    _assert_invariants(stock)


def test_removed_damaged_goods_stay_damaged(client, access_token):
    """从破损库位下架、或因破损（damage / damaged）下架的回到待上架·破损；其它原因的下架按良品"""
    w = _locations(client, _world(client))
    g = w['g1']
    _sort_in(client, access_token, w, g, good=8, damaged=2)
    _ok(_putaway(client, access_token, g, w['std1'], 8))
    _ok(_putaway(client, access_token, g, w['dmg'], 2))

    def remove(location_id, quantity, reason):
        return _ok(client.post('/removal/', json={'goods_id': g, 'location_id': location_id, 'quantity': quantity,
                                                  'reason': reason}, headers=_h(access_token)))

    remove(w['dmg'], 1, 'transfer')         # 破损库位下来的还是破损
    remove(w['std1'], 2, 'damage')          # 因破损下架
    remove(w['std1'], 1, 'damaged')
    remove(w['std1'], 2, 'other')           # 其它原因：良品
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['damage_stock'], stock['onhand_stock']) \
        == (6, 4, 1, 3)
    _assert_invariants(stock)

    _err(_putaway(client, access_token, g, w['std2'], 3), 400, 15016)
    _ok(_putaway(client, access_token, g, w['std2'], 2))
    _ok(_putaway(client, access_token, g, w['dmg'], 4))
    stock = _inv(client, g, w['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['damage_stock'], stock['onhand_stock']) \
        == (0, 0, 5, 5)
    _assert_invariants(stock)


def test_inventory_service_keeps_sorted_damage_invariant(client):
    """服务层：分拣完成的破损数不超过实收；上架扣减按库位类型；generic 更新也夹到 [0, sorted_stock]"""
    from extensions.error import APIException
    w = _world(client)
    g = w['g1']
    with client.application.app_context():
        db.session.add(Inventory(goods_id=g, warehouse_id=w['wh'], received_stock=10))
        db.session.commit()
        InventoryService.asn_completed(g, w['wh'], 10, 4, 9)         # 破损记不过实收
        inv = db.session.get(Inventory, (g, w['wh']))
        assert (inv.sorted_stock, inv.sorted_damage_stock) == (4, 4)

        with pytest.raises(APIException) as exc:
            InventoryService.putaway_completed(g, w['wh'], 1)         # 缺省按 standard
        assert exc.value.biz_code == 15016
        assert exc.value.details['good_pending'] == 0 and exc.value.details['damaged_pending'] == 4
        with pytest.raises(APIException) as exc:
            InventoryService.putaway_completed(g, w['wh'], 5, 'damaged')
        assert exc.value.biz_code == 15007

        InventoryService.putaway_completed(g, w['wh'], 3, 'damaged')
        inv = db.session.get(Inventory, (g, w['wh']))
        assert (inv.sorted_stock, inv.sorted_damage_stock) == (1, 1)

        InventoryService.update_inventory(g, w['wh'], {'sorted_stock': 0})
        assert db.session.get(Inventory, (g, w['wh'])).sorted_damage_stock == 0


# ---------------------------------------------------------------------------
# 待上架列表（GET /inventory/?pending_putaway=true）与汇总（GET /inventory/pending-putaway/summary）
# ---------------------------------------------------------------------------

OVERDUE_KEYS = ('overdue_days', 'overdue_quantity', 'overdue_inventory_count', 'oldest_pending_since')


def _summary(client, token, full=False, **params):
    """汇总接口；full=False 时去掉超期相关的键（另有用例专门测）"""
    headers = _h(token)
    if 'warehouse' in params:
        headers['X-WAREHOUSE-ID'] = str(params.pop('warehouse'))
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    body = _ok(client.get(f'/inventory/pending-putaway/summary?{query}', headers=headers))
    if not full:
        assert set(OVERDUE_KEYS) <= set(body), body
        body = {k: v for k, v in body.items() if k not in OVERDUE_KEYS}
    return body


def test_pending_putaway_list_and_summary(client, access_token):
    w = _locations(client, _world(client))
    _sort_in(client, access_token, w, w['g1'], good=7, damaged=3)
    _sort_in(client, access_token, w, w['g2'], good=4, damaged=0)
    with client.application.app_context():
        other_warehouse = Warehouse.query.filter_by(name='Warehouse Test').first().id
        other_company = Company.query.filter_by(name='Company B').first().id
        company = db.session.get(Warehouse, w['wh']).company_id

    items = _ok(client.get('/inventory/?pending_putaway=true&per_page=50', headers=_h(access_token)))['items']
    assert {i['goods_id']: (i['sorted_stock'], i['sorted_damage_stock']) for i in items} == {
        w['g1']: (10, 3), w['g2']: (4, 0)}
    everything = _ok(client.get('/inventory/?pending_putaway=false&per_page=50', headers=_h(access_token)))
    assert everything['total'] > len(items)

    expected = {'inventory_count': 2, 'goods_count': 2, 'total_quantity': 14, 'damaged_quantity': 3,
                'good_quantity': 11}
    assert _summary(client, access_token) == {'warehouse_id': None, 'company_id': None, **expected}
    assert _summary(client, access_token, warehouse=w['wh']) == {'warehouse_id': w['wh'], 'company_id': None,
                                                                 **expected}
    assert _summary(client, access_token, warehouse_id=w['wh'])['total_quantity'] == 14
    assert _summary(client, access_token, company_id=company)['inventory_count'] == 2
    empty = {'inventory_count': 0, 'goods_count': 0, 'total_quantity': 0, 'damaged_quantity': 0, 'good_quantity': 0}
    assert _summary(client, access_token, warehouse=other_warehouse) == {
        'warehouse_id': other_warehouse, 'company_id': None, **empty}
    assert _summary(client, access_token, company_id=other_company) == {
        'warehouse_id': None, 'company_id': other_company, **empty}

    # 上完商品 2：只剩商品 1
    _ok(_putaway(client, access_token, w['g2'], w['std1'], 4))
    items = _ok(client.get(f"/inventory/?pending_putaway=true&warehouse_id={w['wh']}",
                           headers=_h(access_token)))['items']
    assert [i['goods_id'] for i in items] == [w['g1']]
    assert _summary(client, access_token, warehouse=w['wh']) == {
        'warehouse_id': w['wh'], 'company_id': None, 'inventory_count': 1, 'goods_count': 1, 'total_quantity': 10,
        'damaged_quantity': 3, 'good_quantity': 7}


def test_pending_putaway_summary_for_putaway_operators(client, access_operator_token):
    """只有上架权限（putaway_read）的员工也能看汇总，范围是自己可访问的仓库（要带仓库）；没有权限 403"""
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        inventory = db.session.get(Inventory, (get_goods().id, warehouse_id))
        inventory.sorted_stock, inventory.sorted_damage_stock = 5, 2
        db.session.commit()

    response = client.get('/inventory/pending-putaway/summary',
                          headers={**_h(access_operator_token), 'X-WAREHOUSE-ID': str(warehouse_id)})
    assert response.status_code == 403

    with client.application.app_context():
        permission = Permission.query.filter_by(name='putaway_read').first() or Permission(name='putaway_read')
        role = Role(name='putaway_clerk', is_active=True)
        role.permissions.append(permission)
        operator = User.query.filter_by(user_name='operator').first()
        operator.roles.append(role)
        db.session.commit()

    summary = _summary(client, access_operator_token, warehouse=warehouse_id)
    assert (summary['warehouse_id'], summary['total_quantity'], summary['damaged_quantity'],
            summary['good_quantity'], summary['goods_count']) == (warehouse_id, 5, 2, 3, 1)
    # 普通员工不带仓库 → 与其它仓库接口一样要求带仓库
    assert client.get('/inventory/pending-putaway/summary', headers=_h(access_operator_token)).status_code == 400


# ---------------------------------------------------------------------------
# 待上架的停留时间：pending_since（先进先上）与汇总的超期件数
# ---------------------------------------------------------------------------

def _set_completed_at(client, asn_id, at):
    with client.application.app_context():
        db.session.get(ASN, asn_id).completed_at = at
        db.session.commit()


def _pending_row(client, token, goods_id, warehouse_id):
    items = _ok(client.get(f'/inventory/?pending_putaway=true&goods_id={goods_id}&warehouse_id={warehouse_id}',
                           headers=_h(token)))['items']
    return items[0] if items else None


def _parse(value):
    from datetime import datetime as _datetime
    assert value.endswith('+00:00'), value          # UTC、带时区（与其它接口一致）
    return _datetime.fromisoformat(value)


def test_pending_since_moves_forward_as_goods_are_put_away(client, access_token):
    """多次分拣：pending_since = 凑够当前待上架的最早一笔入账；部分上架后前移；少打包 / 取消退回也算入账"""
    from datetime import timedelta
    from warehouse.common import clock
    w = _locations(client, _world(client))
    g = w['g1']
    now = clock.now().replace(microsecond=0)
    t_old, t_mid, t_new = now - timedelta(days=5), now - timedelta(days=2), now - timedelta(hours=1)
    _set_completed_at(client, _sort_in(client, access_token, w, g, good=4, damaged=0), t_old)
    _set_completed_at(client, _sort_in(client, access_token, w, g, good=3, damaged=0), t_mid)
    _set_completed_at(client, _sort_in(client, access_token, w, g, good=2, damaged=0), t_new)

    row = _pending_row(client, access_token, g, w['wh'])
    assert row['sorted_stock'] == 9 and _parse(row['pending_since']) == t_old
    detail = _ok(client.get(f"/inventory/goods/{g}/warehouse/{w['wh']}", headers=_h(access_token)))
    assert _parse(detail['pending_since']) == t_old
    summary = _summary(client, access_token, full=True, warehouse=w['wh'])
    # 超过 3 天的：5 天前那笔 4 件（2 天前 3 件 + 1 小时前 2 件没超期）
    assert (summary['overdue_days'], summary['overdue_quantity'], summary['overdue_inventory_count']) == (3, 4, 1)
    assert _parse(summary['oldest_pending_since']) == t_old
    one_day = _summary(client, access_token, full=True, warehouse=w['wh'], overdue_days=1)
    assert (one_day['overdue_days'], one_day['overdue_quantity']) == (1, 7)
    response = client.get('/inventory/pending-putaway/summary?overdue_days=-1', headers=_h(access_token))
    assert response.status_code == 400

    # 先进先上：上架 4 件后剩下的是 2 天前和 1 小时前的，pending_since 前移
    _ok(_putaway(client, access_token, g, w['std1'], 4))
    row = _pending_row(client, access_token, g, w['wh'])
    assert row['sorted_stock'] == 5 and _parse(row['pending_since']) == t_mid
    summary = _summary(client, access_token, full=True, warehouse=w['wh'])
    assert (summary['overdue_quantity'], summary['overdue_inventory_count']) == (0, 0)
    assert _parse(summary['oldest_pending_since']) == t_mid

    _ok(_putaway(client, access_token, g, w['std1'], 4))
    assert _parse(_pending_row(client, access_token, g, w['wh'])['pending_since']) == t_new

    # 少打包退回（打包完成时刻）、已拣 DN 取消退回（取消时刻）也是入账
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 3)], [(g, w['std1'], 3)])
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': g, 'packed_quantity': 1}]},
                    headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(access_token)))
    with client.application.app_context():
        db.session.get(DN, dn_id).packed_at = now - timedelta(minutes=30)
        db.session.commit()
    _ok(_putaway(client, access_token, g, w['std1'], 1))         # 1 小时前那 1 件上架 → 只剩少打包退回的 2 件
    row = _pending_row(client, access_token, g, w['wh'])
    assert row['sorted_stock'] == 2 and _parse(row['pending_since']) == now - timedelta(minutes=30)

    dn2, _packing2 = _picked_dn(client, access_token, w, [(g, 1)], [(g, w['std1'], 1)])
    _ok(client.put(f'/dn/{dn2}/cancel/', headers=_h(access_token)))
    with client.application.app_context():
        closed_at = db.session.get(DN, dn2).closed_at
    _ok(_putaway(client, access_token, g, w['std1'], 2))         # 先进的 2 件上架 → 只剩取消退回的 1 件
    row = _pending_row(client, access_token, g, w['wh'])
    assert row['sorted_stock'] == 1 and _parse(row['pending_since']) == closed_at

    # 全部上架：列表里没有这一行，详情的 pending_since 为空
    _ok(_putaway(client, access_token, g, w['std1'], 1))
    assert _pending_row(client, access_token, g, w['wh']) is None
    detail = _ok(client.get(f"/inventory/goods/{g}/warehouse/{w['wh']}", headers=_h(access_token)))
    assert detail['sorted_stock'] == 0 and detail['pending_since'] is None


def test_pending_since_counts_removals_and_unknown_history(client, access_token):
    """下架（拣货下架除外）也是入账；入账不够当前待上架（旧数据）时按最早一笔；完全没有入账为 null（全算超期）"""
    from datetime import timedelta
    from warehouse.common import clock
    w = _locations(client, _world(client))
    g1, g2 = w['g1'], w['g2']
    now = clock.now().replace(microsecond=0)
    _set_completed_at(client, _sort_in(client, access_token, w, g1, good=5, damaged=0), now - timedelta(days=10))
    _ok(_putaway(client, access_token, g1, w['std1'], 5))
    _ok(client.post('/removal/', json={'goods_id': g1, 'location_id': w['std1'], 'quantity': 2, 'reason': 'other'},
                    headers=_h(access_token)))
    with client.application.app_context():
        removed_at = RemovalRecord.query.filter_by(goods_id=g1, reason='other').one().removal_time
        inventory = db.session.get(Inventory, (g1, w['wh']))
        assert inventory.sorted_stock == 2
        inventory.sorted_stock = 4        # 旧数据：比已知入账多 2 件（不知道什么时候进来的）
        inventory.total_stock += 2
        db.session.add(Inventory(goods_id=g2, warehouse_id=w['wh'], sorted_stock=3, total_stock=3))
        db.session.commit()

    row = _pending_row(client, access_token, g1, w['wh'])
    # 下架 2 件 + 10 天前分拣的 5 件里凑 2 件 → 最早一笔是 10 天前
    assert _parse(row['pending_since']) == now - timedelta(days=10)
    with client.application.app_context():
        db.session.get(Inventory, (g1, w['wh'])).sorted_stock = 2
        db.session.commit()
    assert _parse(_pending_row(client, access_token, g1, w['wh'])['pending_since']).replace(microsecond=0) \
        == removed_at.replace(microsecond=0)
    # 列表统一 skip_none：pending_since 为 null 时不出现在行里
    assert _pending_row(client, access_token, g2, w["wh"]).get("pending_since") is None

    summary = _summary(client, access_token, full=True, warehouse=w['wh'])
    # 商品 1 的 2 件是刚下架的（没超期）；商品 2 的 3 件来历不明，算超期
    assert (summary['total_quantity'], summary['overdue_quantity'], summary['overdue_inventory_count']) == (5, 3, 1)
