import io
from decimal import Decimal
import pytest
from tests.helpers import *
from tests.test_inventory_guards import _h, _ok
from tests.test_isolation_inbound import foreign
from warehouse.carrier.freight_models import CarrierInvoice
from warehouse.carrier import freight_files


def world(client):
    with client.application.app_context():
        warehouse, carrier, recipient, admin = get_warehouse(), get_carrier(), get_recipient(), get_admin_user()
        day = warehouse_today()
        dn = DN(warehouse_id=warehouse.id, recipient_id=recipient.id, shipping_address='Test', carrier_id=carrier.id,
                status='delivered', created_by=admin.id, expected_shipping_date=day)
        db.session.add(dn); db.session.flush()
        task = DeliveryTask(dn_id=dn.id, recipient_id=recipient.id, shipping_address='Test', carrier_id=carrier.id,
            expected_shipping_date=day, actual_shipping_date=day, tracking_number='TEST 0001', status='completed',
            shipping_cost=100, currency='JPY', created_by=admin.id)
        db.session.add(task); db.session.commit()
        return dict(carrier=carrier.id, warehouse=warehouse.id, dn=dn.id, day=day.isoformat())


def upload(client, token, w, endpoint, content, number='INV-001'):
    return client.post(f'/carrier/{w["carrier"]}/freight-{endpoint}', headers=_h(token), data={
        'invoice_number': number, 'invoice_date': w['day'], 'period_from': w['day'], 'period_to': w['day'],
        'currency': 'JPY', 'file': (io.BytesIO(content), 'invoice.csv')}, content_type='multipart/form-data')


def csv(w):
    return ('tracking_number,shipping_date,base,tax,total\n'
            f'TEST-0001,{w["day"]},100,10,110\n'
            f'UNKNOWN,{w["day"]},20,2,22\n').encode()


def test_invoice_metadata_reads_consistent_columns_and_scoped_endpoint(client, access_token, foreign):
    w = world(client)
    content = ("invoice_number,invoice_date,currency,shipment date,total\n"
               "META-1,2026-10-31,jpy,2026-10-01,10\n"
               "META-1,2026-10-31,jpy,2026-10-30,20\n").encode()
    expected = dict(invoice_number='META-1', invoice_date='2026-10-31', currency='JPY',
                    period_from='2026-10-01', period_to='2026-10-30')
    assert freight_files.inspect_metadata(content, 'bill.csv') == expected
    response = upload(client, access_token, w, 'metadata', content)
    assert response.status_code == 200 and response.get_json()['metadata'] == expected
    assert upload(client, foreign['token'], w, 'metadata', content).status_code in (403, 404)
    assert upload(client, access_token, w, 'metadata', content.replace(b'META-1,2026-10-31,jpy,2026-10-30', b'META-2,2026-10-31,jpy,2026-10-30')).status_code == 400
    assert upload(client, access_token, w, 'metadata', content.replace(b'2026-10-30', b'invalid')).status_code == 400
    from warehouse.goods.tabular import write_xlsx
    sheet = write_xlsx('Invoice', ['invoice_number','invoice_date','currency','shipping_date'],
                       [['META-1','2026-10-31','JPY','2026-10-01'],['META-1','2026-10-31','JPY','2026-10-30']])
    assert freight_files.inspect_metadata(sheet, 'bill.xlsx') == expected


def test_report_invoice_summary_keeps_unlinked_charges_and_scope_separate(client, access_token, foreign):
    w = world(client)
    invoice = _ok(upload(client, access_token, w, 'import', csv(w)))
    components = _ok(client.get('/carrier/freight/report', headers=_h(access_token)))['distributions']['JPY']['charges']
    assert {row['id']: Decimal(row['billed']) for row in components} == {'base': Decimal(100), 'tax': Decimal(10)}
    assert all(row['comparable_count'] == 0 and row['estimate_count'] == 0 for row in components)
    url = '/carrier/freight/report'
    report = _ok(client.get(url, headers=_h(access_token)))
    summary = report['invoice_summary']
    assert summary['total'] == summary['pending'] == 1
    assert summary['confirmed'] == 0 and summary['unresolved'] == 2
    assert summary['items'][0]['id'] == invoice['id']
    assert summary['totals']['JPY'] == {'amount':'132.00','unlinked_amount':'22.00','unlinked_count':1}
    assert report['totals']['JPY']['billed'] == '110.00'
    assert _ok(client.get(url+'?country=ZZ&state=matched', headers=_h(access_token)))['invoice_summary'] == summary
    assert _ok(client.get(url, headers=_h(foreign['token'])))['invoice_summary']['total'] == 0
    assert _ok(client.get(url+'?start_date=2020-01-01&end_date=2020-01-31', headers=_h(access_token)))['invoice_summary']['total'] == 0


