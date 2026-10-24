"""自动视为签收（规则 B，carriers.auto_sign_days，warehouse/shipping/auto_sign.py）

- DN delivered、当前发货任务 completed、承运商设了 auto_sign_days、有效运单没有经 API 跟踪、完成时刻 + N 天 ≤ 现在
  → 签收：signed_at = 完成时刻 + N 天，signed_source = rule，操作人 = 运单建单人（没有用 DN 创建人），推 dn.completed
- 没到期 / 没设天数 / 经 API 跟踪的不签；一张失败不影响别的
"""
from .helpers import *
from .test_customs import _create_dn, _force_packed, _prepare, _ship, _webhook_key
from .test_carrier_shipment import fedex  # noqa: F401  fedex 是 fixture
from .test_carrier_tracking import _shipped_ready

from datetime import datetime, timedelta      # helpers 的 import * 会把 datetime 换成模块，放在它后面

from system.webhook.models import WebhookEvent
from warehouse.shipping import auto_sign
from warehouse.shipping.auto_sign import AutoSignService


def _shipped(client, token, order_number, days_ago=10, tracking='MAN-1', key=True):
    """国内 DN：手填运单号后完成发货，完成时刻挪到 days_ago 天前 → (dn_id, task_id, key_id)"""
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    task_id = _force_packed(client, dn_id)
    key_id = _webhook_key(client) if key else None
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        db.session.get(DeliveryTask, task_id).carrier_id = get_carrier().id
        db.session.commit()
    response = _ship(client, token, task_id, tracking_number=tracking)
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        task.completed_at = datetime.now().replace(microsecond=0) - timedelta(days=days_ago)
        db.session.commit()
    return dn_id, task_id, key_id


def _set_days(client, days):
    with client.application.app_context():
        get_carrier().auto_sign_days = days
        db.session.commit()


def _sync(client, **kwargs):
    with client.application.app_context():
        return AutoSignService.sync(**kwargs)


def test_rule_signs_after_n_days(client, access_token):
    dn_id, task_id, key_id = _shipped(client, access_token, 'ORD-AS-1', days_ago=10)
    _set_days(client, 7)
    with client.application.app_context():
        completed_at = db.session.get(DeliveryTask, task_id).completed_at
        creator = db.session.get(DN, dn_id).created_by
    assert _sync(client) == {'signed': 1, 'skipped': 0, 'errors': 0}
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        assert (task.status, task.signed_source) == ('signed', 'rule')
        assert task.signed_at == completed_at + timedelta(days=7)
        dn = db.session.get(DN, dn_id)
        assert dn.status == 'completed' and dn.completed_at == task.signed_at
        from warehouse.delivery.models import DeliveryTaskStatusLog
        log = DeliveryTaskStatusLog.query.filter_by(task_id=task_id, new_status='signed').one()
        assert log.operator_id == creator                  # 手填运单的建单人（完成发货的操作员）
        payload = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.completed').one().payload
    assert payload['signed_source'] == 'rule'
    assert datetime.fromisoformat(payload['signed_at']) == (completed_at + timedelta(days=7)).astimezone()
    assert _sync(client)['signed'] == 0                     # 签过的不再签


def test_not_due_or_disabled_is_not_signed(client, access_token):
    _dn_id, task_id, _key = _shipped(client, access_token, 'ORD-AS-2', days_ago=3)
    assert _sync(client)['signed'] == 0                     # 没设天数
    _set_days(client, 7)
    assert _sync(client)['signed'] == 0                     # 还没到期
    assert _sync(client, now=datetime.now() + timedelta(days=5))['signed'] == 1
    with client.application.app_context():
        assert db.session.get(DeliveryTask, task_id).signed_source == 'rule'


def test_api_tracked_shipment_is_left_to_tracking(client, access_token, fedex):
    dn_id, task_id, _key = _shipped_ready(client, access_token, fedex)
    with client.application.app_context():
        get_carrier().auto_sign_days = 1
        db.session.commit()
    assert _ship(client, access_token, task_id).status_code == 200
    with client.application.app_context():
        db.session.get(DeliveryTask, task_id).completed_at = datetime.now() - timedelta(days=5)
        db.session.commit()
    assert _sync(client)['signed'] == 0
    # 跟踪方式改成只给链接：没有 API 跟踪了，规则接手
    with client.application.app_context():
        get_carrier().tracking_method = 'link'
        db.session.commit()
    assert _sync(client)['signed'] == 1


def test_one_failure_does_not_stop_others(client, access_token, monkeypatch):
    first, first_task, _k1 = _shipped(client, access_token, 'ORD-AS-3', days_ago=10, key=False)
    second, second_task, _k2 = _shipped(client, access_token, 'ORD-AS-4', days_ago=9, tracking='MAN-2', key=False)
    _set_days(client, 2)
    original = auto_sign.AutoSignService._sign

    def flaky(dn_id, task_id, now):
        if dn_id == first:
            raise RuntimeError('boom')
        return original(dn_id, task_id, now)

    monkeypatch.setattr(auto_sign.AutoSignService, '_sign', staticmethod(flaky))
    assert _sync(client) == {'signed': 1, 'skipped': 0, 'errors': 1}
    with client.application.app_context():
        assert db.session.get(DeliveryTask, first_task).status == 'completed'
        assert db.session.get(DeliveryTask, second_task).status == 'signed'


def test_scheduler_registers_job(monkeypatch):
    import scheduler
    from flask import Flask
    app = Flask(__name__)
    monkeypatch.setattr(scheduler.scheduler, 'init_app', lambda app: None)
    monkeypatch.setattr(scheduler, '_try_start', lambda app: None)
    monkeypatch.setenv('CARRIER_AUTO_SIGN_INTERVAL_MINUTES', '15')
    scheduler.init_scheduler(app)
    job = next(j for j in app.config['JOBS'] if j['id'] == 'carrier_auto_sign')
    assert job['minutes'] == 15 and job['func'] == 'scheduler:_job_carrier_auto_sign'
    monkeypatch.setenv('CARRIER_AUTO_SIGN_INTERVAL_MINUTES', '0')
    app = Flask(__name__)
    scheduler.init_scheduler(app)
    assert 'carrier_auto_sign' not in [j['id'] for j in app.config['JOBS']]
