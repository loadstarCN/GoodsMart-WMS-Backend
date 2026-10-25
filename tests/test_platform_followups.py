"""平台 / webhook / 配置 / 报关单证的 🔵 后续项（2026-10-04 审计）

- B-90  goods.spec_updated 另带单调递增的 changed_at_ms（changed_at 格式不变）
- B-93  SSRF：is_global（拒绝 100.64.0.0/10、含 100.100.100.200）；严格模式建连时解析并校验全部地址、
        只连校验过的 IP（DNS rebinding），TLS 仍按原域名校验；B-59 的子线程 / 64KB / 总时限不变
- B-94  布尔开关统一解析；生产必填 SETTINGS_ENCRYPTION_KEY、WEBHOOK_URL_STRICT 默认开；没设 FLASK_ENV 只告警
- B-95  测试依赖移到 requirements-dev.txt
- B-96  CI 的 Shipper 国家与完整性检查同一来源（出口国）
- B-97  承运商名含非拉丁字符：以前印在 CI 上要拦（CARRIER_NON_LATIN_TEXT），现在单证不印承运商，不再拦
- B-98  非 JPY 币种：JP_EXPORT_VALUE_UNVERIFIED（warning，请人工确认是否超过 20 万円）
- B-99  收件人超长导致 reportlab LayoutError → 409 16117（不再 500）
- B-100 department 4 个端点在未绑定用户的 API Key 下不再 500
- B-106 SMTP 测试接口不回显异常原文
- B-108 list_goods_locations 稳定排序
"""
from .helpers import *  # noqa: F401,F403  先导入：helpers 里的 `import datetime` 会覆盖同名对象

import importlib.util
import ipaddress
import logging
import os
import re
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import requests
from flask import Blueprint
from flask_restx import Api
from urllib3.exceptions import NewConnectionError

from system.webhook import services as webhook_services
from system.webhook import utils as webhook_utils
from system.webhook.models import WebhookEvent
from warehouse.dn.models import DNDocument
from .test_customs import _customs, _export_dn_ready, _h, _issue

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ===========================================================================
# B-90  goods.spec_updated 的排序字段
# ===========================================================================

def _spec_key(company_id):
    key = APIKey(key=hash_api_key(str(uuid.uuid4())), system_name=f'spec-{uuid.uuid4().hex[:6]}',
                 permissions=['all_access'])
    key.company_id = company_id
    key.webhook_url = 'http://127.0.0.1:9/webhook'
    key.is_active = True
    key.webhook_subscriptions = ['goods.spec_updated']
    db.session.add(key)
    db.session.commit()
    return key.id


def test_spec_updated_payload_has_monotonic_changed_at_ms(client, access_token):
    with client.application.app_context():
        company_id = get_company().id
        key_id = _spec_key(company_id)
        goods_id = Goods.query.filter_by(code='G001').first().id
    auth = {'Authorization': f'Bearer {access_token}'}

    assert client.put(f'/goods/{goods_id}', headers=auth, json={'weight': 1.5}).status_code == 200
    with client.application.app_context():
        first = WebhookEvent.query.filter_by(api_key_id=key_id).one()
        first_payload = dict(first.payload)
        # 模拟「已推送失败过」：下一次变更另建新事件（而不是覆盖），两条都在，便于比较
        first.attempts = 1
        db.session.commit()
    assert client.put(f'/goods/{goods_id}', headers=auth, json={'weight': 2.5}).status_code == 200

    with client.application.app_context():
        events = WebhookEvent.query.filter_by(api_key_id=key_id).order_by(WebhookEvent.id).all()
        assert len(events) == 2
        second_payload = events[1].payload

    for payload in (first_payload, second_payload):
        # changed_at 格式不变：秒精度 + 时区偏移（Wholesale 在解析）
        assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d', payload['changed_at'])
        assert isinstance(payload['changed_at_ms'], int)
        # 是 epoch 毫秒（与 changed_at 同一时刻附近）
        changed_at = datetime.fromisoformat(payload['changed_at']).timestamp()
        assert abs(payload['changed_at_ms'] / 1000 - changed_at) < 5
    assert second_payload['changed_at_ms'] > first_payload['changed_at_ms']
    assert second_payload['goods_weight_kg'] == 2.5


def test_changed_at_ms_strictly_increasing_even_if_clock_goes_back():
    from warehouse.goods import services as goods_services

    values = [goods_services._next_spec_order_ms() for _ in range(500)]
    assert all(b > a for a, b in zip(values, values[1:]))

    last = values[-1]
    with mock.patch.object(goods_services.time, 'time_ns', return_value=(last - 10_000) * 1_000_000):
        backwards = [goods_services._next_spec_order_ms() for _ in range(3)]
    assert backwards == [last + 1, last + 2, last + 3]


