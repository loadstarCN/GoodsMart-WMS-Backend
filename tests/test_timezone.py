"""时区通用化（doc/timezone-utc.md）：时刻存 UTC、业务日期按仓库时区、接口输出带偏移。

- UTCDateTime：Python 侧一律带时区的 UTC；不带时区的输入按旧口径时区；SQLite 存不带时区的 UTC；PostgreSQL 上还没转换的
  timestamp 列读出的值按旧口径时区解释（用假的方言对象模拟）
- clock：仓库时区的回退链、当地日期（与 UTC 日期不同的 America/Los_Angeles）、用户输入时刻的解释
- 仓库时区的校验与输出（timezone / timezone_configured）、单据的顶层 timezone、时刻字段输出 +00:00
- webhook 的 event_time 用仓库时区的偏移；实际发货日按仓库当地日期（午夜前后）
- 生产库转换（timezone_conversion）：列筛选、SQL 生成、事务内的执行顺序与校验（假的游标，不连 PostgreSQL）
"""
from .helpers import *
from .test_customs import _create_dn, _force_packed, _h, _prepare, _ship, _webhook_key

from datetime import date, datetime, timedelta, timezone     # helpers 的 import * 会把 datetime 换成模块，放在它后面
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import text

import config
import timezone_conversion as conversion
from extensions.db import UTCDateTime
from system.webhook.models import WebhookEvent
from warehouse.common import clock
from warehouse.dn.services import event_time

UTC = timezone.utc
LA = ZoneInfo('America/Los_Angeles')
TOKYO = ZoneInfo('Asia/Tokyo')
PG = SimpleNamespace(name='postgresql')
SQLITE = SimpleNamespace(name='sqlite')


def _fixed_now(monkeypatch, moment):
    """把 clock.now() 固定在 moment（带时区）"""
    monkeypatch.setattr(clock, 'utc_now', lambda: moment.astimezone(UTC))


def _set_timezone(client, warehouse_tz):
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.timezone = warehouse_tz
        db.session.commit()
        return warehouse.id


# ---------------------------------------------------------------------------
# UTCDateTime
# ---------------------------------------------------------------------------

def test_utc_datetime_bind_and_result_semantics(client):
    column = UTCDateTime()
    aware = datetime(2026, 10, 25, 9, 30, tzinfo=TOKYO)
    with client.application.app_context():
        client.application.config['WMS_LEGACY_TIMEZONE'] = 'Asia/Tokyo'
        # SQLite：存不带时区的 UTC
        assert column.process_bind_param(aware, SQLITE) == datetime(2026, 10, 25, 0, 30)
        # 不带时区的输入 = 旧口径时区（Asia/Tokyo）的墙上时间
        assert column.process_bind_param(datetime(2026, 10, 25, 9, 30), SQLITE) == datetime(2026, 10, 25, 0, 30)
        # 只给日期 = 旧口径时区当天 00:00；ISO 字符串先解析
        assert column.process_bind_param(date(2026, 10, 25), SQLITE) == datetime(2026, 10, 24, 15, 0)
        assert column.process_bind_param('2026-10-25T00:30:00Z', SQLITE) == datetime(2026, 10, 25, 0, 30)
        # PostgreSQL：绑定带时区的 UTC（还没转换的 timestamp 列由会话时区换成墙上时间）
        bound = column.process_bind_param(aware, PG)
        assert bound.tzinfo is not None and bound == aware and bound.utcoffset() == timedelta(0)
        # SQLite 读出：不带时区的值就是 UTC
        assert column.process_result_value(datetime(2026, 10, 25, 0, 30), SQLITE) == \
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        # PostgreSQL timestamptz 读出：带偏移 → UTC
        read = column.process_result_value(datetime(2026, 10, 25, 9, 30, tzinfo=TOKYO), PG)
        assert read.tzinfo == UTC and read == aware
        # PostgreSQL 还没转换的 timestamp 列：不带时区的值按旧口径时区（Asia/Tokyo）解释
        legacy = column.process_result_value(datetime(2026, 10, 25, 9, 30), PG)
        assert legacy == datetime(2026, 10, 25, 0, 30, tzinfo=UTC) and legacy.tzinfo == UTC
        assert column.process_bind_param(None, PG) is None and column.process_result_value(None, PG) is None
        with pytest.raises(TypeError):
            column.process_bind_param(12345, SQLITE)


