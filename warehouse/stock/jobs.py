"""库存的定时任务（scheduler.py 每小时一次；也可 POST /tasks/task/inventory_snapshot、flask stock daily 手动跑）：
1. 日终结存：各仓库「当地已过 0 点、前一天还没生成」的补生成（缺几天补几天）
2. 库存分析：当地昨天的汇总还没生成就生成
3. 对账：本轮生成了新的日终结存时跑一次（只记日志告警，不自动修正；修正用 flask stock reconcile --apply）
"""
import logging
import time

logger = logging.getLogger(__name__)


def run_stock_daily(warehouse_id=None, audit=True) -> dict:
    from .audit import audit_stock
    from .history import generate_missing
    from .metrics import generate_missing_metrics
    started = time.time()
    days = generate_missing(warehouse_id)
    metrics = generate_missing_metrics(warehouse_id)
    problems = None
    if audit and days:
        problems = audit_stock(warehouse_id)
        if problems:
            logger.warning('[StockAudit] %d problem(s): %s', len(problems), problems[:20])
    return {
        'daily_balances': {str(w): [d.isoformat() for d in ds] for w, ds in days.items()},
        'metrics': {str(w): d.isoformat() for w, d in metrics.items()},
        'audit_problems': None if problems is None else len(problems),
        'seconds': round(time.time() - started, 2),
    }
