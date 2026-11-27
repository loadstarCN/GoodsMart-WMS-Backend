from uuid import uuid4

import pytest

from tests.helpers import *  # noqa: F401,F403
from tests.test_damaged_putaway import _inv
from tests.test_inventory_guards import _create_asn, _err, _h, _ok, _task_ids, _world
from system.user.seed import seed_builtin
from warehouse.putaway.models import ReceiptPendingStock
from warehouse.stock.models import GoodsLot, StockLedger


@pytest.fixture(autouse=True)
def seeded_warehouse_roles(client):
    with client.application.app_context():
        seed_builtin()


def _counts(good, damaged=0, rejected=0):
    return dict(sorted_quantity=good, damage_quantity=damaged, rejected_quantity=rejected)


def _incoming(client, token, world, planned, good, damaged):
    goods_id = world['g1']
    asn_id = _create_asn(client, token, world, [(goods_id, planned)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=_h(token)))
    task_id, = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)
    task_url = f'/sorting/{task_id}'
    _ok(client.put(f'{task_url}/process/', headers=_h(token)))
    batch = _ok(client.post(f'{task_url}/batches/', headers=_h(token),
                            json={'details': [{'goods_id': goods_id, **_counts(good, damaged)}]}))
    return asn_id, task_url, batch['details'][0]['id']


def test_quantity_edit_and_review_preserve_original_record(client, access_token, access_operator_token,
                                                          access_warehouse_admin_token):
    world = _world(client)
    asn_id, task, detail_id = _incoming(client, access_token, world, 10, 8, 1)
    operator = {**_h(access_operator_token), 'X-Warehouse-ID': str(world['wh'])}
    reviewer = {**_h(access_warehouse_admin_token), 'X-Warehouse-ID': str(world['wh'])}
    detail_url = f'{task}/details/{detail_id}'
    edit = {**_counts(7, 1), 'expected_quantities': _counts(8, 1)}
    _ok(client.put(detail_url, headers=operator, json=edit))
    _err(client.put(detail_url, headers=operator, json=edit), 409, 17035)
    _ok(client.put(f'{task}/complete/', headers=operator))
    _err(client.put(detail_url, headers=operator, json=edit), 400, 16010)

    endpoint = f'{task}/quantity-corrections'
    data = dict(goods_id=world['g1'], before=_counts(7, 1), after=_counts(5, 2, 1), reason='Recounted cartons')
    proposal = _ok(client.post(endpoint, headers=operator, json=data))
    assert proposal['status'] == 'pending'
    assert _ok(client.post(endpoint, headers=operator, json=data))['id'] == proposal['id']
    assert _ok(client.get('/sorting/?has_pending_correction=true', headers=reviewer))['total'] == 1
    assert _inv(client, world['g1'], world['wh'])['sorted_stock'] == 8

    decision = f"{endpoint}/{proposal['id']}/review"
    assert client.post(decision, headers=operator, json={'decision': 'approved'}).status_code == 403
    applied = _ok(client.post(decision, headers=reviewer, json={'decision': 'approved'}))
    assert applied['status'] == 'approved'
    assert _ok(client.post(decision, headers=reviewer, json={'decision': 'approved'})) == applied
    assert _ok(client.get('/sorting/?has_pending_correction=true', headers=reviewer))['total'] == 0
    _err(client.post(endpoint, headers=operator, json=data), 409, 17035)

    result = _ok(client.get(task, headers=operator))
    assert (result['total_sorted_quantity'], result['total_damage_quantity'], result['total_rejected_quantity']) == (5, 2, 1)
    original = result['batches'][0]['details'][0]
    assert {key: original[key] for key in _counts(0)} == _counts(7, 1)
    assert result['asn']['details'][0]['actual_quantity'] == 7
    summary = next(step['summary'] for step in _ok(client.get(f'/asn/{asn_id}/flow', headers=operator))['steps']
                   if step['key'] == 'sorting')
    assert (summary['sorted_quantity'], summary['damage_quantity']) == (5, 2)
    snapshot = _ok(client.get(endpoint, headers=operator))
    assert snapshot['rows'][0]['quantities'] == _counts(5, 2, 1)
    stock = _inv(client, world['g1'], world['wh'])
    assert (stock['sorted_stock'], stock['sorted_damage_stock'], stock['total_stock']) == (7, 2, 7)
    with client.application.app_context():
        source = ReceiptPendingStock.query.filter_by(asn_id=asn_id).one()
        assert (source.good_received, source.good_remaining, source.damaged_received, source.damaged_remaining) == (5, 5, 2, 2)
        ledger = StockLedger.query.filter_by(doc_type='sorting_correction', doc_id=proposal['id']).all()
        assert {row.stock_type: row.quantity for row in ledger} == {'pending': -2, 'pending_damaged': 1}


