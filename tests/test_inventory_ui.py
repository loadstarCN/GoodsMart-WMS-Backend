from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff


def test_summary_matches_all_filtered_rows(client, access_token):
    with client.application.app_context():
        wid = get_warehouse().id
    headers = _h(access_token, wid)
    for query in ('', '?keyword=G001', '?pending_putaway=true', '?keyword=not-found'):
        rows = client.get('/inventory/' + query, headers=headers).get_json()['items']
        response = client.get('/inventory/summary' + query, headers=headers)
        assert response.status_code == 200, response.get_json()
        result = response.get_json()
        assert result['inventory_count'] == len(rows)
        for key in ('onhand_stock', 'total_stock', 'available_stock_for_sale', 'sorted_stock'):
            assert result[key] == sum(row[key] for row in rows)


def test_inventory_summary_and_threshold_permissions(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid, gid = warehouse.id, get_goods().id
        reader = _staff('threshold_reader', _role('threshold_reader', ['inventory_read']), [warehouse])
        stranger = _staff('threshold_stranger', _role('threshold_stranger', ['goods_read']), [warehouse])
        reader_token, stranger_token = create_access_token(identity=reader), create_access_token(identity=stranger)
    assert client.get('/inventory/summary', headers=_h(reader_token, wid)).status_code == 200
    assert client.get('/inventory/summary', headers=_h(stranger_token, wid)).status_code == 403
    assert client.put('/inventory/thresholds', headers=_h(reader_token, wid), json={
        'items': [{'goods_id': gid, 'low_stock_threshold': 1}]}).status_code == 403
    assert client.put('/inventory/thresholds', headers=_h(access_token), json={
        'items': [{'goods_id': gid, 'low_stock_threshold': 1}]}).status_code == 400
