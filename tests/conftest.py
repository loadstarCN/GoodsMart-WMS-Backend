"""全局夹具与测试环境。

1. Redis → fakeredis：FlaskRedis.init_app 建客户端的地方换成 fakeredis，每个测试应用一个独立的内存 Redis
   （JWT 吊销名单、IP 黑白名单、找回密码验证码都在里面，测试之间不串数据）；限流（Flask-Limiter）用内存存储。
   测试进程里连 6379 端口一律直接拒绝并让该测试报错：本机没有 Redis 时每次连接要等约 2 秒，绝不能再静默发生。
2. 每条测试结束释放 setup_app 建的应用（tests/helpers.py release_apps：关引擎、把 Api 从命名空间摘下，防内存泄漏）。
3. 每条用 client 的测试结束时自动对账（warehouse/stock/audit.py）：
   核对 ① Inventory 缓存 = 库位库存 + 不在库位上的库存；② 每个桶的流水合计 = 当前结存；③ 最后一条流水的结存 = 当前结存，
   流水链连续；④ 不为负；⑤ 破损 ≤ 待上架；⑥ 批次属于该商品。改了库存却没写流水、或流水与实际不符的入口都会在这里暴露。
   故意造坏数据的用例标 @pytest.mark.stock_audit_skip。测试本身已经失败时不再对账（避免重复报错）。
"""
import sys

import pytest

try:
    import fakeredis
except ImportError as exc:  # pragma: no cover
    raise ImportError('tests need fakeredis: pip install -r requirements-dev.txt') from exc

REDIS_PORT = 6379
_redis_connects = []


class _FakeRedisProvider:
    """FlaskRedis 的 provider_class：init_app 调 from_url(REDIS_URL) 建客户端，这里每次给一个新的 FakeServer"""

    @staticmethod
    def from_url(_url, **kwargs):
        return fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), **kwargs)


def _block_redis_port(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == REDIS_PORT:
            _redis_connects.append(address)
            raise ConnectionRefusedError(f'tests must not connect to Redis at {address[:2]} (tests/conftest.py)')


def _install_fake_redis():
    from flask import current_app, has_app_context
    from werkzeug.local import LocalProxy
    from extensions.limiter import limiter
    from extensions.redis import redis_client
    redis_client.provider_class = _FakeRedisProvider
    original_init_app = redis_client.init_app

    def init_app(app, **kwargs):
        original_init_app(app, **kwargs)
        client = redis_client._redis_client
        app.extensions['tests.redis'] = client
        # FlaskRedis stores its client on the shared extension, not the app.
        # Keep nested test apps isolated; retain legacy access outside a context.
        redis_client._redis_client = LocalProxy(
            lambda: current_app.extensions['tests.redis'] if has_app_context() else client
        )

    redis_client.init_app = init_app
    # 构造 Limiter 时给的 storage_uri（REDIS_URL_*）优先于配置，只能改实例上的值；测试默认关限流，个别用例会打开
    limiter._storage_uri = 'memory://'
    sys.addaudithook(_block_redis_port)


_install_fake_redis()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _test_isolation():
    """最后拆（_stock_audit 依赖它，client 又在 _stock_audit 之后建）：释放本条测试建的应用；测试里有代码连了 Redis 端口则报错"""
    del _redis_connects[:]
    yield
    from tests.helpers import release_apps
    release_apps()
    if _redis_connects:
        pytest.fail(f'connected to Redis port {REDIS_PORT} {len(_redis_connects)} time(s); '
                    'tests must use the fakeredis client (tests/conftest.py)', pytrace=False)


@pytest.fixture(autouse=True)
def _stock_audit(request, _test_isolation):
    """测试结束后对账；先于 client 建立（取 client 作依赖），所以在 client 拆掉之前执行"""
    if 'client' not in request.fixturenames or request.node.get_closest_marker('stock_audit_skip'):
        yield
        return
    client = request.getfixturevalue('client')
    yield
    report = getattr(request.node, 'rep_call', None)
    if report is None or not report.passed:
        return
    from warehouse.stock.audit import audit_stock
    with client.application.app_context():
        problems = audit_stock()
    assert not problems, "stock audit failed after the test:\n" + "\n".join(str(p) for p in problems[:20])
