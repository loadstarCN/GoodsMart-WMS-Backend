"""手填运单号的 manual 运单记录（doc/shipping-flow.md §4，warehouse/shipping/waybills.py）

- 保存运单号（PUT /delivery/<id>/tracking）、修改发货任务、完成发货时带运单号 → 同时写 source = manual 的运单记录
  （carrier_id = 任务的承运商，carrier = 该承运商在线对接的插件，created_by = 操作人）
- 换号 → 旧记录 cancelled（replaced）+ 新号 active；清空 → cancelled（cleared）；写法不同的同一个号码只更新写法
- 锁定规则（16078 / 16079、换号先取消）只对插件运单（source = api）生效；有手填运单号时插件建单照旧拦
  （TRACKING_NUMBER_EXISTS），清空后可以建单
- DN 取消不被手填运单拦（16110 只看插件运单），手填运单一并作废（dn_cancelled）；删掉带运单号的发货任务 → cleared
"""
from .helpers import *
from .test_customs import _create_dn, _force_packed, _h, _prepare, _ship
from .test_carrier_shipment import _create, _ready, fedex  # noqa: F401  fedex 是 fixture

from warehouse.dn.models import DNCarrierShipment


def _domestic(client, token, order_number='ORD-MW-1'):
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).carrier_id = get_carrier().id
        db.session.commit()
    return dn_id, task_id


def _save(client, token, task_id, body):
    return client.put(f'/delivery/{task_id}/tracking', json=body, headers=_h(token))


def _rows(client, dn_id):
    with client.application.app_context():
        return [{'id': r.id, 'source': r.source, 'status': r.status, 'reason': r.reason, 'carrier': r.carrier,
                 'carrier_id': r.carrier_id, 'tracking_number': r.tracking_number, 'created_by': r.created_by,
                 'cancelled_by': r.cancelled_by}
                for r in DNCarrierShipment.query.filter_by(dn_id=dn_id).order_by(DNCarrierShipment.id)]


def _admin_id(client):
    with client.application.app_context():
        return get_admin_user().id


def test_save_change_and_clear_tracking_number(client, access_token):
    dn_id, task_id = _domestic(client, access_token)
    admin_id = _admin_id(client)
    with client.application.app_context():
        carrier_id = get_carrier().id

    response = _save(client, access_token, task_id, {'tracking_number': 'MAN-0001'})
    assert response.status_code == 200, response.get_json()
    rows = _rows(client, dn_id)
    assert rows == [{'id': rows[0]['id'], 'source': 'manual', 'status': 'active', 'reason': None, 'carrier': None,
                     'carrier_id': carrier_id, 'tracking_number': 'MAN-0001', 'created_by': admin_id,
                     'cancelled_by': None}]

    # 同一个号码换了写法：不换记录，只跟上写法
    assert _save(client, access_token, task_id, {'tracking_number': 'MAN0001'}).status_code == 200
    rows = _rows(client, dn_id)
    assert len(rows) == 1 and rows[0]['tracking_number'] == 'MAN0001' and rows[0]['status'] == 'active'

    # 换号：旧记录 replaced，新号 active（手填运单不受「换号先取消」约束）
    assert _save(client, access_token, task_id, {'tracking_number': 'MAN-0002'}).status_code == 200
    rows = _rows(client, dn_id)
    assert [(r['status'], r['reason'], r['tracking_number']) for r in rows] == [
        ('cancelled', 'replaced', 'MAN0001'), ('active', None, 'MAN-0002')]
    assert rows[0]['cancelled_by'] == admin_id

    # 换回以前用过的号码也可以（已取消的手填号码不拦，16078 只看插件运单）
    assert _save(client, access_token, task_id, {'tracking_number': 'MAN0001'}).status_code == 200
    assert _rows(client, dn_id)[-1]['tracking_number'] == 'MAN0001'

    # 清空：当前记录 cleared，没有有效运单
    assert _save(client, access_token, task_id, {'tracking_number': ''}).status_code == 200
    rows = _rows(client, dn_id)
    assert rows[-1]['status'] == 'cancelled' and rows[-1]['reason'] == 'cleared'
    assert not [r for r in rows if r['status'] == 'active']


