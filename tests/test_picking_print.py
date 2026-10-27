from tests.helpers import *
from tests.test_picking_location_type import _setup, _batch
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_outbound import _make_company_b


def test_print_plan_remaining_standard_stock_and_completed_record(client, access_token):
    w,dn_id,task_id=_setup(client,access_token)
    url=f'/picking/{task_id}/print-plan'
    plan=_ok(client.get(url,headers=_h(access_token)))
    assert plan['mode']=='suggestion'
    assert sum(row['quantity'] for row in plan['rows'])==5
    assert len(plan['rows'])==1 and plan['shortages']==[]
    with client.application.app_context():
        assert PickingTaskDetail.query.filter_by(picking_task_id=task_id).count()==0
        assert GoodsLocation.query.filter_by(goods_id=w['g1'],location_id=w['std1']).one().quantity==6
    _ok(_batch(client,access_token,task_id,w['g1'],w['std1'],2))
    plan=_ok(client.get(url,headers=_h(access_token)))
    assert sum(row['quantity'] for row in plan['rows'])==3
    _ok(client.put(f'/picking/{task_id}/complete/',headers=_h(access_token)))
    plan=_ok(client.get(url,headers=_h(access_token)))
    assert plan['mode']=='record' and sum(row['quantity'] for row in plan['rows'])==2


def test_print_plan_does_not_suggest_inactive_or_nonstandard_locations(client,access_token):
    w,_,task_id=_setup(client,access_token)
    with client.application.app_context():
        db.session.get(Location,w['std1']).is_active=False
        db.session.commit()
    plan=_ok(client.get(f'/picking/{task_id}/print-plan',headers=_h(access_token)))
    assert plan['rows']==[]
    assert sum(row['quantity'] for row in plan['shortages'])==5


def test_print_plan_rejects_foreign_warehouse(client,access_token):
    _,_,task_id=_setup(client,access_token)
    headers,_=_make_company_b(client,company_admin=True)
    assert client.get(f'/picking/{task_id}/print-plan',headers=headers).status_code in (403,404)
