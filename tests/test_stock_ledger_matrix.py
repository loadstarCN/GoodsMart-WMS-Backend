"""库存流水：逐入口矩阵测试 + 扫描代码的元测试（doc/库存数值系统说明.md 第九节「防漏网」）。

- 矩阵：逐个调用改实物库存的入口（含批量接口），核对流水的变动类型、单据（doc_type / doc_id / doc_line_id）、
  桶与数量、同一事务共用 trace_id；对账由 tests/conftest.py 的夹具在每条测试结束时自动做
- 元测试：AST 扫描 warehouse/ system/ tasks/ 下所有构造 StockOp 的地方，必须与 ENTRY_POINTS 一一对应，且每个入口
  在本文件有对应的矩阵用例（新增改库存的入口必须登记）；禁止在 warehouse/stock/ 以外给受保护的库存列赋值 / 增减、
  构造 GoodsLocation / InventoryLotStock / StockLedger、用原生 SQL 改这几张表
"""
import ast
import os
import re


from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _create_asn, _err, _h, _inbound, _ok, _picked_dn, _task_ids, _world
from warehouse.stock.guard import INVENTORY_PROTECTED_COLUMNS
from warehouse.stock.models import StockLedger

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 所有构造 StockOp 的地方（文件:函数 → 变动类型）及测它的用例
ENTRY_POINTS = {
    'warehouse/asn/services.py:complete_asn': ({'sort_in'}, 'test_entry_sort_in'),
    'warehouse/putaway/services.py:create_putaway_record': ({'putaway'}, 'test_entry_putaway_single_and_bulk'),
    'warehouse/removal/services.py:create_removal_record': ({'removal'}, 'test_entry_removal_single_and_bulk'),
    'warehouse/picking/services.py:complete_task': ({'pick'}, 'test_entry_pick'),
    'warehouse/transfer/services.py:create_transfer_record': ({'transfer'}, 'test_entry_transfer_single_and_bulk'),
    'warehouse/adjustment/services.py:complete_adjustment': ({'adjust_gain', 'adjust_loss'}, 'test_entry_adjustment'),
    'warehouse/dn/services.py:packing_dn': ({'pack', 'pack_short_return'}, 'test_entry_pack_and_short_return'),
    'warehouse/dn/services.py:_cancel_after_picking': ({'dn_cancel_return'}, 'test_entry_dn_cancel_return'),
    'warehouse/dn/services.py:delivery_dn': ({'ship'}, 'test_entry_ship_and_sign'),
    'warehouse/dn/services.py:complete_dn': ({'sign'}, 'test_entry_ship_and_sign'),
    'warehouse/inventory/services.py:asn_completed': ({'sort_in'}, 'test_entry_inventory_service_fallbacks'),
    'warehouse/inventory/services.py:dn_packed': ({'pack', 'pack_short_return'},
                                                  'test_entry_inventory_service_fallbacks'),
    'warehouse/inventory/services.py:dn_cancelled_after_picking': ({'dn_cancel_return'},
                                                                   'test_entry_inventory_service_fallbacks'),
    'warehouse/inventory/services.py:dn_delivered': ({'ship'}, 'test_entry_inventory_service_fallbacks'),
    'warehouse/inventory/services.py:dn_completed': ({'sign'}, 'test_entry_inventory_service_fallbacks'),
}


def _lines(client, **filters):
    with client.application.app_context():
        query = StockLedger.query.filter(StockLedger.movement_type != 'opening')
        for key, value in filters.items():
            query = query.filter(getattr(StockLedger, key) == value)
        return [{c: getattr(line, c) for c in ('id', 'goods_id', 'location_id', 'stock_type', 'quantity',
                                                'balance_after', 'counter_stock_type', 'movement_type', 'doc_type',
                                                'doc_id', 'doc_no', 'doc_line_id', 'reason', 'source', 'trace_id',
                                                'operator_id', 'remark', 'occurred_at', 'posted_at')}
                for line in query.order_by(StockLedger.id).all()]


def _legs(lines):
    return sorted((l['stock_type'], l['location_id'], l['quantity']) for l in lines)


