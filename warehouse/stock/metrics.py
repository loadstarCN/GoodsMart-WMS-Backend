"""库存分析：库龄、近 N 天出库、周转天数、ABC、呆滞（夜间汇总 inventory_metrics_daily；doc/库存数值系统说明.md 第十一节）。

口径（2026-10-25 定，常量集中在这里，以后做成配置）：
- 出库统计（近 7 / 30 / 90 天出库、周转、ABC、最近出库）按「发货完成」（ship）计，见 flows.py
- 库龄：先进先出估算——把日终的仓内实物（total）看成「最近的若干入库层」，从最新一层往回累加凑够数量；
  入库层 = 分拣完成 + 盘盈；凑不够的部分记「库龄未知」（流水 / 单据之前就在仓库里的货）
- 库龄档位 0–30 / 31–90 / 91–180 / 180+ 天（AGE_BUCKETS）
- 呆滞 = 有库存、近 90 天没有出库、且最老一层库龄 > 90 天（DEAD_IDLE_DAYS / DEAD_AGE_DAYS；库龄未知的部分按
  仓库最早一笔入库的库龄算）
- ABC：本仓库各商品近 90 天出库量从大到小累计，累计占比到 70% 为 A、到 90% 为 B、其余 C（加上这一件之前的累计
  不到阈值就归入该档）；近 90 天没有出库的为空
- 周转天数 = 近 30 天日均库存（日终 total）÷ 近 30 天日均出库；可售天数 = 可售 ÷ 近 30 天日均出库；没有出库为空
汇总接在日终结存之后（每小时检查一次，当地前一天的结存生成了、汇总还没生成就算），保留 METRICS_RETENTION_DAYS 天。
"""
import math
from collections import defaultdict
from datetime import timedelta

from extensions.db import db
from warehouse.common import clock
from .flows import inbound_events, outbound_events
from .models import InventoryMetricsDaily

# 库龄档位：(下限, 上限（含，None = 不封顶）, 汇总表的列)
AGE_BUCKETS = ((0, 30, 'age_0_30'), (31, 90, 'age_31_90'), (91, 180, 'age_91_180'), (181, None, 'age_over_180'))
DEAD_IDLE_DAYS = 90         # 近 N 天没有出库
DEAD_AGE_DAYS = 90          # 且最老一层库龄 > N 天 → 呆滞
ABC_WINDOW_DAYS = 90
ABC_THRESHOLDS = (('A', 0.70), ('B', 0.90))    # 其余 C
TURNOVER_WINDOW_DAYS = 30
OUT_WINDOWS = (7, 30, 90)
METRICS_RETENTION_DAYS = 400


def _age_days(day, moment, warehouse_id):
    return (day - clock.local_date(moment, warehouse_id)).days


def age_layers(total, layers):
    """先进先出：layers 为 [(时刻, 数量)]（任意顺序），total 件由最近的若干层组成 → ([(时刻, 数量)], 凑不够的件数)"""
    remaining = max(0, int(total or 0))
    used = []
    for moment, quantity in sorted(layers, key=lambda item: item[0], reverse=True):
        if remaining <= 0:
            break
        take = min(quantity, remaining)
        if take > 0:
            used.append((moment, take))
            remaining -= take
    return used, remaining


def abc_classes(out_by_goods: dict) -> dict:
    """{goods_id: 近 90 天出库量} → {goods_id: (名次, 'A' / 'B' / 'C')}（出库量为 0 的不排）"""
    ranked = sorted(((g, q) for g, q in out_by_goods.items() if q > 0), key=lambda item: (-item[1], item[0]))
    total = sum(q for _g, q in ranked)
    result = {}
    cumulative = 0
    for rank, (goods_id, quantity) in enumerate(ranked, start=1):
        share_before = cumulative / total if total else 0
        label = 'C'
        for name, threshold in ABC_THRESHOLDS:
            if share_before < threshold:
                label = name
                break
        result[goods_id] = (rank, label)
        cumulative += quantity
    return result


