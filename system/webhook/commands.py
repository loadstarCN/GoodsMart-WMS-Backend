"""Webhook CLI 命令"""
import click
from flask.cli import AppGroup

from .services import push_pending_events

webhook_cli = AppGroup('webhook', help='Webhook management commands')


@webhook_cli.command('push')
def push_command():
    """推送待发送的 Webhook 事件（定时任务每分钟执行）"""
    sent, failed = push_pending_events()
    click.echo(f'Webhook push complete: {sent} sent, {failed} failed')
