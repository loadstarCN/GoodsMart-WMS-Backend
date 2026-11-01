import pytest
from tests.helpers import *
from tests.test_inventory_guards import _h, _ok


def _create(client, token, lots):
    with client.application.app_context():
        payload = {'warehouse_id':get_warehouse().id, 'supplier_id':get_supplier().id,
                   'asn_type':'inbound', 'details':[{'goods_id':get_goods().id, 'quantity':10, 'announced_lots':lots}]}
    return client.post('/asn/', headers=_h(token), json=payload)


def test_announced_lots_roundtrip_quantity_guard_clear_and_lock(client, access_token):
    from warehouse.stock.models import GoodsLot
    with client.application.app_context():
        lot_count = GoodsLot.query.count()
    lots = [{'lot_code':' ANN-01 ', 'expiry_date':'2027-01-31', 'quantity':6},
            {'lot_code':'ANN-02', 'expiry_date':None, 'quantity':4}]
    data = _ok(_create(client, access_token, lots))
    row = data['details'][0]
    assert row['announced_lots'][0]['lot_code'] == 'ANN-01'
    assert row['announced_lots'][1]['expiry_date'] is None
    url = f"/asn/{data['id']}"
    detail_url = f"{url}/details/{row['id']}"
    assert client.put(detail_url, headers=_h(access_token), json={'quantity':9}).status_code == 400
    result = _ok(client.get(url, headers=_h(access_token)))
    assert result['details'][0]['quantity'] == 10
    assert client.put(url, headers=_h(access_token), json={'details':[{**row,'quantity':8}]}).status_code == 400
    updated = _ok(client.put(url, headers=_h(access_token), json={'details':[{**row,'announced_lots':[]}]}))
    assert updated['details'][0]['announced_lots'] == []
    _ok(client.put(detail_url, headers=_h(access_token), json={'announced_lots':lots}))
    _ok(client.put(f'{url}/receive/', headers=_h(access_token)))
    assert client.put(detail_url, headers=_h(access_token), json={'announced_lots':[]}).status_code == 400
    with client.application.app_context():
        assert GoodsLot.query.count() == lot_count


@pytest.mark.parametrize('lots', [{},[{}],[{'lot_code':'A','quantity':0}],
    [{'lot_code':'A','quantity':11}], [{'lot_code':'A','quantity':1,'expiry_date':'2027-02-30'}],
    [{'lot_code':'A','quantity':1,'expiry_date':'20270101'}],
    [{'lot_code':'A','quantity':1},{'lot_code':' A ','quantity':1}],
    [{'lot_code':'A'*41,'quantity':1}]])
def test_announced_lots_reject_invalid(client, access_token, lots):
    assert _create(client, access_token, lots).status_code == 400
