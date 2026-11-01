from tests.helpers import *
from tests.test_inventory_guards import _h, _ok, _world
from tests.test_isolation_outbound import _make_company_b
from tests.test_pick_route_geometry import floor, shape
from warehouse.cyclecount.models import CycleCountTask, CycleCountTaskDetail


def test_count_route_orders_locations_and_preserves_counts(client, access_token):
    w = _world(client)
    with client.application.app_context():
        task = CycleCountTask(warehouse_id=w['wh'], created_by=w['admin'])
        db.session.add(task); db.session.flush()
        details = [CycleCountTaskDetail(task_id=task.id, goods_id=w['g1'], location_id=ident,
                                       system_quantity=7, actual_quantity=2) for ident in [w['std1'], w['std2']]]
        db.session.add_all(details); db.session.commit()
        ident = task.id
        ids = [row.id for row in details]
    url = f'/cyclecount/{ident}/route-plan'
    assert _ok(client.get(url, headers=_h(access_token)))['issue'] == 'no_published_map'
    layout = {'floors': [floor('f', [shape('start', 'station', 1, 1, 1, 1),
        shape('far', 'rack', 12, 10, 2, 3, location_ids=[w['std1']]),
        shape('near', 'rack', 4, 3, 2, 3, location_ids=[w['std2']])])]}
    base = f'/warehouse/{w["wh"]}/layout'
    saved = _ok(client.put(base, headers=_h(access_token), json={'revision': 0, 'layout': layout}))
    _ok(client.post(base+'/publish', headers=_h(access_token), json={'revision': saved['revision']}))
    result = _ok(client.get(url, headers=_h(access_token)))
    assert result['detail_order'] == list(reversed(ids))
    assert result['route']['distance'] > 0 and result['route']['returning']
    _ok(client.put(f'/warehouse/{w["wh"]}', headers=_h(access_token), json={'operations': {'route_enabled': False, 'picking_sort': 'manual'}}))
    with client.application.app_context():
        db.session.get(Location, w['std2']).picking_order = 1
        db.session.commit()
    disabled = _ok(client.get(url, headers=_h(access_token)))
    assert disabled['issue'] == 'route_disabled' and disabled['route'] is None
    assert disabled['detail_order'] == list(reversed(ids))
    foreign, _ = _make_company_b(client, company_admin=True)
    assert client.get(url, headers=foreign).status_code in (403, 404)
    with client.application.app_context():
        assert [(db.session.get(CycleCountTaskDetail, i).system_quantity,
                 db.session.get(CycleCountTaskDetail, i).actual_quantity) for i in ids] == [(7, 2), (7, 2)]
