import importlib.util
import os
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


@pytest.mark.skipif(not os.environ.get('WMS_TEST_DATABASE_URI'), reason='requires PostgreSQL')
@pytest.mark.parametrize('rerun_with_read_only_role', [False, True], ids=['owner', 'non_owner'])
def test_existing_postgresql_enum_types_accept_new_writes(rerun_with_read_only_role):
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/e4b7c912a6d3_legacy_postgresql_enum_values.py'
    spec = importlib.util.spec_from_file_location('legacy_enum_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(os.environ['WMS_TEST_DATABASE_URI'], poolclass=NullPool)
    with engine.connect() as conn:
        with conn.begin():
            conn.exec_driver_sql("CREATE TEMP TABLE enum_migration_probe (id integer)")
            conn.exec_driver_sql("SET search_path TO pg_temp")
            conn.exec_driver_sql("CREATE TYPE pg_temp.dn_type_enum AS ENUM ('shipping', 'return_to_supplier', 'transfer')")
            conn.exec_driver_sql("CREATE TYPE pg_temp.payment_status_enum AS ENUM ('pending', 'paid', 'canceled')")
            conn.exec_driver_sql("ALTER TABLE enum_migration_probe ADD COLUMN kind pg_temp.dn_type_enum, "
                                 "ADD COLUMN status pg_temp.payment_status_enum")
            conn.exec_driver_sql("INSERT INTO enum_migration_probe VALUES (1, 'shipping', 'paid')")

        with conn.begin(), Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        with conn.begin(), Operations.context(MigrationContext.configure(conn)):
            conn.exec_driver_sql("INSERT INTO enum_migration_probe VALUES (2, 'damage_to_supplier', 'failed')")
            migration.downgrade()
            if rerun_with_read_only_role:
                if not conn.exec_driver_sql("SELECT pg_has_role(current_user, 'pg_read_all_data', 'MEMBER')").scalar_one():
                    pytest.skip('test database user cannot switch to pg_read_all_data')
                conn.exec_driver_sql("SET LOCAL ROLE pg_read_all_data")
            migration.upgrade()
        rows = conn.exec_driver_sql("SELECT kind::text, status::text FROM enum_migration_probe ORDER BY id").all()
        assert rows == [('shipping', 'paid'), ('damage_to_supplier', 'failed')]
