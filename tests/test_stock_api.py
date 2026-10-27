"""库存流水 / 按库位 / 历史库存 / 库存分析 / 补货阈值的接口，以及 flask stock 维护命令（期初、校正、对账、回放）"""
import csv
import io
from datetime import timedelta
from urllib.parse import quote

import pytest

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _err, _h, _inbound, _ok, _picked_dn, _task_ids, _world
from warehouse.common import clock
from warehouse.stock.models import StockDailyBalance, StockLedger


def _wh(token, warehouse_id):
    return {**_h(token), 'X-WAREHOUSE-ID': str(warehouse_id)}


def _ship(client, token, w, goods_id, quantity, location_id):
    dn_id, packing_id = _picked_dn(client, token, w, [(goods_id, quantity)], [(goods_id, location_id, quantity)])
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [{'goods_id': goods_id,
                                                                          'packed_quantity': quantity}]},
                    headers=_h(token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(token)))
    delivery_id = _task_ids(client, DeliveryTask, dn_id=dn_id)[0]
    _ok(client.put(f'/delivery/{delivery_id}/process/', headers=_h(token)))
    _ok(client.put(f'/delivery/{delivery_id}/complete/', json={}, headers=_h(token)))
    return dn_id


# ---------------------------------------------------------------------------
# 库存流水
# ---------------------------------------------------------------------------
def test_ledger_list_filters_and_export(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 6)])
    body = _ok(client.get(f'/stock-ledger/?goods_id={g}', headers=_wh(access_token, w['wh'])))
    assert [i['movement_type'] for i in body['items']] == ['putaway', 'putaway', 'sort_in']
    first = body['items'][-1]
    assert first['goods'] == {'id': g, 'code': 'GRD-1', 'name': 'guard-1'} and first['stock_type'] == 'pending'
    assert first['balance_after'] == 10 and first['source'] == 'web' and first['operator']['user_name'] == 'admin'

    onhand = _ok(client.get(f'/stock-ledger/?goods_id={g}&stock_types=onhand&order=asc',
                            headers=_wh(access_token, w['wh'])))
    assert [(i['location']['code'], i['quantity']) for i in onhand['items']] == [('GRD-STD1', 6)]
    by_doc = _ok(client.get(f"/stock-ledger/?doc_type=putaway&doc_id={onhand['items'][0]['doc_id']}",
                            headers=_wh(access_token, w['wh'])))
    assert by_doc['total'] == 2
    trace = _ok(client.get(f"/stock-ledger/?trace_id={first['trace_id']}", headers=_wh(access_token, w['wh'])))
    assert trace['total'] == 1
    assert _ok(client.get('/stock-ledger/?keyword=guard-1&movement_types=sort_in',
                          headers=_wh(access_token, w['wh'])))['total'] == 1
    today = clock.local_today(w['wh']).isoformat()
    assert _ok(client.get(f'/stock-ledger/?goods_id={g}&start_time={today}&end_time={today}',
                          headers=_wh(access_token, w['wh'])))['total'] == 3
    tomorrow = (clock.local_today(w['wh']) + timedelta(days=1)).isoformat()
    assert _ok(client.get(f'/stock-ledger/?goods_id={g}&start_time={tomorrow}',
                          headers=_wh(access_token, w['wh'])))['total'] == 0
    _err(client.get('/stock-ledger/?stock_types=bogus', headers=_wh(access_token, w['wh'])), 400, 15019)
    _err(client.get('/stock-ledger/?start_time=yesterday', headers=_wh(access_token, w['wh'])), 400, 15019)

    response = client.get(f'/stock-ledger/export?goods_id={g}', headers=_wh(access_token, w['wh']))
    assert response.status_code == 200 and response.headers['Content-Type'].startswith('text/csv')
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip('﻿'))))
    assert rows[0][:3] == ['id', 'posted_at', 'occurred_at'] and len(rows) == 4
    assert rows[1][4] == 'GRD-1'
    assert rows[0][-2:] == ['category_balance_after', 'lot_no']
    assert rows[1][-2] == '6'


def test_ledger_scope_and_permission(client, access_operator_token, access_token):
    """只有 inventory_read 能看；员工只能看自己仓库"""
    w = _world(client)
    response = client.get('/stock-ledger/', headers=_wh(access_operator_token, w['wh']))
    assert response.status_code == 403
    with client.application.app_context():
        other = Warehouse.query.filter(Warehouse.id != w['wh']).first().id
        add_stock(w['g1'], other, onhand=2)
    body = _ok(client.get('/stock-ledger/', headers=_wh(access_token, other)))
    assert body['total'] == 1 and all(i['warehouse_id'] == other for i in body['items'])