def test_legacy_timezone_defaults_to_utc(client):
    column = UTCDateTime()
    with client.application.app_context():
        client.application.config['WMS_LEGACY_TIMEZONE'] = 'UTC'
        assert column.process_result_value(datetime(2026, 10, 25, 9, 30), PG) == \
            datetime(2026, 10, 25, 9, 30, tzinfo=UTC)


def test_sqlite_round_trip_and_comparisons(client):
    with client.application.app_context():
        warehouse = get_warehouse()
        moment = datetime(2026, 10, 25, 9, 30, 15, 123456, tzinfo=TOKYO)
        warehouse.created_at = moment
        db.session.commit()
        warehouse_id = warehouse.id
        stored = db.session.execute(text('SELECT created_at FROM warehouses WHERE id = :id'),
                                    {'id': warehouse_id}).scalar()
        assert str(stored).startswith('2026-10-25 00:30:15.123456')       # SQLite 存不带时区的 UTC
        db.session.expire_all()
        loaded = db.session.get(Warehouse, warehouse_id).created_at
        assert loaded == moment and loaded.tzinfo == UTC
        # 带时区的条件：不论用哪个时区表示同一时刻，结果相同
        assert Warehouse.query.filter(Warehouse.id == warehouse_id,
                                      Warehouse.created_at >= datetime(2026, 10, 25, 0, 30, tzinfo=UTC)).count() == 1
        assert Warehouse.query.filter(Warehouse.id == warehouse_id,
                                      Warehouse.created_at > datetime(2026, 10, 24, 17, 31, tzinfo=LA)).count() == 0
        # db.func.now() 的默认值（SQLite CURRENT_TIMESTAMP 是 UTC）读出来也是带时区的现在
        location = get_location()
        assert location.created_at.tzinfo == UTC
        db.session.add(Department(name='TZ Dept', company_id=get_company().id, created_by=get_admin_user().id))
        db.session.commit()
        department = Department.query.filter_by(name='TZ Dept').one()
        assert abs((department.created_at - clock.now()).total_seconds()) < 120


# ---------------------------------------------------------------------------
# clock
# ---------------------------------------------------------------------------

def test_warehouse_timezone_fallback_chain(client):
    with client.application.app_context():
        app_config = client.application.config
        warehouse = get_warehouse()
        app_config.update(WMS_DEFAULT_TIMEZONE='', WMS_TIMEZONE='')
        assert clock.warehouse_timezone_name(warehouse) == 'UTC'
        app_config['WMS_TIMEZONE'] = 'Asia/Tokyo'
        assert clock.warehouse_timezone_name(warehouse) == 'Asia/Tokyo'
        app_config['WMS_DEFAULT_TIMEZONE'] = 'Europe/Berlin'
        assert clock.warehouse_timezone_name(warehouse) == 'Europe/Berlin'
        assert clock.warehouse_timezone_name(None) == 'Europe/Berlin'
        warehouse.timezone = 'America/Los_Angeles'
        assert clock.warehouse_timezone_name(warehouse) == 'America/Los_Angeles'
        assert clock.configured_timezone(warehouse) == 'America/Los_Angeles'
        # 单据 / 仓库 id 也能找到仓库；不合法的设置按默认
        db.session.commit()
        assert clock.warehouse_timezone_name(warehouse.id) == 'America/Los_Angeles'
        assert clock.warehouse_timezone_name(SimpleNamespace(warehouse_id=warehouse.id)) == 'America/Los_Angeles'
        assert clock.warehouse_timezone_name(SimpleNamespace(dn=SimpleNamespace(warehouse=warehouse))) == \
            'America/Los_Angeles'
        warehouse.timezone = 'Mars/Olympus'
        assert clock.warehouse_timezone_name(warehouse) == 'Europe/Berlin'


