from calendar import monthrange
from datetime import date, timedelta
from tests.helpers import *
from tests.test_inventory_availability import _h
from warehouse.common import clock
from warehouse.dn.models import DNCarrierShipment, DNPackage


def test_freight_per_kg_uses_only_paired_known_amount_and_positive_package_weights(client, access_token):
    with client.application.app_context():
        source = get_dn()
        warehouse = db.session.get(Warehouse, source.warehouse_id)
        today = clock.local_today(warehouse)
        wid = warehouse.id
        for currency, amount, weights in [('JPY',100,[2]),('JPY',0,[1,2]),('JPY',900,[]),('JPY',200,[2,0]),('USD',20,[2])]:
            dn = DN(warehouse_id=wid, recipient_id=source.recipient_id, shipping_address='QA',
                expected_shipping_date=today, dn_type='shipping', status='delivered', created_by=source.created_by,
                delivered_at=clock.local_midnight(today,warehouse), carrier_id=source.carrier_id)
            db.session.add(dn); db.session.flush()
            db.session.add(DNCarrierShipment(dn_id=dn.id,status='active',source='api',carrier_id=source.carrier_id,
                net_charge=amount,currency=currency))
            db.session.add(DNCarrierShipment(dn_id=dn.id,status='cancelled',source='api',carrier_id=source.carrier_id,
                net_charge=99999,currency=currency))
            for number, weight in enumerate(weights, 1):
                db.session.add(DNPackage(dn_id=dn.id,package_no=number,gross_weight_kg=weight,
                    length_mm=100,width_mm=100,height_mm=100,created_by=source.created_by))
        db.session.commit()
    url=f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    response=client.get(url,headers=_h(access_token));assert response.status_code==200,response.json
    rows={row['currency']:row for row in response.json['freight']['items']}
    assert rows['JPY']['count']==4 and rows['JPY']['total']==1200
    assert rows['JPY']['weighted_count']==2 and rows['JPY']['gross_weight_kg']==5
    assert rows['JPY']['weighted_amount']==100 and rows['JPY']['per_kg']==20
    assert rows['USD']['per_kg']==10
    assert client.get(url+'&page=99&per_page=1',headers=_h(access_token)).json['freight']==response.json['freight']


def test_outbound_issues_use_planned_cohort_and_current_shared_shipping_stage(client, access_token):
    with client.application.app_context():
        source=get_dn();warehouse=db.session.get(Warehouse,source.warehouse_id)
        today=clock.local_today(warehouse);wid=warehouse.id
        year,month=(today.year-1,12) if today.month==1 else (today.year,today.month-1)
        prior=date(year,month,min(today.day,monthrange(year,month)[1]))
        DN.query.filter_by(warehouse_id=wid).update({'expected_shipping_date':date(2000,1,1)})
        ids=[]
        for planned,status,trouble,active in [(today,'delivered',True,True),(today,'closed',False,True),
                (today,'delivered',False,True),(prior,'delivered',True,True),(today,'closed',False,False)]:
            dn=DN(warehouse_id=wid,recipient_id=source.recipient_id,shipping_address='QA',
                expected_shipping_date=planned,dn_type='shipping',status=status,created_by=source.created_by,
                delivered_at=clock.local_midnight(planned,warehouse) if status=='delivered' else None,
                carrier_id=source.carrier_id,is_active=active)
            db.session.add(dn);db.session.flush();ids.append(dn.id)
            if trouble:db.session.add(DNCarrierShipment(dn_id=dn.id,status='active',source='api',
                carrier_id=source.carrier_id,tracking_status='exception'))
        db.session.commit()
    url=f'/dn/report?warehouse_id={wid}&start_date={today}&end_date={today}'
    response=client.get(url,headers=_h(access_token));assert response.status_code==200,response.json
    report=response.json;issues=report['outbound_issues']
    assert issues['documents']==3 and issues['exception']==issues['closed']==1
    assert issues['exception_rate']==issues['closed_rate']==1/3
    assert {row['id'] for row in issues['items']}==set(ids[:2])
    assert report['summary']['documents']==2  # shipped cohort still excludes closed orders
    previous=report['previous_outbound_issues']
    assert previous['documents']==previous['exception']==1 and previous['closed']==0
    assert client.get(url+'&page=99&per_page=1',headers=_h(access_token)).json['outbound_issues']==issues
    for suffix in ['&recipient_id=999999','&dn_type=transfer','&distribution_dimension=carrier&distribution_value=none']:
        empty=client.get(url+suffix,headers=_h(access_token)).json['outbound_issues']
        assert empty['documents']==0 and empty['exception_rate'] is None and empty['items']==[]
