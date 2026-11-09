"""Verify the complete refactor upgrade on a disposable PostgreSQL database.

The baseline is the committed pre-refactor release, not today's create_all().
Requires full git history and the same production configuration as migration_roundtrip.py.
SQLALCHEMY_DATABASE_URI must name an EMPTY database starting with wms_migration_.
No schemas or databases are dropped. Use a fresh database for each run.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from sqlalchemy import create_engine, inspect, MetaData, select, Table
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'f395c9bebc8b36c39443604f5ff32d36846d0fa1'

# Run in a separate interpreter so old/new SQLAlchemy metadata never mix.
PREPARE = r'''
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from sqlalchemy import select
from flask_migrate import stamp
from app import app
from extensions import db
from system.user.models import User, Role, Permission
from warehouse.company.models import Company
from warehouse.warehouse.models import Warehouse
from warehouse.location.models import Location
from warehouse.supplier.models import Supplier
from warehouse.goods.models import Goods
from warehouse.asn.models import ASN, ASNDetail
from warehouse.stock.service import Leg, StockOp

with app.app_context():
    db.create_all()
    user = User(user_name='migration-user', email='migration@example.invalid')
    role = Role(name='migration-role')
    permission = Permission(name='company_roles_read', description='Existing permission')
    role.permissions.append(permission)
    user.roles.append(role)
    db.session.add(user)
    db.session.flush()
    company = Company(name='Migration company', created_by=user.id)
    db.session.add(company)
    db.session.flush()
    warehouse = Warehouse(name='Migration warehouse', company_id=company.id, created_by=user.id)
    supplier = Supplier(name='Migration supplier', company_id=company.id, created_by=user.id)
    goods = Goods(code='MIGRATION-001', name='Migration goods', company_id=company.id,
                  unit='pcs', weight=1.0, created_by=user.id)
    db.session.add_all([warehouse, supplier, goods])
    db.session.flush()
    location = Location(code='MIGRATION-A01', warehouse_id=warehouse.id, created_by=user.id)
    asn = ASN(warehouse_id=warehouse.id, supplier_id=supplier.id, created_by=user.id)
    db.session.add_all([location, asn])
    db.session.flush()
    db.session.add(ASNDetail(asn_id=asn.id, goods_id=goods.id, quantity=5,
                             actual_quantity=0, created_by=user.id))
    db.session.commit()
    operation = StockOp(warehouse.id, 'reconcile', remark='Migration fixture')
    operation.move(goods.id, 5, dst=Leg.at(location.id))
    operation.commit()
    db.session.commit()
    stamp(revision='head')
    snapshot = {}
    with db.engine.connect() as conn:
        for table in db.metadata.tables.values():
            rows = [dict(row) for row in conn.execute(select(table)).mappings()]
            if rows:
                snapshot[table.name] = rows
    def serialize(value):
        if isinstance(value, datetime) and value.tzinfo:
            return value.astimezone(timezone.utc).isoformat()
        return str(value)
    Path(sys.argv[1]).write_text(json.dumps(snapshot, default=serialize), encoding='utf-8')
    print('Baseline ready: %d populated tables' % len(snapshot), flush=True)
'''


def serialize(value):
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default=BASELINE)
    args = parser.parse_args()
    uri = os.environ.get('SQLALCHEMY_DATABASE_URI', '')
    url = make_url(uri)
    if url.get_backend_name() != 'postgresql' or not (url.database or '').startswith('wms_migration_'):
        raise SystemExit('Use an empty PostgreSQL database named wms_migration_*')
    engine = create_engine(url)
    try:
        if inspect(engine).get_table_names() or inspect(engine).get_view_names():
            raise SystemExit('Refusing to modify a nonempty database; create a fresh test database')
    finally:
        engine.dispose()

    baseline = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', args.baseline_ref + '^{commit}'],
        cwd=ROOT, text=True,
    ).strip()
    print('Baseline commit:', baseline, flush=True)
    os.environ['SCHEDULER_ENABLED'] = 'False'
    os.environ['FLASK_ENV'] = 'production'
    os.environ['CHECK_BLACKLIST'] = 'False'
    os.environ['CHECK_WHITELIST'] = 'False'
    with tempfile.TemporaryDirectory(prefix='wms-migration-') as directory:
        work = Path(directory)
        archive = work / 'baseline.zip'
        subprocess.run(['git', 'archive', '--format=zip', '-o', str(archive), baseline],
                       cwd=ROOT, check=True)
        checkout = work / 'baseline'
        with zipfile.ZipFile(archive) as source:
            source.extractall(checkout)
        snapshot_file = work / 'snapshot.json'
        subprocess.run([sys.executable, '-c', PREPARE, str(snapshot_file)],
                       cwd=checkout, check=True)
        snapshot = json.loads(snapshot_file.read_text(encoding='utf-8'))

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from flask_migrate import upgrade
    from app import app
    from extensions import db

    with app.app_context():
        extension = app.extensions['migrate']
        script = ScriptDirectory.from_config(extension.migrate.get_config(extension.directory))
        with db.engine.connect() as conn:
            before = MigrationContext.configure(conn).get_current_revision()
        revisions = list(script.iterate_revisions('head', before))
        print(f'Upgrading {before} -> {script.get_current_head()} ({len(revisions)} migrations)', flush=True)
        upgrade(revision='head')
        upgrade(revision='head')  # deployment retries must be a no-op
        with db.engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={'compare_type': True})
            assert context.get_current_revision() == script.get_current_head()
            diffs = compare_metadata(context, db.metadata)
            for diff in diffs:
                print('DIFF', diff)
            if diffs:
                raise SystemExit('Upgraded schema differs from current models')
            metadata = MetaData()
            for name, rows in snapshot.items():
                table = Table(name, metadata, autoload_with=conn)
                columns = list(rows[0])
                actual = [json.loads(json.dumps(dict(row), default=serialize)) for row in
                          conn.execute(select(*(table.c[key] for key in columns))).mappings()]
                for row in rows:
                    assert row in actual, f'Existing row changed or lost in {name}: {row}'
        from warehouse.stock.audit import audit_stock
        assert not audit_stock(), 'Stock audit failed after migration'
        from system.user.seed import seed_builtin
        seed_builtin()
        repeated = seed_builtin()
        assert not any(repeated.values()), 'Permission seeding must be idempotent'
        assert app.test_client().get('/health').status_code == 200
        print('Full migration upgrade OK; existing rows preserved; stock audit clean', flush=True)


if __name__ == '__main__':
    main()