def test_business_date_differs_from_utc_date(client, monkeypatch):
    # 2026-10-25 05:00 UTC = 洛杉矶 10-24 22:00（PDT，UTC-7）
    _fixed_now(monkeypatch, datetime(2026, 10, 25, 5, 0, tzinfo=UTC))
    warehouse_id = _set_timezone(client, 'America/Los_Angeles')
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, warehouse_id)
        assert clock.now() == datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
        assert clock.local_today(warehouse) == date(2026, 10, 24)
        assert clock.local_today('Asia/Tokyo') == date(2026, 10, 25)
        assert clock.local_today(None) == date(2026, 10, 25)               # 默认时区（测试里是 UTC）
        assert clock.local_now(warehouse).utcoffset() == timedelta(hours=-7)
        moment = datetime(2026, 10, 25, 6, 59, tzinfo=UTC)
        assert clock.local_date(moment, warehouse) == date(2026, 10, 24)
        assert clock.local_date(moment + timedelta(minutes=1), warehouse) == date(2026, 10, 25)
        assert clock.to_local(moment, warehouse).isoformat() == '2026-10-24T23:59:00-07:00'
        assert clock.local_midnight(date(2026, 10, 24), warehouse) == datetime(2026, 10, 24, 7, 0, tzinfo=UTC)
        start, end = clock.local_day_range(date(2026, 11, 1), warehouse)          # 夏令时结束的那天有 25 小时
        assert end - start == timedelta(hours=25)
        # 跨仓库查询里「各仓库当地的今天」
        other = Warehouse(name='Tokyo WH', company_id=warehouse.company_id, created_by=warehouse.created_by,
                          timezone='Asia/Tokyo')
        db.session.add(other)
        db.session.commit()
        expression = clock.local_today_expression(Warehouse.id)
        rows = dict(db.session.query(Warehouse.id, expression).filter(Warehouse.id.in_([warehouse_id, other.id])))
        assert str(rows[warehouse_id])[:10] == '2026-10-24' and str(rows[other.id])[:10] == '2026-10-25'


def test_parse_input_datetime(client):
    warehouse_id = _set_timezone(client, 'America/Los_Angeles')
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, warehouse_id)
        parse = clock.parse_input_datetime
        # 带偏移的按偏移（含 Z）
        assert parse('2026-10-25T09:30:00+09:00', warehouse) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        assert parse('2026-10-25T00:30:00Z', warehouse) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        # 不带偏移的按仓库时区
        assert parse('2026-10-24T17:30:00', warehouse) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        assert parse('2026-10-24 17:30', warehouse) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        assert parse(datetime(2026, 10, 24, 17, 30), warehouse) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        # 只给日期 = 仓库当地 00:00
        assert parse('2026-10-24', warehouse) == datetime(2026, 10, 24, 7, 0, tzinfo=UTC)
        assert parse(date(2026, 10, 24), warehouse) == datetime(2026, 10, 24, 7, 0, tzinfo=UTC)
        # 没有仓库 = 默认时区（测试里 UTC）；结果总是 UTC
        assert parse('2026-10-24T17:30:00', None) == datetime(2026, 10, 24, 17, 30, tzinfo=UTC)
        assert parse('2026-10-24T17:30:00', 'Asia/Tokyo').tzinfo == UTC
        assert parse(None, warehouse) is None and parse('  ', warehouse) is None
        for bad in ('tomorrow', '2026-13-01', 12345):
            with pytest.raises(ValueError):
                parse(bad, warehouse)


def test_filter_time_range_uses_warehouse_day(client):
    warehouse_id = _set_timezone(client, 'America/Los_Angeles')
    with client.application.app_context():
        warehouse = db.session.get(Warehouse, warehouse_id)
        # 洛杉矶 10-24 一整天 = UTC 10-24 07:00 ~ 10-25 07:00
        warehouse.created_at = datetime(2026, 10, 25, 6, 59, tzinfo=UTC)
        db.session.commit()
        query = Warehouse.query.filter(Warehouse.id == warehouse_id)
        assert clock.filter_time_range(query, Warehouse.created_at, date(2026, 10, 24), date(2026, 10, 24),
                                       warehouse_id).count() == 1
        assert clock.filter_time_range(query, Warehouse.created_at, date(2026, 10, 25), None,
                                       warehouse_id).count() == 0


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def test_timezone_environment_validation(monkeypatch):
    monkeypatch.setenv('WMS_TEST_TZ', 'America/Los_Angeles')
    assert config._timezone_env('WMS_TEST_TZ') == 'America/Los_Angeles'      # 不再限定 Asia/Tokyo
    monkeypatch.setenv('WMS_TEST_TZ', '  Europe/Berlin ')
    assert config._timezone_env('WMS_TEST_TZ') == 'Europe/Berlin'
    monkeypatch.setenv('WMS_TEST_TZ', '')
    assert config._timezone_env('WMS_TEST_TZ') == ''
    for bad in ('Mars/Olympus', 'GMT+25', 'localtime', '../etc/passwd'):
        monkeypatch.setenv('WMS_TEST_TZ', bad)
        with pytest.raises(RuntimeError):
            config._timezone_env('WMS_TEST_TZ')
    assert config.is_valid_timezone('UTC') and not config.is_valid_timezone(None)
    assert not config.is_valid_timezone(' Asia/Tokyo')


