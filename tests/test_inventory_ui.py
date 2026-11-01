from tests.helpers import *
from tests.test_inventory_availability import _h
from tests.test_platform_guards import _role, _staff


def test_summary_matches_all_filtered_rows(client, access_token):
    with client.application.app_context():
        wid = get_warehouse().id
    headers = _h(access_token, wid)
    for query in ('', '?keyword=G001', '?pending_putaway=true', '?keyword=not-found',
                  '?stock_bucket=total_stock', '?stock_bucket=working', '?stock_bucket=damage_or_return',
                  '?stock_bucket=available_stock_for_sale', '?stock_bucket=asn_stock'):
        rows = client.get('/inventory/' + query, headers=headers).get_json()['items']
        response = client.get('/inventory/summary' + query, headers=headers)
        assert response.status_code == 200, response.get_json()
        result = response.get_json()
        assert result['inventory_count'] == len(rows)
        for key in ('onhand_stock', 'total_stock', 'available_stock_for_sale', 'sorted_stock', 'unshippable_stock'):
            assert result[key] == sum(row[key] for row in rows)


def test_summary_explains_stock_blocked_by_lot_rules(client, access_token):
    from warehouse.stock.models import GoodsLotRules
    with client.application.app_context():
        wid, gid = get_warehouse().id, get_goods().id
        db.session.add(GoodsLotRules(goods_id=gid, revision=1, overrides={'mode':'lot','allow_unassigned':False}))
        db.session.commit()
    summary = client.get(f'/inventory/summary?goods_id={gid}', headers=_h(access_token, wid)).json
    assert summary['unshippable_stock'] > 0
    assert summary['onhand_stock'] == sum(summary[key] for key in ('available_stock_for_sale','dn_stock','locked_stock','unshippable_stock'))
    blocked = client.get(f'/inventory/?goods_id={gid}&stock_bucket=unshippable_stock', headers=_h(access_token, wid)).json
    assert blocked['total'] == 1
    assert client.get(f'/inventory/?goods_id={gid}&stock_bucket=available_stock_for_sale', headers=_h(access_token, wid)).json['total'] == 0
    assert client.get('/inventory/?stock_bucket=invalid', headers=_h(access_token, wid)).status_code == 400


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


def test_inventory_metrics_use_latest_warehouse_snapshot_and_keep_live_stock(client, access_token):
    from datetime import date
    from warehouse.stock.models import InventoryMetricsDaily
    with client.application.app_context():
        wid, gid = get_warehouse().id, get_goods().id
        db.session.add(InventoryMetricsDaily(warehouse_id=wid,goods_id=gid,metric_date=date(2026,10,30),
            total_stock=999,out_qty_30d=8,weighted_age_days=2,turnover_days_30d=3,abc_class='A'))
        db.session.commit()
    row = client.get(f'/inventory/?goods_id={gid}',headers=_h(access_token,wid)).json['items'][0]
    assert row['metrics']['out_qty_30d'] == 8 and row['metrics']['metric_date'] == '2026-10-30'
    assert row['total_stock'] != 999
    with client.application.app_context():
        other = Goods.query.filter(Goods.id != gid, Goods.company_id == get_warehouse().company_id).first()
        db.session.add(InventoryMetricsDaily(warehouse_id=wid,goods_id=other.id,metric_date=date(2026,10,31)))
        db.session.commit()
    # Missing the product in the latest warehouse snapshot must not expose its older snapshot as current.
    assert client.get(f'/inventory/?goods_id={gid}',headers=_h(access_token,wid)).json['items'][0].get('metrics') is None

