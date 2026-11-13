from tests.helpers import *
from tests.test_inventory_guards import _world, _h, _ok, _task_ids


def damaged_order(client, token, held_lot=False):
    w = _world(client)
    with client.application.app_context():
        loc = Location(warehouse_id=w['wh'], code='SUPPLIER-DAMAGE', location_type='damaged', created_by=w['admin'])
        db.session.add(loc); db.session.flush()
        w['damage'] = loc.id
        w['lot']=None
        if held_lot:
            from warehouse.stock.models import GoodsLot
            from warehouse.stock.service import StockOp, Leg
            from datetime import timedelta
            lot=GoodsLot(goods_id=w['g1'],lot_no='EXPIRED-DAMAGE',status='hold',expiry_date=warehouse_today()-timedelta(days=1))
            db.session.add(lot);db.session.flush();w['lot']=lot.id
            op=StockOp(w['wh'],'reconcile');op.move(w['g1'],5,dst=Leg.at(loc.id,lot.id));op.commit()
        else:
            add_stock(w['g1'], w['wh'], location_id=loc.id, damaged=5)
        add_stock(w['g1'], w['wh'], location_id=w['std1'], onhand=7)
        db.session.commit()
    body = dict(warehouse_id=w['wh'], recipient_id=w['recipient'], shipping_address='Supplier dock',
        expected_shipping_date=warehouse_today().isoformat(), dn_type='damage_to_supplier',
        supplier_id=w['supplier'], purpose_reason='Damaged on receipt', details=[{'goods_id':w['g1'],'quantity':5}])
    ident = _ok(client.post('/dn/', headers=_h(token), json=body))['id']
    with client.application.app_context():
        inv=db.session.get(Inventory,(w['g1'],w['wh']))
        assert inv.damaged_dn_stock==5 and inv.dn_stock==0 and inv.available_stock_for_sale==7
    assert client.post('/dn/',headers=_h(token),json=body).status_code==400
    _ok(client.put(f'/dn/{ident}/progress/',headers=_h(token)))
    pick=_task_ids(client,PickingTask,dn_id=ident)[0]
    _ok(client.put(f'/picking/{pick}/process/',headers=_h(token)))
    assert client.post(f'/picking/{pick}/batches/',headers=_h(token),json={'details':[{'goods_id':w['g1'],'location_id':w['std1'],'picked_quantity':5}]}).status_code==400
    _ok(client.post(f'/picking/{pick}/batches/',headers=_h(token),json={'details':[{'goods_id':w['g1'],'location_id':w['damage'],'lot_id':w['lot'],'picked_quantity':5}]}))
    _ok(client.put(f'/picking/{pick}/complete/',headers=_h(token)))
    return w,ident


@pytest.mark.parametrize('cancel',[False,True])
@pytest.mark.parametrize('held_lot',[False,True])
def test_damaged_return_preserves_quality_through_short_pack_and_finish(client,access_token,cancel,held_lot):
    w,ident=damaged_order(client,access_token,held_lot)
    h=_h(access_token)
    packing=_task_ids(client,PackingTask,dn_id=ident)[0]
    _ok(client.put(f'/packing/{packing}/process/',headers=h))
    _ok(client.post(f'/packing/{packing}/batches/',headers=h,json={'details':[{'goods_id':w['g1'],'packed_quantity':3}]}))
    _ok(client.put(f'/packing/{packing}/complete/',headers=h))
    if cancel:
        _ok(client.put(f'/dn/{ident}/cancel/',headers=h))
    else:
        delivery=_task_ids(client,DeliveryTask,dn_id=ident)[0]
        for action in ('process','complete','sign'):
            _ok(client.put(f'/delivery/{delivery}/{action}/',headers=h,json={}))
    with client.application.app_context():
        inv=db.session.get(Inventory,(w['g1'],w['wh']))
        assert inv.damaged_dn_stock==0 and inv.dn_stock==0
        assert inv.sorted_damage_stock==(5 if cancel else 2)
        assert inv.available_stock_for_sale==7


def test_lot_hold_revision_and_audit(client,access_token):
    w=_world(client);h=_h(access_token)
    lot=_ok(client.post(f"/goods/{w['g1']}/lots",headers=h,json={'lot_no':'HOLD-TEST'}))
    url=f"/goods/{w['g1']}/lots/{lot['id']}/control"
    _ok(client.post(url,headers=h,json={'status':'hold','revision':0,'reason':'Quality review'}))
    assert client.post(url,headers=h,json={'status':'active','revision':0,'reason':'Stale release'}).status_code==409
    view=_ok(client.get(url,headers=h))
    assert view['status']=='hold' and view['history'][0]['reason']=='Quality review'
    _ok(client.post(url,headers=h,json={'status':'active','revision':1,'reason':'Inspection passed'}))
    assert len(_ok(client.get(url,headers=h))['history'])==2


def test_lot_hold_blocks_packed_sales_and_release_does_not_renew_expiry(client,access_token):
    from tests.test_lot_operations import receive_lots,pick_lots
    from warehouse.stock.models import GoodsLot
    from datetime import timedelta
    w=_world(client);h=_h(access_token)
    lot,_=receive_lots(client,access_token,w)
    dn,pack=pick_lots(client,access_token,w,[(lot,3)])
    _ok(client.put(f'/packing/{pack}/process/',headers=h))
    _ok(client.post(f'/packing/{pack}/batches/',headers=h,json={'details':[dict(goods_id=w['g1'],packed_quantity=3)]}))
    _ok(client.put(f'/packing/{pack}/complete/',headers=h))
    delivery=_task_ids(client,DeliveryTask,dn_id=dn)[0]
    _ok(client.put(f'/delivery/{delivery}/process/',headers=h))
    url=f"/goods/{w['g1']}/lots/{lot}/control"
    _ok(client.post(url,headers=h,json=dict(status='hold',revision=0,reason='Quality incident')))
    assert any(r['id']==dn and r['blocked'] for r in _ok(client.get(url,headers=h))['orders'])
    assert client.put(f'/delivery/{delivery}/complete/',headers=h,json={}).status_code==400
    with client.application.app_context():
        db.session.get(GoodsLot,lot).expiry_date=warehouse_today()-timedelta(days=1);db.session.commit()
    _ok(client.post(url,headers=h,json=dict(status='active',revision=1,reason='Hold released')))
    assert client.put(f'/delivery/{delivery}/complete/',headers=h,json={}).status_code==400
