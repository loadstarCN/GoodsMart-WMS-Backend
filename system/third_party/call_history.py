"""Record safe metadata after the business session has been released."""
from time import perf_counter
from flask import current_app, g, request, has_app_context
from extensions import db
from extensions.db import utc_now
from .models import APICallLog, APIKey


def start_call():
    g.pop('authenticated_api_key_id', None)
    g.pop('api_call_record', None)
    g.api_call_started = (utc_now(), perf_counter())


def finish_call(response):
    key_id = g.get('authenticated_api_key_id')
    if key_id is not None:
        called_at, started = g.get('api_call_started', (utc_now(), perf_counter()))
        g.api_call_record = dict(
            api_key_id=key_id, called_at=called_at,
            method=request.method[:16],
            route=str(request.url_rule)[:512] if request.url_rule else '[unmatched]',
            status_code=response.status_code,
            duration_ms=max(0, min(2147483647, round((perf_counter() - started) * 1000))),
        )
    return response


def persist_call(_error=None):
    if not has_app_context():
        return
    record = g.pop('api_call_record', None)
    if record is None:
        return
    try:
        # Roll back any uncommitted business work before acquiring a log connection.
        # Also safe for SQLite StaticPool (the same physical connection is reused).
        db.session.remove()
        with db.engine.begin() as connection:
            exists = connection.execute(db.select(APIKey.id).where(APIKey.id == record['api_key_id'])).first()
            if exists:
                connection.execute(db.insert(APICallLog), record)
    except Exception:
        current_app.logger.exception('Unable to persist API call metadata')


def register_call_history(app):
    app.before_request(start_call)
    app.after_request(finish_call)
    app.teardown_request(persist_call)