def test_review_cannot_deduct_units_already_put_away_or_borrow_from_another_receipt(
        client, access_token, access_warehouse_admin_token):
    world = _world(client)
    asn_id, task, _ = _incoming(client, access_token, world, 8, 6, 0)
    _ok(client.put(f'{task}/complete/', headers=_h(access_token)))
    _, other_task, _ = _incoming(client, access_token, world, 5, 5, 0)
    _ok(client.put(f'{other_task}/complete/', headers=_h(access_token)))
    with client.application.app_context():
        source_id = ReceiptPendingStock.query.filter_by(asn_id=asn_id).one().id
    endpoint = f'{task}/quantity-corrections'
    proposal = _ok(client.post(endpoint, headers=_h(access_token), json={
        'goods_id': world['g1'], 'before': _counts(6), 'after': _counts(3), 'reason': 'Recounted cartons',
    }))
    _ok(client.post('/putaway/', headers=_h(access_token), json={
        'goods_id': world['g1'], 'location_id': world['std1'], 'quantity': 4, 'receipt_source_id': source_id,
    }))
    decision = f"{endpoint}/{proposal['id']}/review"
    reviewer = {**_h(access_warehouse_admin_token), 'X-Warehouse-ID': str(world['wh'])}
    _err(client.post(decision, headers=_h(access_token), json={'decision': 'approved'}), 409, 17038)
    body = _err(client.post(decision, headers=reviewer, json={'decision': 'approved'}), 409, 17037)
    assert body['details']['good_remaining'] == 2
    snapshot = _ok(client.get(endpoint, headers=_h(access_token)))
    assert snapshot['corrections'][0]['status'] == 'pending'
    assert snapshot['rows'][0]['quantities'] == _counts(6)
    assert _inv(client, world['g1'], world['wh'])['sorted_stock'] == 7
    rejected = _ok(client.post(decision, headers=reviewer,
                               json={'decision': 'rejected', 'note': 'Verify the bin before correcting'}))
    assert rejected['status'] == 'rejected'


def test_review_adds_stock_for_a_product_omitted_during_sorting(client, access_token, access_warehouse_admin_token):
    world = _world(client)
    goods_id = world['g2']
    headers = _h(access_token)
    asn_id = _create_asn(client, access_token, world, [(world['g1'], 2), (goods_id, 3)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=headers))
    task_id, = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)
    task = f'/sorting/{task_id}'
    _ok(client.put(f'{task}/process/', headers=headers))
    _ok(client.post(f'{task}/batches/', headers=headers,
                    json={'details': [{'goods_id': world['g1'], **_counts(2)}]}))
    _ok(client.put(f'{task}/complete/', headers=headers))
    endpoint = f'{task}/quantity-corrections'
    request = dict(goods_id=goods_id, before=_counts(0), after=_counts(2), reason='Found the missing units')
    _err(client.post(endpoint, headers=headers, json={**request, 'after': _counts(4)}), 400, 17033)
    proposal = _ok(client.post(endpoint, headers=headers, json=request))
    reviewer = {**_h(access_warehouse_admin_token), 'X-Warehouse-ID': str(world['wh'])}
    _ok(client.post(f"{endpoint}/{proposal['id']}/review", headers=reviewer, json={'decision': 'approved'}))
    assert _inv(client, goods_id, world['wh'])['sorted_stock'] == 2
    with client.application.app_context():
        source = ReceiptPendingStock.query.filter_by(asn_id=asn_id, goods_id=goods_id).one()
        assert source.good_received == source.good_remaining == 2


