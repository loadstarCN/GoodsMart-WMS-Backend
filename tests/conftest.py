"""全局夹具：每条用 client 的测试结束时自动对账（warehouse/stock/audit.py）。

核对 ① Inventory 缓存 = 库位库存 + 不在库位上的库存；② 每个桶的流水合计 = 当前结存；③ 最后一条流水的结存 = 当前结存，
流水链连续；④ 不为负；⑤ 破损 ≤ 待上架；⑥ 批次属于该商品。改了库存却没写流水、或流水与实际不符的入口都会在这里暴露。
故意造坏数据的用例标 @pytest.mark.stock_audit_skip。测试本身已经失败时不再对账（避免重复报错）。
"""
import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _stock_audit(request):
    """测试结束后对账；先于 client 建立（取 client 作依赖），所以在 client 拆掉（drop_all）之前执行"""
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