def compute_metrics(warehouse_id, day) -> list:
    """算 day（当地日期）日终的各商品分析指标，返回 InventoryMetricsDaily（未入库）"""
    from warehouse.inventory.models import Inventory
    from .history import daily_totals
    end = clock.local_midnight(day + timedelta(days=1), warehouse_id)
    window_start = {n: clock.local_midnight(day - timedelta(days=n - 1), warehouse_id) for n in OUT_WINDOWS}

    # 日终 total 与近 30 天日均
    totals = daily_totals(warehouse_id, day - timedelta(days=TURNOVER_WINDOW_DAYS - 1), day)
    stock_today = totals.get(day, {})
    stock_sum = defaultdict(int)
    for values in totals.values():
        for goods_id, quantity in values.items():
            stock_sum[goods_id] += quantity

    # 出库
    out = {n: defaultdict(int) for n in OUT_WINDOWS}
    sales_30 = defaultdict(int)
    last_out = {}
    for goods_id, moment, quantity, dn_type in outbound_events(warehouse_id, None, end):
        if moment is None:
            continue
        if goods_id not in last_out or moment > last_out[goods_id]:
            last_out[goods_id] = moment
        for n in OUT_WINDOWS:
            if moment >= window_start[n]:
                out[n][goods_id] += quantity
        if moment >= window_start[30] and dn_type == 'shipping':
            sales_30[goods_id] += quantity

    # 入库层
    layers = defaultdict(list)
    for goods_id, moment, quantity, _movement in inbound_events(warehouse_id, end):
        if moment is not None:
            layers[goods_id].append((moment, quantity))
    first_inbound = min((m for values in layers.values() for m, _q in values), default=None)

    available = {inv.goods_id: inv.available_stock_for_sale
                 for inv in Inventory.query.filter(Inventory.warehouse_id == warehouse_id).all()}
    abc = abc_classes(out[ABC_WINDOW_DAYS])
    goods_ids = {g for g, q in stock_today.items() if q > 0} | {g for g, q in out[90].items() if q > 0}

    rows = []
    for goods_id in sorted(goods_ids):
        total = int(stock_today.get(goods_id, 0))
        used, unknown = age_layers(total, layers.get(goods_id, []))
        buckets = {column: 0 for _low, _high, column in AGE_BUCKETS}
        weighted = 0
        for moment, quantity in used:
            age = max(0, _age_days(day, moment, warehouse_id))
            weighted += age * quantity
            for low, high, column in AGE_BUCKETS:
                if age >= low and (high is None or age <= high):
                    buckets[column] += quantity
                    break
        known = total - unknown
        oldest = min((m for m, _q in used), default=None)
        oldest_age = _age_days(day, oldest, warehouse_id) if oldest is not None else None
        if unknown and first_inbound is not None:
            oldest_age = max(oldest_age or 0, _age_days(day, first_inbound, warehouse_id))
        last_out_at = last_out.get(goods_id)
        if last_out_at is not None:
            idle = _age_days(day, last_out_at, warehouse_id)
        elif oldest is not None:
            idle = oldest_age
        else:
            idle = None
        out_30 = out[30].get(goods_id, 0)
        daily_out = out_30 / TURNOVER_WINDOW_DAYS
        avg_stock = stock_sum.get(goods_id, 0) / TURNOVER_WINDOW_DAYS
        sale = available.get(goods_id)
        if total <= 0:
            status = 'empty'
        elif out[DEAD_IDLE_DAYS].get(goods_id, 0) == 0 and oldest_age is not None and oldest_age > DEAD_AGE_DAYS:
            status = 'dead'
        else:
            status = 'normal'
        rank, label = abc.get(goods_id, (None, None))
        in_times = [m for m, _q in layers.get(goods_id, [])]
        rows.append(InventoryMetricsDaily(
            metric_date=day, warehouse_id=warehouse_id, goods_id=goods_id, total_stock=total,
            available_stock_for_sale=sale,
            out_qty_7d=out[7].get(goods_id, 0), out_qty_30d=out_30, out_qty_90d=out[90].get(goods_id, 0),
            sales_qty_30d=sales_30.get(goods_id, 0),
            last_in_at=max(in_times) if in_times else None, last_out_at=last_out_at,
            avg_stock_30d=round(avg_stock, 2),
            turnover_days_30d=round(avg_stock / daily_out, 1) if daily_out > 0 else None,
            days_of_supply=round(max(sale or 0, 0) / daily_out, 1) if daily_out > 0 and sale is not None else None,
            age_unknown=unknown, weighted_age_days=round(weighted / known, 1) if known > 0 else None,
            oldest_in_at=oldest, out_qty_90d_rank=rank, abc_class=label, idle_days=idle, status=status,
            **buckets,
        ))
    return rows