def test_document_freight_matches_report_without_default_date_window(client, access_token, foreign):
    from datetime import date
    w = world(client)
    invoice = _ok(upload(client, access_token, w, 'import', csv(w)))
    expected = next(row for row in _ok(client.get('/carrier/freight/report',headers=_h(access_token)))['items'] if row['dn_id']==w['dn'])
    url = f'/carrier/freight/dn/{w["dn"]}'
    assert _ok(client.get(url,headers=_h(access_token)))['item'] == expected
    assert _ok(client.get(url,headers=_h(foreign['token'])))['item'] is None
    with client.application.app_context():
        task=DeliveryTask.query.filter_by(dn_id=w['dn']).first()
        task.expected_shipping_date=task.actual_shipping_date=date(2001,1,1)
        db.session.commit()
    row = _ok(client.get(url,headers=_h(access_token)))['item']
    assert row['shipping_date']=='2001-01-01' and row['billed']=={'JPY':'110.00'}
    assert row['difference']=='10.00' and row['invoices'][0]['id']==invoice['id']
    assert client.get(url).status_code==401


def test_freight_preview_import_review_confirm_original_and_export(client, access_token):
    w = world(client)
    content = csv(w)
    preview = _ok(upload(client, access_token, w, 'preview', content))
    assert preview['can_import'] and preview['total'] == '132'
    assert [line['state'] for line in preview['lines']] == ['difference', 'unmatched']
    assert preview['lines'][0]['dn_id'] == w['dn']
    with client.application.app_context():
        assert CarrierInvoice.query.count() == 0
    invoice = _ok(upload(client, access_token, w, 'import', content))
    url = f'/carrier/freight/invoices/{invoice["id"]}'
    assert invoice['status'] == 'pending' and invoice['unresolved'] == 2
    assert upload(client, access_token, w, 'import', content).status_code == 409
    assert upload(client, access_token, w, 'import', content, number='INV-002').status_code == 409
    assert client.put(url+'/confirm', json={'revision': 1, 'confirmed': True}, headers=_h(access_token)).status_code == 409
    first, second = invoice['lines']
    invoice = _ok(client.put(url+f'/lines/{first["id"]}', json={'revision': 1, 'review_status': 'accepted', 'note': 'Verified extra tax'}, headers=_h(access_token)))
    assert client.put(url+f'/lines/{second["id"]}', json={'revision': 1, 'review_status': 'external', 'note': 'Inbound charge'}, headers=_h(access_token)).status_code == 409
    invoice = _ok(client.put(url+f'/lines/{second["id"]}', json={'revision': 2, 'review_status': 'external', 'note': '=External charge'}, headers=_h(access_token)))
    invoice = _ok(client.put(url+'/confirm', json={'revision': 3, 'confirmed': True}, headers=_h(access_token)))
    assert invoice['status'] == 'confirmed' and invoice['unresolved'] == 0
    assert invoice['created_by']['id'] and invoice['created_by']['name']
    assert invoice['confirmed_by']['id'] and invoice['confirmed_by']['name']
    assert invoice['created_at'] and invoice['confirmed_at']
    assert client.put(url+f'/lines/{first["id"]}', json={'revision': 4, 'review_status': 'disputed', 'note': 'Changed'}, headers=_h(access_token)).status_code == 409
    assert client.get(url+'/file', headers=_h(access_token)).data == content
    export = client.get(url+'/export', headers=_h(access_token))
    assert export.status_code == 200 and "'=External charge" in export.data.decode('utf-8-sig')
    assert client.get(url+'/export?format=xlsx', headers=_h(access_token)).data.startswith(b'PK')
    reopened = _ok(client.put(url+'/confirm', json={'revision': 4, 'confirmed': False}, headers=_h(access_token)))
    assert reopened['status'] == 'pending'