def test_ledger_summary_in_out(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    _ship(client, access_token, w, g, 4, w['std1'])
    body = _ok(client.get(f'/stock-ledger/summary?goods_id={g}', headers=_wh(access_token, w['wh'])))
    assert body['total'] == 1
    item = body['items'][0]
    # 仓内搬动（上架、拣货、打包）不算入 / 出；发货出仓
    assert (item['opening'], item['inbound'], item['outbound'], item['closing']) == (0, 10, 4, 6)
    assert item['inbound_by_movement'] == {'sort_in': 10} and item['outbound_by_movement'] == {'ship': 4}
    assert item['goods']['code'] == 'GRD-1'

    per_type = _ok(client.get(f'/stock-ledger/summary?goods_id={g}&group_by=goods_stock_type',
                              headers=_wh(access_token, w['wh'])))['items']
    flows = {i['stock_type']: (i['inbound'], i['outbound'], i['closing']) for i in per_type}
    assert flows == {'onhand': (10, 4, 6), 'pending': (10, 10, 0), 'picked': (4, 4, 0), 'packed': (4, 4, 0)}

    with_transit = _ok(client.get(f'/stock-ledger/summary?goods_id={g}&stock_types=onhand,in_transit',
                                  headers=_wh(access_token, w['wh'])))['items'][0]
    assert (with_transit['inbound'], with_transit['outbound'], with_transit['closing']) == (14, 4, 10)
    # 期间之后：期初 = 期末
    later = quote((clock.now() + timedelta(minutes=1)).isoformat())
    end = quote((clock.now() + timedelta(minutes=2)).isoformat())
    after = _ok(client.get(f'/stock-ledger/summary?goods_id={g}&start_time={later}&end_time={end}',
                           headers=_wh(access_token, w['wh'])))
    assert after['items'][0]['opening'] == 6 and after['items'][0]['inbound'] == 0
    location = _ok(client.get(f"/stock-ledger/summary?location_id={w['std1']}",
                              headers=_wh(access_token, w['wh'])))['items'][0]
    assert (location['inbound'], location['outbound'], location['closing']) == (10, 4, 6)


# ---------------------------------------------------------------------------
# 按库位
# ---------------------------------------------------------------------------
def test_inventory_by_location(client, access_token):
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    with client.application.app_context():
        location = db.session.get(Location, w['std2'])
        location.capacity = 1.0
        db.session.get(Goods, g1).length, db.session.get(Goods, g1).width, db.session.get(Goods, g1).height = \
            100, 100, 100
        db.session.commit()
    _inbound(client, access_token, w, [(g1, 10), (g2, 3)], [(g1, w['std2'], 10), (g2, w['std2'], 3)])
    body = _ok(client.get('/inventory/by-location?include_items=true', headers=_wh(access_token, w['wh'])))
    rows = {i['location']['code']: i for i in body['items']}
    std2 = rows['GRD-STD2']
    assert (std2['goods_count'], std2['quantity'], std2['is_empty']) == (2, 13, False)
    assert std2['capacity_m3'] == 1.0 and std2['used_volume_m3'] == 0.01 and std2['volume_utilization'] == 0.01
    assert std2['volume_unknown_quantity'] == 3
    assert [i['goods']['code'] for i in std2['items']] == ['GRD-1', 'GRD-2']
    assert rows['GRD-STD1']['is_empty'] is True
    summary = body['summary']
    assert summary['location_count'] == len(rows) and summary['occupied_count'] == sum(
        1 for r in rows.values() if not r['is_empty'])
    assert summary['sized_location_count'] == 1 and summary['volume_utilization'] == 0.01

    shelves = _ok(client.get('/inventory/by-location?group_by=shelf', headers=_wh(access_token, w['wh'])))['items']
    grd = next(s for s in shelves if s['shelf'] == 'GRD')
    assert (grd['location_count'], grd['occupied_count'], grd['quantity']) == (2, 1, 13)
    prefix = _ok(client.get('/inventory/by-location?group_by=shelf&shelf_prefix_length=3',
                            headers=_wh(access_token, w['wh'])))['items']
    assert {s['shelf'] for s in prefix} >= {'GRD', 'LOC'}

    filtered = _ok(client.get('/inventory/by-location?keyword=guard-2&include_items=true',
                              headers=_wh(access_token, w['wh'])))['items']
    assert [(i['location']['code'], [x['goods']['code'] for x in i['items']]) for i in filtered] == \
        [('GRD-STD2', ['GRD-2'])]
    no_empty = _ok(client.get('/inventory/by-location?include_empty=false', headers=_wh(access_token, w['wh'])))
    assert all(not i['is_empty'] for i in no_empty['items'])

    items = _ok(client.get(f"/inventory/by-location/items?location_id={w['std2']}",
                           headers=_wh(access_token, w['wh'])))
    assert [(i['goods']['code'], i['quantity'], i['stock_type']) for i in items['items']] == \
        [('GRD-1', 10, 'onhand'), ('GRD-2', 3, 'onhand')]


# ---------------------------------------------------------------------------
# 历史库存
# ---------------------------------------------------------------------------
def _at(monkeypatch, moment):
    monkeypatch.setattr(clock, 'now', lambda: moment)


def test_history_daily_balances(client, access_token, monkeypatch):
    """日终 = 当地次日 0 点之前记账的流水合计；生成后查表、未生成现算、今天实时；旧快照转换的只有库位库存"""
    from warehouse.stock.history import generate_missing
    from warehouse.stock.service import Leg, StockOp
    w = _world(client)
    g = w['g1']
    real_now = clock.now()
    with client.application.app_context():
        today = clock.local_today(w['wh'])
        three_days_ago = clock.local_midnight(today - timedelta(days=3), w['wh']) + timedelta(hours=10)
        _at(monkeypatch, three_days_ago)
        add_stock(g, w['wh'], pending=8)
        StockOp(w['wh'], 'putaway').move(g, 5, src=Leg.pending_for('standard'), dst=Leg.at(w['std1'])).commit()
        db.session.commit()
        _at(monkeypatch, three_days_ago + timedelta(days=1))
        StockOp(w['wh'], 'putaway').move(g, 2, src=Leg.pending_for('standard'), dst=Leg.at(w['std2'])).commit()
        db.session.commit()
        monkeypatch.undo()
        assert clock.now() >= real_now

    headers = _wh(access_token, w['wh'])
    day1 = (today - timedelta(days=3)).isoformat()
    body = _ok(client.get(f'/inventory/history?date={day1}&goods_id={g}', headers=headers))
    assert (body['coverage'], body['source']) == ('full', 'computed')
    assert [(i['pending'], i['onhand'], i['total']) for i in body['items']] == [(3, 5, 8)]

    with client.application.app_context():
        done = generate_missing(w['wh'])
    assert [d.isoformat() for d in done[w['wh']]][:2] == [day1, (today - timedelta(days=2)).isoformat()]
    body = _ok(client.get(f'/inventory/history?date={day1}&goods_id={g}&group_by=goods_location', headers=headers))
    assert body['source'] == 'ledger'
    assert sorted(((i.get('location') or {}).get('code') or '', i['total']) for i in body['items']) == \
        [('', 3), ('GRD-STD1', 5)]
    day2 = _ok(client.get(f"/inventory/history?date={(today - timedelta(days=2)).isoformat()}&goods_id={g}",
                          headers=headers))
    assert [(i['pending'], i['onhand']) for i in day2['items']] == [(1, 7)]
    live = _ok(client.get(f'/inventory/history?date={today.isoformat()}&goods_id={g}', headers=headers))
    assert live['source'] == 'realtime' and live['items'][0]['onhand'] == 7
    by_location = _ok(client.get(f'/inventory/history?date={day1}&group_by=location', headers=headers))
    assert [(i['location']['code'], i['onhand']) for i in by_location['items'] if i['location']['code'].startswith('GRD')] \
        == [('GRD-STD1', 5)]
    only_pending = _ok(client.get(f'/inventory/history?date={day1}&goods_id={g}&stock_types=pending',
                                  headers=headers))
    assert [(i['pending'], i['onhand'], i['total']) for i in only_pending['items']] == [(3, 0, 3)]

    series = _ok(client.get(f'/inventory/history/series?goods_id={g}&from={day1}&to={today.isoformat()}',
                            headers=headers))
    assert [p['total'] for p in series['series']] == [8, 8, 8, 8]
    assert {p['coverage'] for p in series['series']} == {'full'}
    extended = _ok(client.get(f'/inventory/history/series?goods_id={g}&from={today-timedelta(days=4)}', headers=headers))
    assert extended['series'][0]['coverage'] == 'none'
    assert extended['series'][1]['coverage'] == 'full'
    multi = _ok(client.get(f"/inventory/history/series?goods_ids={g},{w['g2']}&from={day1}", headers=headers))
    assert [len(item['series']) for item in multi['goods']] == [4, 4]

    at = _ok(client.get(f"/inventory/history/at?goods_id={g}&time={quote((three_days_ago + timedelta(minutes=1)).isoformat())}",
                        headers=headers))
    assert at['coverage'] == 'full' and at['items'][0]['onhand'] == 5
    before = _ok(client.get(f"/inventory/history/at?goods_id={g}&time={quote((three_days_ago - timedelta(days=400)).isoformat())}",
                            headers=headers))
    assert before['coverage'] == 'none' and before['items'] == []

    response = client.get(f'/inventory/history/export?date={day1}&goods_id={g}', headers=headers)
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip('﻿'))))
    assert rows[0][:3] == ['date', 'coverage', 'goods_code'] and rows[1][2] == 'GRD-1'

    _err(client.get('/inventory/history', headers=headers), 400, 15019)
    _err(client.get(f"/inventory/history?date={(today + timedelta(days=1)).isoformat()}", headers=headers), 400, 15019)
    _err(client.get(f'/inventory/history?date={day1}&group_by=bogus', headers=headers), 400, 15019)
    _err(client.get('/inventory/history/series?from=2020-01-01', headers=headers), 400, 15019)


