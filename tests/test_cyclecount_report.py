from datetime import timedelta
import pytest
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role,_staff
from tests.test_isolation_inbound import foreign
from warehouse.common import clock
from warehouse.cyclecount.models import CycleCountTask, CycleCountTaskDetail


def test_accuracy_snapshot_denominators_timezone_and_pagination(client,access_token):
    with client.application.app_context():
        warehouse=get_warehouse();warehouse.timezone='Asia/Tokyo';day=clock.local_today(warehouse)
        task=CycleCountTask(warehouse_id=warehouse.id,created_by=get_admin_user().id,status='completed',completed_at=clock.local_midnight(day,warehouse)+timedelta(minutes=15),is_active=True)
        db.session.add(task);db.session.flush()
        goods=Goods.query.first();location=Location.query.filter_by(warehouse_id=warehouse.id).first()
        for system,actual,diff in [(10,8,-2),(5,5,0),(None,0,None)]:
            db.session.add(CycleCountTaskDetail(task_id=task.id,goods_id=goods.id,location_id=location.id,status='completed',system_quantity=system,actual_quantity=actual,difference=diff))
        wid=warehouse.id;db.session.commit()
    url=f'/cyclecount/report/accuracy?warehouse_id={wid}&start_date={day}&end_date={day}'
    response=client.get(url,headers=_h(access_token));assert response.status_code==200,response.get_json()
    report=response.get_json();summary=report['summary']
    assert summary['tasks']==1 and summary['lines']==2 and summary['unverified']==1
    assert summary['line_accuracy']==.5 and summary['quantity_accuracy']==pytest.approx(1-2/15)
    assert len(report['monthly'])==6 and report['monthly'][-1]['accuracy']==.5
    assert all(m['accuracy'] is None for m in report['monthly'][:-1])
    assert report['items'][0]['date']==str(day)
    second=client.get(url+'&page=2&per_page=1',headers=_h(access_token)).get_json()
    assert second['items']==[] and second['summary']==summary
    tomorrow=day+timedelta(days=1)
    empty=client.get(f'/cyclecount/report/accuracy?warehouse_id={wid}&start_date={tomorrow}&end_date={tomorrow}',headers=_h(access_token)).get_json()
    assert empty['summary']['line_accuracy'] is None and empty['summary']['quantity_accuracy'] is None


def test_accuracy_requires_count_read_and_owned_warehouse(client,access_token,foreign):
    with client.application.app_context():
        warehouse=get_warehouse();wid=warehouse.id
        reader=_staff('asn_only_accuracy',_role('asn_only_accuracy',['asn_read']),[warehouse])
        token=create_access_token(identity=reader)
    assert client.get(f'/cyclecount/report/accuracy?warehouse_id={wid}',headers=_h(token)).status_code==403
    assert client.get(f'/cyclecount/report/accuracy?warehouse_id={wid}',headers=_h(foreign['token'])).status_code in (403,404)