def generate_metrics(warehouse_id, day) -> int:
    """生成（或重算）某仓库 day 的汇总，返回行数；顺带删掉超过保留期的旧汇总。调用方提交"""
    InventoryMetricsDaily.query.filter(InventoryMetricsDaily.warehouse_id == warehouse_id,
                                       InventoryMetricsDaily.metric_date == day).delete(synchronize_session=False)
    rows = compute_metrics(warehouse_id, day)
    db.session.add_all(rows)
    InventoryMetricsDaily.query.filter(
        InventoryMetricsDaily.warehouse_id == warehouse_id,
        InventoryMetricsDaily.metric_date < day - timedelta(days=METRICS_RETENTION_DAYS),
    ).delete(synchronize_session=False)
    db.session.flush()
    return len(rows)


def latest_metric_date(warehouse_id):
    from sqlalchemy import func
    return (db.session.query(func.max(InventoryMetricsDaily.metric_date))
            .filter(InventoryMetricsDaily.warehouse_id == warehouse_id).scalar())


def generate_missing_metrics(warehouse_id=None) -> dict:
    """各仓库当地昨天的汇总还没生成就生成（每天只算最近一天）。返回 {warehouse_id: 日期}"""
    from warehouse.warehouse.models import Warehouse
    query = Warehouse.query.filter(Warehouse.is_active == True)
    if warehouse_id is not None:
        query = query.filter(Warehouse.id == warehouse_id)
    done = {}
    for warehouse in query.order_by(Warehouse.id).all():
        yesterday = clock.local_today(warehouse) - timedelta(days=1)
        latest = latest_metric_date(warehouse.id)
        if latest is not None and latest >= yesterday:
            continue
        generate_metrics(warehouse.id, yesterday)
        db.session.commit()
        done[warehouse.id] = yesterday
    return done


# ---------------------------------------------------------------------------
# 补货阈值建议（2026-10-25 定：日均出库按近 30 天，补货覆盖 14 天、积压 90 天，最小 1 件；近 90 天无出库不给建议）
# ---------------------------------------------------------------------------
SUGGEST_WINDOW_DAYS = 30
SUGGEST_COVER_DAYS = 14
SUGGEST_OVERSTOCK_DAYS = 90
SUGGEST_MIN_QUANTITY = 1
SUGGEST_IDLE_DAYS = 90


def suggest_thresholds(warehouse_id, goods_ids=None, window_days=SUGGEST_WINDOW_DAYS, cover_days=SUGGEST_COVER_DAYS,
                       overstock_days=SUGGEST_OVERSTOCK_DAYS, min_quantity=SUGGEST_MIN_QUANTITY) -> dict:
    """按出库量给补货阈值建议（实时）：{goods_id: {avg_daily_out, out_qty_window, out_qty_90d, suggested_low,
    suggested_high}}；近 90 天没有出库的 suggested_* 为 None"""
    now = clock.now()
    today = clock.local_today(warehouse_id)
    window_start = clock.local_midnight(today - timedelta(days=window_days - 1), warehouse_id)
    idle_start = clock.local_midnight(today - timedelta(days=SUGGEST_IDLE_DAYS - 1), warehouse_id)
    out_window = defaultdict(int)
    out_idle = defaultdict(int)
    for goods_id, moment, quantity, _dn_type in outbound_events(warehouse_id, min(window_start, idle_start), now,
                                                                goods_ids):
        if moment >= window_start:
            out_window[goods_id] += quantity
        if moment >= idle_start:
            out_idle[goods_id] += quantity
    result = {}
    for goods_id in (goods_ids if goods_ids is not None else set(out_window) | set(out_idle)):
        avg = out_window.get(goods_id, 0) / window_days if window_days else 0
        entry = {'avg_daily_out': round(avg, 3), 'out_qty_window': out_window.get(goods_id, 0),
                 'out_qty_90d': out_idle.get(goods_id, 0), 'suggested_low': None, 'suggested_high': None}
        if out_idle.get(goods_id, 0) > 0:
            low = max(min_quantity, math.ceil(avg * cover_days))
            high = max(low + 1, math.ceil(avg * overstock_days))
            entry.update(suggested_low=low, suggested_high=high)
        result[goods_id] = entry
    return result