def test_freight_bad_rows_are_atomic_and_company_scope_is_enforced(client, access_token, foreign):
    w = world(client)
    invalid = csv(w).replace(b'100,10,110', b'100,10,111')
    checked = _ok(upload(client, access_token, w, 'preview', invalid))
    assert checked['error_count'] == 1 and not checked['can_import']
    assert upload(client, access_token, w, 'import', invalid).status_code == 400
    assert upload(client, foreign['token'], w, 'preview', csv(w)).status_code in (403, 404)
    imported = _ok(upload(client, access_token, w, 'import', csv(w)))
    url = f'/carrier/freight/invoices/{imported["id"]}'
    for suffix in ('', '/file', '/export'):
        assert client.get(url+suffix, headers=_h(foreign['token'])).status_code in (403, 404)
    listing = _ok(client.get('/carrier/freight/invoices', headers=_h(foreign['token'])))
    assert listing['items'] == [] and listing['totals'] == {}


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1.001', True, '1e20'])
def test_freight_decimal_rejects_nonfinite_or_lossy_values(value):
    with pytest.raises(ValueError):
        freight_files.money(value, 'total')


def test_freight_report_keeps_unknown_estimates_and_company_scope(client, access_token, foreign):
    w = world(client)
    url = '/carrier/freight/report'
    report = _ok(client.get(url, headers=_h(access_token)))
    row = next(row for row in report['items'] if row['dn_id'] == w['dn'])
    assert row['state'] == 'unbilled' and row['difference'] is None
    invoice = _ok(upload(client, access_token, w, 'import', csv(w)))
    report = _ok(client.get(url, headers=_h(access_token)))
    row = next(row for row in report['items'] if row['dn_id'] == w['dn'])
    assert row['state'] == 'difference' and Decimal(row['difference']) == 10
    assert row['billed'] == {'JPY': '110.00'} and row['invoices'][0]['id'] == invoice['id']
    with client.application.app_context():
        task = DeliveryTask.query.filter_by(dn_id=w['dn']).first()
        task.shipping_cost = 0
        db.session.commit()
    report = _ok(client.get(url, headers=_h(access_token)))
    row = next(row for row in report['items'] if row['dn_id'] == w['dn'])
    assert row['state'] == 'no_estimate' and row['difference'] is None


def test_report_uses_invoice_tolerance_and_full_scope_distribution(client, access_token):
    w = world(client)
    _ok(client.put(f'/carrier/{w["carrier"]}/freight-settings', headers=_h(access_token),
        json=dict(revision=0, tolerance_type='amount', tolerance='10', currency='JPY', column_mapping={})))
    _ok(upload(client, access_token, w, 'import', csv(w)))
    data = _ok(client.get('/carrier/freight/report?per_page=1', headers=_h(access_token)))
    row = next(row for row in data['items'] if row['dn_id'] == w['dn'])
    assert row['state'] == 'matched' and Decimal(row['difference']) == 10
    chart = next(row for row in data['distributions']['JPY']['carrier'] if row['id'] == w['carrier'])
    assert Decimal(chart['billed']) == 110 and Decimal(chart['estimate']) == 100
    assert chart['comparable_count'] == 1 and data['states']['matched'] == 1


def test_freight_country_weight_export_and_dispute_confirmation(client, access_token, foreign):
    from warehouse.dn.models import DNPackage
    w=world(client)
    with client.application.app_context():
        dn=db.session.get(DN,w['dn']);dn.order_number='=unsafe'
        dn.recipient.country='JP'
        db.session.add(DNPackage(dn_id=dn.id,package_no=1,gross_weight_kg=Decimal('2.5'),length_mm=100,width_mm=100,height_mm=100))
        db.session.commit()
    content=f"tracking_number,shipping_date,destination_country,billable_weight,total\nTEST-0001,{w['day']},JP,3.000,110\n".encode()
    invoice=_ok(upload(client,access_token,w,'import',content))
    url='/carrier/freight/report'
    result=_ok(client.get(url+'?country=JP&per_page=1',headers=_h(access_token)))
    row=next(row for row in result['items'] if row['dn_id']==w['dn'])
    assert row['actual_weight']=='2.500' and row['billed_weights'][str(invoice['id'])]=='3.000'
    assert row['package_count']==1 and row['destination_country']=='JP'
    assert _ok(client.get(url+'?country=US',headers=_h(access_token)))['total']==0
    response=client.get(url+'/export?country=JP&page=99',headers=_h(access_token))
    assert response.status_code==200 and "'=unsafe" in response.data.decode('utf-8-sig')
    assert client.get(url+'/export?format=xlsx',headers=_h(access_token)).data.startswith(b'PK')
    assert '=unsafe' not in client.get(url+'/export',headers=_h(foreign['token'])).data.decode('utf-8-sig')
    invoice_url=f'/carrier/freight/invoices/{invoice["id"]}'
    review=_ok(client.put(invoice_url+f'/lines/{invoice["lines"][0]["id"]}',headers=_h(access_token),json={'revision':1,'review_status':'disputed','note':'Carrier weight under review'}))
    assert review['unresolved']==0
    _ok(client.put(invoice_url+'/confirm',headers=_h(access_token),json={'revision':2,'confirmed':True}))
    assert _ok(client.get(url+'?state=disputed',headers=_h(access_token)))['items'][0]['state']=='disputed'
    assert all(row['dn_id'] != w['dn'] for row in _ok(client.get(url, headers=_h(foreign['token'])))['items'])
    assert client.get(url+'?start_date=2020-01-01&end_date=2026-01-01', headers=_h(access_token)).status_code == 400


