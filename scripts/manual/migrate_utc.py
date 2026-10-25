"""One-time conversion of the production WMS time columns to timestamptz (doc/timezone-utc.md §5).

Run only through the manual GitHub Actions workflow "Migrate WMS Time to UTC" after the backend release
that contains the UTCDateTime columns (extensions/db.py) and the warehouse time zone migration has passed
its normal deployment. That code works before and after the conversion: unconverted timestamp columns are
read as the legacy wall clock (WMS_LEGACY_TIMEZONE -> WMS_TIMEZONE), so nothing changes for users.

--dry-run: checks only (code version, health, schema, legacy time zone) and prints the statements.
--apply:   stops wms-api, makes a verified pg_dump backup, converts every public timestamp-without-time-zone
           column in one transaction (ALTER COLUMN ... TYPE timestamptz USING col AT TIME ZONE '<legacy>'),
           records the conversion in wms_timezone_migrations, restarts the API and checks /health.

Failure handling (as in doc/timezone-jst.md): before the transaction commits nothing changed -> restart the
API on the same code. After the commit the conversion is never rolled back automatically; the code works on
the converted schema, so the API is restarted either way. The marker row refuses a second run.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

APP_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_DIR))

import psycopg2  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

import timezone_conversion as conversion  # noqa: E402

ENV_FILE = APP_DIR / '.env'
BACKUP_DIR = Path('/var/www/wms-backups')
MIGRATIONS_DIR = APP_DIR / 'migrations' / 'versions'
# The release that introduced UTCDateTime and warehouses.timezone; the database must be at least there.
REQUIRED_REVISION = 'd4f2a8c6e1b7'


def say(message: str) -> None:
    print(f'[utc] {message}', flush=True)


def checked(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=APP_DIR, env=env, check=True)


def env_values() -> dict:
    return {key: (value or '').strip() for key, value in dotenv_values(ENV_FILE).items()}


def database_settings() -> tuple[str, dict[str, str]]:
    uri = env_values().get('SQLALCHEMY_DATABASE_URI')
    if not uri or not uri.startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('production PostgreSQL URI is missing')
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    pg_env = os.environ.copy()
    pg_env.update(
        PGHOST=parsed.hostname or '',
        PGPORT=str(parsed.port or 5432),
        PGUSER=unquote(parsed.username or ''),
        PGPASSWORD=unquote(parsed.password or ''),
        PGDATABASE=(parsed.path or '').lstrip('/'),
        PGCONNECT_TIMEOUT='5',
    )
    return uri, pg_env


def legacy_timezone_from_env() -> str:
    """The wall clock the stored values were written in: WMS_LEGACY_TIMEZONE, else WMS_TIMEZONE (.env)."""
    values = env_values()
    name = values.get('WMS_LEGACY_TIMEZONE') or values.get('WMS_TIMEZONE')
    if not name:
        raise RuntimeError('neither WMS_LEGACY_TIMEZONE nor WMS_TIMEZONE is set in .env; the legacy wall clock '
                           'is unknown')
    return conversion.check_timezone(name)


def migration_heads() -> set[str]:
    """Alembic head revisions of the checked-out code (revisions nobody revises)."""
    revisions, parents = set(), set()
    for path in MIGRATIONS_DIR.glob('*.py'):
        text = path.read_text(encoding='utf-8')
        revision = re.search(r"^revision\s*=\s*['\"]([0-9a-f]+)['\"]", text, re.M)
        down = re.search(r"^down_revision\s*=\s*(.+)$", text, re.M)
        if revision:
            revisions.add(revision.group(1))
        if down:
            parents.update(re.findall(r"['\"]([0-9a-f]+)['\"]", down.group(1)))
    return revisions - parents


def check_code() -> None:
    """The checked-out code must contain the compatible release (UTCDateTime + REQUIRED_REVISION)."""
    if 'class UTCDateTime' not in (APP_DIR / 'extensions' / 'db.py').read_text(encoding='utf-8'):
        raise RuntimeError('the deployed code has no UTCDateTime columns; deploy the time zone release first')
    if not any(MIGRATIONS_DIR.glob(f'{REQUIRED_REVISION}_*.py')):
        raise RuntimeError(f'migration {REQUIRED_REVISION} is missing from the deployed code')


def inspect_database(uri: str, legacy: str) -> list[tuple[str, str]]:
    """Read-only checks → the columns to convert."""
    heads = migration_heads()
    with psycopg2.connect(uri, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT version_num FROM alembic_version')
            versions = {row[0] for row in cur.fetchall()}
            if versions != heads:
                raise RuntimeError(f'database migrations {sorted(versions)} differ from the code heads '
                                   f'{sorted(heads)}; run the normal deployment first')
            cur.execute('SHOW TIMEZONE')
            session_timezone = cur.fetchone()[0]
            conversion.check_postgres_timezone(cur, legacy)
            result = conversion.plan(cur, legacy)
        conn.rollback()
    if result['done']:
        raise RuntimeError(f'{conversion.MARKER_TABLE} already records {conversion.MIGRATION_KEY}; '
                           'refusing a second run')
    say(f'preflight: database session time zone {session_timezone}; legacy wall clock {legacy}; '
        f"{len(result['columns'])} timestamp columns in {result['tables']} tables")
    return result['columns']


def preflight_application(legacy: str) -> None:
    """The staged application must read the legacy wall clock in the same time zone as the conversion."""
    environment = os.environ.copy()
    environment.update(FLASK_ENV='production', SCHEDULER_ENABLED='False')
    command = (
        'import sys; from sqlalchemy import text; from app import app; from extensions import db; '
        'ctx=app.app_context(); ctx.push(); '
        'legacy=app.config["WMS_LEGACY_TIMEZONE"]; '
        f'assert legacy=={legacy!r}, "application legacy time zone is %s" % legacy; '
        f'assert db.session.execute(text("SHOW TIMEZONE")).scalar()=={legacy!r}; '
        'ctx.pop()'
    )
    checked([sys.executable, '-c', command], env=environment)
    say(f'preflight: the application reads legacy values as {legacy} and its sessions use {legacy}')


def health_ok(url: str, expected_commit: str) -> bool:
    try:
        with urlopen(url, timeout=5) as response:
            import json
            payload = json.load(response)
            return (response.status == 200 and payload.get('status') == 'ok'
                    and payload.get('commit') == expected_commit)
    except (OSError, ValueError, URLError):
        return False


def backup(pg_env: dict[str, str]) -> Path:
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    target = BACKUP_DIR / ('wms-before-utc-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.dump')
    # An offline backup is the recovery point. Keep it outside the Git checkout and only readable by the
    # deploy account.
    checked(['pg_dump', '-Fc', '--no-owner', '--no-privileges', '-f', str(target)], env=pg_env)
    os.chmod(target, 0o600)
    digest_hash = hashlib.sha256()
    with target.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest_hash.update(block)
    subprocess.run(['pg_restore', '--list', str(target)], stdout=subprocess.DEVNULL, check=True)
    checked(['pg_restore', '--file', os.devnull, str(target)])
    say(f'backup verified: {target} ({target.stat().st_size} bytes, sha256={digest_hash.hexdigest()})')
    return target


def convert(uri: str, legacy: str, backup_file: Path, columns, statement_timeout: str) -> None:
    with psycopg2.connect(uri, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            result = conversion.apply(cur, legacy, str(backup_file), expected_columns=columns,
                                      statement_timeout=statement_timeout, log=say)
    # Leaving the psycopg2 connection block commits the transaction (or rolls back on an exception).
    say(f"database conversion committed: {len(result['columns'])} columns in {result['tables']} tables, "
        f"sample {result['sample']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='checks and the statements only')
    mode.add_argument('--apply', action='store_true', help='stop the API, back up, convert, restart')
    parser.add_argument('--health-url', default='http://127.0.0.1:5002/health')
    parser.add_argument('--statement-timeout', default='15min')
    args = parser.parse_args()
    if os.name != 'posix':
        raise RuntimeError('run the maintenance command on the Linux API server')
    for binary in ('pm2', 'pg_dump', 'pg_restore') if args.apply else ():
        if shutil.which(binary) is None:
            raise RuntimeError(f'{binary} is unavailable')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'],
                               cwd=APP_DIR).strip():
        raise RuntimeError('tracked server files have local modifications')
    expected_commit = subprocess.check_output(['git', 'rev-parse', '--short=7', 'HEAD'],
                                              cwd=APP_DIR, text=True).strip()
    check_code()
    if not health_ok(args.health_url, expected_commit):
        raise RuntimeError('existing API health check failed (or it runs another commit); nothing changed')
    legacy = legacy_timezone_from_env()
    uri, pg_env = database_settings()
    columns = inspect_database(uri, legacy)
    preflight_application(legacy)
    if not columns:
        say('no timestamp-without-time-zone columns: nothing to convert')
        return
    if args.dry_run:
        for table, column in columns:
            say(f'column: {table}.{column}')
        for statement in conversion.alter_statements(columns, legacy):
            say(f'statement: {statement};')
        say('dry run finished; nothing changed')
        return

    stopped = False
    migrated = False
    try:
        say('stopping wms-api for the maintenance window')
        stopped = True
        checked(['pm2', 'stop', 'wms-api'])
        target = backup(pg_env)
        columns_now = inspect_database(uri, legacy)
        if columns_now != columns:
            raise RuntimeError('timestamp columns changed while stopping the API; nothing changed')
        convert(uri, legacy, target, columns, args.statement_timeout)
        migrated = True
        checked(['pm2', 'restart', 'wms-api'])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if health_ok(args.health_url, expected_commit):
                say('API health check passed on the converted schema')
                stopped = False
                break
            time.sleep(3)
        else:
            raise RuntimeError('API did not pass its health check within 90 seconds')
    finally:
        if stopped:
            if not migrated:
                say('database was not converted; restarting the API on the unchanged schema')
            else:
                # Committed: never undo automatically. The code works on the converted schema.
                say('database conversion is committed; restarting the API (check pm2 logs and the backup)')
            try:
                checked(['pm2', 'restart', 'wms-api'])
            except subprocess.CalledProcessError:
                say('pm2 restart failed; manual recovery is required')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, conversion.ConversionError) as exc:
        say(f'!!! {exc}')
        sys.exit(1)