def test_history_legacy_snapshot_coverage(client, access_token):
    """期初之前的日子：旧快照（第二天凌晨拍）转换成前一天的日终，只有库位库存 → coverage on_shelf_only"""
    from warehouse.inventory_snapshot.models import InventorySnapshot
    from warehouse.stock.history import import_legacy_snapshots
    w = _world(client)
    with client.application.app_context():
        today = clock.local_today(w['wh'])
        # 测试的期初（tests/helpers.py open_test_ledger）在今天，前天凌晨 2 点的快照 = 大前天的日终
        taken = clock.local_midnight(today - timedelta(days=2), w['wh']) + timedelta(hours=2)
        db.session.add(InventorySnapshot(goods_id=w['g1'], warehouse_id=w['wh'], location_id=w['std1'], quantity=9,
                                         snapshot_time=taken))
        db.session.add(InventorySnapshot(goods_id=w['g1'], warehouse_id=w['wh'], location_id=w['std1'], quantity=4,
                                         snapshot_time=taken + timedelta(hours=12)))   # 同一天第二批：不用
        db.session.commit()
        assert import_legacy_snapshots(w['wh'])[w['wh']] == 1
        assert import_legacy_snapshots(w['wh'])[w['wh']] == 0        # 幂等
    day = (today - timedelta(days=3)).isoformat()
    body = _ok(client.get(f"/inventory/history?date={day}&goods_id={w['g1']}", headers=_wh(access_token, w['wh'])))
    assert (body['coverage'], body['source']) == ('on_shelf_only', 'legacy_snapshot')
    assert [(i['onhand'], i['pending']) for i in body['items']] == [(9, 0)]
    none = _ok(client.get(f"/inventory/history?date={(today - timedelta(days=30)).isoformat()}",
                          headers=_wh(access_token, w['wh'])))
    assert none['coverage'] == 'none' and none['items'] == []
    series = _ok(client.get(
        f"/inventory/history/series?goods_ids={w['g1']},{w['g2']}&from={day}&to={day}",
        headers=_wh(access_token, w['wh'])))
    by_goods = {item['goods_id']: item['series'][0] for item in series['goods']}
    assert by_goods[w['g1']]['coverage'] == 'on_shelf_only'
    assert by_goods[w['g1']]['total'] == 9
    assert by_goods[w['g2']]['coverage'] == 'none'


