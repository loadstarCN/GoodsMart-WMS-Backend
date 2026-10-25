"""拣货只能从 standard 库位拣：破损 / 退货库位上的货不能拣去发货

- 提交拣货批次、改拣货明细数量、完成拣货的复核：库位类型不是 standard → 400 16162，details {location_id, location_type}
- 商品库位列表（GET /goods/locations/）可按 location_type 筛（拣货页传 standard）
"""
from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _err, _h, _inbound, _ok, _task_ids, _world


def _setup(client, token):
    """商品 g1：标准库位 6 件、破损库位 2 件、退货库位 2 件；建 DN（5 件）→ 开始 → 拣货任务开始"""
    w = _world(client)
    with client.application.app_context():
        damaged = Location(warehouse_id=w['wh'], code='PK-DMG', location_type='damaged', created_by=w['admin'])
        returned = Location(warehouse_id=w['wh'], code='PK-RET', location_type='return', created_by=w['admin'])
        db.session.add_all([damaged, returned])
        db.session.commit()
        w.update(dmg=damaged.id, ret=returned.id)
    g = w['g1']
    _inbound(client, token, w, [(g, 10)], [(g, w['std1'], 6), (g, w['dmg'], 2), (g, w['ret'], 2)])
    dn_id = _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': warehouse_today().isoformat(), 'details': [{'goods_id': g, 'quantity': 5}],
    }, headers=_h(token)))['id']
    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(token)))
    picking_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{picking_id}/process/', headers=_h(token)))
    return w, dn_id, picking_id


def _batch(client, token, picking_id, goods_id, location_id, quantity):
    return client.post(f'/picking/{picking_id}/batches/', json={'details': [
        {'goods_id': goods_id, 'location_id': location_id, 'picked_quantity': quantity}]}, headers=_h(token))


def test_picking_batch_rejects_damaged_and_return_locations(client, access_token):
    w, dn_id, picking_id = _setup(client, access_token)
    g = w['g1']
    for location_id, location_type in ((w['dmg'], 'damaged'), (w['ret'], 'return')):
        body = _err(_batch(client, access_token, picking_id, g, location_id, 1), 400, 16162)
        assert body['details'] == {'location_id': location_id, 'location_type': location_type}
        assert body['message'].isascii()
    with client.application.app_context():
        assert PickingTaskDetail.query.filter_by(picking_task_id=picking_id).count() == 0

    # 标准库位照常
    _ok(_batch(client, access_token, picking_id, g, w['std1'], 5))
    _ok(client.put(f'/picking/{picking_id}/complete/', headers=_h(access_token)))
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'picked'
        assert GoodsLocation.query.filter_by(goods_id=g, location_id=w['dmg']).one().quantity == 2
        assert GoodsLocation.query.filter_by(goods_id=g, location_id=w['ret']).one().quantity == 2


def test_picking_complete_rechecks_location_type(client, access_token):
    """历史明细（或批次之后库位被改成别的类型）在完成复核时拦下，库存不动"""
    w, dn_id, picking_id = _setup(client, access_token)
    g = w['g1']
    _ok(_batch(client, access_token, picking_id, g, w['std1'], 2))
    with client.application.app_context():
        task = db.session.get(PickingTask, picking_id)
        batch = PickingBatch.query.filter_by(picking_task_id=picking_id).first()
        db.session.add(PickingTaskDetail(picking_task_id=picking_id, batch_id=batch.id, goods_id=g,
                                         location_id=w['dmg'], picked_quantity=2, operator_id=w['admin']))
        db.session.commit()
        detail_id = PickingTaskDetail.query.filter_by(picking_task_id=picking_id, location_id=w['dmg']).one().id
        assert task.status == 'in_progress'

    body = _err(client.put(f'/picking/{picking_id}/complete/', headers=_h(access_token)), 400, 16162)
    assert body['details'] == {'location_id': w['dmg'], 'location_type': 'damaged'}
    # 改量也拦
    _err(client.put(f'/picking/{picking_id}/details/{detail_id}', json={'picked_quantity': 3},
                    headers=_h(access_token)), 400, 16162)
    with client.application.app_context():
        assert db.session.get(PickingTask, picking_id).status == 'in_progress'
        assert db.session.get(DN, dn_id).status == 'in_progress'
        assert GoodsLocation.query.filter_by(goods_id=g, location_id=w['dmg']).one().quantity == 2
        assert db.session.get(Inventory, (g, w['wh'])).picked_stock == 0


def test_goods_locations_can_be_filtered_by_location_type(client, access_token):
    w, _dn_id, _picking_id = _setup(client, access_token)
    g = w['g1']

    def locations(**params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        items = _ok(client.get(f'/goods/locations/?goods_id={g}&{query}', headers=_h(access_token)))['items']
        return sorted(item['location_id'] for item in items)

    assert locations() == sorted([w['std1'], w['dmg'], w['ret']])
    assert locations(location_type='standard') == [w['std1']]
    assert locations(location_type='damaged') == [w['dmg']]
    response = client.get(f'/goods/locations/?goods_id={g}&location_type=bogus', headers=_h(access_token))
    assert response.status_code == 400