def test_postgresql_session_timezone_is_the_legacy_timezone(monkeypatch):
    monkeypatch.delenv('PGOPTIONS', raising=False)
    options = config._engine_options('postgresql://u:p@db/wms', pool_pre_ping=True)
    assert options['pool_pre_ping'] is True
    assert options['connect_args'] == {'options': f'-c timezone={config.WMS_LEGACY_TIMEZONE}'}
    assert 'connect_args' not in config._engine_options('sqlite:///test.db')


def test_postgresql_options_keep_pgoptions(monkeypatch):
    # libpq ignores PGOPTIONS once options is passed explicitly: the deploy's lock_timeout must be merged in,
    # and the session time zone must still win
    monkeypatch.setenv('PGOPTIONS', ' -c lock_timeout=15s -c timezone=UTC ')
    options = config._engine_options('postgresql://u:p@db/wms')
    assert options['connect_args'] == {
        'options': f'-c lock_timeout=15s -c timezone=UTC -c timezone={config.WMS_LEGACY_TIMEZONE}'}


# ---------------------------------------------------------------------------
# 仓库时区：校验与输出
# ---------------------------------------------------------------------------

def test_warehouse_timezone_validation_and_output(client, access_token):
    with client.application.app_context():
        company_id = get_company().id
    response = client.post('/warehouse/', json={'name': 'LA Warehouse', 'company_id': company_id,
                                                'timezone': 'America/Los_Angeles'}, headers=_h(access_token))
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body['timezone'] == 'America/Los_Angeles' and body['timezone_configured'] == 'America/Los_Angeles'
    assert body['created_at'].endswith('+00:00')
    warehouse_id = body['id']

    for bad in ('Mars/Olympus', 'Tokyo', 'x' * 65):
        response = client.put(f'/warehouse/{warehouse_id}', json={'timezone': bad}, headers=_h(access_token))
        assert response.status_code == 400, (bad, response.get_json())
        assert response.get_json()['code'] == 14022 and response.get_json()['field'] == 'timezone'

    response = client.put(f'/warehouse/{warehouse_id}', json={'name': 'LA Warehouse 2'}, headers=_h(access_token))
    assert response.get_json()['timezone_configured'] == 'America/Los_Angeles'       # 没带 timezone = 不改
    # 清空 = 用默认时区（测试里没设 WMS_DEFAULT_TIMEZONE / WMS_TIMEZONE → UTC）
    response = client.put(f'/warehouse/{warehouse_id}', json={'timezone': ''}, headers=_h(access_token))
    body = response.get_json()
    assert response.status_code == 200 and body['timezone_configured'] is None and body['timezone'] == 'UTC'
    client.application.config['WMS_DEFAULT_TIMEZONE'] = 'Asia/Tokyo'
    detail = client.get(f'/warehouse/{warehouse_id}', headers=_h(access_token)).get_json()
    assert detail['timezone'] == 'Asia/Tokyo' and detail['timezone_configured'] is None
    client.put(f'/warehouse/{warehouse_id}', json={'timezone': 'America/Los_Angeles'}, headers=_h(access_token))
    items = {item['id']: item for item in client.get('/warehouse/?all=true', headers=_h(access_token)).get_json()['items']}
    # 列表行（skip_none）：timezone 总有；timezone_configured 没设时省略（= null）
    assert all(item['timezone'] for item in items.values())
    assert items[warehouse_id]['timezone_configured'] == 'America/Los_Angeles'
    assert all(item.get('timezone_configured') is None for key, item in items.items() if key != warehouse_id)


