"""库存快照 CLI 命令"""
import click
from flask.cli import AppGroup

from .snapshot import run_inventory_snapshot

snapshot_cli = AppGroup('snapshot', help='Inventory snapshot commands')


@snapshot_cli.command('run')
def snapshot_run_command():
    """为所有仓库创建库存快照（可由定时任务调用）"""
    result = run_inventory_snapshot()
    click.echo(result)
