"""新密码复杂度（system/user/password_policy.py，与 Web 修改密码页的规则一致）

8–16 位，含大写字母、小写字母、数字和特殊符号（@$!%*?&），只能由字母、数字和这些符号组成；不合规 → 400 10015。
所有「设新密码」的入口都查：忘记密码重置、修改密码、新建 / 修改平台用户、新建 / 修改员工。已有密码不受影响。
"""
import pytest

from tests.helpers import *  # noqa: F401,F403
from system.user.password_policy import PASSWORD_POLICY_CODE, is_valid_password

STRONG = 'Passw0rd!'
WEAK = 'password123'


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _assert_policy_error(response, field):
    body = response.get_json()
    assert (response.status_code, body.get('code')) == (400, PASSWORD_POLICY_CODE), body
    assert body['field'] == field
    assert body['details'] == {'min_length': 8, 'max_length': 16, 'special_characters': '@$!%*?&'}
    assert body['message'].isascii()


class _FakeRedis:
    """reset_password 用到的几个命令（本机没有 Redis / fakeredis）"""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, _ttl, value):
        self.data[key] = value if isinstance(value, bytes) else str(value).encode()

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def incr(self, key):
        value = int(self.data.get(key, b'0')) + 1
        self.data[key] = str(value).encode()
        return value

    def expire(self, _key, _ttl):
        return True

    def __getattr__(self, _name):
        # 吊销 token 等其它命令：照常 fail-open（与连不上 Redis 时一样）
        def _noop(*_args, **_kwargs):
            return None
        return _noop


@pytest.mark.parametrize('password, valid', [
    ('Passw0rd!', True),
    ('aB3@aB3@aB3@aB3@', True),            # 16 位
    ('Pa1!', False),                      # 太短
    ('Pa1!Pa1!Pa1!Pa1!x', False),         # 17 位
    ('password1!', False),                # 没有大写
    ('PASSWORD1!', False),                # 没有小写
    ('Password!!', False),                # 没有数字
    ('Password12', False),                # 没有特殊符号
    ('Passw0rd#', False),                 # # 不在允许的特殊符号里（同前端）
    ('Pass w0rd!', False),                # 空格
    ('Pässw0rd!', False),                 # 非 ASCII 字母
    ('Passw０rd!', False),                # 全角数字（Python 的 \d 会匹配，这里不行）
    ('Passw0rd!\n', False),               # 结尾换行
    (None, False),
    (12345678, False),
])
def test_password_rule_matches_frontend(password, valid):
    assert is_valid_password(password) is valid


def test_reset_password_requires_complex_password(client, monkeypatch):
    """忘记密码 → 验证码 → 设新密码：弱密码 400 10015，不消耗验证码的尝试次数；合规后用同一验证码重置成功"""
    from extensions.redis import redis_client
    from system.user.services import RESET_ATTEMPTS_PREFIX, RESET_CODE_PREFIX
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, '_redis_client', fake)
    email = 'admin@example.com'
    fake.setex(f'{RESET_CODE_PREFIX}{email}', 300, '123456')

    for weak in ('12345678', 'password', WEAK, 'Password1'):
        response = client.post('/user/reset-password', json={'email': email, 'code': '123456', 'new_password': weak})
        _assert_policy_error(response, 'new_password')
    assert fake.get(f'{RESET_ATTEMPTS_PREFIX}{email}') is None
    assert client.post('/user/login', json={'account': 'admin', 'password': 'password'}).status_code == 200

    response = client.post('/user/reset-password', json={'email': email, 'code': '123456', 'new_password': STRONG})
    assert response.status_code == 200, response.get_json()
    assert client.post('/user/login', json={'account': 'admin', 'password': STRONG}).status_code == 200


def test_change_password_requires_complex_password(client):
    login = client.post('/user/login', json={'account': 'operator', 'password': 'password'})
    token = login.get_json()['access_token']
    response = client.put('/user/change-password', headers=_h(token),
                          json={'old_password': 'password', 'new_password': WEAK})
    _assert_policy_error(response, 'new_password')
    # 旧密码错仍先报 10003
    response = client.put('/user/change-password', headers=_h(token),
                          json={'old_password': 'wrong', 'new_password': WEAK})
    assert response.get_json()['code'] == 10003
    # 没改成功：已有的（弱）密码照常能登录
    assert client.post('/user/login', json={'account': 'operator', 'password': 'password'}).status_code == 200


def test_admin_set_password_entries_require_complex_password(client, access_token):
    """平台用户 / 员工的新建与修改带 password 时都查；不带 password 的修改不受影响"""
    with client.application.app_context():
        company_id = get_company().id
        operator_id = User.query.filter_by(user_name='operator').first().id

    response = client.post('/user/users', headers=_h(access_token), json={
        'user_name': 'weak_user', 'email': 'weak_user@example.com', 'password': WEAK})
    _assert_policy_error(response, 'password')
    response = client.post('/staff/', headers=_h(access_token), json={
        'user_name': 'weak_staff', 'email': 'weak_staff@example.com', 'password': WEAK, 'company_id': company_id})
    _assert_policy_error(response, 'password')
    with client.application.app_context():
        assert User.query.filter(User.user_name.in_(['weak_user', 'weak_staff'])).count() == 0

    _assert_policy_error(client.put(f'/user/users/{operator_id}', headers=_h(access_token),
                                    json={'password': WEAK}), 'password')
    _assert_policy_error(client.put(f'/staff/{operator_id}', headers=_h(access_token),
                                    json={'password': 'short'}), 'password')
    assert client.post('/user/login', json={'account': 'operator', 'password': 'password'}).status_code == 200

    # 不带 password 的修改照常；合规的新密码生效
    response = client.put(f'/staff/{operator_id}', headers=_h(access_token), json={'position': 'Picker'})
    assert response.status_code == 200, response.get_json()
    response = client.put(f'/staff/{operator_id}', headers=_h(access_token), json={'password': STRONG})
    assert response.status_code == 200, response.get_json()
    assert client.post('/user/login', json={'account': 'operator', 'password': STRONG}).status_code == 200
    response = client.post('/user/users', headers=_h(access_token), json={
        'user_name': 'strong_user', 'email': 'strong_user@example.com', 'password': STRONG})
    assert response.status_code == 201, response.get_json()
