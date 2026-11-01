from tests.helpers import *
from tests.test_inventory_guards import _world
from tests.test_lot_operations import receive_lots, pick_lots
from tests.test_inventory_availability import _h


def test_live_distribution_keeps_lot_and_picked_identity_excludes_transit(client, access_token):
    w = _world(client)
    a, b = receive_lots(client, access_token, w)
    pick_lots(client, access_token, w, [(a, 2), (b, 3)])
    with client.application.app_context():
        add_stock(w['g1'], w['wh'], pending=1, in_transit=4)
    response = client.get(f'/inventory/metrics/goods/{w["g1"]}', headers=_h(access_token, w['wh']))
    assert response.status_code == 200
    data = response.get_json()
    rows = data['distribution']
    assert sum(row['quantity'] for row in rows) == data['total_stock'] == 11
    assert not any(row['stock_type'] == 'in_transit' for row in rows)
    assert {(row['lot_id'], row['quantity']) for row in rows if row['stock_type'] == 'picked'} == {(a, 2), (b, 3)}
    assert {(row['lot_id'], row['quantity']) for row in rows if row['location_id'] == w['std1']} == {(a, 3), (b, 2)}
    assert all(row['lot_no'] for row in rows if row['lot_id'])
    with client.application.app_context():
        from warehouse.goods.models import Goods
        from warehouse.stock.lot_policy import effective_rules
        assert data['lot_rules'] == effective_rules(db.session.get(Goods, w['g1']))
    layers = data['lot_age_layers']
    assert sum(row['quantity'] for row in layers) == 11
    assert sum(row['quantity'] for row in layers if row['age_days'] is None) == 1
    assert {(row['lot_id'], row['quantity']) for row in layers if row['age_days'] is not None} == {(a, 5), (b, 5)}
