"""时刻列 timestamp → timestamptz 的一次性转换（doc/timezone-utc.md §5）。

代码在转换前后都能正确工作（extensions/db.py 的 UTCDateTime：还没转换的 timestamp 列按旧口径时区解释），转换只是把
PostgreSQL 里的墙上时间换成带时区的时刻：

    ALTER TABLE "t" ALTER COLUMN "c" TYPE timestamptz USING "c" AT TIME ZONE '<旧口径时区>', ...

- 对象：public 下所有 BASE TABLE 的 timestamp without time zone 列（不含 alembic_version 与标记表），每张表一条 ALTER
  （一张表只重写一次）；在调用方的一个事务里执行，任一步失败整体回滚。
- 旧口径时区：旧数据写入时的墙上时间（WMS_LEGACY_TIMEZONE → WMS_TIMEZONE；生产是 Asia/Tokyo，见 doc/timezone-jst.md）。
- 标记表 wms_timezone_migrations（doc/timezone-jst.md 那次已建）里记一行 MIGRATION_KEY，防止重复执行。
- 抽查：转换前取一列的一个值，转换后按旧口径时区换回墙上时间应该还是那个值。

用法：
- 生产：手动工作流 Migrate WMS Time to UTC → scripts/manual/migrate_utc.py（停服务、pg_dump 备份、转换、重启探测）。
- 自建实例：`flask wms convert-utc --legacy-tz Asia/Tokyo`（只列出要做的事）→ 先自己备份数据库 → 加 `--apply` 执行。
  不停服务也能转（代码前后兼容），但转换期间各表会被逐张锁住；建议在低峰期、先停应用。

本模块顶层只依赖标准库（维护脚本直接 import）；Flask CLI 在 wms_cli。
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIGRATION_KEY = 'wms_timestamp_to_timestamptz_2026_10'
MARKER_TABLE = 'wms_timezone_migrations'
EXCLUDED_TABLES = frozenset({'alembic_version', MARKER_TABLE})
TARGET_TIMEZONE = 'UTC'
SAMPLE_PREFERENCE = (('activity_logs', 'created_at'), ('dn', 'created_at'), ('asn', 'created_at'))

COLUMNS_SQL = """
    SELECT c.table_name, c.column_name
    FROM information_schema.columns c
    JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
      AND c.data_type = 'timestamp without time zone'
    ORDER BY c.table_name, c.ordinal_position
"""
COLUMN_TYPES_SQL = """
    SELECT table_name, column_name, data_type FROM information_schema.columns
    WHERE table_schema = 'public'
"""
MARKER_DDL = f"""
    CREATE TABLE IF NOT EXISTS {MARKER_TABLE} (
        migration_key text PRIMARY KEY,
        migrated_at timestamptz NOT NULL DEFAULT now(),
        source_timezone text NOT NULL,
        target_timezone text NOT NULL,
        backup_file text NOT NULL
    )