def test_grouped_correction_reviews_all_rows_atomically(client, access_token, access_warehouse_admin_token):
    world = _world(client)
    good_a, good_b = world['g1'], world['g2']
    headers = _h(access_token)
    reviewer = {**_h(access_warehouse_admin_token), 'X-Warehouse-ID': str(world['wh'])}
    asn_id = _create_asn(client, access_token, world, [(good_a, 5), (good_b, 6)])
    _ok(client.put(f'/asn/{asn_id}/receive/', headers=headers))
    task_id, = _task_ids(client, SortingTask, asn_id=asn_id, is_active=True)
    task = f'/sorting/{task_id}'
    _ok(client.put(f'{task}/process/', headers=headers))
    _ok(client.post(f'{task}/batches/', headers=headers, json={'details': [
        {'goods_id': good_a, **_counts(2)}, {'goods_id': good_b, **_counts(4)},
    ]}))
    _ok(client.put(f'{task}/complete/', headers=headers))
    endpoint = f'{task}/quantity-corrections'
    group = str(uuid4())
    rows = [
        dict(goods_id=good_a, before=_counts(2), after=_counts(3)),
        dict(goods_id=good_b, before=_counts(4), after=_counts(1)),
    ]
    body = dict(request_group=group, rows=rows, reason='Recounted receiving cartons')
    with client.application.app_context():
        lot = GoodsLot(goods_id=good_a, lot_no='OTHER-LOT')
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    invalid_lot = dict(goods_id=good_a, lot_id=lot_id, before=_counts(0), after=_counts(1))
    assert client.post(endpoint, headers=headers, json={**body, 'rows': [invalid_lot]}).status_code == 400
    proposals = _ok(client.post(endpoint, headers=headers, json=body))['corrections']
    assert len(proposals) == 2 and all(row['request_group'] == group for row in proposals)
    assert _ok(client.post(endpoint, headers=headers, json=body))['corrections'] == proposals
    decision = f"{endpoint}/{proposals[0]['id']}/review"
    assert client.post(decision, headers=reviewer, json={'decision': 'approved'}).status_code == 400
    with client.application.app_context():
        source_id = ReceiptPendingStock.query.filter_by(asn_id=asn_id, goods_id=good_b).one().id
    _ok(client.post('/putaway/', headers=headers, json={
        'goods_id': good_b, 'location_id': world['std1'], 'quantity': 2, 'receipt_source_id': source_id,
    }))
    _err(client.post(decision, headers=reviewer, json={'decision': 'approved', 'request_group': group}), 409, 17037)
    assert _inv(client, good_a, world['wh'])['sorted_stock'] == 2
    assert _ok(client.get(task, headers=headers))['total_sorted_quantity'] == 6
    snapshot = _ok(client.get(endpoint, headers=headers))
    assert {row['status'] for row in snapshot['corrections']} == {'pending'}

    rejected = _ok(client.post(decision, headers=reviewer, json={
        'decision': 'rejected', 'request_group': group, 'note': 'Already put away',
    }))
    assert {row['status'] for row in rejected['corrections']} == {'rejected'}
    new_group = str(uuid4())
    rows[1]['after'] = _counts(5)
    approved_request = _ok(client.post(endpoint, headers=headers, json={
        **body, 'request_group': new_group,
    }))['corrections']
    review_body = {'decision': 'approved', 'request_group': new_group}
    decision = f"{endpoint}/{approved_request[0]['id']}/review"
    approved = _ok(client.post(decision, headers=reviewer, json=review_body))
    assert len(approved['corrections']) == 2 and {row['status'] for row in approved['corrections']} == {'approved'}
    assert _ok(client.post(decision, headers=reviewer, json=review_body)) == approved
    assert _ok(client.get(task, headers=headers))['total_sorted_quantity'] == 8
    assert _inv(client, good_a, world['wh'])['sorted_stock'] == 3
    assert _inv(client, good_b, world['wh'])['sorted_stock'] == 3
