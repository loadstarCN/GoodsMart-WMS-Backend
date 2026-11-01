from .helpers import *
from warehouse.stock.models import GoodsLot


def test_awaiting_approval_filters_before_pagination_and_uses_actor(client, access_company_admin_token):
    with client.application.app_context():
        actor = get_company_admin_user().id
        creator = get_admin_user().id
        rows = [Adjustment(warehouse_id=1, created_by=creator, status='pending', is_active=True,
                           adjustment_reason='approval-filter') for _ in range(7)]
        rows[2].created_by = actor
        rows[3].details_modified_by = actor
        rows[4].status = 'approved'
        rows[5].is_active = False
        rows[6].warehouse_id = 2
        foreign = Warehouse(name='Foreign filter warehouse', company_id=2, created_by=creator)
        db.session.add(foreign)
        db.session.flush()
        db.session.add(Adjustment(warehouse_id=foreign.id, created_by=creator, status='pending',
                                  is_active=True, adjustment_reason='approval-filter'))
        db.session.add_all(rows)
        db.session.commit()
        eligible = {rows[0].id, rows[1].id}
    headers = {'Authorization': f'Bearer {access_company_admin_token}', 'X-Warehouse-ID': '1'}
    query = '/adjustment/?keyword=approval-filter&awaiting_my_approval=true&per_page=1'
    pages = [client.get(query + f'&page={page}&awaiting_approver_id={creator}', headers=headers)
             for page in (1, 2)]
    assert all(response.status_code == 200 for response in pages)
    assert all(response.json['total'] == 2 for response in pages)
    assert {response.json['items'][0]['id'] for response in pages} == eligible
    assert client.get(query + '&status=approved', headers=headers).json['total'] == 0
    assert client.get(query.replace('=true', '=false'), headers=headers).json['total'] == 5
    unscoped = client.get(query, headers={'Authorization': f'Bearer {access_company_admin_token}'})
    assert unscoped.json['total'] == 3
    assert client.get(query).status_code == 401


def test_inventory_task_search_matches_lot_without_duplicate_documents(client, access_company_admin_token):
    with client.application.app_context():
        adjustment = get_adjustment()
        cyclecount = get_cyclecount_task()
        for lines in (adjustment.details, cyclecount.task_details):
            for index, line in enumerate(lines):
                lot = GoodsLot(goods_id=line.goods_id, lot_no=f'QA-SEARCH-{line.goods_id}-{index}')
                db.session.add(lot)
                db.session.flush()
                line.lot_id = lot.id
        db.session.commit()
        expected = {'adjustment': adjustment.id, 'cyclecount': cyclecount.id}
    headers = {'Authorization': f'Bearer {access_company_admin_token}', 'X-Warehouse-ID': '1'}
    for kind, doc_id in expected.items():
        result = client.get(f'/{kind}/?keyword=qa-search&per_page=1', headers=headers)
        assert result.status_code == 200
        assert result.json['total'] == 1
        assert result.json['items'][0]['id'] == doc_id
        assert len(result.json['items'][0]['lot_numbers']) == 2
        assert result.json['items'][0]['goods_count'] == 2
        assert client.get(f'/{kind}/?keyword=no-such-lot', headers=headers).json['total'] == 0


def test_adjustment_source_and_creator_filters(client, access_company_admin_token):
    with client.application.app_context():
        original = get_adjustment()
        original_id = original.id
        actor = get_company_admin_user().id
        linked = Adjustment(warehouse_id=1, created_by=actor, cyclecount_id=get_cyclecount_task().id,
                            status='pending', is_active=True)
        db.session.add(linked)
        db.session.commit()
        linked_id = linked.id
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    assert [row['id'] for row in client.get('/adjustment/?source=manual', headers=headers).json['items']] == [original_id]
    assert [row['id'] for row in client.get('/adjustment/?source=cyclecount', headers=headers).json['items']] == [linked_id]
    assert client.get(f'/adjustment/?source=manual&created_by={actor}', headers=headers).json['total'] == 0
    assert client.get('/adjustment/?source=invalid', headers=headers).status_code == 400


def test_cyclecount_variance_keeps_offsetting_lines_and_ignores_unrecorded(client, access_company_admin_token):
    with client.application.app_context():
        task = get_cyclecount_task()
        for line, delta in zip(task.task_details, (1, -1)):
            line.system_quantity = 10
            line.actual_quantity = 10 + delta
            line.difference = delta
            line.operator_id = get_admin_user().id
        db.session.commit()
        task_id = task.id
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    response = client.get('/cyclecount/?has_variance=true', headers=headers)
    assert response.status_code == 200
    assert [row['id'] for row in response.json['items']] == [task_id]
    assert response.json['items'][0]['recorded_count'] == 2
    assert response.json['items'][0]['variance_count'] == 2
    assert response.json['items'][0]['total_difference'] == 0
    assert client.get('/cyclecount/?has_variance=false', headers=headers).json['total'] == 0
    with client.application.app_context():
        for line in get_cyclecount_task().task_details:
            line.operator_id = None
            line.status = 'pending'
        db.session.commit()
    assert client.get('/cyclecount/?has_variance=true', headers=headers).json['total'] == 0
    ordinary = client.get('/cyclecount/', headers=headers).json['items'][0]
    assert ordinary['recorded_count'] == ordinary['variance_count'] == 0