def test_history_flows(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    _ship(client, access_token, w, g, 4, w['std1'])
    body = _ok(client.get(f'/inventory/history/flows?goods_id={g}', headers=_wh(access_token, w['wh'])))
    assert len(body['items']) == 30
    today = body['items'][-1]
    assert (today['date'], today['inbound'], today['sort_in'], today['outbound']) == \
        (clock.local_today(w['wh']).isoformat(), 10, 10, 4)


# ---------------------------------------------------------------------------
# 库存分析
# ---------------------------------------------------------------------------
def test_metrics_nightly_summary(client, access_token, monkeypatch):
    """近 N 天出库按发货完成；ABC 按近 90 天出库；呆滞 = 有库存、近 90 天无出库、库龄 > 90 天；库龄档 0–30 / 31–90 / …"""
    from warehouse.stock.metrics import generate_metrics
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    _inbound(client, access_token, w, [(g1, 10)], [(g1, w['std1'], 10)])
    _ship(client, access_token, w, g1, 4, w['std1'])
    with client.application.app_context():
        today = clock.local_today(w['wh'])
        # 商品 2：120 天前分拣入库、从没出过库 → 呆滞
        old = clock.local_midnight(today - timedelta(days=120), w['wh']) + timedelta(hours=9)
        asn = ASN(warehouse_id=w['wh'], supplier_id=w['supplier'], asn_type='inbound', status='completed',
                  completed_at=old, created_by=w['admin'])
        db.session.add(asn)
        db.session.flush()
        db.session.add(ASNDetail(asn_id=asn.id, goods_id=g2, quantity=5, actual_quantity=5, created_by=w['admin']))
        db.session.commit()
        add_stock(g2, w['wh'], location_id=w['std2'], onhand=5)
        assert generate_metrics(w['wh'], today) >= 2
        db.session.commit()
    body = _ok(client.get(f'/inventory/metrics?date={today.isoformat()}', headers=_wh(access_token, w['wh'])))
    rows = {i['goods']['code']: i for i in body['items']}
    m1, m2 = rows['GRD-1'], rows['GRD-2']
    assert (m1['total_stock'], m1['out_qty_7d'], m1['out_qty_30d'], m1['out_qty_90d']) == (6, 4, 4, 4)
    assert m1['sales_qty_30d'] == 4 and m1['abc_class'] == 'A' and m1['out_qty_90d_rank'] == 1
    assert m1['status'] == 'normal' and m1['age_buckets']['age_0_30'] == 6 and m1['idle_days'] == 0
    assert m1['turnover_days_30d'] is not None and m1['days_of_supply'] is not None
    assert m2['status'] == 'dead' and m2['abc_class'] is None and m2['age_buckets']['age_91_180'] == 5
    assert m2['idle_days'] == 120 and m2['turnover_days_30d'] is None
    # 种子商品没有任何入库记录（库龄未知 = 早于最早一笔入库）、也没出过库，同样算呆滞
    assert body['summary']['dead_sku'] == 3 and body['summary']['abc'] == {'A': 1, 'B': 0, 'C': 0}

    dead = _ok(client.get(f'/inventory/metrics?date={today.isoformat()}&status=dead&keyword=GRD',
                          headers=_wh(access_token, w['wh'])))
    assert [i['goods']['code'] for i in dead['items']] == ['GRD-2']
    by_age = _ok(client.get(f'/inventory/metrics?date={today.isoformat()}&sort=weighted_age_days&order=desc'
                            f'&keyword=GRD',
                            headers=_wh(access_token, w['wh'])))
    assert by_age['items'][0]['goods']['code'] == 'GRD-2'
    latest = _ok(client.get('/inventory/metrics', headers=_wh(access_token, w['wh'])))
    assert latest['metric_date'] == today.isoformat()
    assert body['summary']['age_buckets']['age_0_30'] == 6
    assert body['summary']['age_buckets']['age_91_180'] == 5
    assert sum(body['summary']['age_buckets'].values()) == body['summary']['total_stock']
    assert body['summary']['out_qty_90d'] == 4
    assert body['summary']['abc_curve'][-1] == {'sku_share': 1, 'outbound_share': 1}
    # Table filters and pagination must never turn the warehouse chart into a partial-page total.
    page = _ok(client.get(f'/inventory/metrics?date={today.isoformat()}&per_page=1&status=dead',
                          headers=_wh(access_token, w['wh'])))
    assert page['summary'] == body['summary']
    aged = _ok(client.get('/inventory/metrics?age_bucket=age_91_180', headers=_wh(access_token, w['wh'])))
    assert aged['total'] == 1 and aged['items'][0]['goods_id'] == g2
    assert aged['summary'] == body['summary']
    unknown = _ok(client.get('/inventory/metrics?age_bucket=unknown', headers=_wh(access_token, w['wh'])))
    assert unknown['total'] == 2 and all(row['age_buckets']['unknown'] > 0 for row in unknown['items'])
    assert _ok(client.get('/inventory/metrics?age_bucket=unknown&keyword=GRD', headers=_wh(access_token, w['wh'])))['total'] == 0
    _err(client.get('/inventory/metrics?age_bucket=bad', headers=_wh(access_token, w['wh'])), 400, 15019)


    _err(client.get('/inventory/metrics?status=bogus', headers=_wh(access_token, w['wh'])), 400, 15019)

    detail = _ok(client.get(f'/inventory/metrics/goods/{g2}', headers=_wh(access_token, w['wh'])))
    assert detail['total_stock'] == 5 and [l['quantity'] for l in detail['layers']] == [5]
    assert detail['layers'][0]['age_days'] == 120 and len(detail['daily']) == 90
    assert detail['latest_metrics']['status'] == 'dead'


def test_metrics_abc_boundaries():
    from warehouse.stock.metrics import abc_classes, age_layers
    classes = abc_classes({1: 50, 2: 30, 3: 15, 4: 5, 5: 0})
    assert {g: c for g, (_r, c) in classes.items()} == {1: 'A', 2: 'A', 3: 'B', 4: 'C'}
    used, unknown = age_layers(7, [(3, 4), (1, 10), (2, 2)])
    assert used == [(3, 4), (2, 2), (1, 1)] and unknown == 0
    assert age_layers(5, [(1, 2)]) == ([(1, 2)], 3)


# ---------------------------------------------------------------------------
# 补货阈值
# ---------------------------------------------------------------------------
def test_thresholds_items(client, access_token):
    w = _world(client)
    headers = _wh(access_token, w['wh'])
    body = _ok(client.put('/inventory/thresholds', json={'items': [
        {'goods_id': w['g1'], 'low_stock_threshold': 5, 'high_stock_threshold': 50},
        {'goods_code': 'G001', 'low_stock_threshold': 10},
    ], 'dry_run': True}, headers=headers))
    assert body['dry_run'] and (body['created'], body['updated']) == (1, 1)
    with client.application.app_context():
        assert db.session.get(Inventory, (w['g1'], w['wh'])) is None
    body = _ok(client.put('/inventory/thresholds', json={'items': [
        {'goods_id': w['g1'], 'low_stock_threshold': 5, 'high_stock_threshold': 50},
        {'goods_code': 'G001', 'low_stock_threshold': 10},
    ]}, headers=headers))
    assert (body['created'], body['updated'], body['unchanged']) == (1, 1, 0)
    assert body['items'][1]['before'] == {'low': -1, 'high': -1} and body['items'][1]['after'] == {'low': 10, 'high': -1}
    with client.application.app_context():
        inv = db.session.get(Inventory, (w['g1'], w['wh']))
        assert (inv.low_stock_threshold, inv.high_stock_threshold, inv.total_stock) == (5, 50, 0)

    body = _err(client.put('/inventory/thresholds', json={'items': [
        {'goods_id': w['g1'], 'high_stock_threshold': 3},          # ≤ low 5
        {'goods_code': 'NOPE', 'low_stock_threshold': 1},
        {'goods_id': w['g2'], 'low_stock_threshold': -5},
        {'goods_id': w['g2'], 'low_stock_threshold': 'x'},
    ]}, headers=headers), 400, 15017)
    assert [(e['index'], e['code']) for e in body['details']['errors']] == [(0, 15015), (1, 16030), (2, 15013),
                                                                            (3, 15017)]
    with client.application.app_context():
        assert db.session.get(Inventory, (w['g1'], w['wh'])).high_stock_threshold == 50   # 全部不改

    # 库存列表：低于补货阈值
    with client.application.app_context():
        add_stock(w['g1'], w['wh'], onhand=3)
    items = _ok(client.get('/inventory/?below_low_threshold=true', headers=headers))['items']
    assert [i['goods_id'] for i in items] == [w['g1']]
    assert _ok(client.get('/inventory/?above_high_threshold=true', headers=headers))['total'] == 0


def test_thresholds_by_filter_and_suggest(client, access_token):
    w = _world(client)
    g1, g2 = w['g1'], w['g2']
    headers = _wh(access_token, w['wh'])
    with client.application.app_context():
        db.session.get(Goods, g1).category = 'Toys'
        db.session.commit()
    _inbound(client, access_token, w, [(g1, 60), (g2, 5)], [(g1, w['std1'], 60), (g2, w['std2'], 5)])
    _ship(client, access_token, w, g1, 30, w['std1'])

    body = _ok(client.put('/inventory/thresholds/by-filter', json={'filter': {'category': 'toys'},
                                                                   'low_stock_threshold': 7}, headers=headers))
    assert [(i['goods_code'], i['after']['low']) for i in body['items']] == [('GRD-1', 7)]

    suggest = _ok(client.get(f'/inventory/thresholds/suggest?goods_ids={g1},{g2}', headers=headers))
    rows = {i['goods']['code']: i for i in suggest['items']}
    # 近 30 天日均出库 1 件：补货覆盖 14 天、积压 90 天
    assert (rows['GRD-1']['avg_daily_out'], rows['GRD-1']['suggested_low'], rows['GRD-1']['suggested_high']) == \
        (1.0, 14, 90)
    assert rows['GRD-1']['current_low'] == 7
    assert rows['GRD-2']['suggested_low'] is None                 # 近 90 天没有出库不给建议
    assert suggest['params'] == {'window_days': 30, 'cover_days': 14, 'overstock_days': 90, 'min_quantity': 1}

    body = _ok(client.put('/inventory/thresholds/by-filter', json={
        'filter': {'goods_ids': [g1, g2]}, 'suggest': {'cover_days': 7}, 'dry_run': True}, headers=headers))
    assert [(i['goods_code'], i['after']) for i in body['items']] == [('GRD-1', {'low': 7, 'high': 90})]
    assert body['items'][0]['changed'] is True and body['skipped'] == [
        {'goods_id': g2, 'goods_code': 'GRD-2', 'reason': 'NO_OUTBOUND_90D'}]
    _err(client.put('/inventory/thresholds/by-filter', json={'filter': {}, 'suggest': {'cover_days': 0}},
                    headers=headers), 400, 15017)
    _err(client.put('/inventory/thresholds/by-filter', json={'filter': {}}, headers=headers), 400, 15017)


def test_pending_putaway_summary_keyword(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 4), (w['g2'], 2)], [])
    headers = _wh(access_token, w['wh'])
    assert _ok(client.get('/inventory/pending-putaway/summary?keyword=guard-2', headers=headers))['total_quantity'] == 2
    assert _ok(client.get('/inventory/pending-putaway/summary?keyword=GRD', headers=headers))['total_quantity'] == 6


