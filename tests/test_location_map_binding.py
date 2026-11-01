from tests.helpers import *
from tests.test_inventory_guards import _h,_ok
from tests.test_picking_location_type import _setup
from tests.test_pick_route_geometry import floor,shape
from tests.test_isolation_outbound import _make_company_b


def test_published_code_binding_manual_override_bounds_and_scope(client,access_token):
    w,_,task_id=_setup(client,access_token);headers=_h(access_token)
    location_url=f"/location/{w['std1']}"
    _ok(client.put(location_url,headers=headers,json={'code':'AUTO-01-3'}))
    layout={'floors':[floor('f',[shape('start','station',1,1,1,1),
        shape('rack-a','rack',8,8,2,4,code='AUTO-01',columns=2,levels=5,location_ids=[]),
        shape('rack-b','rack',13,8,2,4,code='OTHER',columns=1,levels=2,location_ids=[])])]}
    map_url=f"/warehouse/{w['wh']}/layout"
    saved=_ok(client.put(map_url,headers=headers,json={'revision':0,'layout':layout}))
    assert _ok(client.get(f"/location/map-options/{w['wh']}",headers=headers))['racks']==[]
    _ok(client.post(map_url+'/publish',headers=headers,json={'revision':saved['revision']}))
    snapshot=_ok(client.get(map_url+'/versions/1',headers=headers))
    binding=snapshot['bindings'][str(w['std1'])]
    assert binding['rack_id']=='rack-a' and binding['level']==3 and binding['column']==1 and binding['source']=='code'
    assert snapshot['layout']['floors'][0]['objects'][1]['location_ids']==[]
    route=_ok(client.get(f'/picking/{task_id}/route-plan',headers=headers))
    assert not route['unmapped'] and route['stops'][0]['rack_name']=='rack-a'
    for patch in ({'map_rack_id':'foreign'},{'map_rack_id':'rack-b','rack_column':2},
                  {'map_rack_id':'rack-b','rack_level':3}):
        assert client.put(location_url,headers=headers,json=patch).status_code==400
    _ok(client.put(location_url,headers=headers,json={'map_rack_id':'rack-b','rack_column':1,'rack_level':2}))
    binding=_ok(client.get(map_url+'/versions/1',headers=headers))['bindings'][str(w['std1'])]
    assert binding['rack_id']=='rack-b' and binding['source']=='location'
    foreign,_=_make_company_b(client,company_admin=True)
    assert client.get(f"/location/map-options/{w['wh']}",headers=foreign).status_code in (403,404)
