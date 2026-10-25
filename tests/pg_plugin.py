"""pytest 插件：在 PostgreSQL 上跑测试（.github/workflows/test-postgres.yml；本机：
`WMS_TEST_DATABASE_URI=postgresql://postgres@127.0.0.1:5432/wms_test python -m pytest -p tests.pg_plugin tests/...`）。

SQLite 测不出 FOR UPDATE 行锁、函数唯一索引、外键与类型约束在 PostgreSQL 上的差异（库存底座的加锁顺序就靠它验证）。
- tests/helpers.py 写死了 sqlite:///:memory:：写进 Flask 配置的 sqlite 连接串一律换成 $WMS_TEST_DATABASE_URI
- 每条测试前清空 public schema 再建表（上一条失败的拆卸不会漏到下一条）；测试结束释放引擎（每条测试建一个新 app / 引擎，
  不释放连接会超过 max_connections）
- 种子数据（tests/helpers.py init_test_data）里有指向不存在的批次 id 的拣货 / 打包明细（SQLite 不查外键），插种子数据时
  session_replication_role = replica（不触发外键）；测试本身照常查外键。需要超级用户（CI 的 postgres 用户是）
不设 WMS_TEST_DATABASE_URI 时什么都不做。
"""
import os

import flask
import pytest

URI = os.environ.get('WMS_TEST_DATABASE_URI')
_STATE = {'replica': False}
_ENGINES = []


def _install():
    import flask_sqlalchemy.extension as fsa_ext
    import sqlalchemy as sa
    from sqlalchemy import event
    from sqlalchemy.pool import NullPool

    original_setitem = flask.Config.__setitem__

    def setitem(self, key, value):
        if key == 'SQLALCHEMY_DATABASE_URI' and isinstance(value, str) and value.startswith('sqlite'):
            value = URI
        original_setitem(self, key, value)

    flask.Config.__setitem__ = setitem

    original_make_engine = fsa_ext.SQLAlchemy._make_engine

    def make_engine(self, bind_key, options, app):
        engine = original_make_engine(self, bind_key, options, app)
        if engine.dialect.name == 'postgresql':
            _ENGINES.append(engine)

            @event.listens_for(engine, 'checkout')
            def on_checkout(dbapi_conn, record, proxy):
                want = 'replica' if _STATE['replica'] else 'origin'
                if record.info.get('srr', 'origin') != want:
                    cursor = dbapi_conn.cursor()
                    cursor.execute(f'SET session_replication_role = {want}')
                    cursor.close()
                    dbapi_conn.commit()
                    record.info['srr'] = want
        return engine

    fsa_ext.SQLAlchemy._make_engine = make_engine

    import tests.helpers as helpers
    original_init = helpers.init_test_data

    def init_test_data(app):
        reset = sa.create_engine(URI, poolclass=NullPool, isolation_level='AUTOCOMMIT')
        with reset.connect() as conn:
            conn.exec_driver_sql('DROP SCHEMA public CASCADE')
            conn.exec_driver_sql('CREATE SCHEMA public')
        reset.dispose()
        _STATE['replica'] = True
        try:
            return original_init(app)
        finally:
            _STATE['replica'] = False

    helpers.init_test_data = init_test_data


def pytest_configure(config):
    if URI:
        _install()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    yield
    while _ENGINES:
        try:
            _ENGINES.pop().dispose()
        except Exception:  # noqa: BLE001 — 释放失败不影响下一条
            pass