# ---------------------------------------------------------------------------
# 维护命令：期初 / 校正 / 对账 / 回放
# ---------------------------------------------------------------------------
def _cli(client, *args):
    from warehouse.stock.commands import stock_cli
    runner = client.application.test_cli_runner()
    return runner.invoke(stock_cli, list(args))


def test_reconcile_finds_and_fixes_drift(client, access_token):
    """绕过 StockService 改了库存（模拟部署窗口的旧进程）：对账报出来；reconcile 演练不改，--apply 校正缓存并写 reconcile 行"""
    from warehouse.stock import guard
    from warehouse.stock.audit import audit_stock
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 6)])
    with client.application.app_context(), guard.suspended():
        db.session.get(Inventory, (g, w['wh'])).sorted_stock = 1          # 旧进程上架 3 件：计数器少了 3
        db.session.get(Inventory, (g, w['wh'])).total_stock -= 3
        db.session.get(Inventory, (g, w['wh'])).onhand_stock += 3
        db.session.get(Inventory, (g, w['wh'])).total_stock += 3
        GoodsLocation.query.filter_by(goods_id=g, location_id=w['std1']).one().quantity = 9
        db.session.commit()
        problems = audit_stock()
        assert {p['type'] for p in problems} >= {'ledger_sum_mismatch'}

    result = _cli(client, 'audit')
    assert result.exit_code == 1 and 'ledger_sum_mismatch' in result.output
    result = _cli(client, 'reconcile')
    assert result.exit_code == 0 and 'dry run' in result.output
    with client.application.app_context():
        assert audit_stock()
    result = _cli(client, 'reconcile', '--apply', '--sync-legacy-counters')
    assert result.exit_code == 0, result.output
    with client.application.app_context():
        assert audit_stock() == []
        lines = StockLedger.query.filter_by(goods_id=g, movement_type='reconcile').all()
        assert sorted((l.stock_type, l.quantity) for l in lines) == [('onhand', 3), ('pending', -3)]
    assert _cli(client, 'audit').exit_code == 0


def test_init_ledger_is_idempotent(client):
    result = _cli(client, 'init-ledger')
    assert result.exit_code == 0 and 'skipped' in result.output


