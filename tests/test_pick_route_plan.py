from tests.helpers import *
from tests.test_inventory_guards import _h, _ok, _inv
from tests.test_picking_location_type import _setup
from tests.test_isolation_outbound import _make_company_b
from tests.test_pick_route_geometry import floor, shape


def test_route_uses_published_map_only_and_preserves_stock(client, access_token):
    w,_,task_id=_setup(client,access_token)
    url=f'/picking/{task_id}/route-plan'
    assert _ok(client.get(url,headers=_h(access_token)))['issue']=='no_published_map'
    layout={'floors':[floor('f',[shape('start','station',1,1,1,1),
        shape('rack','rack',8,8,2,4,location_ids=[w['std1']],pick_face='front'),
        shape('finish','station',3,1,1,1)])]}
    map_url=f'/warehouse/{w["wh"]}/layout'
    saved=_ok(client.put(map_url,headers=_h(access_token),json={'revision':0,'layout':layout}))
    assert _ok(client.get(url,headers=_h(access_token)))['issue']=='no_published_map'
    published=_ok(client.post(map_url+'/publish',headers=_h(access_token),json={'revision':saved['revision']}))
    before=_inv(client,w['g1'],w['wh'])
    result=_ok(client.get(url,headers=_h(access_token)))
    assert result['version']==1 and result['issue'] is None
    assert result['stops'][0]['items'][0]['quantity']==5
    assert result['route']['legs'][0]['stop_id']==w['std1']
    assert result['route']['returning'] is not None
    resumed=_ok(client.get(url+f'?start_location_id={w["std1"]}',headers=_h(access_token)))
    assert resumed['route']['distance'] < result['route']['distance']
    assert resumed['route']['returning']['path'][-1][1:]==[1.5,1.5]
    assert client.get(url+f'?start_location_id={w["std2"]}',headers=_h(access_token)).status_code==400
    assert result['editable'] is True
    settings_url=f'/warehouse/{w["wh"]}'
    _ok(client.put(settings_url,headers=_h(access_token),json={'operations':{'start_station_id':'start','end_station_id':'finish'}}))
    configured=_ok(client.get(url,headers=_h(access_token)))
    assert configured['station_id']=='start' and configured['end_station_id']=='finish'
    assert configured['route']['returning']['path'][-1][1:]==[3.5,1.5]
    _ok(client.put(settings_url,headers=_h(access_token),json={'operations':{'route_enabled':False}}))
    disabled=_ok(client.get(url,headers=_h(access_token)))
    assert disabled['issue']=='route_disabled' and disabled['route'] is None and len(disabled['unmapped'])==1
    _ok(client.put(settings_url,headers=_h(access_token),json={'operations':{'route_enabled':True,'start_station_id':None,'end_station_id':None}}))
    assert _inv(client,w['g1'],w['wh'])==before
    layout['floors'][0]['objects'][1]['location_ids']=[]
    saved=_ok(client.put(map_url,headers=_h(access_token),json={'revision':published['revision'],'layout':layout}))
    assert _ok(client.get(url,headers=_h(access_token)))['unmapped']==[]
    _ok(client.post(map_url+'/publish',headers=_h(access_token),json={'revision':saved['revision']}))
    assert len(_ok(client.get(url,headers=_h(access_token)))['unmapped'])==1
    assert _ok(client.get(url+'?version=1',headers=_h(access_token)))['unmapped']==[]
    assert client.get(url+'?station_id=foreign',headers=_h(access_token)).status_code==400
    assert client.get(url+'?version=999',headers=_h(access_token)).status_code==400
    foreign_headers,_=_make_company_b(client,company_admin=True)
    assert client.get(url,headers=foreign_headers).status_code in (403,404)
