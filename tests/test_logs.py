from .helpers import *

def test_get_user_logs(client, access_token):
    response = client.get('/logs/', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_log_query_with_filters(client, access_token):
    response = client.get('/logs/?page=1&per_page=10&actor=admin&method=POST&status_code=200', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0
    for item in data['items']:
        assert item['actor'] == 'admin'
        assert item['method'] == 'POST'
        assert item['status_code'] == 200
        # 列表 mask 里的 processing_time 在 log_model 里有定义（毫秒）
        assert item['processing_time'] == 12


def test_log_method_filter_only_allows_logged_methods(client, access_token):
    """只记 POST / PUT / DELETE：按 GET 筛不会有结果，直接 400（与 Web 的筛选项一致）"""
    response = client.get('/logs/?method=GET', headers={'Authorization': f'Bearer {access_token}'})
    assert response.status_code == 400
    for method in ('POST', 'PUT', 'DELETE'):
        response = client.get(f'/logs/?method={method}', headers={'Authorization': f'Bearer {access_token}'})
        assert response.status_code == 200, response.get_json()


def test_activity_log_to_dict(client):
    """to_dict 以前引用不存在的 trace_id，一调用就 AttributeError；IP 脱敏"""
    with client.application.app_context():
        log = get_activity_log()
        data = log.to_dict()
    assert data['id'] == log.id and data['method'] == 'POST' and data['processing_time'] == 12
    assert 'trace_id' not in data
    assert data['created_at'] and data['ip_address'].endswith('******')
    assert '127.0.0.1' not in data['ip_address']


def test_write_requests_are_logged_with_processing_time(client, access_token):
    """请求钩子只记写操作，处理时间按整数毫秒落库；GET 不记"""
    from system.logs.utils import after_request_logging, before_request_logging
    from system.logs.models import ActivityLog
    app = client.application
    with app.app_context():
        before = ActivityLog.query.count()
    with app.test_request_context('/api/test-log', method='GET'):
        before_request_logging()
        after_request_logging(app.response_class('{}', mimetype='application/json'))
    with app.test_request_context('/api/test-log', method='PUT', json={'name': 'x'}):
        before_request_logging()
        after_request_logging(app.response_class('{}', mimetype='application/json'))
    with app.app_context():
        logs = ActivityLog.query.filter_by(endpoint='/api/test-log').all()
        assert ActivityLog.query.count() == before + 1
        assert [log.method for log in logs] == ['PUT']
        assert isinstance(logs[0].processing_time, int) and logs[0].processing_time >= 0


def test_get_user_log_detail(client, access_token):
    with client.application.app_context():
        log = get_activity_log()
        response = client.get(f'/logs/{log.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == log.id
        assert data['actor'] == log.actor
        assert data['endpoint'] == log.endpoint
        assert data['method'] == log.method
        assert data['status_code'] == log.status_code


def test_method_filter_is_an_equality_on_postgresql(client):
    # activity_logs.method is a native enum on PostgreSQL (method_enum), which has no ILIKE operator:
    # ?method=GET returned 500 in production. The filter must be a plain equality.
    from sqlalchemy.dialects import postgresql
    from system.logs.services import LogService
    with client.application.app_context():
        sql = str(LogService.list_logs({'method': 'get'}).statement.compile(dialect=postgresql.dialect()))
    assert 'ILIKE' not in sql.upper()
    assert 'activity_logs.method = ' in sql


def test_log_list_end_time_filter(client, access_token):
    """created_at_end：小于等于；只给日期含那一整天（默认时区）"""
    from datetime import datetime, timezone
    from system.logs.models import ActivityLog
    with client.application.app_context():
        for day in (1, 5):
            db.session.add(ActivityLog(actor='range', endpoint='/api/range', method='PUT', status_code=200,
                                       created_at=datetime(2026, 10, day, 12, 0, tzinfo=timezone.utc)))
        db.session.commit()

    def days(query):
        response = client.get(f'/logs/?actor=range&{query}', headers={'Authorization': f'Bearer {access_token}'})
        assert response.status_code == 200, response.get_json()
        return sorted(int(item['created_at'][8:10]) for item in response.get_json()['items'])

    assert days('') == [1, 5]
    assert days('created_at_end=2026-10-03') == [1]
    assert days('created_at_end=2026-10-05') == [1, 5]                  # 含当天
    assert days('created_at_end=2026-10-05T11:00:00%2B00:00') == [1]
    assert days('created_at=2026-10-02T00:00:00%2B00:00&created_at_end=2026-10-06') == [5]
    response = client.get('/logs/?created_at_end=bogus', headers={'Authorization': f'Bearer {access_token}'})
    assert response.status_code == 400