def test_stock_daily_job_and_task_endpoint(client, access_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 3)], [])
    response = client.post('/task/inventory_snapshot', headers=_h(access_token))
    assert response.status_code == 200 and 'daily balances' in response.get_json()['message']
    response = client.post('/task/inventory_metrics', headers=_h(access_token))
    assert response.status_code == 200
    result = _cli(client, 'daily')
    assert result.exit_code == 0, result.output


def test_replay_dry_run_and_apply(client, access_token):
    """回放：从单据回放期初之前的流水，与旧快照逐批核对；--apply 写 derived 流水、在期初时刻写 reconcile、删 opening，
    结存不变、之后的真实流水不受影响，期初之前的日终由回放重建"""
    from warehouse.inventory_snapshot.models import InventorySnapshot
    from warehouse.stock import guard
    from warehouse.stock.audit import audit_stock, bucket_balances, reconcile
    from warehouse.stock.replay import replay
    w = _world(client)
    g = w['g1']
    with client.application.app_context():
        # 第二个仓库：种子数据里没有库存和单据
        wh = Warehouse.query.filter_by(name='Warehouse Test').one().id
        loc1 = Location(warehouse_id=wh, code='R-01', location_type='standard', created_by=w['admin'])
        loc2 = Location(warehouse_id=wh, code='R-02', location_type='standard', created_by=w['admin'])
        db.session.add_all([loc1, loc2])
        db.session.commit()
        loc1, loc2 = loc1.id, loc2.id
        today = clock.local_today(wh)
        t_history = clock.local_midnight(today - timedelta(days=5), wh) + timedelta(hours=10)
        # 期初之前的单据：5 天前入库 8 件、上架 6 件；当晚的旧快照拍到库位 6 件
        asn = ASN(warehouse_id=wh, supplier_id=w['supplier'], asn_type='inbound', status='completed',
                  completed_at=t_history, created_by=w['admin'])
        db.session.add(asn)
        db.session.flush()
        db.session.add(ASNDetail(asn_id=asn.id, goods_id=g, quantity=8, actual_quantity=8, created_by=w['admin']))
        db.session.add(PutawayRecord(goods_id=g, location_id=loc1, quantity=6, operator_id=w['admin'],
                                     putaway_time=t_history + timedelta(hours=1)))
        db.session.add(InventorySnapshot(goods_id=g, warehouse_id=wh, location_id=loc1, quantity=6,
                                         snapshot_time=t_history + timedelta(hours=16)))
        db.session.commit()
        # 这些单据对应的库存（上线前的存量，不经流水），再写期初（同 init-ledger）
        with guard.suspended():
            db.session.add(GoodsLocation(goods_id=g, location_id=loc1, quantity=6))
            inv = InventoryService.ensure_inventory(g, wh)
            inv.sorted_stock = 2
            inv.total_stock = 8
            db.session.commit()
        reconcile(wh, apply=True, sync_legacy_counters=True, movement_type='opening')
    # 期初之后的真实作业
    _ok(client.post('/putaway/', json={'goods_id': g, 'location_id': loc2, 'quantity': 1}, headers=_h(access_token)))
    with client.application.app_context():
        before = bucket_balances(wh, [g])
        report = replay(wh)
        assert report['events'] == 2 and report['by_movement'] == {'sort_in': 1, 'putaway': 1}
        assert report['snapshots_compared'] == 1 and report['snapshots_mismatched'] == 0
        assert report['final_diff_buckets'] == 0 and report['applied'] is False
        assert StockLedger.query.filter(StockLedger.trace_id.like('replay-%')).count() == 0

        report = replay(wh, apply=True)
        assert report['applied'] and report['derived_lines'] == 3
        assert StockLedger.query.filter_by(warehouse_id=wh, movement_type='opening').count() == 0
        assert bucket_balances(wh, [g]) == before
        assert audit_stock(wh) == []
        derived_day = today - timedelta(days=5)
        rows = StockDailyBalance.query.filter_by(warehouse_id=wh, goods_id=g, balance_date=derived_day).all()
        assert sorted((r.stock_type, r.quantity, r.source) for r in rows) == [('onhand', 6, 'derived'),
                                                                             ('pending', 2, 'derived')]
        with pytest.raises(RuntimeError):
            replay(wh, apply=True)                          # 只能回放一次
    body = _ok(client.get(f'/inventory/history?date={derived_day.isoformat()}&goods_id={g}',
                          headers=_wh(access_token, wh)))
    assert (body['coverage'], body['source']) == ('full', 'derived')
    assert [(i['onhand'], i['pending']) for i in body['items']] == [(6, 2)]
    result = _cli(client, 'replay', '--warehouse', str(wh))
    assert result.exit_code == 0 and 'events 0' in result.output


def test_replay_reports_snapshot_mismatch(client):
    """单据和旧快照对不上（旧数据直接改过库）：演练报告列出差异，不写库"""
    from warehouse.inventory_snapshot.models import InventorySnapshot
    from warehouse.stock.replay import replay
    w = _world(client)
    with client.application.app_context():
        wh = Warehouse.query.filter_by(name='Warehouse Test').one().id
        loc = Location(warehouse_id=wh, code='M-01', location_type='standard', created_by=w['admin'])
        db.session.add(loc)
        db.session.flush()
        moment = clock.now() - timedelta(days=2)
        db.session.add(PutawayRecord(goods_id=w['g1'], location_id=loc.id, quantity=3, operator_id=w['admin'],
                                     putaway_time=moment))
        db.session.add(InventorySnapshot(goods_id=w['g1'], warehouse_id=wh, location_id=loc.id, quantity=5,
                                         snapshot_time=moment + timedelta(hours=1)))
        db.session.commit()
        location_id = loc.id
        report = replay(wh)
    assert report['snapshots_mismatched'] == 1 and report['snapshot_max_abs_diff'] == 2
    assert report['snapshots'][0]['samples'] == [{'goods_id': w['g1'], 'location_id': location_id,
                                                  'snapshot': 5, 'replayed': 3}]
    assert report['negative_balances'] == 1          # 上架时待上架没有货（入库单不在）