def test_freight_settings_revision_and_currency_tolerance(client, access_token):
    w = world(client)
    url = f'/carrier/{w["carrier"]}/freight-settings'
    body = dict(revision=0, tolerance_type='amount', tolerance='10', currency='JPY', column_mapping={})
    saved = _ok(client.put(url, json=body, headers=_h(access_token)))
    assert saved['revision'] == 1
    assert client.put(url, json=body, headers=_h(access_token)).status_code == 409
    result = _ok(upload(client, access_token, w, 'preview', csv(w)))
    assert result['lines'][0]['state'] == 'matched'
    body.update(revision=1, currency='USD')
    _ok(client.put(url, json=body, headers=_h(access_token)))
    assert upload(client, access_token, w, 'import', csv(w)).status_code == 400


def test_freight_package_lines_count_quote_once_and_enforce_reader_scope(client, access_token):
    from warehouse.dn.models import DNCarrierShipment
    from tests.test_platform_guards import _role, _staff
    w=world(client)
    with client.application.app_context():
        db.session.add(DNCarrierShipment(dn_id=w['dn'],source='manual',status='active',tracking_number='MASTER',
            package_tracking_numbers=['CHILD-1','CHILD-2'],carrier_id=w['carrier'],net_charge=100,currency='JPY'))
        reader=_staff('freight_reader',_role('freight_reader',['freight_read']),[get_warehouse()])
        token=create_access_token(identity=reader)
        db.session.commit()
    content=f'tracking_number,shipping_date,total\nCHILD-1,{w["day"]},60\nCHILD-2,{w["day"]},50\n'.encode()
    # Company-wide invoice cannot be exposed to a partial warehouse reader.
    company_invoice=_ok(upload(client,access_token,w,'import',content))
    assert client.get(f'/carrier/freight/invoices/{company_invoice["id"]}',headers={**_h(token),'X-WAREHOUSE-ID':str(w['warehouse'])}).status_code==403
    assert _ok(client.get('/carrier/freight/invoices',headers={**_h(token),'X-WAREHOUSE-ID':str(w['warehouse'])}))['items']==[]
    detail=_ok(client.get(f'/carrier/freight/dn/{w["dn"]}',headers={**_h(token),'X-WAREHOUSE-ID':str(w['warehouse'])}))['item']
    assert detail['billed']=={} and detail['invoices']==[] and detail['difference'] is None
    assert client.post(f'/carrier/{w["carrier"]}/freight-import',headers={**_h(token),'X-WAREHOUSE-ID':str(w['warehouse'])}).status_code==403
    assert all(line['estimated_amount'] is None for line in company_invoice['lines'])
    report=_ok(client.get('/carrier/freight/report',headers=_h(access_token)))
    row=next(row for row in report['items'] if row['dn_id']==w['dn'])
    assert row['estimated_amount']=='100.00' and row['billed']=={'JPY':'110.00'}
    assert Decimal(report['totals']['JPY']['estimate'])==100
    assert row['difference']=='10.00'


def test_freight_migration_upgrade_and_protected_downgrade():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'migrations/versions/f8c2d6e3b045_carrier_invoices.py'
    spec = importlib.util.spec_from_file_location('freight_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        for table in ('companies', 'carriers', 'warehouses', 'users', 'dn'):
            connection.execute(sa.text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)'))
        connection.execute(sa.text('CREATE TABLE permissions (name TEXT PRIMARY KEY, description TEXT)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT COUNT(*) FROM permissions')).scalar() == 2
            connection.execute(sa.text("INSERT INTO carrier_freight_settings (carrier_id,revision,tolerance_type,tolerance,column_mapping) VALUES (1,1,'amount',0,'{}')"))
            with pytest.raises(RuntimeError, match='Cannot discard'):
                migration.downgrade()
            connection.execute(sa.text('DELETE FROM carrier_freight_settings'))
            migration.downgrade()
            assert 'carrier_invoices' not in sa.inspect(connection).get_table_names()
