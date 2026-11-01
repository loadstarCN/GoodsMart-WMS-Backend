from copy import deepcopy
from tests.helpers import *
from tests.test_inventory_guards import _world, _ok, _h, _task_ids, _inv
from tests.test_lot_operations import receive_lots, pick_lots
from tests.test_isolation_inbound import foreign
from warehouse.stock.models import GoodsLot


def test_carton_assignment_merges_same_lot_across_pick_locations_without_stock_change(client, access_token):
    w = _world(client)
    a, _ = receive_lots(client, access_token, w)
    _ok(client.post('/transfer/', headers=_h(access_token), json=dict(goods_id=w['g1'], lot_id=a,
        from_location_id=w['std1'], to_location_id=w['std2'], quantity=2)))
    dn = _ok(client.post('/dn/', headers=_h(access_token), json=dict(warehouse_id=w['wh'],recipient_id=w['recipient'],
        shipping_address='Test',expected_shipping_date=warehouse_today().isoformat(),details=[dict(goods_id=w['g1'],quantity=4)])))['id']
    _ok(client.put(f'/dn/{dn}/progress/',headers=_h(access_token)))
    picking = _task_ids(client,PickingTask,dn_id=dn)[0]
    _ok(client.put(f'/picking/{picking}/process/',headers=_h(access_token)))
    _ok(client.post(f'/picking/{picking}/batches/',headers=_h(access_token),json={'details':[
        dict(goods_id=w['g1'],lot_id=a,location_id=location,picked_quantity=2) for location in (w['std1'],w['std2'])]}))
    _ok(client.put(f'/picking/{picking}/complete/',headers=_h(access_token)))
    packing = _task_ids(client,PackingTask,dn_id=dn)[0]
    _ok(client.put(f'/packing/{packing}/process/',headers=_h(access_token)))
    _ok(client.post(f'/packing/{packing}/batches/',headers=_h(access_token),json={'details':[dict(goods_id=w['g1'],packed_quantity=4)]}))
    _ok(client.put(f'/packing/{packing}/complete/',headers=_h(access_token)))
    _ok(client.put(f'/dn/{dn}/packages',headers=_h(access_token),json={'packages':[dict(package_no=1,gross_weight_kg=1,length_mm=100,width_mm=100,height_mm=100)]}))
    endpoint = f'/dn/{dn}/package-contents'
    choices = _ok(client.get(endpoint,headers=_h(access_token)))['items']
    assert len(choices) == 1 and choices[0]['lot_id'] == a and choices[0]['quantity'] == 4
    before = _inv(client,w['g1'],w['wh'])
    _ok(client.put(endpoint,headers=_h(access_token),json={'packages':[dict(package_no=1,contents=[dict(goods_id=w['g1'],lot_id=a,quantity=4)])]}))
    assert _inv(client,w['g1'],w['wh']) == before


def test_explicit_carton_lots_match_packed_totals_and_survive_size_edits(client, access_token, foreign):
    w = _world(client)
    a,b = receive_lots(client, access_token, w)
    dn,pack = pick_lots(client, access_token, w, [(a,2),(b,2)])
    endpoint = f'/dn/{dn}/package-contents'
    assert client.put(endpoint, headers=_h(access_token), json={'packages':[]}).status_code == 409
    _ok(client.put(f'/packing/{pack}/process/', headers=_h(access_token)))
    _ok(client.post(f'/packing/{pack}/batches/', headers=_h(access_token), json={'details':[{'goods_id':w['g1'],'packed_quantity':4}]}))
    _ok(client.put(f'/packing/{pack}/complete/', headers=_h(access_token)))
    boxes = [dict(package_no=i,gross_weight_kg=1,length_mm=100,width_mm=100,height_mm=100) for i in (1,2)]
    _ok(client.put(f'/dn/{dn}/packages', headers=_h(access_token), json={'packages':boxes}))
    data = _ok(client.get(endpoint, headers=_h(access_token)))
    assert {row['lot_no']:row['quantity'] for row in data['items']} == {'LOT-A':2,'LOT-B':2}
    with client.application.app_context():
        expected_expiry = db.session.get(GoodsLot, a).expiry_date.isoformat()
    assert next(row for row in data['items'] if row['lot_id'] == a)['expiry_date'] == expected_expiry
    assert all(row['contents'] is None for row in data['packages'])
    body = {'packages':[dict(package_no=i,contents=[dict(goods_id=w['g1'],lot_id=lot,quantity=2)]) for i,lot in ((1,a),(2,b))]}
    invalid = deepcopy(body); invalid['packages'][0]['contents'][0]['quantity'] = 3
    assert client.put(endpoint, headers=_h(access_token), json=invalid).status_code == 400
    invalid = deepcopy(body); invalid['packages'][0]['contents'][0]['lot_id'] = b
    assert client.put(endpoint, headers=_h(access_token), json=invalid).status_code == 400
    saved = _ok(client.put(endpoint, headers=_h(access_token), json=body))
    assert saved['packages'][0]['contents'][0]['lot_no'] == 'LOT-A'
    assert saved['packages'][0]['contents'][0]['expiry_date'] == expected_expiry
    boxes[0]['length_mm'] = 200
    changed = _ok(client.put(f'/dn/{dn}/packages', headers=_h(access_token), json={'packages':boxes}))
    assert changed['packages'][0]['contents'] == saved['packages'][0]['contents']
    assert client.put(f'/dn/{dn}/packages', headers=_h(access_token), json={'packages':boxes[:1]}).status_code == 409
    cleared = _ok(client.put(endpoint, headers=_h(access_token), json={'packages':[{'package_no':i,'contents':None} for i in (1,2)]}))
    assert all(row['contents'] is None for row in cleared['packages'])
    _ok(client.put(f'/dn/{dn}/packages', headers=_h(access_token), json={'packages':boxes[:1]}))
    # The foreign company actor cannot inspect packed-lot choices or assign them.
    assert client.get(endpoint, headers=_h(foreign['token'])).status_code in (403,404)


def test_package_contents_migration_preserves_unknown_and_blocks_data_loss():
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('contents_migration',Path(__file__).resolve().parents[1]/'migrations/versions/f4c8d2e9b601_package_contents.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE dn_packages (id INTEGER PRIMARY KEY)'))
        connection.execute(sa.text('INSERT INTO dn_packages (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert connection.execute(sa.text('SELECT contents FROM dn_packages')).scalar() is None
            connection.execute(sa.text("UPDATE dn_packages SET contents='[]'"))
            with pytest.raises(RuntimeError): module.downgrade()
            connection.execute(sa.text('UPDATE dn_packages SET contents=NULL'))
            module.downgrade()