def test_ledger_overview_shipment_boundary_and_pagination(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 4)], [(g, w['std1'], 4)])
    _inbound(client, access_token, w, [(w['g2'], 3)], [])
    url = '/stock-ledger/overview?per_page=1&keyword=GRD-'
    body = _ok(client.get(url, headers=_wh(access_token, w['wh'])))
    assert body['totals'] == dict(opening=0, inbound=13, outbound=0, closing=13)
    assert body['total'] == 2 and len(body['items']) == 1
    assert body['series'][-1]['total'] == 13
    assert 'picked' in body['stock_types'] and 'packed' in body['stock_types']
    report_url='/stock-ledger/summary?per_page=1&keyword=GRD-'
    report=_ok(client.get(report_url,headers=_wh(access_token,w['wh'])))
    assert report['totals']['closing_by_stock_type']['picked']==4
    assert report['totals']['closing_by_stock_type']['onhand']==6
    assert report['totals']['closing_by_stock_type']['pending']==3
    assert sum(report['totals']['closing_by_stock_type'].values())==13
    assert sum(report['totals']['opening_by_stock_type'].values())==0

    second = _ok(client.get(url+'&page=2&movement_types=ship&doc_no=missing', headers=_wh(access_token, w['wh'])))
    assert second['totals'] == body['totals'] and second['series'] == body['series']
    assert second['items'][0]['goods_id'] != body['items'][0]['goods_id']
    scoped = _ok(client.get(f'/stock-ledger/overview?goods_id={g}', headers=_wh(access_token, w['wh'])))
    assert scoped['totals']['closing'] == 10 and scoped['items'][0]['lot_no'] is None
    assert scoped['totals']['opening'] + scoped['totals']['inbound'] - scoped['totals']['outbound'] == scoped['totals']['closing']
    _err(client.get('/stock-ledger/overview?start_time=2020-01-01&end_time=2026-10-26', headers=_wh(access_token, w['wh'])),400,15019)

    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details':[{'goods_id':g,'packed_quantity':4}]},headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/',headers=_h(access_token)))
    packed = _ok(client.get(url,headers=_wh(access_token,w['wh'])))
    assert packed['totals'] == body['totals']
    report=_ok(client.get(report_url+'&group_by=brand',headers=_wh(access_token,w['wh'])))
    assert report['totals']['closing_by_stock_type']['picked']==0
    assert report['totals']['closing_by_stock_type']['packed']==4
    assert report['totals']['outbound']==0

    delivery_id = _task_ids(client, DeliveryTask, dn_id=dn_id)[0]
    _ok(client.put(f'/delivery/{delivery_id}/process/',headers=_h(access_token)))
    _ok(client.put(f'/delivery/{delivery_id}/complete/',json={},headers=_h(access_token)))
    shipped = _ok(client.get(url,headers=_wh(access_token,w['wh'])))
    assert shipped['totals'] == dict(opening=0,inbound=13,outbound=4,closing=9)
    assert shipped['series'][-1]['total'] == 9
    report=_ok(client.get(report_url,headers=_wh(access_token,w['wh'])))
    assert report['totals']['closing_by_stock_type']['packed']==0
    assert sum(report['totals']['closing_by_stock_type'].values())==9
    later=quote((clock.now()+timedelta(minutes=1)).isoformat())
    end=quote((clock.now()+timedelta(minutes=2)).isoformat())
    report=_ok(client.get(report_url+f'&start_time={later}&end_time={end}',headers=_wh(access_token,w['wh'])))
    assert report['totals']['opening_by_stock_type']==report['totals']['closing_by_stock_type']
    assert sum(report['totals']['opening_by_stock_type'].values())==9



def test_ledger_overview_permission_and_warehouse_scope(client, access_token, access_operator_token):
    w = _world(client)
    _inbound(client, access_token, w, [(w['g1'], 10)], [])
    assert client.get('/stock-ledger/overview',headers=_wh(access_operator_token,w['wh'])).status_code == 403
    with client.application.app_context():
        other = Warehouse.query.filter(Warehouse.id != w['wh']).first().id
    body = _ok(client.get('/stock-ledger/overview',headers=_wh(access_token,other)))
    assert body['totals']['closing'] == 0 and not body['items']


def test_ledger_category_balance_not_cut_by_location_filter_or_page(client, access_token):
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g,w['std1'],6),(g,w['std2'],4)])
    body = _ok(client.get(f"/stock-ledger/?goods_id={g}&stock_types=onhand&location_id={w['std2']}&per_page=1",headers=_wh(access_token,w['wh'])))
    row = body['items'][0]
    assert row['balance_after'] == 4
    assert row['category_balance_after'] == 10
    assert row.get('lot_no') is None


def test_summary_totals_cover_all_pages_and_filtered_scope(client, access_token):
    w = _world(client)
    _inbound(client,access_token,w,[(w['g1'],10),(w['g2'],3)],[])
    url='/stock-ledger/summary?keyword=GRD-&per_page=1'
    first=_ok(client.get(url,headers=_wh(access_token,w['wh'])))
    second=_ok(client.get(url+'&page=2',headers=_wh(access_token,w['wh'])))
    assert first['total']==2 and len(first['items'])==1
    assert first['totals']==second['totals']
    assert first['totals']['inbound']==13 and first['totals']['closing']==13
    assert first['totals']['inbound_by_movement']=={'sort_in':13}
    scoped=_ok(client.get(url+f"&goods_id={w['g2']}",headers=_wh(access_token,w['wh'])))
    assert scoped['totals']['closing']==3


