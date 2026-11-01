import pytest
from tests.helpers import *
from tests.test_inventory_guards import _h, _ok
from tests.test_picking_location_type import _setup


def test_nullable_operations_roundtrip_and_reset(client, access_token):
    from warehouse.warehouse.operations import DEFAULTS
    with client.application.app_context():
        ident = get_warehouse().id
    url = f'/warehouse/{ident}'
    original = _ok(client.get(url, headers=_h(access_token)))
    assert original['operations'] is None
    assert _ok(client.put(url, headers=_h(access_token), json={**original, 'name': 'Roundtrip'}))['name'] == 'Roundtrip'
    _ok(client.put(url, headers=_h(access_token), json={'operations': {'allow_unassigned': False, 'picking_sort': 'manual'}}))
    assert _ok(client.put(url, headers=_h(access_token), json={'operations': None}))['operations'] == DEFAULTS


def test_warehouse_unassigned_policy_controls_availability_plan_and_shipping(client, access_token):
    from warehouse.stock.models import GoodsLotRules
    from warehouse.stock.lot_policy import blocked_onhand, blocked_stock_map, effective_rules
    from warehouse.stock.lots import assert_shippable
    from warehouse.picking.print_plan import print_plan
    from extensions.error import BadRequestException
    w, _, task_id = _setup(client, access_token)
    with client.application.app_context():
        db.session.add(GoodsLotRules(goods_id=w['g1'], overrides={'mode':'lot','allow_unassigned':True}))
        db.session.commit()
    url = f"/warehouse/{w['wh']}"
    saved = _ok(client.put(url, headers=_h(access_token), json={'operations':{'allow_unassigned':False}}))
    assert saved['operations']['allow_unassigned'] is False
    with client.application.app_context():
        assert blocked_onhand(w['g1'],w['wh']) == 6
        assert blocked_stock_map([(w['g1'],w['wh'])])[(w['g1'],w['wh'])] == 6
        assert effective_rules(db.session.get(Goods,w['g1']))['values']['allow_unassigned'] is True
        assert effective_rules(db.session.get(Goods,w['g1']),warehouse_id=w['wh'])['sources']['allow_unassigned'] == 'warehouse'
        with pytest.raises(BadRequestException): assert_shippable(w['g1'],None,w['wh'])
        assert print_plan(db.session.get(PickingTask,task_id))['shortages'][0]['quantity'] == 5
    _ok(client.put(url,headers=_h(access_token),json={'operations':{'allow_unassigned':None}}))
    with client.application.app_context():
        assert blocked_onhand(w['g1'],w['wh']) == 0
        assert blocked_stock_map([(w['g1'],w['wh'])]) == {}
        assert_shippable(w['g1'],None,w['wh'])
        assert not print_plan(db.session.get(PickingTask,task_id))['shortages']


@pytest.mark.parametrize('operations', [{'allow_unassigned':'false'},{'map_enabled':False},
    {'route_enabled':'true'},{'start_station_id':'foreign-station'},{'picking_sort':'bad'},{'surprise':True}])
def test_warehouse_operations_invalid(client, access_token, operations):
    with client.application.app_context(): ident=get_warehouse().id
    assert client.put(f'/warehouse/{ident}',headers=_h(access_token),json={'operations':operations}).status_code == 400


def test_location_position_validation_and_ordering(client, access_token):
    from warehouse.location.ordering import location_key
    w, _, _ = _setup(client,access_token)
    url=f"/location/{w['std1']}"
    for key in ('picking_order','rack_column','rack_level'):
        for bad in (0,-1,True,1.5,'2'):
            assert client.put(url,headers=_h(access_token),json={key:bad}).status_code==400
    result=_ok(client.put(url,headers=_h(access_token),json={'picking_order':2,'rack_column':3,'rack_level':1}))
    assert result['picking_order']==2 and result['rack_column']==3 and result['rack_level']==1
    with client.application.app_context():
        warehouse=db.session.get(Warehouse,w['wh'])
        one=db.session.get(Location,w['std1']);two=db.session.get(Location,w['std2'])
        one.code='ORDER-10';two.code='ORDER-2'
        assert sorted([one,two],key=lambda row:location_key(row,warehouse))==[two,one]
        warehouse.operations={'picking_sort':'manual'}
        assert sorted([one,two],key=lambda row:location_key(row,warehouse))==[one,two]
    result=_ok(client.put(url,headers=_h(access_token),json={'picking_order':None}))
    assert result['picking_order'] is None
