"""Regression checks for the fast test harness: commits, Redis and app cleanup."""
import sqlite3

import pytest

from extensions import db, redis_client
from tests import helpers


@pytest.mark.skipif(not helpers.USE_TEMPLATE_DB, reason='SQLite template only')
def test_committed_changes_do_not_modify_template_or_next_copy():
    template = helpers._template_db()
    first = sqlite3.connect(':memory:')
    second = sqlite3.connect(':memory:')
    try:
        template.backup(first)
        first.execute('CREATE TABLE isolation_probe (value TEXT)')
        first.execute("INSERT INTO isolation_probe VALUES ('committed')")
        first.commit()
        template.backup(second)
        assert second.execute("SELECT name FROM sqlite_master WHERE name = 'isolation_probe'").fetchall() == []
        assert first.execute('SELECT value FROM isolation_probe').fetchone() == ('committed',)
    finally:
        first.close()
        second.close()


def test_redis_state_is_isolated_between_applications():
    first = helpers.setup_app()
    second = helpers.setup_app()
    try:
        with first.app_context():
            redis_client.set('isolation_probe', 'first')
        with second.app_context():
            assert redis_client.get('isolation_probe') is None
            redis_client.set('isolation_probe', 'second')
        with first.app_context():
            assert redis_client.get('isolation_probe') in ('first', b'first')
    finally:
        helpers.release_app(first)
        helpers.release_app(second)


def test_release_detaches_namespaces_and_disposes_database():
    app = helpers.setup_app()
    api = app.extensions['tests.restx_api']
    namespaces = list(api.namespaces)
    with app.app_context():
        engine = db.engine
    disposed = []
    from sqlalchemy import event
    event.listen(engine, 'engine_disposed', lambda engine: disposed.append(engine))
    helpers.release_app(app)
    helpers.release_app(app)  # cleanup remains safe after explicit fixture teardown
    assert app not in helpers._LIVE_APPS
    assert 'tests.restx_api' not in app.extensions
    assert all(api not in namespace.apis for namespace in namespaces)
    assert disposed
