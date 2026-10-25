"""库存底座的维护命令：flask stock …（doc/库存数值系统说明.md 第十节）

- init-ledger：期初。流水为空时才执行：（一期首次部署）无批次的 inventory_lot_stock ← inventory 计数器，缓存 ← 行表，
  把当前结存写成 opening 行；再把旧快照转成流水覆盖之前各天的日终结存（legacy_snapshot，只有库位库存）
- reconcile：校正。默认演练（只打印差额）；--apply 写入：缓存 ← 行表，「当前结存 − 流水合计」写成 reconcile 行。
  --sync-legacy-counters 先从 inventory 计数器重建无批次的 inventory_lot_stock（只用于一期首次部署：部署窗口里旧进程
  只改 inventory，不改新表）
- audit：对账，有问题时退出码 1
- daily：补生成日终结存 + 库存分析（同定时任务）；rebuild-daily：重建一段日期的日终结存；metrics：重算某天的分析
- replay：历史流水回放。默认演练，只输出与旧快照逐批核对的差异报告；--apply 才写入（见 warehouse/stock/replay.py）
"""
import json
import sys
from datetime import date

import click
from flask.cli import AppGroup

stock_cli = AppGroup('stock', help='Stock ledger / daily balance / reconcile commands')


def _warehouses(warehouse_id):
    from warehouse.warehouse.models import Warehouse
    query = Warehouse.query
    if warehouse_id is not None:
        query = query.filter(Warehouse.id == warehouse_id)
    return query.order_by(Warehouse.id).all()


def _print_report(report, limit=50):
    for key in ('lot_stock', 'cache', 'ledger'):
        rows = report[key]
        click.echo(f"{key}: {len(rows)}")
        for row in rows[:limit]:
            click.echo(f"  {row}")
        if len(rows) > limit:
            click.echo(f"  … {len(rows) - limit} more")


@stock_cli.command('init-ledger')
@click.option('--warehouse', 'warehouse_id', type=int, default=None, help='Only this warehouse')
@click.option('--no-legacy-snapshots', is_flag=True, help='Do not convert inventory_snapshot into daily balances')
def init_ledger_command(warehouse_id, no_legacy_snapshots):
    """期初：流水为空时，按当前库存写 opening 行（幂等：已有流水就跳过）"""
    from extensions.db import db
    from .audit import reconcile
    from .history import import_legacy_snapshots
    from .models import StockLedger
    query = db.session.query(StockLedger.id)
    if warehouse_id is not None:
        query = query.filter(StockLedger.warehouse_id == warehouse_id)
    if query.first() is not None:
        click.echo('stock ledger is not empty; init-ledger skipped')
        return
    report = reconcile(warehouse_id, apply=True, sync_legacy_counters=True, movement_type='opening',
                       remark='opening balance')
    click.echo(f"opening lines: {len(report['ledger'])}; lot stock synced from counters: {len(report['lot_stock'])}; "
               f"cache fixed: {len(report['cache'])}")
    for row in report['cache'][:50]:
        click.echo(f"  cache {row}")
    if not no_legacy_snapshots:
        click.echo(f"legacy snapshot days: {import_legacy_snapshots(warehouse_id)}")


@stock_cli.command('reconcile')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
@click.option('--apply', 'apply_', is_flag=True, help='Write the corrections (default: dry run)')
@click.option('--sync-legacy-counters', is_flag=True,
              help='First rebuild no-lot inventory_lot_stock rows from the inventory counters (first phase-1 deploy)')
def reconcile_command(warehouse_id, apply_, sync_legacy_counters):
    """校正：缓存 ← 行表，流水 ← 当前结存（reconcile 行）。默认演练"""
    from .audit import reconcile
    report = reconcile(warehouse_id, apply=apply_, sync_legacy_counters=sync_legacy_counters)
    click.echo(f"reconcile ({'applied' if apply_ else 'dry run'})")
    _print_report(report)


@stock_cli.command('audit')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
@click.option('--no-chain', is_flag=True, help='Skip the per-line balance chain check')
def audit_command(warehouse_id, no_chain):
    """对账：缓存 = 行表、流水合计 = 结存、流水链连续；有问题退出码 1"""
    from .audit import audit_stock
    problems = audit_stock(warehouse_id, check_chain=not no_chain)
    click.echo(f"problems: {len(problems)}")
    for problem in problems[:200]:
        click.echo(f"  {problem}")
    if problems:
        sys.exit(1)


@stock_cli.command('daily')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
def daily_command(warehouse_id):
    """补生成日终结存与库存分析（同定时任务）"""
    from .jobs import run_stock_daily
    click.echo(json.dumps(run_stock_daily(warehouse_id), ensure_ascii=False))


@stock_cli.command('rebuild-daily')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
@click.option('--from', 'date_from', required=True, help='YYYY-MM-DD (warehouse local date)')
@click.option('--to', 'date_to', required=True, help='YYYY-MM-DD')
def rebuild_daily_command(warehouse_id, date_from, date_to):
    """重建 [from, to] 的日终结存（流水覆盖之前的日子保留旧快照转换的行）"""
    from .history import rebuild
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    for warehouse in _warehouses(warehouse_id):
        click.echo(f"warehouse {warehouse.id}: {rebuild(warehouse.id, start, end)} day(s)")


@stock_cli.command('metrics')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
@click.option('--date', 'day', default=None, help='YYYY-MM-DD (default: warehouse local yesterday)')
def metrics_command(warehouse_id, day):
    """重算某天的库存分析"""
    from datetime import timedelta
    from extensions.db import db
    from warehouse.common import clock
    from .metrics import generate_metrics
    for warehouse in _warehouses(warehouse_id):
        target = date.fromisoformat(day) if day else clock.local_today(warehouse) - timedelta(days=1)
        count = generate_metrics(warehouse.id, target)
        db.session.commit()
        click.echo(f"warehouse {warehouse.id} {target}: {count} row(s)")


@stock_cli.command('replay')
@click.option('--warehouse', 'warehouse_id', type=int, default=None)
@click.option('--apply', 'apply_', is_flag=True, help='Write the derived ledger (default: dry run, report only)')
@click.option('--report', 'report_path', default=None, help='Write the full JSON report to this file')
def replay_command(warehouse_id, apply_, report_path):
    """历史流水回放：从单据回放 T0（期初）之前的流水并与旧快照逐批核对。默认演练"""
    from .replay import replay
    reports = []
    for warehouse in _warehouses(warehouse_id):
        report = replay(warehouse.id, apply=apply_)
        reports.append(report)
        click.echo(
            f"warehouse {warehouse.id}: events {report['events']} (first {report['first_event_at']}), "
            f"negative balances {report['negative_balances']}, snapshots {report['snapshots_compared']} "
            f"(mismatched {report['snapshots_mismatched']}, max abs diff {report['snapshot_max_abs_diff']}), "
            f"final diff buckets {report['final_diff_buckets']} (abs {report['final_abs_diff']}), "
            f"applied {report['applied']}")
        for sample in report['final_diff_samples'][:10]:
            click.echo(f"  final diff {sample}")
        worst = sorted(report['snapshots'], key=lambda s: -s['abs_diff'])[:5]
        for snapshot in worst:
            if snapshot['abs_diff']:
                click.echo(f"  snapshot {snapshot['balance_date']}: rows {snapshot['rows']}, "
                           f"mismatched {snapshot['mismatched_rows']}, abs diff {snapshot['abs_diff']}")
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as handle:
            json.dump(reports, handle, ensure_ascii=False, indent=1, default=str)
        click.echo(f"report written to {report_path}")