def test_entry_sort_in(client, access_token):
    w = _world(client)
    g = w['g1']
    asn_id = _create_asn(client, access_token, w, [(g, 10)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(access_token)))
    task_id = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)[0]
    _ok(client.put(f'/sorting/{task_id}/process/', headers=_h(access_token)))
    _ok(client.post(f'/sorting/{task_id}/batches/', json={'details': [
        {'goods_id': g, 'sorted_quantity': 7, 'damage_quantity': 3}]}, headers=_h(access_token)))
    _ok(client.put(f'/sorting/{task_id}/complete/', headers=_h(access_token)))

    lines = _lines(client, doc_type='asn', doc_id=asn_id)
    assert {l['movement_type'] for l in lines} == {'sort_in'}
    assert _legs(lines) == [('pending', None, 7), ('pending_damaged', None, 3)]
    with client.application.app_context():
        detail_id = ASNDetail.query.filter_by(asn_id=asn_id, goods_id=g).one().id
        admin_id = get_admin_user().id
    assert {l['doc_line_id'] for l in lines} == {detail_id}
    assert {l['counter_stock_type'] for l in lines} == {None}
    assert len({l['trace_id'] for l in lines}) == 1
    assert {l['source'] for l in lines} == {'web'} and {l['operator_id'] for l in lines} == {admin_id}
    assert [l['balance_after'] for l in sorted(lines, key=lambda l: l['stock_type'])] == [7, 3]


def test_entry_putaway_single_and_bulk(client, access_token):
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    _inbound(client, access_token, w, [(g1, 10), (g2, 6)], [])
    record = _ok(client.post('/putaway/', json={'goods_id': g1, 'location_id': w['std1'], 'quantity': 4},
                             headers=_h(access_token)))
    lines = _lines(client, doc_type='putaway', doc_id=record['id'])
    assert {l['movement_type'] for l in lines} == {'putaway'}
    assert _legs(lines) == [('onhand', w['std1'], 4), ('pending', None, -4)]
    assert {l['counter_stock_type'] for l in lines} == {'onhand', 'pending'}
    assert [l['balance_after'] for l in lines] == [6, 4]

    records = _ok(client.post('/putaway/bulk', json=[
        {'goods_id': g2, 'location_id': w['std2'], 'quantity': 6},
        {'goods_id': g1, 'location_id': w['std2'], 'quantity': 6},
    ], headers={**_h(access_token), 'X-Client-App': 'pda/1.4.1'}))
    bulk = [l for r in records for l in _lines(client, doc_type='putaway', doc_id=r['id'])]
    assert len(bulk) == 4 and len({l['trace_id'] for l in bulk}) == 1     # 一个事务一个 trace_id
    assert {l['source'] for l in bulk} == {'pda'}


def test_entry_removal_single_and_bulk(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    record = _ok(client.post('/removal/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 2,
                                                'reason': 'damage', 'remark': 'box crushed'},
                             headers=_h(access_token)))
    lines = _lines(client, doc_type='removal', doc_id=record['id'])
    assert {l['movement_type'] for l in lines} == {'removal'}
    assert _legs(lines) == [('onhand', w['std1'], -2), ('pending_damaged', None, 2)]
    assert {l['reason'] for l in lines} == {'damage'} and {l['remark'] for l in lines} == {'box crushed'}

    records = _ok(client.post('/removal/bulk', json=[
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 3, 'reason': 'other'},
        {'goods_id': g, 'location_id': w['std1'], 'quantity': 5, 'reason': 'other'},
    ], headers=_h(access_token)))
    bulk = [l for r in records for l in _lines(client, doc_type='removal', doc_id=r['id'])]
    assert _legs(bulk) == [('onhand', w['std1'], -5), ('onhand', w['std1'], -3), ('pending', None, 3),
                           ('pending', None, 5)]
    with client.application.app_context():
        assert GoodsLocation.query.filter_by(goods_id=g, location_id=w['std1']).count() == 0   # 归零删行


def test_entry_pick(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 6), (g, w['std2'], 4)])
    dn_id, _packing = _picked_dn(client, access_token, w, [(g, 8)], [(g, w['std1'], 6), (g, w['std2'], 2)])
    with client.application.app_context():
        task = PickingTask.query.filter_by(dn_id=dn_id).one()
        detail_ids = {d.id for d in task.task_details}
        order_number = db.session.get(DN, dn_id).order_number
        task_id = task.id
        inventory = db.session.get(Inventory, (g, w['wh']))
        assert (inventory.picked_stock, inventory.onhand_stock) == (8, 2)
    lines = _lines(client, doc_type='picking_task', doc_id=task_id)
    assert {l['movement_type'] for l in lines} == {'pick'}
    assert _legs(lines) == [('onhand', w['std1'], -6), ('onhand', w['std2'], -2), ('picked', None, 2),
                            ('picked', None, 6)]
    assert {l['doc_line_id'] for l in lines} == detail_ids and {l['doc_no'] for l in lines} == {order_number}
    assert len({l['trace_id'] for l in lines}) == 1


