"""历史数据校正 flask stock fix-legacy-pending（warehouse/stock/legacy_fixes.py）：
B-06 修复前拣货下架误计入待上架；--include-picked 另校正 B-54 修复前少打包差额留在已拣。
演练不写；--apply 经 StockService 写 correction 流水（对账由 tests/conftest.py 自动做）；幂等；不扣到负数；破损部分不动。"""
from datetime import timedelta

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _world
from warehouse.stock.legacy_fixes import B06_CUTOFF, B54_CUTOFF, fix_legacy_pending
from warehouse.stock.models import StockLedger

BEFORE_B06 = B06_CUTOFF - timedelta(days=10)
AFTER_B06 = B06_CUTOFF + timedelta(days=2)
BEFORE_B54 = B54_CUTOFF - timedelta(days=1)


def _goods(w, code):
    goods = Goods(code=code, company_id=db.session.get(Warehouse, w['wh']).company_id, name=code.lower(), unit='pcs',
                  is_active=True, created_by=w['admin'])
    db.session.add(goods)
    db.session.flush()
    return goods.id


def _asn(w, goods_id, actual, damage=0, at=BEFORE_B06):
    asn = ASN(warehouse_id=w['wh'], supplier_id=w['supplier'], asn_type='inbound', status='completed',
              completed_at=at, created_by=w['admin'])
    db.session.add(asn)
    db.session.flush()
    db.session.add(ASNDetail(asn_id=asn.id, goods_id=goods_id, quantity=actual, actual_quantity=actual,
                             sorted_quantity=actual - damage, damage_quantity=damage, created_by=w['admin']))


def _putaway(w, goods_id, quantity, at=BEFORE_B06):
    db.session.add(PutawayRecord(goods_id=goods_id, location_id=w['std1'], quantity=quantity, operator_id=w['admin'],
                                 putaway_time=at))


def _removal(w, goods_id, quantity, reason, at=BEFORE_B06):
    db.session.add(RemovalRecord(goods_id=goods_id, location_id=w['std1'], quantity=quantity, reason=reason,
                                 operator_id=w['admin'], removal_time=at))


def _dn(w, goods_id, picked, packed, status, packed_at=None):
    dn = DN(warehouse_id=w['wh'], recipient_id=w['recipient'], shipping_address='x', dn_type='shipping', status=status,
            expected_shipping_date=BEFORE_B54.date(),
            created_by=w['admin'], picked_at=BEFORE_B54 - timedelta(hours=1), packed_at=packed_at)
    db.session.add(dn)
    db.session.flush()
    db.session.add(DNDetail(dn_id=dn.id, goods_id=goods_id, quantity=picked, picked_quantity=picked,
                            packed_quantity=packed, delivered_quantity=packed if status == 'delivered' else 0,
                            created_by=w['admin']))


def _scenario(client):
    """各种情形的单据（时刻在修复之前）与当前的计数器（经 add_stock 造，流水为 reconcile）"""
    w = _world(client)
    with client.application.app_context():
        g = {name: _goods(w, f'FIX-{name}') for name in ('bug', 'legit', 'partial', 'damaged', 'later', 'picked')}
        # bug：入库 10、上架 4、修复前拣货下架 3（误进待上架）→ 计数器 9 = 真实 6 + 3
        _asn(w, g['bug'], 10)
        _putaway(w, g['bug'], 4)
        _removal(w, g['bug'], 2, 'picking')
        _removal(w, g['bug'], 1, 'picking', at=BEFORE_B06 + timedelta(days=1))
        # legit：入库 5、上架 5、普通下架 2 → 待上架 2 是真的（不扣）
        _asn(w, g['legit'], 5)
        _putaway(w, g['legit'], 5)
        _removal(w, g['legit'], 2, 'other')
        # partial：修复前拣货 4，但计数器只多 1（其余已被消耗）→ 只扣 1
        _asn(w, g['partial'], 6)
        _putaway(w, g['partial'], 6)
        _removal(w, g['partial'], 4, 'picking')
        # damaged：待上架 7 里破损 6，计数器多 2 → 只能扣良品部分 1，破损不动
        _asn(w, g['damaged'], 5, damage=5)
        _removal(w, g['damaged'], 2, 'picking')
        # later：修复之后的拣货下架没有误计（不扣）
        _asn(w, g['later'], 3)
        _removal(w, g['later'], 3, 'picking', at=AFTER_B06)
        # picked：修复前少打包（拣 5 打 3）差额 2 留在已拣；另有一张拣货完成、打包未完成的 DN 已拣 4
        _dn(w, g['picked'], 5, 3, 'delivered', packed_at=BEFORE_B54)
        _dn(w, g['picked'], 4, 0, 'picked')
        db.session.commit()
        add_stock(g['bug'], w['wh'], pending=9)
        add_stock(g['legit'], w['wh'], pending=2)
        add_stock(g['partial'], w['wh'], pending=1)
        add_stock(g['damaged'], w['wh'], pending=1, pending_damaged=6)
        add_stock(g['later'], w['wh'], pending=3)
        add_stock(g['picked'], w['wh'], picked=6)
    return w, g


def _ils(client, warehouse_id, goods_id):
    from warehouse.stock.models import InventoryLotStock
    with client.application.app_context():
        row = InventoryLotStock.query.filter_by(warehouse_id=warehouse_id, goods_id=goods_id).first()
        return (row.sorted_stock, row.sorted_damage_stock, row.picked_stock) if row else (0, 0, 0)


