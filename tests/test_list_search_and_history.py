"""列表 / 历史的几处输出与筛选

- 关键字按单号搜索兼容界面显示的前缀（ASN-12、SORT-3、DN-163、PICK- / PACK- / DEL-、CC-、ADJ-）
- 调整单明细（商品 / 库位详情页的调整历史）带所属调整单概要、审批人、操作人；调整单详情的仓库是仓库（有名字）
- 盘点列表按计划时间筛选（scheduled_start_date / scheduled_end_date / scheduled_date）
"""
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone

import pytest

from tests.helpers import *  # noqa: F401,F403
from warehouse.common.search import parse_document_number


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _ids(client, token, url, keyword):
    response = client.get(f'{url}?per_page=100&keyword={keyword}', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return sorted(item['id'] for item in response.get_json()['items'])


@pytest.mark.parametrize('keyword, parsed', [
    ('12', (None, 12)),
    (' ASN-12 ', ('ASN', 12)),
    ('asn12', ('ASN', 12)),
    ('DN #163', ('DN', 163)),
    ('sort_3', ('SORT', 3)),
    ('PICK-0005', ('PICK', 5)),
    ('ASN-', None),
    ('ASN-12a', None),
    ('4901772434184', None),          # 13 位商品编码：超出编号范围，不当单号
    ('', None),
    (None, None),
])
def test_parse_document_number(keyword, parsed):
    assert parse_document_number(keyword) == parsed


def test_keyword_search_accepts_displayed_prefixes(client, access_token):
    with client.application.app_context():
        admin = get_admin_user()
        warehouse = get_warehouse()
        supplier = Supplier.query.filter_by(company_id=warehouse.company_id).first()
        # 多建几张入库单，让编号对不上的也有
        extra = [ASN(warehouse_id=warehouse.id, supplier_id=supplier.id, asn_type='inbound', status='pending',
                     created_by=admin.id) for _ in range(3)]
        db.session.add_all(extra)
        db.session.commit()
        asn_id = extra[-1].id
        sorting = SortingTask.query.first()
        picking, packing, delivery = PickingTask.query.first(), PackingTask.query.first(), DeliveryTask.query.first()
        cyclecount, adjustment = CycleCountTask.query.first(), Adjustment.query.first()
        ids = {
            'sorting': (sorting.id, sorting.asn_id), 'picking': (picking.id, picking.dn_id),
            'packing': (packing.id, packing.dn_id), 'delivery': (delivery.id, delivery.dn_id),
            'cyclecount': cyclecount.id, 'adjustment': adjustment.id,
        }
        dn_id = DN.query.order_by(DN.id.desc()).first().id

    assert _ids(client, access_token, '/asn/', f'ASN-{asn_id}') == [asn_id]
    assert _ids(client, access_token, '/asn/', f'asn{asn_id}') == [asn_id]
    assert _ids(client, access_token, '/asn/', str(asn_id)) == [asn_id]
    assert _ids(client, access_token, '/asn/', f'DN-{asn_id}') == []          # 别的单据的前缀不按编号找

    task_id, task_asn_id = ids['sorting']
    assert task_id in _ids(client, access_token, '/sorting/', f'SORT-{task_id}')
    assert task_id in _ids(client, access_token, '/sorting/', f'ASN-{task_asn_id}')

    assert _ids(client, access_token, '/dn/', f'DN-{dn_id}') == [dn_id]
    for url, key, prefix in (('/picking/', 'picking', 'PICK'), ('/packing/', 'packing', 'PACK'),
                             ('/delivery/', 'delivery', 'DEL')):
        own_id, own_dn_id = ids[key]
        assert own_id in _ids(client, access_token, url, f'{prefix}-{own_id}'), url
        assert own_id in _ids(client, access_token, url, f'DN-{own_dn_id}'), url
        assert own_id in _ids(client, access_token, url, str(own_id)), url

    assert _ids(client, access_token, '/cyclecount/', f"CC-{ids['cyclecount']}") == [ids['cyclecount']]
    assert _ids(client, access_token, '/adjustment/', f"ADJ-{ids['adjustment']}") == [ids['adjustment']]
    assert _ids(client, access_token, '/adjustment/', f"adj {ids['adjustment']}") == [ids['adjustment']]


def test_adjustment_history_rows_carry_adjustment_approver_and_operator(client, access_token):
    with client.application.app_context():
        adjustment = Adjustment.query.first()
        admin = get_admin_user()
        operator = User.query.filter_by(user_name='operator').first()
        adjustment.adjustment_reason = 'Damaged in storage'
        adjustment.approved_by, adjustment.operator_id = admin.id, operator.id
        adjustment.approved_at = _datetime(2026, 10, 20, 1, 2, 3, tzinfo=_timezone.utc)
        adjustment.completed_at = _datetime(2026, 10, 21, 4, 5, 6, tzinfo=_timezone.utc)
        db.session.commit()
        adjustment_id = adjustment.id
        detail = AdjustmentDetail.query.filter_by(adjustment_id=adjustment_id).first()
        goods_id, location_id = detail.goods_id, detail.location_id
        warehouse_name = adjustment.warehouse.name

    for query in (f'goods_id={goods_id}', f'location_id={location_id}'):
        items = client.get(f'/adjustment/details/search/?{query}', headers=_h(access_token)).get_json()['items']
        row = next(item for item in items if item['adjustment_id'] == adjustment_id)
        assert row['adjustment']['adjustment_reason'] == 'Damaged in storage'
        assert row['adjustment']['approved_at'].startswith('2026-10-20T01:02:03')
        assert row['adjustment']['completed_at'].startswith('2026-10-21T04:05:06')
        assert row['approver']['user_name'] == 'admin'
        assert row['operator']['user_name'] == 'operator'

    body = client.get(f'/adjustment/{adjustment_id}', headers=_h(access_token)).get_json()
    assert body['warehouse']['name'] == warehouse_name and body['warehouse']['id'] == body['warehouse_id']
    assert body['details'][0]['adjustment']['id'] == adjustment_id


def test_cycle_count_list_filters_by_scheduled_date(client, access_token):
    with client.application.app_context():
        admin = get_admin_user()
        warehouse = get_warehouse()
        early = CycleCountTask(task_name='early', warehouse_id=warehouse.id, status='pending', is_active=True,
                               created_by=admin.id, scheduled_date=_datetime(2026, 11, 2, 3, 0, tzinfo=_timezone.utc))
        late = CycleCountTask(task_name='late', warehouse_id=warehouse.id, status='pending', is_active=True,
                              created_by=admin.id, scheduled_date=_datetime(2026, 11, 9, 3, 0, tzinfo=_timezone.utc))
        db.session.add_all([early, late])
        db.session.commit()
        early_id, late_id = early.id, late.id

    def names(query):
        response = client.get(f'/cyclecount/?per_page=100&{query}', headers=_h(access_token))
        assert response.status_code == 200, response.get_json()
        return sorted(item['id'] for item in response.get_json()['items'] if item['id'] in (early_id, late_id))

    assert names('') == [early_id, late_id]
    assert names('scheduled_start_date=2026-11-05') == [late_id]
    assert names('scheduled_end_date=2026-11-05') == [early_id]
    # 结束日只给日期：含那一整天
    assert names('scheduled_start_date=2026-11-02&scheduled_end_date=2026-11-02') == [early_id]
    assert names('scheduled_start_date=2026-11-09T00:00:00%2B00:00') == [late_id]
    assert names('scheduled_date=2026-11-09') == [late_id]
    assert client.get('/cyclecount/?scheduled_start_date=nope', headers=_h(access_token)).status_code == 400


def test_dn_search_by_current_tracking_number(client, access_token):
    """DN 列表关键字按当前运单号匹配（有效 / 取消中的运单，没有用当前发货任务上的；去空白 / 连字符，6 位以上可搜一段）"""
    from sqlalchemy.dialects import postgresql
    from warehouse.dn.models import DNCarrierShipment
    from warehouse.dn.services import DNService
    with client.application.app_context():
        task = DeliveryTask.query.filter_by(tracking_number='DT-TEST-001').one()
        task.tracking_number = 'AB 7946-0000-01'
        db.session.commit()
        dn_id = task.dn_id

    def found(keyword):
        response = client.get(f'/dn/?per_page=100&keyword={keyword}', headers=_h(access_token))
        assert response.status_code == 200, response.get_json()
        return dn_id in [item['id'] for item in response.get_json()['items']]

    assert found('AB 7946-0000-01') and found('ab794600000') and found('7946000001')
    assert found('946000')                     # 6 位以上：一段也行
    assert not found('AB79')                   # 太短：只按完整号码比
    assert not found('ZZ794600')

    # 有了有效运单就按运单的号码（发货任务上的旧号码不再算）
    with client.application.app_context():
        db.session.add(DNCarrierShipment(dn_id=dn_id, source='manual', status='active', tracking_number='ZX-999 888 777'))
        db.session.commit()
    assert found('ZX999888777') and found('999888')
    assert not found('AB794600000')
    with client.application.app_context():
        record = DNCarrierShipment.query.filter_by(dn_id=dn_id, status='active').one()
        record.status = 'cancelled'
        db.session.commit()
    assert found('AB794600000') and not found('ZX999888777')

    with client.application.app_context():
        sql = str(DNService.list_dns({'keyword': '7946-0000'}).statement.compile(dialect=postgresql.dialect()))
    assert 'replace(' in sql.lower()


def test_picking_and_packing_search_by_dn_goods_before_anything_is_handled(client, access_token):
    """拣货 / 打包列表按商品编码搜：按所属 DN 的商品明细匹配，还没拣 / 还没打包的任务也搜得到"""
    from tests.test_inventory_guards import _inbound, _ok as _ok_g, _picked_dn, _task_ids, _world
    w = _world(client)
    g = w['g1']
    _inbound(client, access_token, w, [(g, 10)], [(g, w['std1'], 10)])
    _dn_id, packing_id = _picked_dn(client, access_token, w, [(g, 2)], [(g, w['std1'], 2)])   # 打包还没录
    dn2 = _ok_g(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': warehouse_today().isoformat(), 'details': [{'goods_id': g, 'quantity': 1}],
    }, headers=_h(access_token)))['id']
    _ok_g(client.put(f'/dn/{dn2}/progress/', headers=_h(access_token)))
    pending_picking = _task_ids(client, PickingTask, dn_id=dn2)[0]          # 还没拣

    assert pending_picking in _ids(client, access_token, '/picking/', 'GRD-1')
    assert packing_id in _ids(client, access_token, '/packing/', 'GRD-1')
    assert pending_picking not in _ids(client, access_token, '/picking/', 'GRD-2')
    assert packing_id not in _ids(client, access_token, '/packing/', 'GRD-2')