def test_create_warehouse_rejects_invalid_timezone(client, access_token):
    with client.application.app_context():
        company_id = get_company().id
    response = client.post('/warehouse/', json={'name': 'Bad TZ', 'company_id': company_id,
                                                'timezone': 'Asia/Tokio'}, headers=_h(access_token))
    assert response.status_code == 400 and response.get_json()['code'] == 14022
    with client.application.app_context():
        assert Warehouse.query.filter_by(name='Bad TZ').count() == 0


def test_document_outputs_carry_timezone_and_utc_times(client, access_token):
    _prepare(client)
    _set_timezone(client, 'America/Los_Angeles')
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-TZ-1').get_json()['id']
    detail = client.get(f'/dn/{dn_id}', headers=_h(access_token)).get_json()
    assert detail['timezone'] == 'America/Los_Angeles'
    assert detail['warehouse']['timezone'] == 'America/Los_Angeles'
    assert detail['created_at'].endswith('+00:00')
    rows = client.get('/dn/?all=true', headers=_h(access_token)).get_json()['items']
    row = next(r for r in rows if r['id'] == dn_id)
    assert row['warehouse_id'] and row['created_at'].endswith('+00:00')
    flow = client.get(f'/dn/{dn_id}/shipping-flow', headers=_h(access_token)).get_json()
    assert flow['timezone'] == 'America/Los_Angeles'
    ordered = next(step for step in flow['steps'] if step['key'] == 'ordered')
    assert ordered['at'].endswith('+00:00')


# ---------------------------------------------------------------------------
# webhook event_time 与实际发货日
# ---------------------------------------------------------------------------

def test_event_time_uses_warehouse_offset(client):
    moment = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse.timezone = 'Asia/Tokyo'
        assert event_time(moment, warehouse) == '2026-10-25T14:00:00+09:00'
        warehouse.timezone = 'America/Los_Angeles'
        assert event_time(moment, warehouse) == '2026-10-24T22:00:00-07:00'
        assert datetime.fromisoformat(event_time(moment, warehouse)) == moment        # 同一时刻
        assert event_time(None, warehouse) is None


def test_actual_shipping_date_in_warehouse_timezone_near_midnight(client, access_token):
    _prepare(client)
    _set_timezone(client, 'America/Los_Angeles')
    dn_id = _create_dn(client, access_token, customs=None, order_number='ORD-TZ-SHIP').get_json()['id']
    task_id = _force_packed(client, dn_id)
    key_id = _webhook_key(client)
    with client.application.app_context():
        db.session.get(DN, dn_id).api_key_id = key_id
        task = db.session.get(DeliveryTask, task_id)
        task.carrier_id = get_carrier().id
        task.expected_shipping_date = date(2026, 10, 1)
        db.session.commit()
    # 洛杉矶 10-10 23:30（UTC 已是 10-11 06:30）交承运商：实际发货日是当地的 10-10
    response = _ship(client, access_token, task_id, shipped_at='2026-10-10T23:30:00')
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['actual_shipping_date'] == '2026-10-10'
    assert body['completed_at'] == '2026-10-11T06:30:00+00:00'
    with client.application.app_context():
        task = db.session.get(DeliveryTask, task_id)
        assert task.completed_at == datetime(2026, 10, 11, 6, 30, tzinfo=UTC)
        assert task.actual_shipping_date == date(2026, 10, 10)
        payload = WebhookEvent.query.filter_by(api_key_id=key_id, event_type='dn.delivered').one().payload
    assert payload['shipped_at'] == '2026-10-10T23:30:00-07:00'

    # 签收只给日期（PDA）：仓库当地 00:00
    response = client.put(f'/delivery/{task_id}/sign/', json={'signed_at': '2026-10-12'}, headers=_h(access_token))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['signed_at'] == '2026-10-12T07:00:00+00:00'


# ---------------------------------------------------------------------------
# 生产库转换（不连 PostgreSQL：假的游标）
# ---------------------------------------------------------------------------