def _corrections(client, **filters):
    with client.application.app_context():
        return StockLedger.query.filter_by(movement_type='correction', **filters).order_by(StockLedger.id).all()


def test_fix_legacy_pending_dry_run_apply_idempotent(client):
    w, g = _scenario(client)
    before = {name: _ils(client, w['wh'], goods_id) for name, goods_id in g.items()}
    with client.application.app_context():
        report = fix_legacy_pending(w['wh'])
    plan = {row['goods_id']: row for row in report['pending']}
    assert (plan[g['bug']]['derived'], plan[g['bug']]['picking_before_fix'], plan[g['bug']]['excess'],
            plan[g['bug']]['deduct']) == (6, 3, 3, 3)
    assert (plan[g['legit']]['derived'], plan[g['legit']]['deduct']) == (2, 0)
    assert (plan[g['partial']]['excess'], plan[g['partial']]['deduct']) == (1, 1)
    assert (plan[g['damaged']]['excess'], plan[g['damaged']]['deduct']) == (2, 1)
    assert (plan[g['later']]['picking_before_fix'], plan[g['later']]['deduct']) == (0, 0)
    assert report['totals']['pending_deduct'] == 5 and report['picked'] == []
    # 演练不写
    assert _corrections(client) == []
    assert {name: _ils(client, w['wh'], goods_id) for name, goods_id in g.items()} == before

    with client.application.app_context():
        report = fix_legacy_pending(w['wh'], apply=True)
    assert _ils(client, w['wh'], g['bug']) == (6, 0, 0)
    assert _ils(client, w['wh'], g['partial']) == (0, 0, 0)            # 扣到 0 为止，不为负
    assert _ils(client, w['wh'], g['damaged']) == (6, 6, 0)            # 破损部分不动
    assert _ils(client, w['wh'], g['legit']) == (2, 0, 0)
    lines = _corrections(client)
    assert [(l.goods_id, l.stock_type, l.quantity, l.counter_stock_type) for l in lines] == [
        (g['bug'], 'pending', -3, None), (g['partial'], 'pending', -1, None), (g['damaged'], 'pending', -1, None)]
    assert {(l.reason, l.doc_type, l.doc_no, l.source) for l in lines} == {('B-06', 'legacy_fix', 'B-06', 'system')}
    assert len({l.trace_id for l in lines}) == 1 and lines[0].trace_id.startswith('fix-B-06-')
    assert '2026-09-29 前拣货下架误计入待上架' in lines[0].remark
    with client.application.app_context():
        inventory = db.session.get(Inventory, (g['bug'], w['wh']))
        assert (inventory.sorted_stock, inventory.total_stock) == (6, 6)

    # 幂等：已校正的商品跳过，不重复扣
    with client.application.app_context():
        again = fix_legacy_pending(w['wh'], apply=True)
    assert len(_corrections(client)) == 3
    skipped = {row['goods_id']: row['skipped'] for row in again['pending']}
    assert skipped[g['bug']] == 'ALREADY_CORRECTED' and again['totals']['pending_deduct'] == 0


def test_fix_legacy_picked_residue(client):
    w, g = _scenario(client)
    with client.application.app_context():
        report = fix_legacy_pending(w['wh'], include_picked=True)
    picked = {row['goods_id']: row for row in report['picked']}[g['picked']]
    assert (picked['picked_stock'], picked['in_picking'], picked['short_pack_before_fix'], picked['residue'],
            picked['move']) == (6, 4, 2, 2, 2)
    with client.application.app_context():
        fix_legacy_pending(w['wh'], apply=True, include_picked=True)
    assert _ils(client, w['wh'], g['picked']) == (2, 0, 4)            # 已拣 6 → 4（进行中的 DN 不动），差额 2 退回待上架
    lines = _corrections(client, reason='B-54')
    assert sorted((l.stock_type, l.quantity, l.counter_stock_type) for l in lines) == [
        ('pending', 2, 'picked'), ('picked', -2, 'pending')]
    # 退回的量算进 B-06 的单据推算（不会被当成误计再扣掉）；再跑一遍什么都不变
    with client.application.app_context():
        again = fix_legacy_pending(w['wh'], apply=True, include_picked=True)
        row = {r['goods_id']: r for r in again['pending']}[g['picked']]
        assert (row['b54_return'], row['derived'], row['excess'], row['deduct']) == (2, 2, 0, 0)
    assert len(_corrections(client, reason='B-54')) == 2 and _ils(client, w['wh'], g['picked']) == (2, 0, 4)


def test_fix_legacy_pending_command(client):
    from warehouse.stock.commands import stock_cli
    w, g = _scenario(client)
    runner = client.application.test_cli_runner()
    result = runner.invoke(stock_cli, ['fix-legacy-pending', '--warehouse', str(w['wh']), '--include-picked'])
    assert result.exit_code == 0, result.output
    assert 'dry run' in result.output and "'pending_deduct': 5" in result.output
    assert f"{g['bug']}: 9 (0) | 6 = 10 + 0 + 0 + 0 + 0 - 4 | 3 | 3 | 3" in result.output
    assert _corrections(client) == []
    result = runner.invoke(stock_cli, ['fix-legacy-pending', '--warehouse', str(w['wh']), '--include-picked', '--apply'])
    assert result.exit_code == 0 and 'APPLIED' in result.output
    assert len(_corrections(client)) == 5