def test_update_task_and_carrier_change_replace(client, access_token):
    dn_id, task_id = _domestic(client, access_token, 'ORD-MW-2')
    with client.application.app_context():
        base = get_carrier()
        other = Carrier(name='Other Express', code='other', company_id=base.company_id, created_by=base.created_by)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    response = client.put(f'/delivery/{task_id}', json={'tracking_number': 'UPD-1'}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert [(r['status'], r['tracking_number']) for r in _rows(client, dn_id)] == [('active', 'UPD-1')]

    # 同一个号码换承运商：换记录（揽收 / 跟踪按承运商算）
    assert _save(client, access_token, task_id, {'carrier_id': other_id}).status_code == 200
    rows = _rows(client, dn_id)
    assert [(r['status'], r['reason'], r['carrier_id']) for r in rows] == [
        ('cancelled', 'replaced', rows[0]['carrier_id']), ('active', None, other_id)]


def test_complete_with_tracking_number_records_manual_waybill(client, access_token):
    dn_id, task_id = _domestic(client, access_token, 'ORD-MW-3')
    response = _ship(client, access_token, task_id, tracking_number='DOM-777')
    assert response.status_code == 200, response.get_json()
    rows = _rows(client, dn_id)
    assert [(r['source'], r['status'], r['tracking_number']) for r in rows] == [('manual', 'active', 'DOM-777')]
    with client.application.app_context():
        assert db.session.get(DN, dn_id).status == 'delivered'


def test_manual_number_and_api_shipment(client, access_token, fedex):
    dn_id, task_id = _ready(client, access_token)          # 海外 DN，发货任务的承运商有 FedEx 对接
    assert _save(client, access_token, task_id, {'tracking_number': '794699999999'}).status_code == 200
    rows = _rows(client, dn_id)
    assert [(r['source'], r['carrier'], r['status']) for r in rows] == [('manual', 'fedex', 'active')]

    # 有手填运单号时插件建单照旧拦；清空后可以建
    response = _create(client, access_token, dn_id)
    assert response.status_code == 409
    assert 'TRACKING_NUMBER_EXISTS' in [b['code'] for b in response.get_json()['details']['blockers']]
    assert fedex.of('/ship/v1/shipments') == []
    assert _save(client, access_token, task_id, {'tracking_number': None}).status_code == 200
    response = _create(client, access_token, dn_id)
    assert response.status_code == 201, response.get_json()
    rows = _rows(client, dn_id)
    assert [(r['source'], r['status'], r['reason']) for r in rows] == [
        ('manual', 'cancelled', 'cleared'), ('api', 'active', None)]

    # 插件运单的锁定规则照旧：换号 → 16078，且不另写 manual 记录
    response = _save(client, access_token, task_id, {'tracking_number': 'OTHER-1'})
    assert response.status_code == 409 and response.get_json()['code'] == 16078
    assert len(_rows(client, dn_id)) == 2


def test_cancel_dn_closes_manual_waybill(client, access_token):
    dn_id, task_id = _domestic(client, access_token, 'ORD-MW-4')
    assert _save(client, access_token, task_id, {'tracking_number': 'CXL-1'}).status_code == 200
    response = client.put(f'/dn/{dn_id}/cancel/', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()            # 手填运单不拦取消（16110 只看插件运单）
    rows = _rows(client, dn_id)
    assert [(r['status'], r['reason']) for r in rows] == [('cancelled', 'dn_cancelled')]


def test_delete_task_clears_manual_waybill(client, access_token):
    dn_id, task_id = _domestic(client, access_token, 'ORD-MW-5')
    assert _save(client, access_token, task_id, {'tracking_number': 'DEL-1'}).status_code == 200
    response = client.delete(f'/delivery/{task_id}', headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert [(r['status'], r['reason']) for r in _rows(client, dn_id)] == [('cancelled', 'cleared')]


def test_one_open_waybill_per_dn(client, access_token):
    """部分唯一索引不分来源：同一 DN 只有一条未结束的运单记录"""
    dn_id, task_id = _domestic(client, access_token, 'ORD-MW-6')
    for number in ('A-1', 'A-2', 'A-3'):
        assert _save(client, access_token, task_id, {'tracking_number': number}).status_code == 200
    rows = _rows(client, dn_id)
    assert [r['status'] for r in rows] == ['cancelled', 'cancelled', 'active']
    with client.application.app_context():
        db.session.add(DNCarrierShipment(dn_id=dn_id, source='manual', status='active', tracking_number='DUP'))
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()