def test_conversion_column_discovery_and_statements():
    rows = [('activity_logs', 'created_at'), ('alembic_version', 'x'), ('dn', 'created_at'), ('dn', 'picked_at'),
            ('wms_timezone_migrations', 'migrated_at'), ('dn', 'created_at'), ('we"ird', 'col')]
    columns = conversion.convertible_columns(rows)
    assert columns == [('activity_logs', 'created_at'), ('dn', 'created_at'), ('dn', 'picked_at'), ('we"ird', 'col')]
    statements = conversion.alter_statements(columns, 'Asia/Tokyo')
    assert statements == [
        'ALTER TABLE "activity_logs" ALTER COLUMN "created_at" TYPE timestamptz USING "created_at" '
        "AT TIME ZONE 'Asia/Tokyo'",
        'ALTER TABLE "dn" ALTER COLUMN "created_at" TYPE timestamptz USING "created_at" AT TIME ZONE \'Asia/Tokyo\', '
        'ALTER COLUMN "picked_at" TYPE timestamptz USING "picked_at" AT TIME ZONE \'Asia/Tokyo\'',
        'ALTER TABLE "we""ird" ALTER COLUMN "col" TYPE timestamptz USING "col" AT TIME ZONE \'Asia/Tokyo\'',
    ]
    assert conversion.sample_column(columns) == ('activity_logs', 'created_at')
    for bad in ('', None, 'Mars/Olympus', "Asia/Tokyo'; DROP TABLE dn; --", 'localtime'):
        with pytest.raises(conversion.ConversionError):
            conversion.alter_statements(columns, bad)


class FakeCursor:
    """记下执行的 SQL，按 SQL 内容回固定结果（模拟 PostgreSQL 的 information_schema / 标记表）"""

    def __init__(self, columns, marker=False, sample=datetime(2026, 10, 24, 18, 0), converted=True):
        self.columns, self.marker, self.sample, self.converted = columns, marker, sample, converted
        self.executed, self._result = [], []
        self.altered = False

    def execute(self, sql, params=None):
        self.executed.append((' '.join(sql.split()), params))
        compact = ' '.join(sql.split())
        if 'pg_timezone_names' in compact:
            self._result = [(1,)]
        elif compact.startswith('SELECT to_regclass'):
            self._result = [('wms_timezone_migrations',)] if self.marker else [(None,)]
        elif compact.startswith('SELECT 1 FROM wms_timezone_migrations'):
            self._result = [(1,)] if self.marker else []
        elif 'data_type = \'timestamp without time zone\'' in compact:
            self._result = [] if self.altered else list(self.columns)
        elif compact.startswith('SELECT table_name, column_name, data_type'):
            kind = 'timestamp with time zone' if self.converted else 'timestamp without time zone'
            self._result = [(t, c, kind) for t, c in self.columns]
        elif compact.startswith('SELECT "created_at" FROM "activity_logs"'):
            self._result = [(self.sample,)]
        elif compact.startswith('SELECT count(*) FROM "activity_logs"'):
            self._result = [(1,)]
        elif compact.startswith('ALTER TABLE'):
            self.altered = True
            self._result = []
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def test_conversion_apply_runs_in_order_and_writes_marker():
    columns = [('activity_logs', 'created_at'), ('dn', 'created_at'), ('dn', 'picked_at')]
    cursor = FakeCursor(columns)
    logs = []
    result = conversion.apply(cursor, 'Asia/Tokyo', '/var/www/wms-backups/x.dump', expected_columns=columns,
                              log=logs.append)
    assert result == {'columns': columns, 'tables': 2, 'sample': ('activity_logs', 'created_at')}
    sql = [statement for statement, _ in cursor.executed]
    assert sql[0].startswith('SET LOCAL lock_timeout') and sql[1].startswith('SET LOCAL statement_timeout')
    assert 'pg_advisory_xact_lock' in sql[2]
    alters = [s for s in sql if s.startswith('ALTER TABLE')]
    assert len(alters) == 2
    # 抽查在 ALTER 之前取值、之后按旧口径时区比对；标记表最后写
    first_alter = sql.index(alters[0])
    assert any(s.startswith('SELECT "created_at" FROM "activity_logs"') for s in sql[:first_alter])
    check = next((s, p) for s, p in cursor.executed if s.startswith('SELECT count(*) FROM "activity_logs"'))
    assert check[1] == ('Asia/Tokyo', datetime(2026, 10, 24, 18, 0))
    assert sql[-2].startswith('CREATE TABLE IF NOT EXISTS wms_timezone_migrations')
    assert cursor.executed[-1][1] == (conversion.MIGRATION_KEY, 'Asia/Tokyo', 'UTC', '/var/www/wms-backups/x.dump')
    assert len(logs) == 2


