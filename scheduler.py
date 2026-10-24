"""APScheduler 定时任务模块

内嵌在 Flask 进程中运行，无需 cron 或外部任务队列。
Gunicorn 多 worker 环境下通过文件锁确保只有一个 worker 启动 scheduler。
"""
import os
import logging

from flask_apscheduler import APScheduler

from config import Config

logger = logging.getLogger(__name__)

scheduler = APScheduler()


def init_scheduler(app):
    """初始化并启动定时任务调度器"""
    app.config['SCHEDULER_API_ENABLED'] = False

    # 默认每分钟一次（与 .env.example、README 一致）
    webhook_interval = int(os.getenv('WEBHOOK_PUSH_INTERVAL_MINUTES', '1'))
    snapshot_hour = int(os.getenv('SNAPSHOT_HOUR', '2'))
    snapshot_minute = int(os.getenv('SNAPSHOT_MINUTE', '0'))
    # 承运商跟踪（自动完成发货 / 签收），默认每 30 分钟；0 = 不跑
    tracking_interval = int(os.getenv('CARRIER_TRACKING_INTERVAL_MINUTES', '30'))
    # 自动视为签收（carriers.auto_sign_days，没有 API 跟踪的运单），默认每 60 分钟；0 = 不跑
    auto_sign_interval = int(os.getenv('CARRIER_AUTO_SIGN_INTERVAL_MINUTES', '60'))

    app.config['JOBS'] = [
        {
            'id': 'webhook_push',
            'func': 'scheduler:_job_webhook_push',
            'trigger': 'interval',
            'minutes': webhook_interval,
            'misfire_grace_time': 60,
            'kwargs': {'time_budget': _webhook_time_budget(webhook_interval)},
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
    if tracking_interval > 0:
        app.config['JOBS'].append({
            'id': 'carrier_tracking',
            'func': 'scheduler:_job_carrier_tracking',
            'trigger': 'interval',
            'minutes': tracking_interval,
            'misfire_grace_time': 300,
        })
    if auto_sign_interval > 0:
        app.config['JOBS'].append({
            'id': 'carrier_auto_sign',
            'func': 'scheduler:_job_carrier_auto_sign',
            'trigger': 'interval',
            'minutes': auto_sign_interval,
            'misfire_grace_time': 600,
        })

    scheduler.init_app(app)
    _try_start(app)


def _try_start(app):
    """启动 scheduler，多 worker 环境用文件锁防止重复启动"""
    # 与 config.py 用同一个 FLASK_ENV 判定（默认 development），否则本地不设 env 时
    # config 走开发模式而这里按生产处理，reloader 父子进程会各启动一份调度器
    is_debug = Config.FLASK_ENV == 'development'
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
        jobs = {job['id']: job for job in app.config.get('JOBS', [])}
        logger.info('[Scheduler] Started — webhook push every %s min, snapshot at %s:%02d',
                     jobs.get('webhook_push', {}).get('minutes', '?'),
                     jobs.get('inventory_snapshot', {}).get('hour', '?'),
                     int(jobs.get('inventory_snapshot', {}).get('minute', 0)))
    except Exception as e:
        logger.warning(f'[Scheduler] Failed to start: {e}')


def _webhook_time_budget(interval_minutes):
    """每轮 webhook 推送的时间预算（秒）：推送间隔减去单次推送总时限与余量，不低于默认预算。
    任务 max_instances=1，本轮拖过下一次触发时间的话下一轮会被跳过"""
    from system.webhook.services import PUSH_TIME_BUDGET_SECONDS, SEND_DEADLINE_SECONDS
    return max(PUSH_TIME_BUDGET_SECONDS, interval_minutes * 60 - SEND_DEADLINE_SECONDS - 15)


def _job_webhook_push(time_budget=None):
    """定时任务：推送待发送的 Webhook 事件"""
    app = scheduler.app
    if app is None:
        return
    with app.app_context():
        try:
            from system.webhook.services import push_pending_events
            sent, failed = push_pending_events(time_budget=time_budget)
            if sent or failed:
                logger.info(f'[Scheduler] Webhook push: {sent} sent, {failed} failed')
        except Exception as e:
            logger.error(f'[Scheduler] Webhook push failed: {e}')


def _job_carrier_tracking():
    """定时任务：查有效自动运单的承运商跟踪，自动完成发货 / 签收（warehouse/dn/tracking_services.py）"""
    app = scheduler.app
    if app is None:
        return
    with app.app_context():
        try:
            from warehouse.dn.tracking_services import CarrierTrackingService
            stats = CarrierTrackingService.sync()
            if stats['tracked'] or stats['errors']:
                logger.info(f'[Scheduler] Carrier tracking: {stats}')
        except Exception as e:
            logger.error(f'[Scheduler] Carrier tracking failed: {e}')


def _job_carrier_auto_sign():
    """定时任务：交承运商后 N 天（carriers.auto_sign_days）还没签收、没有 API 跟踪的发货任务按规则签收
    （warehouse/shipping/auto_sign.py）"""
    app = scheduler.app
    if app is None:
        return
    with app.app_context():
        try:
            from warehouse.shipping.auto_sign import AutoSignService
            stats = AutoSignService.sync()
            if stats['signed'] or stats['errors']:
                logger.info(f'[Scheduler] Carrier auto sign: {stats}')
        except Exception as e:
            logger.error(f'[Scheduler] Carrier auto sign failed: {e}')


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