def _pack(client, token, packing_id, goods_id, quantity):
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': goods_id,
                                                                          'packed_quantity': quantity}]},
                    headers=_h(token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(token)))


def test_entry_pack_and_short_return(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])
    _pack(client, access_token, packing_id, g, 3)
    lines = _lines(client, doc_type='packing_task', doc_id=packing_id)
    assert _legs([l for l in lines if l['movement_type'] == 'pack']) == [('packed', None, 3), ('picked', None, -3)]
    assert _legs([l for l in lines if l['movement_type'] == 'pack_short_return']) == [('pending', None, 2),
                                                                                       ('picked', None, -2)]
    with client.application.app_context():
        detail_id = DNDetail.query.filter_by(dn_id=dn_id, goods_id=g).one().id
    assert {l['doc_line_id'] for l in lines} == {detail_id}


def test_entry_ship_and_sign(client, access_token):
    from datetime import timedelta
    from warehouse.common import clock
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 4)], [(g, w['std1'], 4)])
    _pack(client, access_token, packing_id, g, 4)
    delivery_id = _task_ids(client, DeliveryTask, dn_id=dn_id)[0]
    _ok(client.put(f'/delivery/{delivery_id}/process/', headers=_h(access_token)))
    shipped_at = (clock.now() - timedelta(hours=3)).replace(microsecond=0)
    _ok(client.put(f'/delivery/{delivery_id}/complete/', json={'shipped_at': shipped_at.isoformat()},
                   headers=_h(access_token)))
    ship = _lines(client, doc_type='dn', doc_id=dn_id, movement_type='ship')
    assert _legs(ship) == [('in_transit', None, 4), ('packed', None, -4)]
    # 业务发生时刻 = 实际发货时刻（早于记账时刻）
    assert {l['occurred_at'] for l in ship} == {shipped_at}
    assert all(l['posted_at'] > shipped_at for l in ship)
    with client.application.app_context():
        assert db.session.get(Inventory, (g, w['wh'])).delivered_stock == 4

    _ok(client.put(f'/delivery/{delivery_id}/sign/', json={}, headers=_h(access_token)))
    sign = _lines(client, doc_type='dn', doc_id=dn_id, movement_type='sign')
    assert _legs(sign) == [('in_transit', None, -4)]
    assert sign[0]['counter_stock_type'] is None and sign[0]['balance_after'] == 0
    with client.application.app_context():
        assert db.session.get(Inventory, (g, w['wh'])).delivered_stock == 0


def test_entry_dn_cancel_return(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, _packing = _picked_dn(client, access_token, w, [(g, 5)], [(g, w['std1'], 5)])
    _ok(client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token)))
    lines = _lines(client, doc_type='dn', doc_id=dn_id, movement_type='dn_cancel_return')
    assert _legs(lines) == [('pending', None, 5), ('picked', None, -5)]


