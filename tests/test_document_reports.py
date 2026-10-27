from datetime import timedelta

import pytest
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.common import clock


@pytest.mark.parametrize('kind', ['asn', 'dn'])
def test_report_cohort_pagination_and_timezone(client, access_token, kind):
    with client.application.app_context():
        document = get_asn() if kind == 'asn' else get_dn()
        warehouse = db.session.get(Warehouse, document.warehouse_id)
        warehouse.timezone = 'Asia/Tokyo'
        today = clock.local_today(warehouse)
        scheduled = 'expected_arrival_date' if kind == 'asn' else 'expected_shipping_date'
        timestamp = 'received_at' if kind == 'asn' else 'delivered_at'
        setattr(document, scheduled, today)
        setattr(document, timestamp, clock.local_midnight(today, warehouse) + timedelta(hours=1))
        document.is_active = True
        document.status = 'received' if kind == 'asn' else 'delivered'
        wid, ident = warehouse.id, document.id
        db.session.commit()
    url = f'/{kind}/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    response = client.get(url, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    row = next(row for row in data['items'] if row['id'] == ident)
    assert row['actual_date'] == str(today)
    assert row['on_time'] is True
    assert data['timezone'] == 'Asia/Tokyo'
    second = client.get(url + '&page=2&per_page=1', headers=_h(access_token)).get_json()
    assert second['summary'] == data['summary']
    assert second['previous'] == data['previous']
    assert second['daily'] == data['daily']
    assert second['distributions'] == data['distributions']
    if kind == 'dn':
        for rows in data['distributions'].values():
            assert sum(row['count'] for row in rows) == data['summary']['documents']
            assert sum(row['previous'] for row in rows) == data['previous']['documents']
    assert sum(row['documents'] for row in data['daily']) == data['summary']['documents']
    assert sum(row['actual'] for row in data['daily']) == data['summary']['actual']
    import csv,io
    exported=client.get(url.replace('/report?','/report/export?')+'&page=99&per_page=1',headers=_h(access_token))
    assert exported.status_code==200
    rows=list(csv.DictReader(io.StringIO(exported.get_data(as_text=True).lstrip('\ufeff'))))
    assert len(rows)==data['total']
    assert sum(int(row['actual'] or 0) for row in rows)==data['summary']['actual']



