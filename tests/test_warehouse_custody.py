from uuid import uuid4
from tests.helpers import *
from tests.test_inventory_guards import _world,_h,_ok,_inbound,_picked_dn,_task_ids


def command(**data):return dict(request_id=uuid4().hex,reason='Warehouse test',**data)


def race(client, calls):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    with client.application.app_context():
        if db.engine.dialect.name != 'postgresql':
            pytest.skip('Requires PostgreSQL row locks')
    barrier=Barrier(len(calls))
    def invoke(call):
        with client.application.test_client() as worker:
            barrier.wait(timeout=10)
            result=call(worker)
            return result.status_code, result.get_json()
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(invoke,calls))


def test_concurrent_transfer_receipt_posts_once(client,access_token):
    w=transfer_world(client,access_token);h=_h(access_token)
    doc=_ok(client.post('/transfer/orders',headers=h,json=command(source_warehouse_id=w['wh'],destination_warehouse_id=w['dest'],lines=[dict(goods_id=w['g1'],source_location_id=w['std1'],quantity=10)])))
    url=f"/transfer/orders/{doc['id']}";line=doc['lines'][0]['id']
    _ok(client.post(url+'/ship',headers=h,json=command(lines=[dict(id=line,quantity=10)])))
    body=command(lines=[dict(id=line,quantity=10,location_id=w['dest_loc'])])
    results=race(client,[lambda c:c.post(url+'/receive',headers=h,json=body)]*2)
    assert [r[0] for r in results]==[200,200],results
    with client.application.app_context():
        assert db.session.get(Inventory,(w['g1'],w['dest'])).onhand_stock==10


def test_concurrent_return_authorization_cannot_exceed_shipped(client,access_token):
    w=return_world(client,access_token);h=_h(access_token)
    def authorize(c):
        return c.post('/asn/returns',headers=h,json=command(warehouse_id=w['wh'],original_dn_id=w['dn'],lines=[dict(goods_id=w['g1'],quantity=3)]))
    results=race(client,[authorize,authorize])
    assert sorted(r[0] for r in results)==[201,409],results


def transfer_world(client,token):
    w=_world(client)
    _inbound(client,token,w,[(w['g1'],10)],[(w['g1'],w['std1'],10)])
    with client.application.app_context():
        company=db.session.get(Warehouse,w['wh']).company_id
        other=Warehouse.query.filter(Warehouse.company_id==company,Warehouse.id!=w['wh']).first()
        loc=Location(warehouse_id=other.id,code='TRANSFER-IN',location_type='standard',created_by=w['admin'])
        db.session.add(loc);db.session.commit();w.update(dest=other.id,dest_loc=loc.id)
    return w


def test_transfer_partial_receive_idempotency_loss_and_cancel(client,access_token):
    w=transfer_world(client,access_token);h=_h(access_token)
    source_headers={**h,'X-WAREHOUSE-ID':str(w['wh'])}
    doc=_ok(client.post('/transfer/orders',headers=source_headers,json=dict(source_warehouse_id=w['wh'],destination_warehouse_id=w['dest'],
        reason='Rebalance',lines=[dict(goods_id=w['g1'],source_location_id=w['std1'],quantity=10)])))
    ident=doc['id'];line=doc['lines'][0]['id'];url=f'/transfer/orders/{ident}'
    _ok(client.post(url+'/ship',headers=h,json=command(lines=[dict(id=line,quantity=10)])))
    body=command(lines=[dict(id=line,quantity=8,location_id=w['dest_loc'])])
    for _ in range(2):doc=_ok(client.post(url+'/receive',headers=h,json=body))
    assert doc['lines'][0]['outstanding']==2
    assert client.post(url+'/receive',headers=h,json={**body,'lines':[dict(id=line,quantity=7,location_id=w['dest_loc'])]}).status_code==409
    assert client.post(url+'/receive',headers=h,json=command(lines=[dict(id=line,quantity=3,location_id=w['dest_loc'])])).status_code==400
    doc=_ok(client.post(url+'/loss',headers=h,json=command(lines=[dict(id=line,quantity=2)])))
    assert doc['status']=='completed'
    with client.application.app_context():
        assert db.session.get(Inventory,(w['g1'],w['wh'])).delivered_stock==0
        assert db.session.get(Inventory,(w['g1'],w['dest'])).onhand_stock==8


