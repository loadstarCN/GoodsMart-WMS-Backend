import pytest
from tests.helpers import *
from tests.test_inventory_guards import _h,_ok
from tests.test_isolation_outbound import _make_company_b


def payload(client):
    with client.application.app_context(): warehouse_id=get_warehouse().id
    return dict(warehouse_id=warehouse_id,zone='QA-GEN',rack_from=1,rack_to=2,level_from=1,level_to=3,rack_digits=2)


def test_preview_generation_replay_and_existing_preservation(client,access_token):
    body=payload(client);headers=_h(access_token);url='/location/generate'
    preview=_ok(client.post(url,headers=headers,json=body))
    assert preview['pending']==6 and preview['created']==0
    assert preview['items'][0]['code']=='QA-GEN-01-1' and preview['items'][-1]['code']=='QA-GEN-02-3'
    created=_ok(client.post(url,headers=headers,json={**body,'execute':True}))
    assert created['created']==6 and all(row['id'] for row in created['items'])
    replay=_ok(client.post(url,headers=headers,json={**body,'execute':True,'description':'must not overwrite'}))
    assert replay['created']==0 and replay['existing']==6
    detail=_ok(client.get('/location/'+str(created['items'][0]['id']),headers=headers))
    assert detail['description'] is None and detail['rack_column']==1 and detail['rack_level']==1
    assert _ok(client.post(url,headers=headers,json=body))['pending']==0
    foreign,_=_make_company_b(client,company_admin=True)
    assert client.post(url,headers=foreign,json={**body,'execute':True}).status_code in (403,404)


@pytest.mark.parametrize('patch',[{'rack_to':0},{'rack_from':3},{'level_to':1000},{'rack_digits':7},
    {'rack_from':True},{'execute':'true'},{'location_type':'bad'},{'width':-1}])
def test_generation_invalid(client,access_token,patch):
    assert client.post('/location/generate',headers=_h(access_token),json={**payload(client),**patch}).status_code==400