def test_summary_dimensions_and_missing_group_drilldown(client, access_token):
    w=_world(client)
    with client.application.app_context():
        first=db.session.get(Goods,w['g1']);second=db.session.get(Goods,w['g2'])
        first.brand='Brand A';second.brand=None
        first.category=second.category='Food'
        db.session.commit()
    _inbound(client,access_token,w,[(w['g1'],10),(w['g2'],3)],[])
    url='/stock-ledger/summary?keyword=GRD-'
    brand=_ok(client.get(url+'&group_by=brand',headers=_wh(access_token,w['wh'])))
    assert brand['total']==2 and brand['totals']['closing']==13
    groups={row.get('group_value'):row for row in brand['items']}
    assert groups['Brand A']['closing']==10 and groups[None]['closing']==3
    category=_ok(client.get(url+'&group_by=category',headers=_wh(access_token,w['wh'])))
    assert category['total']==1 and category['items'][0]['goods_count']==2
    assert category['items'][0]['closing']==13
    missing=_ok(client.get(url+'&brand_missing=1',headers=_wh(access_token,w['wh'])))
    assert missing['total']==1 and missing['items'][0]['goods_id']==w['g2']
    named=_ok(client.get(url+'&brand=Brand%20A',headers=_wh(access_token,w['wh'])))
    assert named['total']==1 and named['totals']['closing']==10

    ledger=_ok(client.get('/stock-ledger/?keyword=GRD-&brand_missing=1',headers=_wh(access_token,w['wh'])))
    assert ledger['total'] and all(row['goods_id']==w['g2'] for row in ledger['items'])
    overview=_ok(client.get('/stock-ledger/overview?keyword=GRD-&brand=Brand%20A',headers=_wh(access_token,w['wh'])))
    assert overview['totals']['closing']==10


def test_summary_export_all_pages_totals_and_spreadsheet_text(client,access_token,access_operator_token):
    w=_world(client)
    with client.application.app_context():
        db.session.get(Goods,w['g1']).name='=1+1'
        db.session.commit()
    _inbound(client,access_token,w,[(w['g1'],10),(w['g2'],3)],[])
    url='/stock-ledger/summary/export?keyword=GRD-&page=2&per_page=1'
    response=client.get(url,headers=_wh(access_token,w['wh']))
    assert response.status_code==200
    rows=list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip('\ufeff'))))
    assert len(rows)==3 and rows[-1]['row_type']=='total' and rows[-1]['closing']=='13'
    assert rows[0]['goods_name']=="'=1+1"
    assert sum(int(row['inbound_sort_in']) for row in rows[:-1])==13
    assert sum(int(row['closing_pending']) for row in rows[:-1])==13
    assert rows[-1]['closing_pending']=='13' and rows[-1]['opening_pending']=='0'
    assert all(row['closing_picked']=='0' and row['closing_packed']=='0' for row in rows)
    grouped=client.get(url+'&group_by=brand',headers=_wh(access_token,w['wh']))
    group_rows=list(csv.DictReader(io.StringIO(grouped.get_data(as_text=True).lstrip('\ufeff'))))
    assert len(group_rows)==2
    assert group_rows[0]['closing_pending']==group_rows[1]['closing_pending']=='13'
    scoped=client.get(url+f"&goods_id={w['g2']}&stock_types=pending",headers=_wh(access_token,w['wh']))
    scope_rows=list(csv.DictReader(io.StringIO(scoped.get_data(as_text=True).lstrip('\ufeff'))))
    assert scope_rows[-1]['closing_pending']=='3' and 'closing_onhand' not in scope_rows[-1]

    assert client.get(url,headers=_wh(access_operator_token,w['wh'])).status_code==403


def test_summary_calendar_timezone_and_explicit_drilldown_instants(client, access_token):
    from warehouse.warehouse.models import Warehouse
    original=client.application.config.get('WMS_DEFAULT_TIMEZONE')
    client.application.config['WMS_DEFAULT_TIMEZONE']='America/Los_Angeles'
    try:
        w=_world(client)
        with client.application.app_context():
            db.session.get(Warehouse,w['wh']).timezone='Asia/Tokyo'
            db.session.commit()
        dates='start_time=2026-03-08&end_time=2026-03-08'
        all_report=_ok(client.get('/stock-ledger/summary?'+dates,headers=_h(access_token)))
        assert all_report['timezone']=='America/Los_Angeles'
        assert all_report['start_time'].startswith('2026-03-08T08:00:00')
        assert all_report['end_time'].startswith('2026-03-09T06:59:59')
        local=_ok(client.get('/stock-ledger/summary?'+dates,headers=_wh(access_token,w['wh'])))
        assert local['timezone']=='Asia/Tokyo'
        assert local['start_time'].startswith('2026-03-07T15:00:00')
        explicit='start_time='+quote(all_report['start_time'])+'&end_time='+quote(all_report['end_time'])
        child=_ok(client.get('/stock-ledger/summary?'+explicit,headers=_wh(access_token,w['wh'])))
        assert child['start_time']==all_report['start_time']
        assert child['end_time']==all_report['end_time']
        overview=_ok(client.get('/stock-ledger/overview?'+explicit,headers=_wh(access_token,w['wh'])))
        assert overview['start_time']==all_report['start_time']
        assert overview['end_time']==all_report['end_time']
    finally:
        client.application.config['WMS_DEFAULT_TIMEZONE']=original


def test_metric_age_chart_counts_skus_per_bucket_and_limits_oldest():
    from types import SimpleNamespace
    from warehouse.stock.views import _metric_chart_summary
    rows=[]
    for ident, quantity in enumerate([1, 8, 8, 3, 9], 1):
        rows.append(SimpleNamespace(goods_id=ident, goods=SimpleNamespace(id=ident,code=str(ident),name=str(ident)),
            age_0_30=2,age_31_90=0,age_91_180=0,age_over_180=quantity,age_unknown=1,
            total_stock=quantity+3,out_qty_90d=0,abc_class=None,weighted_age_days=None,last_out_at=None))
    result=_metric_chart_summary(rows)
    assert result['age_sku_counts']==dict(age_0_30=5,age_31_90=0,age_91_180=0,age_over_180=5,unknown=5)
    assert [row['goods_id'] for row in result['aged_top']]==[5,2,3,4]
    assert result['aged_top'][0]['quantity']==9
    assert result['aged_top'][0]['weighted_age_days'] is None
    assert result['aged_top'][0]['last_out_at'] is None
    assert _metric_chart_summary([])['aged_top']==[]
    rows[0].out_qty_90d=10
    rows[0].abc_class='A'
    curve=_metric_chart_summary(rows)
    assert curve['abc_sku_total']==5 and curve['abc_unclassified']==4
    assert curve['abc_curve'][1]=={'sku_share':.2,'outbound_share':1}
    assert curve['abc_curve'][-1]=={'sku_share':1,'outbound_share':1}
    assert curve['abc_outbound']['A']==10