def return_world(client,token):
    w=_world(client)
    _inbound(client,token,w,[(w['g1'],5)],[(w['g1'],w['std1'],5)])
    dn,packing=_picked_dn(client,token,w,[(w['g1'],5)],[(w['g1'],w['std1'],5)])
    h=_h(token)
    _ok(client.post(f'/packing/{packing}/batches/',headers=h,json={'details':[dict(goods_id=w['g1'],packed_quantity=5)]}))
    _ok(client.put(f'/packing/{packing}/complete/',headers=h))
    delivery=_task_ids(client,DeliveryTask,dn_id=dn)[0]
    for action in ('process','complete','sign'):_ok(client.put(f'/delivery/{delivery}/{action}/',headers=h,json={}))
    with client.application.app_context():
        loc=Location(warehouse_id=w['wh'],code='RMA-HOLD',location_type='return',created_by=w['admin'])
        db.session.add(loc);db.session.commit();w.update(hold=loc.id,dn=dn)
    return w


def test_return_limit_quarantine_review_and_receipt_replay(client,access_token,access_company_admin_token):
    w=return_world(client,access_token);h=_h(access_token)
    body=dict(warehouse_id=w['wh'],original_dn_id=w['dn'],reason='Customer return',lines=[dict(goods_id=w['g1'],quantity=3)])
    doc=_ok(client.post('/asn/returns',headers=h,json=body))
    assert client.post('/asn/returns',headers=h,json=body).status_code==409
    url=f"/asn/returns/{doc['id']}";line=doc['lines'][0]['id']
    receive=command(lines=[dict(id=line,quantity=3,location_id=w['hold'])])
    for _ in range(2):doc=_ok(client.post(url+'/receive',headers=h,json=receive))
    assert doc['lines'][0]['received']==3
    assert client.post('/transfer/',headers=h,json=dict(goods_id=w['g1'],from_location_id=w['hold'],to_location_id=w['std1'],quantity=1,reason='Bypass')).status_code==409
    doc=_ok(client.post(url+'/propose',headers=h,json=command(line_id=line,location_id=w['hold'],target_location_id=w['std1'],action='restock',quantity=2)))
    decision=doc['dispositions'][0]['id']
    assert client.post(url+f'/dispositions/{decision}/review',headers=h,json=command(approve=True)).status_code==403
    review=command(approve=True)
    for _ in range(2):doc=_ok(client.post(url+f'/dispositions/{decision}/review',headers=_h(access_company_admin_token),json=review))
    assert doc['lines'][0]['disposed']==2
    with client.application.app_context():
        inv=db.session.get(Inventory,(w['g1'],w['wh']))
        assert inv.available_stock_for_sale==2 and inv.return_stock==1
    _ok(client.post(url+'/cancel',headers=h,json=command()))


def test_unlinked_return_requires_reason_and_cannot_use_plain_asn(client,access_token):
    w=_world(client);h=_h(access_token)
    body=dict(warehouse_id=w['wh'],recipient_id=w['recipient'],reason='Unknown original order',lines=[dict(goods_id=w['g1'],quantity=1)])
    assert client.post('/asn/returns',headers=h,json=body).status_code==400
    _ok(client.post('/asn/returns',headers=h,json={**body,'exception_reason':'Customer provided receipt but legacy DN is unavailable'}))
    assert client.post('/asn/',headers=h,json=dict(warehouse_id=w['wh'],supplier_id=w['supplier'],asn_type='return_from_customer',details=[dict(goods_id=w['g1'],quantity=1)])).status_code==400


@pytest.mark.parametrize('cancel_stage', ['pending', 'picked', 'packed'])
def test_supplier_disposition_reversal_restores_quarantine(client, access_token, access_company_admin_token, cancel_stage):
    w=return_world(client,access_token);h=_h(access_token)
    doc=_ok(client.post('/asn/returns',headers=h,json=command(warehouse_id=w['wh'],original_dn_id=w['dn'],lines=[dict(goods_id=w['g1'],quantity=5)])))
    url=f"/asn/returns/{doc['id']}";line=doc['lines'][0]['id']
    _ok(client.post(url+'/receive',headers=h,json=command(lines=[dict(id=line,quantity=5,location_id=w['hold'])])))
    doc=_ok(client.post(url+'/propose',headers=h,json=command(line_id=line,location_id=w['hold'],action='supplier_return',quantity=5,supplier_id=w['supplier'],supplier_recipient_id=w['recipient'])))
    decision=doc['dispositions'][0]['id']
    doc=_ok(client.post(url+f'/dispositions/{decision}/review',headers=_h(access_company_admin_token),json=command(approve=True)))
    dn=doc['dispositions'][0]['linked_dn_id']
    assert client.post('/transfer/',headers=h,json=dict(goods_id=w['g1'],from_location_id=w['hold'],to_location_id=w['std1'],quantity=1,reason='Bypass approved return')).status_code==409
    assert client.put(f'/dn/{dn}',headers=h,json={'details':[dict(goods_id=w['g1'],quantity=6)]}).status_code==409
    assert client.put(f'/dn/{dn}/ship-to',headers=h,json={'ship_to':None}).status_code==409
    if cancel_stage!='pending':
        _ok(client.put(f'/dn/{dn}/progress/',headers=h))
        picking=_task_ids(client,PickingTask,dn_id=dn)[0]
        _ok(client.put(f'/picking/{picking}/process/',headers=h))
        _ok(client.post(f'/picking/{picking}/batches/',headers=h,json={'details':[dict(goods_id=w['g1'],location_id=w['hold'],picked_quantity=4)]}))
        _ok(client.put(f'/picking/{picking}/complete/',headers=h))
        if cancel_stage=='packed':
            packing=_task_ids(client,PackingTask,dn_id=dn)[0]
            _ok(client.put(f'/packing/{packing}/process/',headers=h))
            _ok(client.post(f'/packing/{packing}/batches/',headers=h,json={'details':[dict(goods_id=w['g1'],packed_quantity=3)]}))
            _ok(client.put(f'/packing/{packing}/complete/',headers=h))
    _ok(client.put(f'/dn/{dn}/cancel/',headers=h))
    doc=_ok(client.get(url,headers=h))
    assert doc['status']=='inspecting' and doc['lines'][0]['disposed']==0
    assert sum(r['quantity'] for r in doc['lines'][0]['holdings'])==5
    assert doc['dispositions'][0]['restored_quantity']==5
    assert client.post('/transfer/',headers=h,json=dict(goods_id=w['g1'],from_location_id=w['hold'],to_location_id=w['std1'],quantity=1,reason='Bypass')).status_code==409


