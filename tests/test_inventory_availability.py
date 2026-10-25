"""发货单批量添加：按仓库批量查可售量（GET / POST /inventory/availability）

- 按请求顺序、去重；找不到的在 not_found；该仓库没有库存行的商品可售为 0；只认仓库所属公司的商品
- 可售 = available_stock_for_sale（与库存列表同口径）
- 必须指定仓库；编码为空 / 超过 1,000 个 → 400 15019；权限 inventory_read 或 dn_edit
"""
from sqlalchemy.orm import lazyload

from tests.helpers import *  # noqa: F401,F403
from tests.test_platform_guards import _role, _staff


def _h(token, warehouse_id=None):
    headers = {'Authorization': f'Bearer {token}'}
    if warehouse_id is not None:
        headers['X-WAREHOUSE-ID'] = str(warehouse_id)
    return headers


def _available(client, code, warehouse_id):
    with client.application.app_context():
        goods = Goods.query.filter_by(code=code).first()
        inventory = (Inventory.query.options(lazyload('*'))
                     .filter_by(goods_id=goods.id, warehouse_id=warehouse_id).first())
        return goods.id, inventory.available_stock_for_sale if inventory else 0


def test_availability_by_codes(client, access_token):
    with client.application.app_context():
        warehouse_id = get_warehouse().id
        warehouse_b = Warehouse.query.filter_by(name='Warehouse Test').first().id
        other = Goods(code='OTHER-CO', name='Other company', company_id=2, unit='pcs', created_by=1)
        db.session.add_all([other, Goods(code='NO-STOCK', name='No stock', company_id=1, unit='box', created_by=1)])
        db.session.commit()

    response = client.get(f'/inventory/availability?warehouse_id={warehouse_id}'
                          '&goods_codes=G002, ZZZ ,G001,G002,OTHER-CO,NO-STOCK', headers=_h(access_token))
    body = response.get_json()
    assert response.status_code == 200, body
    assert body['warehouse_id'] == warehouse_id and body['not_found'] == ['ZZZ', 'OTHER-CO']
    assert [item['code'] for item in body['items']] == ['G002', 'G001', 'NO-STOCK']
    g1_id, g1_available = _available(client, 'G001', warehouse_id)
    g1 = body['items'][1]
    assert (g1['goods_id'], g1['name'], g1['unit'], g1['available_stock_for_sale']) == (
        g1_id, 'Sample Goods', 'pcs', g1_available)
    assert set(g1) >= {'goods_id', 'code', 'name', 'unit', 'available_stock_for_sale', 'onhand_stock', 'dn_stock'}
    assert body['items'][2]['available_stock_for_sale'] == 0 and body['items'][2]['unit'] == 'box'

    # POST（长列表）；另一个仓库没有库存 → 0
    response = client.post('/inventory/availability', headers=_h(access_token, warehouse_b),
                           json={'goods_codes': ['G001']})
    assert response.status_code == 200
    assert response.get_json()['items'][0]['available_stock_for_sale'] == _available(client, 'G001', warehouse_b)[1]


def test_availability_validation_and_permissions(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse_id = warehouse.id
        dn_staff = _staff('dn_maker', _role('dn_maker', ['dn_edit']), [warehouse])
        reader = _staff('goods_only', _role('goods_only', ['goods_read']), [warehouse])
        dn_token, reader_token = create_access_token(identity=dn_staff), create_access_token(identity=reader)

    response = client.get('/inventory/availability?goods_codes=G001', headers=_h(access_token))
    assert (response.status_code, response.get_json()['code']) == (400, 14003)
    for query in ('', '&goods_codes=,,'):
        response = client.get(f'/inventory/availability?warehouse_id={warehouse_id}{query}', headers=_h(access_token))
        assert (response.status_code, response.get_json()['code'], response.get_json()['field']) == (
            400, 15019, 'goods_codes')
    response = client.post('/inventory/availability', headers=_h(access_token, warehouse_id),
                           json={'goods_codes': [f'C{i}' for i in range(1001)]})
    assert (response.status_code, response.get_json()['details']) == (400, {'max_codes': 1000, 'count': 1001})

    assert client.get('/inventory/availability?goods_codes=G001',
                      headers=_h(dn_token, warehouse_id)).status_code == 200
    assert client.get('/inventory/availability?goods_codes=G001',
                      headers=_h(reader_token, warehouse_id)).status_code == 403