def test_conversion_apply_refuses_rerun_changed_schema_and_failed_alter():
    columns = [('dn', 'created_at')]
    with pytest.raises(conversion.ConversionError, match='second conversion'):
        conversion.apply(FakeCursor(columns, marker=True), 'Asia/Tokyo', 'b', log=lambda _m: None)
    with pytest.raises(conversion.ConversionError, match='changed since the preflight'):
        conversion.apply(FakeCursor(columns), 'Asia/Tokyo', 'b', expected_columns=[('dn', 'updated_at')],
                         log=lambda _m: None)
    with pytest.raises(conversion.ConversionError, match='not converted'):
        conversion.apply(FakeCursor(columns, converted=False), 'Asia/Tokyo', 'b', log=lambda _m: None)


def test_conversion_plan_is_read_only():
    cursor = FakeCursor([('dn', 'created_at')])
    result = conversion.plan(cursor, 'Asia/Tokyo')
    assert result['columns'] == [('dn', 'created_at')] and result['tables'] == 1 and result['done'] is False
    assert not any(s.startswith(('ALTER', 'INSERT', 'CREATE')) for s, _ in cursor.executed)


def test_convert_utc_cli_on_sqlite_does_nothing(client):
    from timezone_conversion import wms_cli
    app = client.application
    app.cli.add_command(wms_cli)
    result = app.test_cli_runner().invoke(args=['wms', 'convert-utc', '--legacy-tz', 'Asia/Tokyo', '--apply'])
    assert result.exit_code == 0, result.output
    assert 'not PostgreSQL' in result.output


def _migrate_utc_module():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'manual' / 'migrate_utc.py'
    spec = importlib.util.spec_from_file_location('migrate_utc_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_maintenance_script_checks_code_and_single_migration_head(monkeypatch, tmp_path):
    module = _migrate_utc_module()
    # 迁移链只有一个 head（脚本要求库的 alembic_version 等于它）；代码里有 UTCDateTime 与迁移 d4f2a8c6e1b7
    assert len(module.migration_heads()) == 1
    module.check_code()
    env_file = tmp_path / '.env'
    env_file.write_text('WMS_TIMEZONE=Asia/Tokyo\n', encoding='utf-8')
    monkeypatch.setattr(module, 'ENV_FILE', env_file)
    assert module.legacy_timezone_from_env() == 'Asia/Tokyo'
    env_file.write_text('WMS_TIMEZONE=Asia/Tokyo\nWMS_LEGACY_TIMEZONE=Asia/Shanghai\n', encoding='utf-8')
    assert module.legacy_timezone_from_env() == 'Asia/Shanghai'
    env_file.write_text('FLASK_ENV=production\n', encoding='utf-8')
    with pytest.raises(RuntimeError):
        module.legacy_timezone_from_env()


def test_production_warns_when_legacy_timezone_unknown(caplog):
    import logging
    from cryptography.fernet import Fernet
    good = {'JWT_SECRET_KEY': 'x' * 40, 'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@db/wms',
            'SETTINGS_ENCRYPTION_KEY': Fernet.generate_key().decode(), 'WEBHOOK_URL_STRICT': True,
            'CORS_ORIGINS': 'https://admin.example.com', 'WMS_TIMEZONE': ''}
    logger = logging.getLogger('tz-test')
    with caplog.at_level(logging.WARNING, logger='tz-test'):
        config.check_runtime_config(good, logger, flask_env='production', flask_env_set=True, environ={})
    assert 'Neither WMS_LEGACY_TIMEZONE nor WMS_TIMEZONE is set' in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger='tz-test'):
        config.check_runtime_config({**good, 'WMS_TIMEZONE': 'Asia/Tokyo'}, logger, flask_env='production',
                                    flask_env_set=True, environ={})
        config.check_runtime_config(good, logger, flask_env='production', flask_env_set=True,
                                    environ={'WMS_LEGACY_TIMEZONE': 'UTC'})
    assert 'WMS_LEGACY_TIMEZONE' not in caplog.text