def test_report_today_pending_is_not_late_and_empty_rate_is_null(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        warehouse = db.session.get(Warehouse, asn.warehouse_id)
        today = clock.local_today(warehouse)
        ASN.query.filter(ASN.warehouse_id == warehouse.id).update({'expected_arrival_date': today, 'received_at': None})
        wid = warehouse.id
        db.session.commit()
    data = client.get(f'/asn/report?warehouse_id={wid}&start_date={today}&end_date={today}', headers=_h(access_token)).get_json()
    assert data['summary']['due'] == 0
    assert data['summary']['on_time_rate'] is None
    assert data['summary']['actual'] == 0


@pytest.mark.parametrize('query', ['start_date=bad', 'start_date=2026-02-30', 'start_date=2026-02-02&end_date=2026-02-01', 'start_date=2020-01-01&end_date=2026-01-01', 'page=0', 'per_page=101', 'supplier_id=bad', 'asn_type=invalid'])
def test_report_invalid_filters(client, access_token, query):
    with client.application.app_context():
        wid = get_warehouse().id
    assert client.get(f'/asn/report?warehouse_id={wid}&{query}', headers=_h(access_token)).status_code == 400


def test_report_scope_and_permissions(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        reader = _staff('report_reader', _role('report_reader', ['asn_read']), [warehouse])
        token = create_access_token(identity=reader)
    assert client.get(f'/asn/report?warehouse_id={wid}', headers=_h(foreign['token'])).status_code in (403, 404)
    assert client.get(f'/asn/report?warehouse_id={wid}', headers=_h(token)).status_code == 200
    assert client.get(f'/dn/report?warehouse_id={wid}', headers=_h(token)).status_code == 403
    assert client.get(f'/dn/report/export?warehouse_id={wid}', headers=_h(token)).status_code == 403
    assert client.get(f'/asn/report/export?warehouse_id={wid}', headers=_h(foreign['token'])).status_code in (403,404)


def test_outbound_daily_uses_handover_date_not_planned_date(client, access_token):
    with client.application.app_context():
        document=get_dn()
        warehouse=db.session.get(Warehouse,document.warehouse_id)
        today=clock.local_today(warehouse)
        document.expected_shipping_date=today-timedelta(days=2)
        document.delivered_at=clock.local_midnight(today,warehouse)+timedelta(hours=1)
        document.status='delivered'
        document.is_active=True
        wid,ident=warehouse.id,document.id
        db.session.commit()
    response=client.get(f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}',headers=_h(access_token))
    assert response.status_code==200,response.get_json()
    data=response.get_json()
    row=next(row for row in data['items'] if row['id']==ident)
    assert row['planned']==str(today-timedelta(days=2))
    assert sum(day['late'] for day in data['daily'])>=1
    assert all(day['date']==str(today) for day in data['daily'])
    units = [unit for day in data['daily'] for unit in day['units']]
    assert units and any(unit['timing'] == 'late' for unit in units)
    assert all(unit['id'] > 0 and unit['actual'] >= 0 for unit in units)


@pytest.mark.parametrize('kind',['picking','packing'])
def test_operator_report_completed_task_quantity_and_elapsed_time(client,access_token,kind):
    from tests.test_inventory_guards import _world,_inbound,_picked_dn,_ok
    from warehouse.picking.models import PickingTask
    from warehouse.packing.models import PackingTask
    w=_world(client)
    _inbound(client,access_token,w,[(w['g1'],10)],[(w['g1'],w['std1'],10)])
    dn_id,packing_id=_picked_dn(client,access_token,w,[(w['g1'],4)],[(w['g1'],w['std1'],4)])
    _ok(client.post(f'/packing/{packing_id}/batches/',json={'details':[{'goods_id':w['g1'],'packed_quantity':4}]},headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/',headers=_h(access_token)))
    with client.application.app_context():
        task=(PickingTask if kind=='picking' else PackingTask).query.filter_by(dn_id=dn_id).one()
        task.completed_at=clock.now()
        task.started_at=task.completed_at-timedelta(hours=2)
        db.session.commit()
    url=f'/{kind}/report/operators?warehouse_id={w["wh"]}'
    report=_ok(client.get(url,headers=_h(access_token)))
    assert report['summary']['quantity']==4 and report['summary']['tasks']==1
    assert report['items'][0]['seconds']==7200
    assert report['items'][0]['per_hour']==2
    assert sum(day['quantity'] for day in report['daily'])==4
    assert sum(day['quantity'] for day in report['items'][0]['daily'])==4
    assert report['task_chart']['total']==1
    assert report['task_chart']['median_per_hour']==2
    assert report['task_chart']['items'][0]['quantity']==4
    assert report['task_chart']['items'][0]['seconds']==7200
    assert _ok(client.get(url+'&page=99&per_page=1',headers=_h(access_token)))['task_chart']==report['task_chart']
    import csv,io
    exported=client.get(url.replace('/operators?','/operators/export?')+'&page=99&per_page=1',headers=_h(access_token))
    assert exported.status_code==200
    rows=list(csv.DictReader(io.StringIO(exported.get_data(as_text=True).lstrip('\ufeff'))))
    assert len(rows)==1 and rows[0]['quantity']=='4' and float(rows[0]['per_hour'])==2

    assert _ok(client.get(url+'&page=2&per_page=1',headers=_h(access_token)))['summary']==report['summary']
    with client.application.app_context():
        task=(PickingTask if kind=='picking' else PackingTask).query.filter_by(dn_id=dn_id).one()
        task.started_at=None
        db.session.commit()
    report=_ok(client.get(url,headers=_h(access_token)))
    assert report['items'][0]['quantity']==4 and report['items'][0]['per_hour'] is None
    assert report['items'][0]['untimed_tasks']==1
    assert report['task_chart']==dict(items=[],total=0,untimed=1,median_per_hour=None)


def test_operator_report_permissions_and_foreign_warehouse(client,access_token,foreign):
    with client.application.app_context():
        warehouse=get_warehouse()
        wid=warehouse.id
        reader=_staff('operator_report_reader',_role('operator_report_reader',['picking_read']),[warehouse])
        token=create_access_token(identity=reader)
    assert client.get(f'/picking/report/operators?warehouse_id={wid}',headers=_h(token)).status_code==200
    assert client.get(f'/packing/report/operators?warehouse_id={wid}',headers=_h(token)).status_code==403
    assert client.get(f'/picking/report/operators?warehouse_id={wid}',headers=_h(foreign['token'])).status_code in (403,404)


@pytest.mark.parametrize('start,end,previous_start,previous_end', [
    ('2026-10-01','2026-10-25','2026-09-01','2026-09-25'),
    ('2026-03-01','2026-03-31','2026-02-01','2026-02-28'),
    ('2024-03-29','2024-03-31','2024-02-29','2024-02-29'),
    ('2026-01-01','2026-01-20','2025-12-01','2025-12-20'),
])
def test_report_previous_month_calendar_dates(client, access_token, start, end, previous_start, previous_end):
    with client.application.app_context():
        wid = get_warehouse().id
    for kind in ('asn','dn'):
        response = client.get(f'/{kind}/report?warehouse_id={wid}&start_date={start}&end_date={end}', headers=_h(access_token))
        assert response.status_code == 200
        data = response.get_json()
        assert (data['previous_start'],data['previous_end']) == (previous_start,previous_end)


def test_outbound_daily_tiles_bounded_without_losing_totals(client, access_token):
    with client.application.app_context():
        source = get_dn()
        warehouse = db.session.get(Warehouse, source.warehouse_id)
        today = clock.local_today(warehouse)
        for index in range(23):
            db.session.add(DN(warehouse_id=warehouse.id, recipient_id=source.recipient_id,
                              shipping_address=source.shipping_address, created_by=source.created_by,
                              expected_shipping_date=today, delivered_at=clock.local_midnight(today, warehouse),
                              status='delivered', is_active=True))
        wid = warehouse.id
        db.session.commit()
    url = f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    data = client.get(url+'&per_page=1', headers=_h(access_token)).get_json()
    bucket = next(day for day in data['daily'] if day['date'] == str(today))
    assert bucket['documents'] >= 23
    assert len(bucket['units']) == 20
    assert len(data['items']) == 1
    assert client.get(url+'&per_page=1&page=2', headers=_h(access_token)).get_json()['daily'] == data['daily']


def test_outbound_distribution_snapshot_and_drilldown(client, access_token):
    with client.application.app_context():
        document = get_dn()
        warehouse = db.session.get(Warehouse, document.warehouse_id)
        today = clock.local_today(warehouse)
        document.ship_to = {'country': 'jp'}
        document.delivered_at = clock.local_midnight(today, warehouse)
        document.status = 'delivered'
        document.is_active = True
        wid, ident = warehouse.id, document.id
        db.session.commit()
    url = f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    data = client.get(url, headers=_h(access_token)).get_json()
    assert any(row['value']=='JP' and row['count']>=1 for row in data['distributions']['country'])
    filtered = client.get(url+'&distribution_dimension=country&distribution_value=JP', headers=_h(access_token)).get_json()
    assert ident in [row['id'] for row in filtered['items']]
    assert all(row['value']=='JP' for row in filtered['distributions']['country'])
    empty = client.get(url+'&distribution_dimension=country&distribution_value=XX', headers=_h(access_token)).get_json()
    assert empty['total']==0 and empty['items']==[]
    assert client.get(url+'&distribution_dimension=secret&distribution_value=1', headers=_h(access_token)).status_code==400
    assert client.get(url+'&distribution_dimension=carrier', headers=_h(access_token)).status_code==400
    import csv, io
    exported = client.get(url.replace('/report?', '/report/export?')+'&distribution_dimension=country&distribution_value=JP', headers=_h(access_token))
    rows = list(csv.DictReader(io.StringIO(exported.get_data(as_text=True).lstrip('\ufeff'))))
    assert len(rows)==filtered['total']


def test_lead_time_uses_all_active_task_completion_and_keeps_missing_null(client, access_token):
    from warehouse.picking.models import PickingTask
    from warehouse.packing.models import PackingTask
    with client.application.app_context():
        document=get_dn()
        warehouse=db.session.get(Warehouse,document.warehouse_id)
        today=clock.local_today(warehouse)
        end=clock.local_midnight(today,warehouse)+timedelta(hours=12)
        document.created_at=end-timedelta(days=3)
        document.delivered_at=end
        document.expected_shipping_date=today
        document.status='delivered'
        document.is_active=True
        document.picked_at=end-timedelta(hours=1)  # DN summary is not the task completion source.
        PickingTask.query.filter_by(dn_id=document.id).update({'is_active':False})
        PackingTask.query.filter_by(dn_id=document.id).update({'is_active':False})
        for hours in (60,48):
            db.session.add(PickingTask(dn_id=document.id,created_by=document.created_by,status='completed',completed_at=end-timedelta(hours=hours)))
        db.session.add(PackingTask(dn_id=document.id,created_by=document.created_by,status='completed',completed_at=end-timedelta(hours=24)))
        wid,ident,creator=warehouse.id,document.id,document.created_by
        db.session.commit()
    url=f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    data=client.get(url,headers=_h(access_token)).get_json()
    row=next(row for row in data['items'] if row['id']==ident)
    assert row['lead_hours']==dict(pick=24,pack=24,handover=24,total=72)
    assert data['slowest']['total_hours'] >= 72
    other_page=client.get(url+'&page=99',headers=_h(access_token)).get_json()
    assert other_page['slowest']==data['slowest']
    assert other_page['monthly_on_time']==data['monthly_on_time']
    assert data['lead_time']['total']['count']>=1
    with client.application.app_context():
        db.session.add(PickingTask(dn_id=ident,created_by=creator,status='pending'))
        db.session.commit()
    data=client.get(url,headers=_h(access_token)).get_json()
    row=next(row for row in data['items'] if row['id']==ident)
    assert row['lead_hours']['pick'] is None and row['lead_hours']['pack'] is None
    assert row['lead_hours']['total']==72


def test_duration_percentiles_and_missing_values():
    from warehouse.common.document_reports import duration_summary, duration_hours
    from datetime import datetime, timezone
    assert duration_summary([])==dict(count=0,median_hours=None,p90_hours=None)
    result=duration_summary([None,0,10,20,30])
    assert result==dict(count=4,median_hours=15,p90_hours=27)
    now=datetime.now(timezone.utc)
    assert duration_hours(now,now)==0
    assert duration_hours(now,now-timedelta(seconds=1)) is None


def test_monthly_on_time_uses_local_handover_and_empty_months(client, access_token):
    with client.application.app_context():
        doc=get_dn()
        warehouse=db.session.get(Warehouse,doc.warehouse_id)
        warehouse.timezone='Asia/Tokyo'
        today=clock.local_today(warehouse)
        doc.expected_shipping_date=today-timedelta(days=2)
        doc.delivered_at=clock.local_midnight(today,warehouse)+timedelta(minutes=1)
        doc.status='delivered'
        doc.is_active=True
        wid,ident=warehouse.id,doc.id
        db.session.commit()
    result=client.get(f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}',headers=_h(access_token)).get_json()
    months=result['monthly_on_time']
    assert len(months)==6
    assert months[-1]['month']==today.isoformat()[:7]
    assert months[-1]['partial'] is True
    assert months[-1]['due']-months[-1]['on_time']>=1
    assert any(row['id']==ident and row['days_late']==2 for row in result['monthly_late'])
    for month in months:
        if month['due']==0:
            assert month['rate'] is None
        else:
            assert month['rate']==month['on_time']/month['due']

@pytest.mark.parametrize('kind',['picking','packing'])
def test_operator_task_chart_combines_people_without_multiplying_elapsed_time(client,access_token,kind):
    from tests.test_inventory_guards import _world,_inbound,_picked_dn,_ok
    from warehouse.picking.models import PickingTask
    from warehouse.packing.models import PackingTask
    from sqlalchemy import inspect
    w=_world(client)
    _inbound(client,access_token,w,[(w['g1'],10)],[(w['g1'],w['std1'],10)])
    dn_id,packing_id=_picked_dn(client,access_token,w,[(w['g1'],4)],[(w['g1'],w['std1'],4)])
    _ok(client.post(f'/packing/{packing_id}/batches/',json={'details':[{'goods_id':w['g1'],'packed_quantity':4}]},headers=_h(access_token)))
    _ok(client.put(f'/packing/{packing_id}/complete/',headers=_h(access_token)))
    with client.application.app_context():
        warehouse=db.session.get(Warehouse,w['wh'])
        warehouse.timezone='Asia/Tokyo'
        task=(PickingTask if kind=='picking' else PackingTask).query.filter_by(dn_id=dn_id).one()
        day=clock.local_today(warehouse)
        task.completed_at=clock.local_midnight(day,warehouse)+timedelta(minutes=30)
        task.started_at=task.completed_at-timedelta(hours=2)
        other=_staff('other_task_operator',None,[warehouse])
        detail=task.task_details[0]
        values={attr.key:getattr(detail,attr.key) for attr in inspect(type(detail)).column_attrs if attr.key!='id'}
        values['operator_id']=other.id
        values['picked_quantity' if kind=='picking' else 'packed_quantity']=3
        db.session.add(type(detail)(**values))
        db.session.commit()
    url=f'/{kind}/report/operators?warehouse_id={w["wh"]}&start_date={day}&end_date={day}&per_page=1'
    data=_ok(client.get(url,headers=_h(access_token)))
    assert data['total']==2 and len(data['items'])==1
    point=data['task_chart']['items'][0]
    assert data['task_chart']['total']==1
    assert point['quantity']==7 and point['seconds']==7200
    assert len(point['operators'])==2 and point['date']==str(day)
    assert data['task_chart']['median_per_hour']==3.5
    assert data['daily']==[dict(date=str(day),quantity=7)]
    assert data['items'][0]['daily']==[dict(date=str(day),quantity=4)]
    overview=_ok(client.get(f'/picking/report/operators/overview?warehouse_id={w["wh"]}&start_date={day}&end_date={day}&per_page=1',headers=_h(access_token)))
    assert overview['total']==2
    assert set(overview['phases'])=={'picking','packing'}
    assert len(overview['items'])==1
    assert overview['items'][0]['quantity']==8
    assert sum(d['quantity'] for d in overview['items'][0]['daily'])==8
    assert overview['phases'][kind]['task_chart']['items'][0]['seconds']==7200
    drill=_ok(client.get(f'/picking/report/operators/tasks?warehouse_id={w["wh"]}&start_date={day}&end_date={day}&operator_id={overview["items"][0]["operator_id"]}&per_page=1',headers=_h(access_token)))
    assert drill['total']==2 and len(drill['items'])==1
    assert drill['items'][0]['quantity']==4


@pytest.mark.parametrize('kind',['picking','packing'])
def test_operator_overview_omits_unauthorized_phase(client,access_token,foreign,kind):
    with client.application.app_context():
        warehouse=get_warehouse()
        wid=warehouse.id
        reader=_staff('overview_reader',_role('overview_reader',[kind+'_read']),[warehouse])
        token=create_access_token(identity=reader)
    report=client.get(f'/picking/report/operators/overview?warehouse_id={wid}',headers=_h(token))
    assert report.status_code==200
    assert set(report.get_json()['phases'])=={kind}
    other='packing' if kind=='picking' else 'picking'
    assert client.get(f'/picking/report/operators/tasks?warehouse_id={wid}&operator_id=1&kind={other}',headers=_h(token)).status_code==403
    assert client.get(f'/picking/report/operators/tasks?warehouse_id={wid}&operator_id=bad',headers=_h(token)).status_code==400
    assert client.get(f'/picking/report/operators/tasks?warehouse_id={wid}&operator_id=1',headers=_h(foreign['token'])).status_code in (403,404)
    assert client.get('/picking/report/operators/overview',headers=_h(token)).status_code==400
    assert client.get(f'/picking/report/operators/overview?warehouse_id={wid}',headers=_h(foreign['token'])).status_code in (403,404)

def test_inbound_spec_coverage_uses_actual_receipts_unique_goods_and_current_specs(client,access_token):
    from tests.test_inventory_guards import _world,_inbound,_ok
    w=_world(client)
    _inbound(client,access_token,w,[(w['g1'],10),(w['g2'],5)],[(w['g1'],w['std1'],10),(w['g2'],w['std1'],5)])
    _inbound(client,access_token,w,[(w['g1'],3)],[(w['g1'],w['std1'],3)])
    with client.application.app_context():
        warehouse=db.session.get(Warehouse,w['wh'])
        today=clock.local_today(warehouse)
        for asn in ASN.query.filter_by(warehouse_id=w['wh']).all():
            asn.expected_arrival_date=today+timedelta(days=10)
            asn.received_at=clock.local_midnight(today,warehouse)+timedelta(hours=1)
        g1,g2=db.session.get(Goods,w['g1']),db.session.get(Goods,w['g2'])
        g1.weight=1;g1.length=10;g1.width=20;g1.height=30
        g2.weight=0;g2.length=10;g2.width=20;g2.height=0
        db.session.commit()
    url=f'/asn/report?warehouse_id={w["wh"]}&start_date={today}&end_date={today}'
    data=_ok(client.get(url,headers=_h(access_token)))
    assert data['summary']['documents']==0
    specs=data['spec_coverage']
    assert specs['total']==2 and specs['complete']==1 and specs['weight']==1 and specs['dimensions']==1
    assert specs['missing_total']==1 and specs['missing'][0]['id']==w['g2']
    assert specs['missing'][0]['quantity']==5 and specs['missing'][0]['missing']==['weight','dimensions']
    assert _ok(client.get(url+'&page=99&per_page=1',headers=_h(access_token)))['spec_coverage']==specs
    tomorrow=today+timedelta(days=1)
    assert _ok(client.get(f'/asn/report?warehouse_id={w["wh"]}&start_date={tomorrow}&end_date={tomorrow}',headers=_h(access_token)))['spec_coverage']['total']==0


def test_inbound_supplier_filter_applies_to_summary_specs_and_export(client, access_token):
    import csv, io
    with client.application.app_context():
        asn = get_asn()
        warehouse = db.session.get(Warehouse, asn.warehouse_id)
        today = clock.local_today(warehouse)
        asn.expected_arrival_date = today
        asn.received_at = clock.local_midnight(today, warehouse) + timedelta(hours=1)
        asn.status = 'received'
        asn.is_active = True
        wid, supplier_id, ident = warehouse.id, asn.supplier_id, asn.id
        db.session.commit()
    url = f'/asn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    matched = client.get(url + f'&supplier_id={supplier_id}', headers=_h(access_token)).get_json()
    assert ident in [row['id'] for row in matched['items']]
    empty = client.get(url + '&supplier_id=2147483647', headers=_h(access_token)).get_json()
    assert empty['total'] == 0 and empty['summary']['actual'] == 0
    assert empty['spec_coverage']['total'] == 0
    exported = client.get(url.replace('/report?', '/report/export?') + '&supplier_id=2147483647', headers=_h(access_token))
    assert exported.status_code == 200
    assert list(csv.DictReader(io.StringIO(exported.get_data(as_text=True).lstrip('\ufeff')))) == []


def test_outbound_freight_preserves_currency_zero_missing_and_pagination(client,access_token):
    from warehouse.dn.models import DNCarrierShipment
    with client.application.app_context():
        source=get_dn()
        warehouse=db.session.get(Warehouse,source.warehouse_id)
        today=clock.local_today(warehouse)
        wid=warehouse.id
        for index,(currency,amount) in enumerate([('JPY',1000),('USD',20),('JPY',0),(None,30),('JPY',None)]):
            dn=DN(warehouse_id=wid,recipient_id=source.recipient_id,shipping_address=source.shipping_address,
                  expected_shipping_date=today,dn_type='shipping',status='delivered',created_by=source.created_by,
                  delivered_at=clock.local_midnight(today,warehouse)+timedelta(hours=2),carrier_id=source.carrier_id)
            db.session.add(dn);db.session.flush()
            db.session.add(DNCarrierShipment(dn_id=dn.id,status='active',source='api',carrier_id=source.carrier_id,
                           net_charge=amount,currency=currency))
            db.session.add(DNCarrierShipment(dn_id=dn.id,status='cancelled',source='api',carrier_id=source.carrier_id,
                           net_charge=99999,currency='JPY'))
        db.session.commit()
    url=f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    response=client.get(url,headers=_h(access_token))
    assert response.status_code==200,response.get_json()
    data=response.get_json()
    rows={row['currency']:row for row in data['freight']['items']}
    assert rows['JPY']['total']==1000 and rows['JPY']['count']==2 and rows['JPY']['average']==500
    assert rows['USD']['total']==20 and rows['USD']['average']==20
    assert data['freight']['missing']==2
    page=client.get(url+'&page=99&per_page=1',headers=_h(access_token)).get_json()
    assert page['freight']==data['freight']
    assert page['summary']['documents']==5


def test_inbound_difference_categories_do_not_cancel_each_other(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        warehouse = db.session.get(Warehouse, asn.warehouse_id)
        today = clock.local_today(warehouse)
        ASN.query.filter_by(warehouse_id=warehouse.id).update({'expected_arrival_date':today,'received_at':None})
        asn.received_at = clock.now()
        details = ASNDetail.query.filter_by(asn_id=asn.id).order_by(ASNDetail.id).all()
        assert len(details) >= 2
        for line in details:
            line.actual_quantity = line.quantity
            line.damage_quantity = 0
        details[0].actual_quantity = details[0].quantity - 2
        details[1].actual_quantity = details[1].quantity + 2
        details[1].damage_quantity = 1
        wid, ident = warehouse.id, asn.id
        expected = sum(line.quantity for line in details)
        db.session.commit()
    url=f'/asn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    response=client.get(url,headers=_h(access_token))
    assert response.status_code == 200, response.json
    result=response.json['receipt_differences']
    assert result['received']==1 and result['documents']==1 and result['expected']==expected
    assert result['types']=={'short':{'quantity':2,'documents':1},'over':{'quantity':2,'documents':1},'damage':{'quantity':1,'documents':1}}
    assert result['rejected'] is None
    assert result['items'][0]['id']==ident
    assert client.get(url+'&page=99&per_page=1',headers=_h(access_token)).json['receipt_differences']==result
    assert client.get(url+'&supplier_id=999999',headers=_h(access_token)).json['receipt_differences']['received']==0