# ===========================================================================
# B-93  SSRF：is_global + 建连时固定已校验地址
# ===========================================================================

@pytest.mark.parametrize('address, public', [
    ('100.100.100.200', False),      # 阿里云元数据
    ('100.64.0.1', False),           # 共享地址 100.64.0.0/10
    ('100.127.255.254', False),
    ('10.0.0.1', False), ('172.16.0.1', False), ('192.168.1.1', False),
    ('127.0.0.1', False), ('169.254.169.254', False), ('0.0.0.0', False),
    ('224.0.0.1', False), ('240.0.0.1', False), ('198.18.0.1', False),
    ('::1', False), ('fe80::1', False), ('fc00::1', False),
    ('::ffff:127.0.0.1', False), ('::ffff:100.100.100.200', False),
    ('2002:7f00:1::', False),        # 6to4 内嵌 127.0.0.1
    ('not-an-ip', False),
    ('8.8.8.8', True), ('100.128.0.1', True), ('2606:4700::1111', True),
])
def test_is_public_ip_uses_is_global(address, public):
    assert webhook_utils._is_public_ip(address) is public


def _addrinfo(*ips, port=443):
    out = []
    for ip in ips:
        family = socket.AF_INET6 if ipaddress.ip_address(ip).version == 6 else socket.AF_INET
        sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr))
    return out


class _Resolver:
    """只接管指定主机名的 getaddrinfo，其余（IP 字面量等）交给真实实现；answers 按调用次数依次返回"""

    def __init__(self, host, *answers):
        self.host = host
        self.answers = list(answers)
        self.calls = 0
        self._real = socket.getaddrinfo

    def __call__(self, host, port, *args, **kwargs):
        if host != self.host:
            return self._real(host, port, *args, **kwargs)
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return _addrinfo(*answer, port=port)


def test_validate_webhook_url_rejects_metadata_address(client):
    with client.application.app_context():
        with mock.patch('socket.getaddrinfo', _Resolver('hook.example', ['100.100.100.200'])):
            with pytest.raises(Exception) as exc:
                webhook_utils.validate_webhook_url('https://hook.example/cb', strict=True)
    assert getattr(exc.value, 'biz_code', None) == 14013


class _FakeSock:
    def __init__(self, peer):
        self.peer = peer
        self.closed = False

    def getpeername(self):
        return (self.peer, 443)

    def close(self):
        self.closed = True


def test_pinned_connection_rejects_if_any_address_is_private():
    conn = webhook_utils._PinnedHTTPSConnection('hook.example', 443)
    with mock.patch('socket.getaddrinfo', _Resolver('hook.example', ['93.184.216.34', '10.0.0.5'])), \
            mock.patch.object(webhook_utils._u3_connection, 'create_connection') as create:
        with pytest.raises(NewConnectionError, match='non-public address 10.0.0.5'):
            conn._new_conn()
    create.assert_not_called()


def test_pinned_connection_connects_to_validated_ip_and_keeps_hostname():
    conn = webhook_utils._PinnedHTTPSConnection('hook.example', 443)
    fake = _FakeSock('93.184.216.34')
    with mock.patch('socket.getaddrinfo', _Resolver('hook.example', ['93.184.216.34'])), \
            mock.patch.object(webhook_utils._u3_connection, 'create_connection', return_value=fake) as create:
        assert conn._new_conn() is fake
    # 连的是校验过的 IP 字面量（不再按主机名解析）；TLS 用的主机名仍是原域名
    assert create.call_args[0][0] == ('93.184.216.34', 443)
    assert conn.host == 'hook.example'


def test_pinned_connection_checks_peer_address():
    conn = webhook_utils._PinnedHTTPSConnection('hook.example', 443)
    fake = _FakeSock('127.0.0.1')
    with mock.patch('socket.getaddrinfo', _Resolver('hook.example', ['93.184.216.34'])), \
            mock.patch.object(webhook_utils._u3_connection, 'create_connection', return_value=fake):
        with pytest.raises(NewConnectionError, match='reached a non-public address'):
            conn._new_conn()
    assert fake.closed


def _strict_job(url):
    return {
        'event_id': 1, 'api_key_id': 900001, 'url': url, 'strict': True,
        'body': b'{}', 'headers': {'Content-Type': 'application/json'}, 'lease': None,
    }