def test_entry_transfer_single_and_bulk(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    record = _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'],
                                                 'to_location_id': w['std2'], 'quantity': 4},
                             headers=_h(access_token)))
    lines = _lines(client, doc_type='transfer', doc_id=record['id'])
    assert {l['movement_type'] for l in lines} == {'transfer'}
    assert _legs(lines) == [('onhand', w['std1'], -4), ('onhand', w['std2'], 4)]
    records = _ok(client.post('/transfer/bulk', json=[
        {'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1},
        {'goods_id': g, 'from_location_id': w['std2'], 'to_location_id': w['std1'], 'quantity': 2},
    ], headers=_h(access_token)))
    bulk = [l for r in records for l in _lines(client, doc_type='transfer', doc_id=r['id'])]
    assert len(bulk) == 4 and len({l['trace_id'] for l in bulk}) == 1


def test_transfer_back_to_standard_needs_reason(client, access_token):
    """破损 / 退货库位移回标准库位允许，但必须填原因（400 15018），原因写进流水备注；反方向不要求"""
    w = _world(client)
    g = w['g1']
    with client.application.app_context():
        damaged = Location(warehouse_id=w['wh'], code='DMG-T', location_type='damaged', created_by=w['admin'])
        returned = Location(warehouse_id=w['wh'], code='RET-T', location_type='return', created_by=w['admin'])
        db.session.add_all([damaged, returned])
        db.session.commit()
        dmg, ret = damaged.id, returned.id
    _inbound(client, access_token, w, [(g, 10)], [(g, dmg, 5), (g, ret, 5)])
    for source in (dmg, ret):
        body = _err(client.post('/transfer/', json={'goods_id': g, 'from_location_id': source,
                                                    'to_location_id': w['std1'], 'quantity': 1},
                                headers=_h(access_token)), 400, 15018)
        assert body['field'] == 'reason' and body['details']['to_location_type'] == 'standard'
        _err(client.post('/transfer/', json={'goods_id': g, 'from_location_id': source, 'to_location_id': w['std1'],
                                             'quantity': 1, 'reason': '   '}, headers=_h(access_token)), 400, 15018)
    record = _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': dmg, 'to_location_id': w['std1'],
                                                 'quantity': 2, 'reason': 'inspected: no damage'},
                             headers=_h(access_token)))
    assert record['remark'] == 'inspected: no damage'
    assert {l['remark'] for l in _lines(client, doc_type='transfer', doc_id=record['id'])} == {'inspected: no damage'}
    # 没给 reason 时非空的 remark 也算原因
    record = _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': ret, 'to_location_id': w['std1'],
                                                 'quantity': 1, 'remark': 'customer return checked'},
                             headers=_h(access_token)))
    assert {l['remark'] for l in _lines(client, doc_type='transfer', doc_id=record['id'])} == {'customer return checked'}
    # 标准 → 破损、破损 → 退货不要求原因
    _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': dmg,
                                        'quantity': 1}, headers=_h(access_token)))
    _ok(client.post('/transfer/', json={'goods_id': g, 'from_location_id': dmg, 'to_location_id': ret,
                                        'quantity': 1}, headers=_h(access_token)))
    # 批量里任何一行缺原因整批不落库
    _err(client.post('/transfer/bulk', json=[
        {'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'], 'quantity': 1},
        {'goods_id': g, 'from_location_id': dmg, 'to_location_id': w['std2'], 'quantity': 1},
    ], headers=_h(access_token)), 400, 15018)


def test_entry_adjustment(client, access_token):
    from warehouse.adjustment.services import AdjustmentService
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    _inbound(client, access_token, w, [(g1, 10)], [(g1, w['std1'], 10)])
    with client.application.app_context():
        approver = get_company_admin_user().id
        adjustment = Adjustment(warehouse_id=w['wh'], status='approved', is_active=True, created_by=w['admin'],
                                approved_by=approver, adjustment_reason='cycle count')
        db.session.add(adjustment)
        db.session.flush()
        loss = AdjustmentDetail(adjustment_id=adjustment.id, goods_id=g1, location_id=w['std1'], system_quantity=10,
                                actual_quantity=7, adjustment_quantity=-3)
        gain = AdjustmentDetail(adjustment_id=adjustment.id, goods_id=g2, location_id=w['std2'], system_quantity=0,
                                actual_quantity=5, adjustment_quantity=5, remark='found')
        db.session.add_all([loss, gain])
        db.session.commit()
        adjustment_id, loss_id, gain_id = adjustment.id, loss.id, gain.id
        AdjustmentService.complete_adjustment(adjustment_id, approver)
    lines = _lines(client, doc_type='adjustment', doc_id=adjustment_id)
    assert [(l['movement_type'], l['doc_line_id'], l['stock_type'], l['location_id'], l['quantity']) for l in lines] \
        == [('adjust_loss', loss_id, 'onhand', w['std1'], -3), ('adjust_gain', gain_id, 'onhand', w['std2'], 5)]
    assert {l['reason'] for l in lines} == {'cycle count'}
    assert [l['remark'] for l in lines] == [None, 'found']


def test_entry_inventory_service_fallbacks(client):
    """InventoryService 的记账方法不传 op 时单独记一笔（没有单据）"""
    from warehouse.inventory.services import InventoryService
    from warehouse.stock.service import Leg, StockOp
    w = _world(client)
    g = w['g1']
    with client.application.app_context():
        db.session.add(Inventory(goods_id=g, warehouse_id=w['wh'], received_stock=10))
        db.session.commit()
        InventoryService.asn_completed(g, w['wh'], 10, 10, 1)
        StockOp(w['wh'], 'pick').move(g, 0, src=Leg.pending(), dst=Leg.picked()).commit()   # 0 件不记
        add_stock(g, w['wh'], picked=8)
        InventoryService.dn_packed(g, w['wh'], 5, 6)
        InventoryService.dn_cancelled_after_picking(g, w['wh'], picked_quantity=2)
        InventoryService.dn_delivered(g, w['wh'], 3)
        InventoryService.dn_completed(g, w['wh'], 2)
        InventoryService.dn_cancelled_after_picking(g, w['wh'], packed_quantity=2)
        db.session.commit()
    lines = _lines(client, goods_id=g)
    movements = [l['movement_type'] for l in lines if l['movement_type'] != 'reconcile']
    assert movements == ['sort_in', 'sort_in', 'pack', 'pack', 'pack_short_return', 'pack_short_return',
                         'dn_cancel_return', 'dn_cancel_return', 'ship', 'ship', 'sign',
                         'dn_cancel_return', 'dn_cancel_return']
    assert {l['doc_type'] for l in lines} == {None}
    with client.application.app_context():
        inv = db.session.get(Inventory, (g, w['wh']))
        assert (inv.sorted_stock, inv.sorted_damage_stock, inv.picked_stock, inv.packed_stock,
                inv.delivered_stock) == (10 + 1 + 2 + 2, 1, 0, 0, 1)


