"""APScheduler 定时任务模块

内嵌在 Flask 进程中运行，无需 cron 或外部任务队列。
Gunicorn 多 worker 环境下通过文件锁确保只有一个 worker 启动 scheduler。
"""
import os
import logging

from flask_apscheduler import APScheduler

logger = logging.getLogger(__name__)

scheduler = APScheduler()


def init_scheduler(app):
    """初始化并启动定时任务调度器"""
    app.config['SCHEDULER_API_ENABLED'] = False

    webhook_interval = int(os.getenv('WEBHOOK_PUSH_INTERVAL_MINUTES', '30'))
    snapshot_hour = int(os.getenv('SNAPSHOT_HOUR', '2'))
    snapshot_minute = int(os.getenv('SNAPSHOT_MINUTE', '0'))

    app.config['JOBS'] = [
        {
            'id': 'webhook_push',
            'func': 'scheduler:_job_webhook_push',
            'trigger': 'interval',
            'minutes': webhook_interval,
            'misfire_grace_time': 60,
        },
        {
            'id': 'inventory_snapshot',
            'func': 'scheduler:_job_inventory_snapshot',
            'trigger': 'cron',
            'hour': snapshot_hour,
            'minute': snapshot_minute,
            'misfire_grace_time': 3600,
        },
    ]

    scheduler.init_app(app)
    _try_start(app)


def _try_start(app):
    """启动 scheduler，多 worker 环境用文件锁防止重复启动"""
    is_debug = os.getenv('FLASK_ENV', 'production') == 'development'
    if is_debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return  # Werkzeug reloader 父进程不启动

    # 多进程环境（Gunicorn）用文件锁
    try:
        import fcntl
        lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.scheduler.lock')
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app._scheduler_lock_fd = lock_fd
    except ImportError:
        pass  # Windows 无 fcntl，单进程运行无需锁
    except (IOError, OSError):
        logger.info('[Scheduler] Skipped (another worker owns it)')
        return

    try:
        scheduler.start()
        logger.info('[Scheduler] Started — webhook push every %s min, snapshot at %s:%s',
                     os.getenv('WEBHOOK_PUSH_INTERVAL_MINUTES', '1'),
                     os.getenv('SNAPSHOT_HOUR', '2'),
                     os.getenv('SNAPSHOT_MINUTE', '0'))
    except Exception as e:
        logger.warning(f'[Scheduler] Failed to start: {e}')


def _job_webhook_push():
    """定时任务：推送待发送的 Webhook 事件"""
    app = scheduler.app
    if app is None:
        return
    with app.app_context():
        try:
            from system.webhook.services import push_pending_events
            sent, failed = push_pending_events()
            if sent or failed:
                logger.info(f'[Scheduler] Webhook push: {sent} sent, {failed} failed')
        except Exception as e:
            logger.error(f'[Scheduler] Webhook push failed: {e}')


def _job_inventory_snapshot():
    """定时任务：为所有仓库创建库存快照"""
    app = scheduler.app
    if app is None:
        return
    with app.app_context():
        try:
            from tasks.snapshot import run_inventory_snapshot
            result = run_inventory_snapshot()
            logger.info(f'[Scheduler] {result}')
        except Exception as e:
            logger.error(f'[Scheduler] Inventory snapshot failed: {e}')