def test_return_repair_rejection_scrap_and_damage(client,access_token,access_company_admin_token):
    w=return_world(client,access_token);h=_h(access_token)
    with client.application.app_context():
        repair=Location(warehouse_id=w['wh'],code='REPAIR-HOLD',location_type='return',created_by=w['admin'])
        damage=Location(warehouse_id=w['wh'],code='RMA-DAMAGE',location_type='damaged',created_by=w['admin'])
        db.session.add_all([repair,damage]);db.session.commit();repair_id,damage_id=repair.id,damage.id
    body=command(warehouse_id=w['wh'],original_dn_id=w['dn'],lines=[dict(goods_id=w['g1'],quantity=3)])
    doc=_ok(client.post('/asn/returns',headers=h,json=body))
    assert _ok(client.post('/asn/returns',headers=h,json=body))['id']==doc['id']
    url=f"/asn/returns/{doc['id']}";line=doc['lines'][0]['id']
    _ok(client.post(url+'/receive',headers=h,json=command(lines=[dict(id=line,quantity=3,location_id=w['hold'])])))
    def disposition(action,quantity,source,target=None,approve=True):
        doc=_ok(client.post(url+'/propose',headers=h,json=command(line_id=line,location_id=source,target_location_id=target,action=action,quantity=quantity)))
        decision=doc['dispositions'][-1]['id']
        return _ok(client.post(url+f'/dispositions/{decision}/review',headers=_h(access_company_admin_token),json=command(approve=approve)))
    doc=disposition('repair',2,w['hold'],repair_id)
    assert doc['lines'][0]['disposed']==0 and sum(r['quantity'] for r in doc['lines'][0]['holdings'])==3
    disposition('restock',2,repair_id,w['std1'],approve=False)
    disposition('scrap',2,repair_id)
    doc=disposition('damaged',1,w['hold'],damage_id)
    assert doc['status']=='completed' and doc['lines'][0]['disposed']==3 and not doc['lines'][0]['holdings']
    with client.application.app_context():
        inv=db.session.get(Inventory,(w['g1'],w['wh']))
        assert inv.damage_stock==1 and inv.return_stock==0 and inv.available_stock_for_sale==0


def test_workflows_enforce_warehouse_scope_and_lot_control_authority(client,access_token):
    from tests.test_process_guards import _staff_token
    w=transfer_world(client,access_token)
    limited=_staff_token(client,'warehouse-custody-limited',['transfer_edit','transfer_read','inventory_edit','inventory_read'])
    body=command(source_warehouse_id=w['wh'],destination_warehouse_id=w['dest'],lines=[dict(goods_id=w['g1'],source_location_id=w['std1'],quantity=10)])
    assert client.post('/transfer/orders',headers=limited,json=body).status_code==403
    doc=_ok(client.post('/transfer/orders',headers=_h(access_token),json=body))
    assert client.post(f"/transfer/orders/{doc['id']}/receive",headers=limited,json=command(lines=[dict(id=doc['lines'][0]['id'],quantity=1,location_id=w['dest_loc'])])).status_code==403
    lot=_ok(client.post(f"/goods/{w['g1']}/lots",headers=_h(access_token),json={'lot_no':'SCOPE-HOLD'}))
    assert client.post(f"/goods/{w['g1']}/lots/{lot['id']}/control",headers=limited,json=dict(status='hold',revision=0,reason='Company-wide hold')).status_code==403