def test_stock_service_errors_keep_old_codes(client, access_token):
    """来源不足沿用原错误码；回滚后库存、流水都不动"""
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 3)], [(g, w['std1'], 3)])
    before = _lines(client, goods_id=g)
    _err(client.post('/putaway/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 1},
                     headers=_h(access_token)), 400, 15007)
    _err(client.post('/removal/', json={'goods_id': g, 'location_id': w['std1'], 'quantity': 4, 'reason': 'other'},
                     headers=_h(access_token)), 400, 15001)
    _err(client.post('/transfer/', json={'goods_id': g, 'from_location_id': w['std1'], 'to_location_id': w['std2'],
                                         'quantity': 4}, headers=_h(access_token)), 400, 15001)
    assert _lines(client, goods_id=g) == before


# ---------------------------------------------------------------------------
# 元测试
# ---------------------------------------------------------------------------
SCAN_DIRS = ('warehouse', 'system', 'tasks')
STOCK_DIR = os.path.join('warehouse', 'stock') + os.sep
ROW_MODELS = {'GoodsLocation', 'InventoryLotStock', 'StockLedger'}
RAW_SQL = re.compile(r'\b(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+"?(inventory|goods_locations|inventory_lot_stock|'
                     r'stock_ledger)\b', re.IGNORECASE)


def _python_files():
    for top in SCAN_DIRS:
        for folder, _dirs, files in os.walk(os.path.join(ROOT, top)):
            if '__pycache__' in folder:
                continue
            for name in files:
                if name.endswith('.py'):
                    path = os.path.join(folder, name)
                    yield os.path.relpath(path, ROOT), path


def _functions(tree):
    """{节点: 所在函数名}"""
    owner = {}

    def visit(node, name):
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            owner[child] = child_name
            visit(child, child_name)
    visit(tree, None)
    return owner


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_meta_every_stock_op_call_site_is_registered():
    found = set()
    for relpath, path in _python_files():
        if relpath.startswith(STOCK_DIR):
            continue
        tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
        owner = _functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == 'StockOp':
                found.add(f"{relpath.replace(os.sep, '/')}:{owner.get(node)}")
    assert found == set(ENTRY_POINTS), {
        'not registered (add to ENTRY_POINTS with a matrix test)': sorted(found - set(ENTRY_POINTS)),
        'registered but gone': sorted(set(ENTRY_POINTS) - found),
    }
    tests = {name for name in globals() if name.startswith('test_entry_')}
    missing = {entry: test for entry, (_m, test) in ENTRY_POINTS.items() if test not in tests}
    assert not missing, missing


def test_meta_no_direct_stock_writes_outside_stock_service():
    problems = []
    for relpath, path in _python_files():
        if relpath.startswith(STOCK_DIR):
            continue
        source = open(path, encoding='utf-8').read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                for item in (target.elts if isinstance(target, ast.Tuple) else [target]):
                    if isinstance(item, ast.Attribute) and item.attr in INVENTORY_PROTECTED_COLUMNS:
                        problems.append(f"{relpath}:{node.lineno} assigns .{item.attr}")
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in ROW_MODELS:
                    problems.append(f"{relpath}:{node.lineno} constructs {name}")
                if name == 'Inventory' and any(k.arg in INVENTORY_PROTECTED_COLUMNS for k in node.keywords):
                    problems.append(f"{relpath}:{node.lineno} constructs Inventory with stock")
                if name == 'setattr' and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                        and node.args[1].value in INVENTORY_PROTECTED_COLUMNS:
                    problems.append(f"{relpath}:{node.lineno} setattr .{node.args[1].value}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and RAW_SQL.search(node.value):
                problems.append(f"{relpath}:{node.lineno} raw SQL writes a stock table")
    assert not problems, problems
