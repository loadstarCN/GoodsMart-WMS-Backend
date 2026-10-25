"""拣货完成 / 打包完成推 dn.picked / dn.packed（批发站显示仓库作业进度用）

- 走真实接口：开始处理 → 拣货完成 → 打包完成，依次推 dn.in_progress、dn.picked、dn.packed
- dn.picked 带各商品计划量 / 已拣量；dn.packed 另带已打包量（少打包时能看到差额）
- 各事件带这一步的实际时刻（started_at / picked_at / packed_at，带时区偏移），不是推送时刻
- 只推给创建 DN 的 API Key；WMS 内部建的 DN（没有 api_key_id）不推
"""
import uuid

from tests.helpers import *  # noqa: F401,F403
from tests.test_inventory_guards import _h, _inbound, _ok, _task_ids, _world
from system.webhook.models import WebhookEvent

from datetime import date, datetime     # helpers 的 import * 会把 datetime 换成模块，放在它后面


def _hook_key(client):
    with client.application.app_context():
        key = APIKey(key=hash_api_key(uuid.uuid4().hex), key_prefix='progress', system_name='progress-hook',
                     permissions=['dn_read'])
        key.company_id = get_company().id
        key.webhook_url = 'https://example.com/hook'
        key.is_active = True
        db.session.add(key)
        db.session.commit()
        return key.id


def _run_to_packed(client, token, w, api_key_id, pick, pack):
    """g1 入库 10 件 → DN 4 件 → 开始 → 拣 pick 件完成 → 打 pack 件完成，返回 dn_id"""
    g = w['g1']
    _inbound(client, token, w, [(g, 10)], [(g, w['std1'], 10)])
    dn_id = _ok(client.post('/dn/', json={
        'warehouse_id': w['wh'], 'recipient_id': w['recipient'], 'shipping_address': 'x',
        'expected_shipping_date': warehouse_today().isoformat(), 'order_number': 'SHP-PROGRESS-1',
        'details': [{'goods_id': g, 'quantity': 4}],
    }, headers=_h(token)))['id']
    if api_key_id:
        with client.application.app_context():
            db.session.get(DN, dn_id).api_key_id = api_key_id
            db.session.commit()

    _ok(client.put(f'/dn/{dn_id}/progress/', headers=_h(token)))
    picking_id = _task_ids(client, PickingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/picking/{picking_id}/process/', headers=_h(token)))
    _ok(client.post(f'/picking/{picking_id}/batches/', json={'details': [
        {'goods_id': g, 'location_id': w['std1'], 'picked_quantity': pick}]}, headers=_h(token)))
    _ok(client.put(f'/picking/{picking_id}/complete/', headers=_h(token)))

    packing_id = _task_ids(client, PackingTask, dn_id=dn_id)[0]
    _ok(client.put(f'/packing/{packing_id}/process/', headers=_h(token)))
    _ok(client.post(f'/packing/{packing_id}/batches/', json={'details': [
        {'goods_id': g, 'packed_quantity': pack}]}, headers=_h(token)))
    _ok(client.put(f'/packing/{packing_id}/complete/', headers=_h(token)))
    return dn_id


def test_picked_and_packed_emit_progress_events(client, access_token):
    """少打包（拣 4 打 3）：三个事件按顺序落库，dn.packed 的 packed_quantity = 3"""
    w = _world(client)
    key_id = _hook_key(client)
    dn_id = _run_to_packed(client, access_token, w, key_id, pick=4, pack=3)

    with client.application.app_context():
        events = WebhookEvent.query.filter_by(api_key_id=key_id).order_by(WebhookEvent.id).all()
        assert [e.event_type for e in events] == ['dn.in_progress', 'dn.picked', 'dn.packed']
        started, picked, packed = (e.payload for e in events)
        dn = db.session.get(DN, dn_id)
        # 各步的实际时刻（带时区偏移），与 DN 上记的同一刻
        times = {'started_at': dn.started_at, 'picked_at': dn.picked_at, 'packed_at': dn.packed_at}

    assert started == {'dn_id': dn_id, 'status': 'in_progress', 'order_number': 'SHP-PROGRESS-1',
                       'started_at': started['started_at']}
    assert picked == {
        'dn_id': dn_id, 'status': 'picked', 'order_number': 'SHP-PROGRESS-1', 'picked_at': picked['picked_at'],
        'details': [{'goods_code': 'GRD-1', 'planned_quantity': 4, 'picked_quantity': 4}],
    }
    assert packed == {
        'dn_id': dn_id, 'status': 'packed', 'order_number': 'SHP-PROGRESS-1', 'packed_at': packed['packed_at'],
        'details': [{'goods_code': 'GRD-1', 'planned_quantity': 4, 'picked_quantity': 4, 'packed_quantity': 3}],
    }
    for payload, key in ((started, 'started_at'), (picked, 'picked_at'), (packed, 'packed_at')):
        moment = datetime.fromisoformat(payload[key])
        assert moment.tzinfo is not None
        assert moment.replace(microsecond=0) == times[key].astimezone().replace(microsecond=0)


def test_internal_dn_emits_nothing(client, access_token):
    """WMS 内部建的 DN 没有 api_key_id：照常流转，不产生 webhook 事件"""
    w = _world(client)
    _run_to_packed(client, access_token, w, None, pick=4, pack=4)
    with client.application.app_context():
        assert WebhookEvent.query.filter(WebhookEvent.event_type.in_(('dn.picked', 'dn.packed'))).count() == 0
