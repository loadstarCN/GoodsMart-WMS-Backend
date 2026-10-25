"""迁移往返检查（.github/workflows/test-postgres.yml 用；本机也可跑）：

空库 create_all（当前模型）→ stamp head → downgrade 到上一个修订 → upgrade head → 与模型比对（alembic compare_metadata），
有差异退出码 1。迁移链不是从空库开始的（基础表靠 create_all），所以只验证最新一个修订的降级与升级。
完整验证（旧结构的库升级）用 git worktree 检出上一版本建库、stamp 到上一修订再 upgrade（见 doc/库存数值系统说明.md 第十三节）。

用法：SQLALCHEMY_DATABASE_URI=postgresql://… FLASK_ENV=production（加生产配置必填的环境变量）python scripts/manual/migration_roundtrip.py
会清空目标库的 public schema（PostgreSQL）——只对一次性的测试库用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('SCHEDULER_ENABLED', 'False')

from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext     # noqa: E402
from alembic.script import ScriptDirectory         # noqa: E402
from flask_migrate import downgrade, stamp, upgrade  # noqa: E402
from sqlalchemy import text                         # noqa: E402

import app as app_module                            # noqa: E402
from extensions import db                           # noqa: E402

app = app_module.app

with app.app_context():
    if db.engine.dialect.name == 'postgresql':
        with db.engine.begin() as conn:
            conn.execute(text('DROP SCHEMA public CASCADE'))
            conn.execute(text('CREATE SCHEMA public'))
    db.create_all()
    migrate_ext = app.extensions['migrate']
    script = ScriptDirectory.from_config(migrate_ext.migrate.get_config(migrate_ext.directory))
    head = script.get_current_head()
    previous = script.get_revision(head).down_revision
    print(f'head {head}, previous {previous}')
    stamp(revision='head')
    downgrade(revision=previous)
    upgrade(revision='head')
    with db.engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts={'compare_type': True}), db.metadata)
    for diff in diffs:
        print('DIFF', diff)
    if diffs:
        sys.exit(1)
    print('migration round trip ok')