def test_metrics_export_all_filtered_rows_and_formats(client, access_token):
    import csv, io
    from datetime import date
    from openpyxl import load_workbook
    from warehouse.stock.models import InventoryMetricsDaily
    with client.application.app_context():
        wid = get_warehouse().id
        goods = Goods.query.filter_by(company_id=get_warehouse().company_id).all()
        for index, good in enumerate(goods):
            db.session.add(InventoryMetricsDaily(warehouse_id=wid,goods_id=good.id,metric_date=date(2026,10,31),
                total_stock=index+1,out_qty_30d=0,status='normal'))
        count = len(goods)
        goods[0].name = '=QA formula'
        db.session.commit()
    headers = _h(access_token,wid)
    response = client.get('/inventory/metrics/export?per_page=1&page=2',headers=headers)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
    assert len(rows) == count+1
    assert any(row[4] == "'=QA formula" for row in rows[1:])
    assert all(row[6] == '0' and row[8] == '' for row in rows[1:])
    empty = client.get('/inventory/metrics/export?status=dead',headers=headers)
    assert len(list(csv.reader(io.StringIO(empty.data.decode('utf-8-sig'))))) == 1
    excel = client.get('/inventory/metrics/export?format=xlsx',headers=headers)
    workbook = load_workbook(io.BytesIO(excel.data),read_only=True)
    assert len(list(workbook.active.values)) == count+1
    workbook.close()
    assert client.get('/inventory/metrics/export?format=bad',headers=headers).status_code == 400
    assert client.get('/inventory/metrics/export?status=bad',headers=headers).status_code == 400
    assert client.get('/inventory/metrics/export').status_code == 401

def test_analysis_metadata_scope_is_shared_across_panels_and_export(client,access_token):
    from datetime import date
    from warehouse.stock.models import InventoryMetricsDaily
    import csv,io
    with client.application.app_context():
        warehouse=get_warehouse();wid=warehouse.id;gid=get_goods().id
        Goods.query.filter_by(company_id=warehouse.company_id).update({'brand':'OTHER','category':'OTHER'})
        good=db.session.get(Goods,gid);good.brand='QA-BRAND';good.category='QA-CATEGORY'
        db.session.add(InventoryMetricsDaily(warehouse_id=wid,goods_id=gid,metric_date=date(2026,11,1),total_stock=8))
        db.session.commit()
    headers=_h(access_token,wid)
    inventory=client.get(f'/inventory/?goods_id={gid}',headers=headers).json['items'][0]
    filtered=client.get('/inventory/?brand=QA-BRAND&category=QA-CATEGORY',headers=headers).json
    assert filtered['total']==1 and filtered['items'][0]['goods_id']==gid
    summary=client.get('/inventory/summary?brand=QA-BRAND',headers=headers).json
    assert summary['onhand_stock']==inventory['onhand_stock']
    metrics=client.get('/inventory/metrics?brand=QA-BRAND',headers=headers).json
    assert metrics['total']==1 and metrics['summary']['sku_with_stock']==1
    options=client.get('/inventory/analysis-options?dimension=brand',headers=headers).json
    assert 'QA-BRAND' in options['items']
    locations=client.get('/inventory/by-location?brand=QA-BRAND',headers=headers).json
    assert locations['summary']['quantity']==inventory['onhand_stock']
    for endpoint in ('/inventory/metrics','/inventory/expiry','/inventory/','/inventory/by-location'):
        empty=client.get(endpoint+'?brand=NOT-EXIST',headers=headers).json
        if endpoint.endswith('by-location'):
            assert empty['summary']['quantity']==0
        else:
            assert empty['total']==0
    csv_response=client.get('/inventory/metrics/export?brand=QA-BRAND',headers=headers)
    rows=list(csv.DictReader(io.StringIO(csv_response.data.decode('utf-8-sig'))))
    assert len(rows)==1 and rows[0]['goods_id']==str(gid)
    empty_series=client.get('/inventory/history/series?brand=NOT-EXIST',headers=headers).json
    assert all(point['total']==0 for point in empty_series['series'])


def test_analysis_zone_filters_only_matching_locations(client,access_token):
    with client.application.app_context():
        wid=get_warehouse().id
    headers=_h(access_token,wid)
    options=client.get('/inventory/analysis-options?dimension=zone',headers=headers).json
    assert options['items']
    zone=options['items'][0]
    response=client.get('/inventory/by-location',query_string={'zone':zone},headers=headers).json
    assert response['items'] and all(row['location']['code'].split('-')[0]==zone for row in response['items'])
    assert client.get('/inventory/by-location?zone=NOT-EXIST',headers=headers).json['summary']['location_count']==0
