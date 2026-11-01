from .helpers import *


def headers(token):
    return {'Authorization': f'Bearer {token}', 'X-Warehouse-ID': '1'}


def test_sorting_variance_requires_completed_result(client, access_company_admin_token):
    h = headers(access_company_admin_token)
    with client.application.app_context():
        task = get_sorting_task()
        task_id = task.id
        for line in task.asn.details:
            line.actual_quantity = line.quantity
        task.asn.details[0].actual_quantity -= 1
        db.session.commit()
    assert client.get('/sorting/?has_variance=true', headers=h).json['total'] == 0
    with client.application.app_context():
        get_sorting_task().status = 'completed'
        db.session.commit()
    result = client.get('/sorting/?has_variance=true&per_page=1', headers=h)
    assert result.status_code == 200
    assert result.json['total'] == 1
    assert result.json['items'][0]['id'] == task_id
    assert result.json['items'][0]['has_variance'] is True
    assert client.get('/sorting/?has_variance=false', headers=h).json['total'] == 0
    assert client.get('/sorting/?has_variance=true&status=pending', headers=h).json['total'] == 0


def test_sorting_received_date_includes_whole_warehouse_day(client, access_company_admin_token):
    h = headers(access_company_admin_token)
    with client.application.app_context():
        task = get_sorting_task()
        task.asn.warehouse.timezone = 'Asia/Tokyo'
        task.asn.received_at = datetime.datetime(2026, 10, 31, 15, tzinfo=datetime.timezone.utc)
        db.session.commit()
    path = '/sorting/?received_from=2026-11-01&received_to=2026-11-01'
    assert client.get(path, headers=h).json['total'] == 1
    with client.application.app_context():
        get_sorting_task().asn.received_at = datetime.datetime(2026, 11, 1, 14, 59, 59, tzinfo=datetime.timezone.utc)
        db.session.commit()
    assert client.get(path, headers=h).json['total'] == 1
    with client.application.app_context():
        get_sorting_task().asn.received_at = datetime.datetime(2026, 11, 1, 15, tzinfo=datetime.timezone.utc)
        db.session.commit()
    assert client.get(path, headers=h).json['total'] == 0
    assert client.get('/sorting/?received_from=bad', headers=h).status_code == 400
    assert client.get('/sorting/?received_from=2026-11-02&received_to=2026-11-01', headers=h).status_code == 400


def test_sorting_pending_specs_follow_existing_capture_rules(client, access_company_admin_token):
    h = headers(access_company_admin_token)
    with client.application.app_context():
        task = get_sorting_task()
        task.asn.spec_capture_fields = ['weight']
        task.asn.spec_capture_mode = 'remeasure'
        db.session.commit()
    result = client.get('/sorting/?spec_pending=true', headers=h)
    assert result.status_code == 200
    assert result.json['total'] == 1
    assert result.json['items'][0]['spec_pending_count'] == 2
    with client.application.app_context():
        for line in get_sorting_task().asn.details:
            line.spec_capture_note = 'supplier sealed'
        db.session.commit()
    assert client.get('/sorting/?spec_pending=true', headers=h).json['total'] == 0
    assert client.get('/sorting/?spec_pending=false', headers=h).json['items'][0]['spec_pending_count'] == 0


def test_packing_variance_summary_and_warehouse_scope(client, access_company_admin_token):
    h = headers(access_company_admin_token)
    with client.application.app_context():
        task = get_packing_task()
        for line in task.dn.details:
            line.picked_quantity = 10
            line.packed_quantity = 9
        db.session.commit()
    assert client.get('/packing/?has_variance=true', headers=h).json['total'] == 0
    with client.application.app_context():
        get_packing_task().status = 'completed'
        db.session.commit()
    result = client.get('/packing/?has_variance=true&per_page=1&warehouse_id=2', headers=h)
    assert result.status_code == 200
    assert result.json['total'] == 1
    row = result.json['items'][0]
    assert row['has_variance'] is True
    assert row['expected_goods_count'] == 1
    assert row['packed_goods_count'] == 1
    assert row['package_count'] == 0
    assert client.get('/packing/?has_variance=false', headers=h).json['total'] == 0
    assert client.get('/packing/?has_variance=true', headers={**h, 'X-Warehouse-ID': '2'}).json['total'] == 0


def test_sorting_damage_filtered_before_pagination(client, access_company_admin_token):
    h = headers(access_company_admin_token)
    with client.application.app_context():
        task = get_sorting_task()
        batch = SortingBatch(sorting_task_id=task.id, operator_id=get_admin_user().id)
        db.session.add(batch)
        db.session.flush()
        db.session.add(SortingTaskDetail(sorting_task_id=task.id, batch_id=batch.id,
            goods_id=task.asn.details[0].goods_id, damage_quantity=1,
            sorted_quantity=0, operator_id=get_admin_user().id))
        db.session.add(SortingTask(asn_id=task.asn_id, status='pending', is_active=True,
                                   created_by=get_admin_user().id))
        db.session.commit()
        expected = task.id
    result = client.get('/sorting/?has_damage=true&per_page=1&warehouse_id=2', headers=h)
    assert result.status_code == 200
    assert result.json['total'] == 1
    assert result.json['items'][0]['id'] == expected
    assert client.get('/sorting/?has_damage=false', headers=h).json['total'] == 1
    assert client.get('/sorting/?has_damage=true', headers={**h, 'X-Warehouse-ID':'2'}).json['total'] == 0


def test_sorting_search_finds_goods_before_work_starts(client, access_company_admin_token):
    with client.application.app_context():
        task = get_sorting_task()
        code = task.asn.details[0].goods.code
        task_id = task.id
        assert len(task.task_details) == 0
    result = client.get('/sorting/', query_string={'keyword':code}, headers=headers(access_company_admin_token))
    assert result.status_code == 200
    assert result.json['total'] == 1
    assert result.json['items'][0]['id'] == task_id
