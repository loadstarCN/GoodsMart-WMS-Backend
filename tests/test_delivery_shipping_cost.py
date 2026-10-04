"""发货任务运费（shipping_cost）的输入契约

Web / APP 的运费输入框提交的是字符串；后端接受数字与数字字符串，
空串 / null 在完成发货时视为不改已存值；非数字、负数、NaN / Infinity → 400 16081。
"""
import pytest

from .helpers import *
from .test_customs import _create_dn, _force_packed, _h, _prepare, _ship


def _packed_task(client, token, order_number):
    _prepare(client)
    dn_id = _create_dn(client, token, customs=None, order_number=order_number).get_json()['id']
    return _force_packed(client, dn_id)


def _stored_cost(client, task_id):
    with client.application.app_context():
        return db.session.get(DeliveryTask, task_id).shipping_cost


@pytest.mark.parametrize('value, expected', [('1500', 1500.0), (' 12.5 ', 12.5), (800, 800.0), (0, 0.0)])
def test_complete_accepts_number_and_numeric_string(client, access_token, value, expected):
    task_id = _packed_task(client, access_token, f'ORD-COST-{value!s}'.strip())
    response = _ship(client, access_token, task_id, shipping_cost=value)
    assert response.status_code == 200, response.get_json()
    assert _stored_cost(client, task_id) == expected


@pytest.mark.parametrize('value', ['', '   ', None])
def test_complete_with_empty_cost_keeps_saved_value(client, access_token, value):
    task_id = _packed_task(client, access_token, 'ORD-COST-KEEP')
    response = client.put(f'/delivery/{task_id}', headers=_h(access_token), json={'shipping_cost': '800'})
    assert response.status_code == 200, response.get_json()
    assert _stored_cost(client, task_id) == 800.0

    response = _ship(client, access_token, task_id, shipping_cost=value)
    assert response.status_code == 200, response.get_json()
    assert _stored_cost(client, task_id) == 800.0


@pytest.mark.parametrize('value', ['abc', '-1', -1, 'NaN', 'inf', True, [1]])
def test_complete_rejects_invalid_cost(client, access_token, value):
    task_id = _packed_task(client, access_token, 'ORD-COST-BAD')
    response = _ship(client, access_token, task_id, shipping_cost=value)
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 16081
