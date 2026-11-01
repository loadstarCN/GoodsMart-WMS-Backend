import csv
import io
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff


def test_pending_export_matches_scope_and_ignores_pagination(client, access_token):
    with client.application.app_context():
        wid, gid = get_warehouse().id, get_goods().id
        add_stock(gid, wid, pending=4)
        goods = get_goods()
        goods.name = '=SUM(1,2)'
        db.session.commit()
    headers = _h(access_token, wid)
    result = client.get('/inventory/pending-putaway/export?page=99&per_page=1', headers=headers)
    assert result.status_code == 200
    rows = list(csv.DictReader(io.StringIO(result.data.decode('utf-8-sig'))))
    source = client.get('/inventory/?pending_putaway=true&all=true', headers=headers).get_json()['items']
    assert len(rows) == len(source)
    assert {int(row['warehouse_id']) for row in rows} == {wid}
    assert sum(int(row['total_quantity']) for row in rows) == sum(row['sorted_stock'] for row in source)
    assert next(row for row in rows if int(row['goods_id']) == gid)['goods_name'] == "'=SUM(1,2)"
    empty = client.get('/inventory/pending-putaway/export?keyword=definitely-absent', headers=headers)
    assert list(csv.DictReader(io.StringIO(empty.data.decode('utf-8-sig')))) == []


def test_pending_export_requires_inventory_read(client):
    with client.application.app_context():
        warehouse = get_warehouse()
        stranger = _staff('pending_export_stranger', _role('pending_export_stranger', ['goods_read']), [warehouse])
        token, wid = create_access_token(identity=stranger), warehouse.id
    assert client.get('/inventory/pending-putaway/export', headers=_h(token, wid)).status_code == 403


def test_pending_age_filters_use_known_quantities_even_with_unknown_age(client, access_token, monkeypatch):
    from datetime import timedelta
    from warehouse.common import clock
    from warehouse.inventory import pending
    with client.application.app_context():
        gid, wid = get_goods().id, get_warehouse().id
        add_stock(gid, wid, pending=4)
    monkeypatch.setattr(pending, '_entries', lambda pairs: {})
    monkeypatch.setattr(pending, '_tracked_entries', lambda pairs: {
        (gid, wid): [(None, 2), (clock.now()-timedelta(days=5), 2)]})
    headers = _h(access_token, wid)
    response = client.get('/inventory/?pending_putaway=true&pending_overdue_days=3', headers=headers)
    assert response.status_code == 200
    rows = response.get_json()['items']
    assert len(rows) == 1 and rows[0]['goods_id'] == gid and rows[0].get('pending_since') is None
    assert client.get('/inventory/?pending_overdue_days=7', headers=headers).get_json()['total'] == 0
    assert client.get('/inventory/?pending_damaged=true', headers=headers).get_json()['total'] == 0
    export = client.get('/inventory/pending-putaway/export?pending_overdue_days=7', headers=headers)
    assert list(csv.DictReader(io.StringIO(export.data.decode('utf-8-sig')))) == []
    assert client.get('/inventory/?pending_overdue_days=-1', headers=headers).status_code == 400