def test_dns_rebinding_between_validation_and_connect_is_refused():
    """校验时解析到公网、建连时改指 127.0.0.1：严格模式在建连时拒绝，一个字节都不发"""
    resolver = _Resolver('rebind.example', ['93.184.216.34'], ['127.0.0.1'])
    with mock.patch('socket.getaddrinfo', resolver), \
            mock.patch.object(webhook_utils._u3_connection, 'create_connection') as create:
        error = webhook_services._deliver(_strict_job('https://rebind.example/hook'))
    assert isinstance(error, requests.ConnectionError)
    assert 'non-public address 127.0.0.1' in str(error)
    assert resolver.calls == 2            # 校验一次、建连一次
    create.assert_not_called()


# ---- 本机 TLS 接收端：证书校验仍按原域名；严格通道下 B-59 的正文上限照旧 ----

def _self_signed(tmp_path, hostname):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / 'cert.pem'
    key_path = tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return str(cert_path), str(key_path)


class _TLSReceiver:
    """127.0.0.1 上的一次性 TLS 接收端：读完请求头后回 200 + body_size 字节正文，记下 SNI 与实际写出的字节数"""

    def __init__(self, cert, key, body_size=2):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.sni = []
        self.context.sni_callback = lambda sock, name, ctx: self.sni.append(name)
        self.body_size = body_size
        self.sent = 0
        self._sock = socket.socket()
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(5)
        self._sock.settimeout(10)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                raw, _ = self._sock.accept()
            except OSError:
                return
            try:
                conn = self.context.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError):
                raw.close()
                continue
            try:
                conn.settimeout(5)
                data = b''
                while b'\r\n\r\n' not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                # 读完请求正文再回应（带着没读的数据关连接会发 RST，客户端读不到响应）
                head, _, body = data.partition(b'\r\n\r\n')
                length = re.search(rb'(?i)content-length:\s*(\d+)', head)
                remaining = (int(length.group(1)) if length else 0) - len(body)
                while remaining > 0:
                    chunk = conn.recv(min(4096, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n'
                             + f'Content-Length: {self.body_size}\r\n\r\n'.encode())
                block = b'x' * 16384
                while self.sent < self.body_size:
                    n = min(len(block), self.body_size - self.sent)
                    conn.sendall(block[:n])
                    self.sent += n
            except (OSError, ssl.SSLError):
                pass
            finally:
                conn.close()

    def close(self):
        self._sock.close()


def test_strict_session_verifies_certificate_against_original_hostname(tmp_path):
    cert, key = _self_signed(tmp_path, 'hook.test')
    server = _TLSReceiver(cert, key)
    try:
        resolver = _Resolver('hook.test', ['127.0.0.1'])
        other = _Resolver('other.test', ['127.0.0.1'])
        # 本机地址不是公网：这里放行，只验证 TLS 行为
        with mock.patch.object(webhook_utils, '_is_public_ip', return_value=True):
            with mock.patch('socket.getaddrinfo', resolver):
                with webhook_utils.strict_webhook_session() as session:
                    resp = session.post(f'https://hook.test:{server.port}/cb', data=b'{}', verify=cert, timeout=5)
                    assert resp.status_code == 200
            assert server.sni[-1] == 'hook.test'           # SNI 是原域名，不是 IP
            # 同一台服务器、证书不含 other.test：主机名校验照旧生效
            with mock.patch('socket.getaddrinfo', other):
                with webhook_utils.strict_webhook_session() as session:
                    with pytest.raises(requests.exceptions.SSLError):
                        session.post(f'https://other.test:{server.port}/cb', data=b'{}', verify=cert, timeout=5)
    finally:
        server.close()


def test_strict_delivery_keeps_response_body_limit(tmp_path):
    """严格模式走固定地址的通道，B-59 的子线程 + 64KB 正文上限 + 总时限不受影响"""
    cert, key = _self_signed(tmp_path, 'hook.test')
    body_size = 32 * 1024 * 1024
    server = _TLSReceiver(cert, key, body_size=body_size)
    real_session = webhook_utils.strict_webhook_session

    def session_trusting_test_ca():
        session = real_session()
        session.verify = cert
        return session

    try:
        with mock.patch.object(webhook_utils, '_is_public_ip', return_value=True), \
                mock.patch('socket.getaddrinfo', _Resolver('hook.test', ['127.0.0.1'])), \
                mock.patch.object(webhook_utils, 'strict_webhook_session', session_trusting_test_ca), \
                mock.patch.object(webhook_services, '_post_in_thread',
                                  wraps=webhook_services._post_in_thread) as in_thread:
            started = time.monotonic()
            error = webhook_services._deliver(_strict_job(f'https://hook.test:{server.port}/cb'))
            elapsed = time.monotonic() - started
        assert error is None
        assert in_thread.call_count == 1                   # 仍在子线程里发送
        assert elapsed < webhook_services.SEND_DEADLINE_SECONDS
        time.sleep(0.3)
        assert server.sent < body_size                     # 读够 64KB 就断开，没有读完 32MB
    finally:
        server.close()


# ===========================================================================
# B-94  配置开关
# ===========================================================================

def _fresh_config(monkeypatch, **env):
    """按给定环境变量重新执行一份 config.py（不替换已导入的 config 模块）"""
    for name in ('FLASK_ENV', 'WEBHOOK_URL_STRICT', 'RATELIMIT_ENABLED', 'SCHEDULER_ENABLED', 'DEBUG',
                 'CHECK_WHITELIST', 'CHECK_BLACKLIST', 'SETTINGS_ENCRYPTION_KEY'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location(f'config_under_test_{uuid.uuid4().hex}',
                                                  os.path.join(BACKEND_DIR, 'config.py'))
    module = importlib.util.module_from_spec(spec)
    with mock.patch('dotenv.load_dotenv', return_value=False):
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('raw, expected', [
    ('1', True), ('true', True), ('True', True), ('TRUE', True), (' yes ', True), ('On', True),
    ('0', False), ('false', False), ('False', False), ('no', False), ('off', False), ('ture', False),
])
def test_parse_bool_is_case_insensitive(raw, expected):
    from config import parse_bool
    assert parse_bool(raw) is expected
    assert parse_bool(raw, default=True) is expected


def test_parse_bool_default_for_missing():
    from config import parse_bool
    assert parse_bool(None, True) is True
    assert parse_bool('  ', True) is True
    assert parse_bool(None, False) is False


def test_lowercase_switches_now_take_effect(monkeypatch):
    cfg = _fresh_config(monkeypatch, FLASK_ENV=' Production ', WEBHOOK_URL_STRICT='true',
                        RATELIMIT_ENABLED='1', SCHEDULER_ENABLED='true', CHECK_BLACKLIST='yes',
                        DEBUG='false')
    assert cfg.Config.FLASK_ENV == 'production' and cfg.Config.FLASK_ENV_SET is True
    assert cfg.ProductionConfig.WEBHOOK_URL_STRICT is True
    assert cfg.Config.RATELIMIT_ENABLED is True
    assert cfg.Config.SCHEDULER_ENABLED is True
    assert cfg.Config.CHECK_BLACKLIST is True
    assert cfg.Config.DEBUG is False


def test_production_webhook_strict_defaults_true(monkeypatch):
    cfg = _fresh_config(monkeypatch, FLASK_ENV='production')
    assert cfg.ProductionConfig.WEBHOOK_URL_STRICT is True
    assert cfg.DevelopmentConfig.WEBHOOK_URL_STRICT is False      # 开发默认仍关闭
    cfg = _fresh_config(monkeypatch, FLASK_ENV='production', WEBHOOK_URL_STRICT='False')
    assert cfg.ProductionConfig.WEBHOOK_URL_STRICT is False        # 显式关闭仍可以（启动时告警）


def test_cors_max_age_default_and_override(monkeypatch):
    """预检缓存默认 7200 秒（Chromium 上限），可用 CORS_MAX_AGE 覆盖"""
    monkeypatch.delenv('CORS_MAX_AGE', raising=False)
    assert _fresh_config(monkeypatch).Config.CORS_MAX_AGE == 7200
    assert _fresh_config(monkeypatch, CORS_MAX_AGE='600').Config.CORS_MAX_AGE == 600


def test_cors_preflight_sends_max_age(monkeypatch):
    """与 app.py 相同的 CORS 调用：预检响应带 Access-Control-Max-Age，APP 的预检结果能被缓存"""
    from flask import Flask
    from flask_cors import CORS

    monkeypatch.delenv('CORS_MAX_AGE', raising=False)
    cfg = _fresh_config(monkeypatch).Config
    app = Flask(__name__)
    app.add_url_rule('/ping', 'ping', lambda: 'ok')
    CORS(app, origins=['https://appassets.androidplatform.net'], max_age=cfg.CORS_MAX_AGE)

    resp = app.test_client().options('/ping', headers={
        'Origin': 'https://appassets.androidplatform.net',
        'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'authorization,x-warehouse-id',
    })
    assert resp.headers['Access-Control-Allow-Origin'] == 'https://appassets.androidplatform.net'
    assert resp.headers['Access-Control-Max-Age'] == '7200'


def test_unrecognised_bool_is_reported(monkeypatch, caplog):
    cfg = _fresh_config(monkeypatch, FLASK_ENV='development', RATELIMIT_ENABLED='ture')
    assert cfg.Config.RATELIMIT_ENABLED is False
    logger = logging.getLogger('b94-test')
    with caplog.at_level(logging.WARNING, logger='b94-test'):
        cfg.check_runtime_config({'SQLALCHEMY_DATABASE_URI': 'sqlite://'}, logger, environ={})
    assert "RATELIMIT_ENABLED='ture'" in caplog.text


_GOOD_PROD = {
    'JWT_SECRET_KEY': 'k' * 40,
    'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@db/wms',
    'CORS_ORIGINS': 'https://admin.example.com',
    'WEBHOOK_URL_STRICT': True,
}


def _check(config, flask_env, flask_env_set=True, environ=None, logger=None):
    from config import check_runtime_config
    return check_runtime_config(config, logger or logging.getLogger('b94-test'), flask_env=flask_env,
                                flask_env_set=flask_env_set, environ=environ or {})


def test_production_requires_settings_encryption_key():
    with pytest.raises(RuntimeError, match='SETTINGS_ENCRYPTION_KEY must be set'):
        _check(dict(_GOOD_PROD), 'production')
    with pytest.raises(RuntimeError, match='not a valid Fernet key'):
        _check({**_GOOD_PROD, 'SETTINGS_ENCRYPTION_KEY': 'not-a-key'}, 'production')

    from cryptography.fernet import Fernet
    _check({**_GOOD_PROD, 'SETTINGS_ENCRYPTION_KEY': Fernet.generate_key().decode()}, 'production')


def test_production_warns_when_webhook_strict_off(caplog):
    from cryptography.fernet import Fernet
    config = {**_GOOD_PROD, 'SETTINGS_ENCRYPTION_KEY': Fernet.generate_key().decode(), 'WEBHOOK_URL_STRICT': False}
    with caplog.at_level(logging.WARNING, logger='b94-test'):
        _check(config, 'production')
    assert 'WEBHOOK_URL_STRICT is off in production' in caplog.text


def test_missing_flask_env_with_non_sqlite_database_only_warns(caplog):
    with caplog.at_level(logging.WARNING, logger='b94-test'):
        _check({'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@db/wms'}, 'development', flask_env_set=False)
    assert 'FLASK_ENV is not set' in caplog.text

    caplog.clear()
    # 开发库是 sqlite、但环境里配着生产 PG 地址：同样提醒
    with caplog.at_level(logging.WARNING, logger='b94-test'):
        _check({'SQLALCHEMY_DATABASE_URI': 'sqlite:///test.db'}, 'development', flask_env_set=False,
               environ={'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@db/wms'})
    assert 'FLASK_ENV is not set' in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger='b94-test'):
        _check({'SQLALCHEMY_DATABASE_URI': 'sqlite:///test.db'}, 'development', flask_env_set=False)
        _check({'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@db/wms'}, 'development', flask_env_set=True)
    assert 'FLASK_ENV is not set' not in caplog.text


# ===========================================================================
# B-95  测试依赖不进生产
# ===========================================================================

DEV_ONLY_PACKAGES = ('fakeredis', 'pytest', 'iniconfig', 'pluggy')


def _requirement_names(path):
    names = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('-'):
                names.append(re.split(r'[=<>!~\[ ]', line, maxsplit=1)[0].lower())
    return names


def test_dev_requirements_split():
    prod = _requirement_names(os.path.join(BACKEND_DIR, 'requirements.txt'))
    dev = _requirement_names(os.path.join(BACKEND_DIR, 'requirements-dev.txt'))
    for name in DEV_ONLY_PACKAGES:
        assert name not in prod
        assert name in dev
    with open(os.path.join(BACKEND_DIR, 'requirements-dev.txt'), encoding='utf-8') as f:
        assert f.readline().strip() == '-r requirements.txt'


def test_production_code_does_not_import_dev_packages():
    pattern = re.compile(r'^\s*(?:import|from)\s+(' + '|'.join(DEV_ONLY_PACKAGES) + r')\b', re.M)
    skip_dirs = {'tests', 'venv', '.venv', '__pycache__', 'migrations', '.git', 'node_modules', 'instance', 'logs'}
    offenders = []
    for root, dirs, files in os.walk(BACKEND_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if name.endswith('.py'):
                path = os.path.join(root, name)
                with open(path, encoding='utf-8', errors='ignore') as f:
                    if pattern.search(f.read()):
                        offenders.append(os.path.relpath(path, BACKEND_DIR))
    assert offenders == []


# ===========================================================================
# B-96 ~ B-99  报关单证
# ===========================================================================

def _view(client, token, dn_id):
    response = client.get(f'/dn/{dn_id}/customs', headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _document_data(client, dn_id):
    from warehouse.dn.customs_services import CustomsService
    with client.application.app_context():
        dn = db.session.get(DN, dn_id)
        return CustomsService._document_data(dn, CustomsService.build_view(dn))


def _problems(view, code):
    return [p for p in view['problems'] if p['code'] == code]


@pytest.mark.parametrize('company_country, warehouse_country', [(None, 'JP'), ('CN', 'JP')])
def test_shipper_country_is_export_country(client, access_token, company_country, warehouse_country):
    dn_id, _task = _export_dn_ready(client, access_token)
    with client.application.app_context():
        get_company().country_code = company_country
        get_warehouse().country_code = warehouse_country
        db.session.commit()
    view = _view(client, access_token, dn_id)
    assert not _problems(view, 'EXPORTER_PROFILE_INCOMPLETE')
    data = _document_data(client, dn_id)
    assert data['exporter']['country'] == 'JAPAN'
    assert data['exporter']['country'] == data['country_of_export']
    assert _issue(client, access_token, dn_id).status_code == 201


def test_missing_export_country_still_reported(client, access_token):
    dn_id, _task = _export_dn_ready(client, access_token)
    with client.application.app_context():
        get_company().country_code = None
        get_warehouse().country_code = None
        db.session.commit()
    view = _view(client, access_token, dn_id)
    assert [p['field'] for p in _problems(view, 'EXPORTER_PROFILE_INCOMPLETE')] == ['company.country_code']
    assert _document_data(client, dn_id)['exporter']['country'] == ''


def _set_task_carrier(client, token, task_id, name, code):
    with client.application.app_context():
        carrier = Carrier(name=name, code=code, company_id=get_company().id, created_by=get_admin_user().id)
        db.session.add(carrier)
        db.session.commit()
        carrier_id = carrier.id
    response = client.put(f'/delivery/{task_id}/tracking', json={'carrier_id': carrier_id}, headers=_h(token))
    assert response.status_code == 200, response.get_json()
    return carrier_id


def test_non_latin_carrier_name_does_not_block_issue(client, access_token):
    """承运商名不印在单证上：日文名（如「ヤマト運輸」）不再拦签发"""
    dn_id, task_id = _export_dn_ready(client, access_token)
    _set_task_carrier(client, access_token, task_id, 'ヤマト運輸', 'yamato')

    view = _view(client, access_token, dn_id)
    assert _problems(view, 'CARRIER_NON_LATIN_TEXT') == []
    assert view['ready'] is True
    assert _issue(client, access_token, dn_id).status_code == 201


def test_latin_carrier_name_not_flagged(client, access_token):
    dn_id, task_id = _export_dn_ready(client, access_token)
    _set_task_carrier(client, access_token, task_id, 'Yamato Transport', 'yamato')
    view = _view(client, access_token, dn_id)
    assert not _problems(view, 'CARRIER_NON_LATIN_TEXT')
    assert _issue(client, access_token, dn_id).status_code == 201


def test_non_jpy_currency_asks_for_manual_threshold_check(client, access_token):
    dn_id, _task = _export_dn_ready(client, access_token, customs=_customs(currency='USD'))
    view = _view(client, access_token, dn_id)
    problems = _problems(view, 'JP_EXPORT_VALUE_UNVERIFIED')
    assert len(problems) == 1
    assert problems[0]['level'] == 'warning' and problems[0]['field'] == 'jp_export_code'
    assert 'USD' in problems[0]['message'] and '200,000 JPY' in problems[0]['message']
    assert 'G001' in problems[0]['message'] and 'G002' in problems[0]['message']
    assert view['ready'] is True                        # 只提示，不拦出单证


def test_non_jpy_warning_skipped_when_all_codes_present(client, access_token):
    customs = _customs(currency='USD')
    customs['lines'][0]['jp_export_code'] = '950300000'
    customs['lines'][1]['jp_export_code'] = '392640000'
    dn_id, _task = _export_dn_ready(client, access_token, customs=customs)
    assert not _problems(_view(client, access_token, dn_id), 'JP_EXPORT_VALUE_UNVERIFIED')


def test_non_jpy_warning_skipped_when_not_exporting_from_japan(client, access_token):
    dn_id, _task = _export_dn_ready(client, access_token, customs=_customs(currency='USD'))
    with client.application.app_context():
        get_company().country_code = 'DE'
        get_warehouse().country_code = 'DE'
        db.session.commit()
    assert not _problems(_view(client, access_token, dn_id), 'JP_EXPORT_VALUE_UNVERIFIED')


def test_jpy_currency_unchanged(client, access_token):
    dn_id, _task = _export_dn_ready(client, access_token)          # 默认 JPY，货值 < 20 万
    view = _view(client, access_token, dn_id)
    assert not _problems(view, 'JP_EXPORT_VALUE_UNVERIFIED')
    assert not _problems(view, 'JP_EXPORT_CODE_MISSING')


def test_overlong_consignee_returns_409_instead_of_500(client, access_token):
    """审计复现：收件人 8 个字段各 255 个（上限）CJK 字符 → reportlab LayoutError（实测 230 还排得下）"""
    base = '東京都港区芝公園四丁目二番八号' * 20
    consignee = {k: base[:255] for k in ('name', 'company', 'address_line1', 'address_line2', 'city', 'state',
                                         'postal_code', 'phone')}
    consignee['country'] = 'CN'
    customs = _customs()
    customs['consignee'] = consignee
    dn_id, _task = _export_dn_ready(client, access_token, customs=customs)

    view = _view(client, access_token, dn_id)
    assert view['ready'] is True                        # 非拉丁收件人只是 warning，能走到渲染
    response = _issue(client, access_token, dn_id)
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body['code'] == 16117
    assert body['details']['reason'] == 'LAYOUT_OVERFLOW'
    assert body['details']['document'] in ('CI', 'PL')
    assert 'consignee' in body['message']
    with client.application.app_context():
        assert DNDocument.query.filter_by(dn_id=dn_id).count() == 0


def test_layout_error_wrapped_by_renderer():
    from reportlab.platypus.doctemplate import LayoutError
    from warehouse.dn import customs_documents

    with mock.patch('reportlab.platypus.SimpleDocTemplate.build', side_effect=LayoutError('Flowable too large')):
        with pytest.raises(customs_documents.DocumentLayoutError):
            customs_documents._build([], 'Commercial Invoice X', 'author', lambda canvas, doc: None)


# ===========================================================================
# B-100  department 在未绑定用户的 API Key 下
# ===========================================================================

def _plain_key(company_id, permissions):
    plain = 'k-' + uuid.uuid4().hex
    key = APIKey(key=hash_api_key(plain), key_prefix=plain[:8], system_name='dept-' + uuid.uuid4().hex[:6],
                 permissions=list(permissions), user_id=None)
    key.company_id = company_id
    key.is_active = True
    db.session.add(key)
    db.session.commit()
    return plain


@pytest.fixture
def dept_setup(client):
    with client.application.app_context():
        company_a = Company.query.filter_by(name='Company A').first()
        company_b = Company.query.filter_by(name='Company B').first()
        other = Department(name='B-Dept', company_id=company_b.id, description='b',
                           created_by=get_admin_user().id)
        db.session.add(other)
        db.session.commit()
        perms = ['department_read', 'department_edit', 'department_delete']
        return {
            'key_a': {'X-API-KEY': _plain_key(company_a.id, perms)},
            'company_a': company_a.id,
            'own': get_department().id,
            'other': other.id,
        }


def test_department_list_with_userless_api_key(client, dept_setup):
    response = client.get('/department/', headers=dept_setup['key_a'])
    assert response.status_code == 200, response.get_json()
    items = response.get_json()['items']
    assert items and all(item['company_id'] == dept_setup['company_a'] for item in items)


def test_department_detail_with_userless_api_key(client, dept_setup):
    headers = dept_setup['key_a']
    assert client.get(f"/department/{dept_setup['own']}", headers=headers).status_code == 200
    response = client.get(f"/department/{dept_setup['other']}", headers=headers)
    assert response.status_code == 403
    assert response.get_json()['code'] == 12001

    response = client.put(f"/department/{dept_setup['own']}", headers=headers,
                          json={'name': 'HR-renamed', 'company_id': 999})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['name'] == 'HR-renamed'
    assert response.get_json()['company_id'] == dept_setup['company_a']     # 归属不能借更新接口迁移
    assert client.put(f"/department/{dept_setup['other']}", headers=headers,
                      json={'name': 'x'}).status_code == 403
    assert client.delete(f"/department/{dept_setup['other']}", headers=headers).status_code == 403


def test_department_create_with_userless_api_key_needs_user(client, dept_setup):
    response = client.post('/department/', headers=dept_setup['key_a'],
                           json={'name': 'New', 'company_id': dept_setup['company_a']})
    assert response.status_code == 400
    assert response.get_json()['code'] == 11008


def test_department_delete_with_userless_api_key(client, dept_setup):
    with client.application.app_context():
        dept = Department(name='Empty', company_id=dept_setup['company_a'], description='e',
                          created_by=get_admin_user().id)
        db.session.add(dept)
        db.session.commit()
        dept_id = dept.id
    assert client.delete(f'/department/{dept_id}', headers=dept_setup['key_a']).status_code == 200


def test_department_staff_still_limited_to_own_company(client, access_company_admin_token, dept_setup):
    headers = {'Authorization': f'Bearer {access_company_admin_token}'}
    assert client.get(f"/department/{dept_setup['own']}", headers=headers).status_code == 200
    assert client.get(f"/department/{dept_setup['other']}", headers=headers).status_code == 403
    response = client.post('/department/', headers=headers, json={'name': 'X', 'company_id': 999999})
    assert response.status_code == 403
    response = client.post('/department/', headers=headers, json={'name': 'Mine'})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['company_id'] == dept_setup['company_a']


# ===========================================================================
# B-106  SMTP 测试接口不回显异常原文
# ===========================================================================

def test_smtp_test_error_is_generic(client, caplog):
    from system.settings.schemas import api_ns as settings_ns

    app = client.application
    blueprint = Blueprint(f'settings_test_{uuid.uuid4().hex[:6]}', __name__)
    api = Api(blueprint, doc=False)
    api.add_namespace(settings_ns, path='/settings')
    app.register_blueprint(blueprint, url_prefix='/sys-test')

    secret = '535 auth failed for smtp-user@10.1.2.3:2525 password=hunter2'
    with mock.patch('system.settings.views.SettingsService.send_test_email', side_effect=Exception(secret)), \
            caplog.at_level(logging.ERROR, logger='system.settings.views'):
        response = client.post('/sys-test/settings/smtp/test', json={'to': 'a@example.com'},
                               headers={'X-API-KEY': TEST_API_KEY_PLAIN})
    assert response.status_code == 400
    message = response.get_json()['message']
    assert 'hunter2' not in message and '10.1.2.3' not in message and '535' not in message
    assert 'Failed to send the test email' in message
    assert 'hunter2' in caplog.text                     # 原文只进日志


# ===========================================================================
# B-108  list_goods_locations 稳定排序
# ===========================================================================

def test_list_goods_locations_ordered_by_id(client):
    from warehouse.goods.services import GoodsLocationService

    with client.application.app_context():
        query = GoodsLocationService.list_goods_locations({'warehouse_id': get_warehouse().id})
        sql = str(query.statement.compile(compile_kwargs={'literal_binds': True})).lower()
        assert 'order by goods_locations.id' in sql
        ids = [row.id for row in GoodsLocationService.list_goods_locations({}).all()]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# W-51：单证签发人 / 运单建单人带用户名（前端不再显示裸 id）
# ---------------------------------------------------------------------------

def test_document_and_shipment_carry_user_ref(client):
    from datetime import date as _date
    from warehouse.dn.models import DNCarrierShipment, DNDocument, _user_ref
    from warehouse.dn.schemas import dn_document_meta_model
    from flask_restx import marshal
    with client.application.app_context():
        admin = get_admin_user()
        assert _user_ref(admin.id) == {'id': admin.id, 'user_name': admin.user_name}
        assert _user_ref(None) is None and _user_ref(999999) is None
        dn = DN.query.first()
        doc = DNDocument(dn_id=dn.id, doc_type='commercial_invoice', version=1, document_number='CI-REF-1', status='issued',
                         sha256='0' * 64, size_bytes=1, file_name='ci.pdf', content=b'x', issued_by=admin.id,
                         invoice_date=warehouse_today(), data_sha256='0' * 64)
        shipment = DNCarrierShipment(dn_id=dn.id, carrier='fedex', status='failed', created_by=admin.id)
        db.session.add_all([doc, shipment])
        db.session.flush()
        assert doc.to_meta()['issued_by_user'] == {'id': admin.id, 'user_name': admin.user_name}
        assert marshal(doc, dn_document_meta_model)['issued_by_user']['user_name'] == admin.user_name
        assert shipment.to_dict()['created_by_user']['user_name'] == admin.user_name
        db.session.rollback()
