import pytest
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from tests.test_isolation_inbound import foreign
from warehouse.stock.models import GoodsLot


def test_lot_registration_dates_normalization_and_no_stock_mutation(client, access_token):
    with client.application.app_context():
        goods = Goods.query.filter_by(code='G001').first()
        gid = goods.id
        before = [(row.id, row.quantity) for row in GoodsLocation.query.filter_by(goods_id=gid).all()]
    url = f'/goods/{gid}/lots'
    response = client.post(url, json={'lot_no': ' ｌ２６０８ａ ', 'production_date': '2026-08-17', 'expiry_date': '2027-08-17'}, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert client.get(url+'/'+str(data['id']), headers=_h(access_token)).get_json()['lot_no'] == 'L2608A'
    with client.application.app_context():
        other_id = Goods.query.filter(Goods.id != gid).first().id
    assert client.get(f'/goods/{other_id}/lots/{data["id"]}', headers=_h(access_token)).status_code in (403,404)
    assert client.get(url+'/999999', headers=_h(access_token)).status_code == 404
    assert data['lot_no'] == 'L2608A' and data['expiry_date'] == '2027-08-17'
    assert client.post(url, json={'lot_no': 'l2608a'}, headers=_h(access_token)).status_code == 409
    listing = client.get(url+'?keyword=ｌ２６０８', headers=_h(access_token)).get_json()
    assert listing['total'] == 1 and listing['items'][0]['id'] == data['id']
    with client.application.app_context():
        assert [(row.id, row.quantity) for row in GoodsLocation.query.filter_by(goods_id=gid).all()] == before
        assert db.session.get(GoodsLot, data['id']).first_inbound_at is None


@pytest.mark.parametrize('body', [None, [], {}, {'lot_no':True}, {'lot_no':'a'*41}, {'lot_no':'x\nY'},
    {'lot_no':'X','expiry_date':'2026-02-30'}, {'lot_no':'X','expiry_date':'20261026'},
    {'lot_no':'X','production_date':'2026-10-26','expiry_date':'2026-10-25'},
    {'lot_no':'X','goods_id':999}, {'lot_no':'X','status':'hold'}])
def test_invalid_lot_is_atomic(client, access_token, body):
    with client.application.app_context():
        gid = Goods.query.filter_by(code='G001').first().id
        before = GoodsLot.query.count()
    response = client.post(f'/goods/{gid}/lots', json=body, headers=_h(access_token))
    assert response.status_code == 400, response.get_json()
    with client.application.app_context():
        assert GoodsLot.query.count() == before


def test_lots_permissions_company_isolation_and_pagination(client, access_token, foreign):
    with client.application.app_context():
        warehouse = get_warehouse()
        gid = Goods.query.filter_by(company_id=warehouse.company_id, code='G001').first().id
        reader = _staff('lot_reader', _role('lot_reader', ['goods_read']), [warehouse])
        token = create_access_token(identity=reader)
        wid = warehouse.id
    url = f'/goods/{gid}/lots'
    assert client.get(url, headers=_h(token, wid)).status_code == 200
    assert client.post(url, json={'lot_no':'L1'}, headers=_h(token, wid)).status_code == 403
    assert client.get(url, headers=_h(foreign['token'])).status_code in (403,404)
    assert client.post(url, json={'lot_no':'L1'}, headers=_h(foreign['token'])).status_code in (403,404)
    for query in ['page=0','per_page=101','page=bad']:
        assert client.get(url+'?'+query, headers=_h(access_token)).status_code == 400
