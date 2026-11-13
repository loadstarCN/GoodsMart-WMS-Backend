import pytest
from tests.helpers import *
from tests.test_audit_count_and_scope import _count
from tests.test_tenant_scope_regressions import _h


@pytest.mark.parametrize('actual', [0, 99, 100])
def test_accuracy_includes_batch_recorded_lines(client, access_token, actual):
    task, _, wh, before = _count(client, access_token)
    h = _h(access_token)
    assert client.put(f'/cyclecount/{task}/process/', headers=h).status_code == 200
    rows = client.get(f'/cyclecount/{task}/details/', headers=h).json
    assert client.post(f'/cyclecount/{task}/details-batch-save/', headers=h,
        json={'details': [{'id': rows[0]['id'], 'actual_quantity': actual}]}).status_code == 201
    assert client.put(f'/cyclecount/{task}/complete/', headers=h).status_code == 200
    response = client.get(f'/cyclecount/report/accuracy?warehouse_id={wh}', headers=h)
    assert response.status_code == 200, response.json
    reported = next(row for row in response.json['items'] if row['id'] == task)
    assert reported['lines'] == 1 and reported['unverified'] == 0
    assert reported['different'] == int(actual != before)
    assert reported['difference'] == abs(actual - before)
    summary = response.json['summary']
    assert summary['line_accuracy'] == int(actual == before)
    assert summary['quantity_accuracy'] == pytest.approx(1 - abs(actual - before) / before)


@pytest.mark.parametrize('by_filter', [False, True])
def test_threshold_conflict_rolls_back_the_whole_batch(client, access_token, monkeypatch, by_filter):
    from warehouse.stock import thresholds
    from tests.test_inventory_guards import _world
    w = _world(client)
    with client.application.app_context():
        for goods_id in (w['g1'], w['g2']):
            db.session.add(Inventory(goods_id=goods_id, warehouse_id=w['wh'],
                low_stock_threshold=5, high_stock_threshold=20))
        db.session.commit()
    original = thresholds._apply
    def interleave(warehouse, plans, dry_run):
        # Writer B commits after writer A validated its stale thresholds.
        with client.application.app_context():
            inv = db.session.get(Inventory, (w['g2'], w['wh']))
            inv.high_stock_threshold = 10
            db.session.commit()
        return original(warehouse, plans, dry_run)
    monkeypatch.setattr(thresholds, '_apply', interleave)
    body = ({'filter': {'goods_ids': [w['g1'], w['g2']]}, 'low_stock_threshold': 15} if by_filter else
            {'items': [{'goods_id': ident, 'low_stock_threshold': 15} for ident in (w['g1'], w['g2'])]})
    response = client.put('/inventory/thresholds' + ('/by-filter' if by_filter else ''),
                          headers=_h(access_token, w['wh']), json=body)
    assert response.status_code == 409, response.json
    with client.application.app_context():
        first = db.session.get(Inventory, (w['g1'], w['wh']))
        second = db.session.get(Inventory, (w['g2'], w['wh']))
        assert (first.low_stock_threshold, first.high_stock_threshold) == (5, 20)
        assert (second.low_stock_threshold, second.high_stock_threshold) == (5, 10)


def test_threshold_conflict_when_inventory_is_created_during_request(client, access_token, monkeypatch):
    from warehouse.stock import thresholds
    from tests.test_inventory_guards import _world
    w = _world(client)
    original = thresholds._apply
    def interleave(warehouse, plans, dry_run):
        with client.application.app_context():
            assert db.session.get(Inventory, (w['g1'], w['wh'])) is None
            db.session.add(Inventory(goods_id=w['g1'], warehouse_id=w['wh'],
                low_stock_threshold=5, high_stock_threshold=10))
            db.session.commit()
        return original(warehouse, plans, dry_run)
    monkeypatch.setattr(thresholds, '_apply', interleave)
    response = client.put('/inventory/thresholds', headers=_h(access_token, w['wh']),
        json={'items': [{'goods_id': w['g1'], 'low_stock_threshold': 15}]})
    assert response.status_code == 409, response.json
    with client.application.app_context():
        inv = db.session.get(Inventory, (w['g1'], w['wh']))
        assert (inv.low_stock_threshold, inv.high_stock_threshold) == (5, 10)