"""
MARKER_INSERT = (f"INSERT INTO {MARKER_TABLE} (migration_key, source_timezone, target_timezone, backup_file) "
                 "VALUES (%s, %s, %s, %s)")


class ConversionError(RuntimeError):
    """转换的前提不满足 / 校验失败（事务由调用方回滚）"""


def quote_ident(name: str) -> str:
    """SQL 标识符（表名 / 列名）加双引号"""
    return '"' + str(name).replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """SQL 字符串常量"""
    return "'" + str(value).replace("'", "''") + "'"


def check_timezone(name) -> str:
    """旧口径时区必须是 zoneinfo 认得的 IANA 名（Asia/Tokyo、America/Los_Angeles……）"""
    text = (name or '').strip() if isinstance(name, str) else ''
    if not text or text in ('localtime', 'posixrules'):
        raise ConversionError(f'legacy time zone {name!r} is not an IANA time zone name')
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ConversionError(f'legacy time zone {name!r} is not an IANA time zone name') from None
    return text


def convertible_columns(rows) -> list[tuple[str, str]]:
    """information_schema 的 (表, 列) → 要转换的列（去掉 alembic_version / 标记表、去重，保持顺序）"""
    seen, result = set(), []
    for table, column in rows:
        key = (str(table), str(column))
        if key[0] in EXCLUDED_TABLES or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def group_by_table(columns) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for table, column in columns:
        grouped.setdefault(table, []).append(column)
    return grouped


def alter_statements(columns, legacy_timezone: str) -> list[str]:
    """每张表一条 ALTER TABLE（一张表只重写一次）：
    ALTER TABLE "t" ALTER COLUMN "c" TYPE timestamptz USING "c" AT TIME ZONE '<legacy>', ..."""
    tz = quote_literal(check_timezone(legacy_timezone))
    statements = []
    for table, names in group_by_table(columns).items():
        clauses = ', '.join(
            f'ALTER COLUMN {quote_ident(name)} TYPE timestamptz USING {quote_ident(name)} AT TIME ZONE {tz}'
            for name in names)
        statements.append(f'ALTER TABLE {quote_ident(table)} {clauses}')
    return statements


def sample_column(columns):
    """抽查用的列：优先常用的创建时间列，没有取第一列；没有列为 None"""
    available = set(columns)
    for candidate in SAMPLE_PREFERENCE:
        if candidate in available:
            return candidate
    return columns[0] if columns else None


def _scalar(cursor, sql, params=None):
    cursor.execute(sql, params) if params is not None else cursor.execute(sql)
    row = cursor.fetchone()
    return row[0] if row else None


def marker_exists(cursor) -> bool:
    """标记表里已有本次转换的记录"""
    if _scalar(cursor, f"SELECT to_regclass('public.{MARKER_TABLE}')") is None:
        return False
    return _scalar(cursor, f'SELECT 1 FROM {MARKER_TABLE} WHERE migration_key = %s', (MIGRATION_KEY,)) is not None


def list_columns(cursor) -> list[tuple[str, str]]:
    cursor.execute(COLUMNS_SQL)
    return convertible_columns(cursor.fetchall())


def plan(cursor, legacy_timezone: str) -> dict:
    """只读：{legacy_timezone, columns, statements, tables, done}；done = 已经转换过（标记表里有记录）"""
    legacy = check_timezone(legacy_timezone)
    columns = list_columns(cursor)
    return {
        'legacy_timezone': legacy,
        'columns': columns,
        'tables': len(group_by_table(columns)),
        'statements': alter_statements(columns, legacy) if columns else [],
        'done': marker_exists(cursor),
    }


def check_postgres_timezone(cursor, legacy_timezone: str):
    """PostgreSQL 自己也得认得这个时区名（AT TIME ZONE 用的是数据库的时区库）"""
    if not _scalar(cursor, 'SELECT count(*) FROM pg_timezone_names WHERE name = %s', (legacy_timezone,)):
        raise ConversionError(f'PostgreSQL does not know the time zone {legacy_timezone!r}')


def apply(cursor, legacy_timezone: str, backup_file: str, *, expected_columns=None,
          lock_timeout='15s', statement_timeout='15min', log=print) -> dict:
    """在调用方的事务里转换（调用方提交 / 回滚）→ {columns, tables, sample}。
    expected_columns：事先核对过的列（维护脚本在备份前列过一次）；与现在的不一致就停（期间有新的迁移）。"""
    legacy = check_timezone(legacy_timezone)
    cursor.execute(f"SET LOCAL lock_timeout = {quote_literal(lock_timeout)}")
    cursor.execute(f"SET LOCAL statement_timeout = {quote_literal(statement_timeout)}")
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('wms-timestamp-to-timestamptz'))")
    check_postgres_timezone(cursor, legacy)
    if marker_exists(cursor):
        raise ConversionError(f'{MARKER_TABLE} already records {MIGRATION_KEY}; refusing a second conversion')
    columns = list_columns(cursor)
    if expected_columns is not None and list(expected_columns) != columns:
        raise ConversionError(f'timestamp columns changed since the preflight: '
                              f'missing={sorted(set(expected_columns) - set(columns))}, '
                              f'new={sorted(set(columns) - set(expected_columns))}')
    sample = sample_column(columns)
    before = None
    if sample is not None:
        table, column = map(quote_ident, sample)
        before = _scalar(cursor, f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL '
                                 f'ORDER BY {column} DESC LIMIT 1')
    for statement in alter_statements(columns, legacy):
        cursor.execute(statement)
        log(f'converted: {statement.split(" ALTER COLUMN ")[0][len("ALTER TABLE "):]}')
    cursor.execute(COLUMN_TYPES_SQL)
    types = {(table, column): data_type for table, column, data_type in cursor.fetchall()}
    left = [key for key in columns if types.get(key) != 'timestamp with time zone']
    if left:
        raise ConversionError(f'columns not converted: {left}')
    if sample is not None and before is not None:
        table, column = map(quote_ident, sample)
        found = _scalar(cursor, f'SELECT count(*) FROM {table} WHERE ({column} AT TIME ZONE %s) = %s',
                        (legacy, before))
        if not found:
            raise ConversionError(f'sample check failed: {sample[0]}.{sample[1]} = {before} in {legacy} '
                                  'is gone after the conversion')
    cursor.execute(MARKER_DDL)
    cursor.execute(MARKER_INSERT, (MIGRATION_KEY, legacy, TARGET_TIMEZONE, str(backup_file)))
    return {'columns': columns, 'tables': len(group_by_table(columns)), 'sample': sample}


# ---------------------------------------------------------------------------
# Flask CLI：flask wms convert-utc（自建实例升级用；生产走 scripts/manual/migrate_utc.py）
# ---------------------------------------------------------------------------

import click  # noqa: E402
from flask.cli import AppGroup  # noqa: E402

wms_cli = AppGroup('wms', help='WMS maintenance commands')


@wms_cli.command('convert-utc')
@click.option('--legacy-tz', 'legacy_tz', default=None,
              help='Time zone of the wall-clock values stored so far (IANA name such as Asia/Tokyo). '
                   'Default: WMS_LEGACY_TIMEZONE, else WMS_TIMEZONE.')
@click.option('--apply', 'do_apply', is_flag=True, help='Convert (without it only the plan is printed).')
@click.option('--yes', is_flag=True, help='Do not ask for confirmation (with --apply).')
@click.option('--statement-timeout', default='15min', show_default=True,
              help='PostgreSQL statement_timeout for each ALTER TABLE.')
def convert_utc_command(legacy_tz, do_apply, yes, statement_timeout):
    """Convert all timestamp columns to timestamptz (doc/timezone-utc.md, section 5)."""
    from flask import current_app
    from extensions.db import db

    engine = db.engine
    if engine.dialect.name != 'postgresql':
        click.echo(f'The database is {engine.dialect.name}, not PostgreSQL: nothing to convert '
                   '(SQLite stores times as UTC).')
        return
    configured = (current_app.config.get('WMS_LEGACY_TIMEZONE') or '').strip()
    explicit = bool(legacy_tz) or bool((current_app.config.get('WMS_TIMEZONE') or '').strip()) \
        or bool(_env('WMS_LEGACY_TIMEZONE'))
    try:
        legacy = check_timezone(legacy_tz or configured or 'UTC')
    except ConversionError as exc:
        raise click.ClickException(str(exc))
    if legacy_tz and configured and legacy != configured:
        click.echo(click.style(
            f'Warning: --legacy-tz {legacy} differs from the configured legacy time zone {configured}. Until the '
            f'conversion the application reads the old values as {configured}; set WMS_LEGACY_TIMEZONE={legacy} '
            'and restart it if the values were really written in that time zone.', fg='yellow'))

    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        result = plan(cursor, legacy)
        connection.rollback()
        if result['done']:
            click.echo(f'Already converted ({MARKER_TABLE} has {MIGRATION_KEY}).')
            return
        click.echo(f"Legacy time zone: {legacy}; {len(result['columns'])} timestamp columns in "
                   f"{result['tables']} tables:")
        for table, column in result['columns']:
            click.echo(f'  {table}.{column}')
        if not result['columns']:
            click.echo('No timestamp-without-time-zone columns: nothing to convert.')
            return
        if not do_apply:
            click.echo('\nStatements (one transaction):')
            for statement in result['statements']:
                click.echo(f'  {statement};')
            click.echo('\nDry run only. Back up the database (pg_dump -Fc), then run again with --apply.')
            return
        if not explicit:
            raise click.ClickException(
                'Neither --legacy-tz, WMS_LEGACY_TIMEZONE nor WMS_TIMEZONE is set. The stored values are the '
                'server wall clock of the old version: pass --legacy-tz explicitly (for example --legacy-tz '
                'Asia/Tokyo; --legacy-tz UTC if they really are UTC).')
        click.echo(click.style('This rewrites every table listed above in one transaction. BACK UP THE DATABASE '
                               'FIRST (pg_dump -Fc); stopping the application during the conversion is '
                               'recommended.', fg='red', bold=True))
        if not yes and not click.confirm(f'Convert {len(result["columns"])} columns from {legacy} wall clock '
                                         'to timestamptz?'):
            raise click.Abort()
        cursor = connection.cursor()
        try:
            done = apply(cursor, legacy, 'none (flask wms convert-utc)', statement_timeout=statement_timeout,
                         log=click.echo)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        click.echo(f"Converted {len(done['columns'])} columns in {done['tables']} tables (committed).")
    except ConversionError as exc:
        raise click.ClickException(str(exc))
    finally:
        connection.close()


def _env(name) -> str:
    import os
    return (os.getenv(name) or '').strip()
