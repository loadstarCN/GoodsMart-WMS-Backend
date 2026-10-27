from datetime import datetime as dt, timezone as tz, timedelta as delta
from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff
from warehouse.asn.models import ASN


def test_supplier_summary_counts_and_last_actual_receipt(client, access_token):
    with client.application.app_context():
        wid = get_warehouse().id
        docs = ASN.query.filter_by(warehouse_id=wid).order_by(ASN.id).all()
        sid = docs[0].supplier_id
        now = dt.now(tz.utc)
        docs[0].received_at = now
        docs[1].received_at = now - delta(days=2)
        expected_last = docs[0].id
        count = len([doc for doc in docs if doc.supplier_id == sid and doc.is_active])
        db.session.commit()
    result = client.get(f'/asn/supplier-summary?supplier_ids={sid},2147483647', headers=_h(access_token, wid))
    assert result.status_code == 200, result.get_json()
    rows = result.get_json()['items']
    assert len(rows) == 1
    assert rows[0]['total'] == count
    assert rows[0]['asn_id'] == expected_last
    assert rows[0]['received_at'] is not None


def test_supplier_summary_permissions_and_input(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        wid = warehouse.id
        user = _staff('supplier_stats_reader', _role('supplier_stats_reader', ['supplier_read']), [warehouse])
        token = create_access_token(identity=user)
    assert client.get('/asn/supplier-summary?supplier_ids=1', headers=_h(token, wid)).status_code == 403
    for value in ('', '0', '-1', '1,x', '2147483648', ','.join(['1'] * 101)):
        assert client.get('/asn/supplier-summary?supplier_ids=' + value, headers=_h(access_token, wid)).status_code == 400


from tests.test_isolation_inbound import foreign


def test_supplier_summary_does_not_leak_foreign_receipts(client, foreign):
    with client.application.app_context():
        sid = get_asn().supplier_id
        wid = get_warehouse().id
    headers = _h(foreign['token'])
    response = client.get(f'/asn/supplier-summary?supplier_ids={sid}', headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {'items': []}
    assert client.get(f'/asn/supplier-summary?supplier_ids={sid}',
                      headers=_h(foreign['token'], wid)).status_code == 403


def test_asn_keyword_finds_order_number(client, access_token):
    with client.application.app_context():
        asn = get_asn()
        asn.order_number = 'PO-VISUAL-173'
        asn_id, wid = asn.id, asn.warehouse_id
        db.session.commit()
    response = client.get('/asn/?keyword=PO-VISUAL-173', headers=_h(access_token, wid))
    assert response.status_code == 200
    assert [row['id'] for row in response.get_json()['items']] == [asn_id]
