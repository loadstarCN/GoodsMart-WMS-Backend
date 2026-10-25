"""入库单流程汇总（GET /asn/<id>/flow，doc/shipping-flow.md §7.1）：入库单详情页按它渲染，不自己推算状态。

一张入库单一条（轻量的）流程：创建 / 预计到货 → 在途（入库单的运单号）→ 到货收货 → 分拣 → 上架 → 完成。
每一步给 state、实际发生的时刻 at（UTC，带 +00:00；界面按顶层 timezone —— 入库单仓库的时区 —— 显示）、来源 source、
界面要的概要 summary 和能做的操作 actions，
结构与出库流程（warehouse/shipping/flow.py）相同，界面用同一套组件。

- state：done / current / todo / warning（要人处理）/ blocked / skipped（不适用：没有运单号的在途、取消后没做的步骤……）
- source：warehouse（仓库操作）/ carrier（承运商跟踪）/ api（入库单来自来源系统）/ system（WMS 自动：分拣完成时自动完成）
- actions[].key：refresh_tracking（POST /asn/<id>/track）、open_tracking（承运商公开查询页，带 url）、
  receive（PUT /asn/<id>/receive/）；enabled = false 时带 reason（英文句子）与 reason_code（REASON_CODES）。
  分拣任务的入口在 sorting 一步的 summary.task_ids。
- stage 与 ASN 列表同一个 SQL 口径（stage.py）。

本系统里入库单的状态：pending（待收货）→ received（收货时自动建分拣任务）→ completed（分拣任务完成时自动完成，实收数量
在这时定下来）；pending 可以关闭、received 且分拣没开始可以取消（都是 closed）。上架不按入库单记录，「上架」一步按本单
商品的待上架库存估算（stage.putaway_pending），可能在「完成」之后才做完：入库单「完成」是单据完成，上架是之后的仓库作业。
"""
from sqlalchemy import func

from extensions.db import db
from warehouse.carrier.methods import methods_of, tracking_url as carrier_tracking_url
from warehouse.common import clock
# 先初始化出库包：warehouse.shipping.flow 引用 warehouse.dn 的服务，warehouse.dn 包初始化时又引用 shipping.flow
import warehouse.dn.services  # noqa: F401,E402
from warehouse.shipping.common import local_today
from warehouse.shipping.flow import _action, _at, _carrier_time, _problem, _step

from .stage import STAGE_PUTAWAY, TRACK_DELIVERED, TRACK_EXCEPTION, putaway_pending, stage_of
from .tracking import ASNTrackingService, tracking_number_of

STEP_KEYS = ('created', 'in_transit', 'received', 'sorting', 'putaway', 'completed')
STATES = ('done', 'current', 'todo', 'warning', 'blocked', 'skipped')
ACTION_KEYS = ('refresh_tracking', 'open_tracking', 'receive')
# 操作不能做的原因码（actions[].reason_code）
REASON_CODES = ('ASN_NOT_PENDING', 'NO_DETAILS')

RECEIVED_OR_LATER = ('received', 'completed')


def _user_ref(user_id):
    from warehouse.dn.models import _user_ref as user_ref
    return user_ref(user_id)


class InboundFlowService:

    @staticmethod
    def build(asn) -> dict:
        from warehouse.sorting.models import SortingTask
        carrier = asn.carrier
        methods = methods_of(carrier)
        number = tracking_number_of(asn)
        integration = carrier.integration if carrier is not None else None
        url = (carrier_tracking_url(carrier, number, integration.adapter if integration is not None else None)
               if carrier is not None and methods['tracking'] != 'none' else None)
        details = list(asn.details)
        tasks = (SortingTask.query.filter(SortingTask.asn_id == asn.id, SortingTask.is_active.is_(True))
                 .order_by(SortingTask.id).all())
        stage = stage_of(asn)
        facts = {
            'asn': asn, 'carrier': carrier, 'methods': methods, 'tracking_number': number, 'tracking_url': url,
            'tracked': number is not None and ASNTrackingService.trackable(asn),
            'tracking': asn.tracking_payload() if number else None,
            'details': details, 'tasks': tasks, 'stage': stage, 'problems': [],
            'received': asn.status in RECEIVED_OR_LATER or (asn.status == 'closed' and asn.received_at is not None),
            'expected_quantity': sum(d.quantity or 0 for d in details),
        }
        steps = []
        for builder in (InboundFlowService._created, InboundFlowService._in_transit, InboundFlowService._received,
                        InboundFlowService._sorting, InboundFlowService._putaway, InboundFlowService._completed):
            steps.append(builder(facts, steps))
        if asn.status == 'closed':
            # 关闭 / 取消：没做完的步骤都不再适用，也没有要处理的问题了
            for step in steps:
                if step['state'] != 'done':
                    step['state'] = 'skipped'
                    step['actions'] = []
            facts['problems'] = []

        supplier = asn.supplier
        return {
            'asn_id': asn.id,
            'asn_status': asn.status,
            'asn_type': asn.asn_type,
            'order_number': asn.order_number,
            'warehouse_id': asn.warehouse_id,
            # 入库单所属仓库的生效时区（doc/timezone-utc.md §3）：界面按它显示 at 等时刻、判断日期
            'timezone': clock.warehouse_timezone_name(asn),
            'expected_arrival_date': asn.expected_arrival_date.isoformat() if asn.expected_arrival_date else None,
            'closed_at': _at(asn.closed_at) if asn.status == 'closed' else None,
            'supplier': {'id': supplier.id, 'name': supplier.name} if supplier is not None else None,
            'carrier': InboundFlowService._carrier_payload(carrier, methods, url),
            'tracking_number': number,
            'stage': stage,
            'steps': steps,
            'problems': facts['problems'],
            'tasks': {'sorting': [task.id for task in tasks]},
        }

    @staticmethod
    def _carrier_payload(carrier, methods, url):
        if carrier is None:
            return None
        integration = carrier.integration
        return {
            'id': carrier.id,
            'name': carrier.name,
            'code': carrier.code,
            'integration': integration.adapter if integration is not None else None,
            'tracking_method': methods['tracking'],
            'tracking_url': url,
        }

    # ------------------------------------------------------------------
    # 创建 / 在途 / 收货
    # ------------------------------------------------------------------

    @staticmethod
    def _created(f, _steps):
        asn = f['asn']
        return _step('created', 'done', asn.created_at, 'api' if asn.api_key_id else 'warehouse', {
            'order_number': asn.order_number,
            'asn_type': asn.asn_type,
            'expected_arrival_date': asn.expected_arrival_date.isoformat() if asn.expected_arrival_date else None,
            'line_count': len(f['details']),
            'quantity': f['expected_quantity'],
            'creator': _user_ref(asn.created_by),
        })

    @staticmethod
    def _in_transit(f, _steps):
        asn, tracking = f['asn'], f['tracking']
        summary = {'tracking_number': f['tracking_number'], 'tracking': tracking, 'tracking_url': f['tracking_url'],
                   'tracked_by_api': f['tracked'], 'tracking_method': f['methods']['tracking']}
        actions = []
        if f['tracked']:
            actions.append(_action('refresh_tracking', asn.status == 'pending',
                                   "The ASN has been received; tracking is no longer needed", 'ASN_NOT_PENDING'))
        if f['tracking_url']:
            actions.append(_action('open_tracking', url=f['tracking_url']))
        if not f['tracking_number']:
            # 没有运单号：不知道货在不在路上
            return _step('in_transit', 'skipped', summary=summary, actions=actions)
        status = (tracking or {}).get('status')
        events = (tracking or {}).get('events') or []
        latest_at = _carrier_time(events[0].get('time')) if events else None
        delivered_at = _carrier_time((tracking or {}).get('delivered_at')) if status == TRACK_DELIVERED else None
        if f['received']:
            # 已收货：在途结束（承运商说了送达时刻就用它）
            return _step('in_transit', 'done', delivered_at, 'carrier' if delivered_at else None, summary, actions)
        if status == TRACK_DELIVERED:
            # 承运商说已送达：不自动收货（收货是仓库的操作），只提示
            return _step('in_transit', 'done', delivered_at or latest_at, 'carrier', summary, actions)
        if status == TRACK_EXCEPTION:
            f['problems'].append(_problem('TRACKING_EXCEPTION', 'warning',
                                          (tracking or {}).get('status_text') or "The carrier reports an exception",
                                          'in_transit'))
            return _step('in_transit', 'warning', latest_at, 'carrier', summary, actions)
        if tracking and tracking.get('error'):
            f['problems'].append(_problem('TRACKING_ERROR', 'warning', tracking['error'], 'in_transit'))
            return _step('in_transit', 'warning', latest_at, 'carrier' if latest_at else None, summary, actions)
        return _step('in_transit', 'current', latest_at, 'carrier' if latest_at else None, summary, actions)

    @staticmethod
    def _received(f, steps):
        asn, details = f['asn'], f['details']
        completed = asn.status == 'completed'
        actual = sum(d.actual_quantity or 0 for d in details) if completed else None
        overdue = bool(asn.status == 'pending' and asn.expected_arrival_date is not None
                       and asn.expected_arrival_date < local_today(asn))      # 预计到货日已过（仓库当地日期）
        summary = {
            'line_count': len(details),
            'expected_quantity': f['expected_quantity'],
            # 实收在分拣完成时才定下来（分拣的合格 + 损坏）
            'actual_quantity': actual,
            'difference': actual - f['expected_quantity'] if actual is not None else None,
            'expected_arrival_date': asn.expected_arrival_date.isoformat() if asn.expected_arrival_date else None,
            'overdue': overdue,
        }
        if f['received']:
            return _step('received', 'done', asn.received_at, 'warehouse', summary)
        if details:
            action = _action('receive')
        else:
            action = _action('receive', False, "The ASN has no lines to receive", 'NO_DETAILS')
            f['problems'].append(_problem('NO_DETAILS', 'warning', "The ASN has no lines", 'received'))
        if overdue:
            f['problems'].append(_problem('ARRIVAL_OVERDUE', 'warning',
                                          f"The expected arrival date {asn.expected_arrival_date.isoformat()} has "
                                          "passed", 'received', date=asn.expected_arrival_date.isoformat()))
        # 承运商说还在路上（或跟踪异常）时，收货还没轮到
        in_transit = steps[-1]['state'] if steps and steps[-1]['key'] == 'in_transit' else 'skipped'
        state = 'todo' if in_transit in ('current', 'warning') else 'current'
        return _step('received', state, summary=summary, actions=[action])

    # ------------------------------------------------------------------
    # 分拣 / 上架 / 完成
    # ------------------------------------------------------------------

    @staticmethod
    def _sorting_totals(asn):
        """有效分拣任务已记的合格 / 损坏数量（分拣中也有，完成时汇总到入库单明细）"""
        from warehouse.sorting.models import SortingTask, SortingTaskDetail
        sorted_qty, damage_qty = db.session.query(
            func.coalesce(func.sum(SortingTaskDetail.sorted_quantity), 0),
            func.coalesce(func.sum(SortingTaskDetail.damage_quantity), 0),
        ).join(SortingTask, SortingTaskDetail.sorting_task_id == SortingTask.id).filter(
            SortingTask.asn_id == asn.id, SortingTask.is_active.is_(True)).one()
        return int(sorted_qty or 0), int(damage_qty or 0)

    @staticmethod
    def _sorting(f, _steps):
        asn, tasks = f['asn'], f['tasks']
        sorted_qty, damage_qty = InboundFlowService._sorting_totals(asn)
        summary = {
            'task_ids': [task.id for task in tasks],
            'tasks': [{'id': task.id, 'status': task.status, 'started_at': _at(task.started_at),
                       'completed_at': _at(task.completed_at)} for task in tasks],
            'sorted_quantity': sorted_qty,
            'damage_quantity': damage_qty,
            'quantity': sorted_qty + damage_qty,
            'expected_quantity': f['expected_quantity'],
            'spec_capture': InboundFlowService._spec_capture(asn),
        }
        if asn.status == 'completed':
            done_at = [task.completed_at for task in tasks if task.completed_at is not None]
            return _step('sorting', 'done', max(done_at) if done_at else asn.completed_at, 'warehouse', summary)
        if asn.status != 'received':
            return _step('sorting', 'todo', summary=summary)
        if not tasks:
            f['problems'].append(_problem('SORTING_TASK_MISSING', 'error',
                                          "The ASN was received but has no active sorting task", 'sorting'))
            return _step('sorting', 'warning', summary=summary)
        started = [task.started_at for task in tasks if task.started_at is not None]
        return _step('sorting', 'current', min(started) if started else None, 'warehouse' if started else None,
                     summary)

    @staticmethod
    def _spec_capture(asn):
        """规格采集（warehouse/asn/spec_capture.py）：单据的采集项与模式、仓库的提示 / 拦截、有待采集项的行数，以及本单已分拣到
        但还没采集的行（分拣完成时提示 / 拦截的就是这些）"""
        from warehouse.common.spec_capture import DEFAULT_SPEC_CAPTURE_ENFORCEMENT
        from .spec_capture import asn_spec_pending_count, sorted_pending_lines, spec_capture_payload
        return {
            **spec_capture_payload(asn),
            'enforcement': getattr(asn.warehouse, 'spec_capture_enforcement', None) or DEFAULT_SPEC_CAPTURE_ENFORCEMENT,
            'pending_count': asn_spec_pending_count(asn),
            'sorted_pending': sorted_pending_lines(asn),
        }

    @staticmethod
    def _putaway(f, _steps):
        asn = f['asn']
        if asn.status != 'completed':
            return _step('putaway', 'todo', summary={'quantity': None, 'pending_quantity': None, 'goods': []})
        from warehouse.goods.models import Goods
        rows = putaway_pending(asn)
        goods = {g.id: g for g in Goods.query.filter(Goods.id.in_([r['goods_id'] for r in rows])).all()} if rows else {}
        quantity = sum(r['quantity'] for r in rows)
        pending = sum(r['pending'] for r in rows)
        last_at = InboundFlowService._last_putaway_at(asn, [r['goods_id'] for r in rows])
        summary = {
            'quantity': quantity,
            'pending_quantity': pending,
            'put_away_quantity': quantity - pending,
            'goods': [{'goods_id': r['goods_id'], 'code': getattr(goods.get(r['goods_id']), 'code', None),
                       'name': getattr(goods.get(r['goods_id']), 'name', None), 'quantity': r['quantity'],
                       'pending': r['pending'],
                       # 仓库里该商品的待上架库存（不只本单）与其中的破损品（只能上到 damaged 库位，其余可上到
                       # standard / return 库位）：上架时界面据此提示
                       'sorted_stock': r['sorted_stock'], 'sorted_damage_stock': r['sorted_damage_stock']}
                      for r in rows],
            'last_putaway_at': _at(last_at),
            # 上架记录不带入库单：按待上架库存估算（见 stage.py）
            'estimated': True,
        }
        if f['stage'] == STAGE_PUTAWAY:
            return _step('putaway', 'current', None, None, summary)
        return _step('putaway', 'done', last_at, 'warehouse' if last_at else None, summary)

    @staticmethod
    def _last_putaway_at(asn, goods_ids):
        """本单商品在本仓库最近一次上架的时刻（没有为 None）。待上架清零之前一定有分拣完成后的上架，所以不再按完成时刻
        过滤（上架时刻是数据库的 now()，与 WMS 应用写的完成时刻不是同一个时钟）"""
        if not goods_ids:
            return None
        from warehouse.location.models import Location
        from warehouse.putaway.models import PutawayRecord
        return db.session.query(func.max(PutawayRecord.putaway_time)).join(
            Location, PutawayRecord.location_id == Location.id).filter(
            PutawayRecord.goods_id.in_(goods_ids), Location.warehouse_id == asn.warehouse_id).scalar()

    @staticmethod
    def _completed(f, _steps):
        asn, details = f['asn'], f['details']
        summary = {'expected_quantity': f['expected_quantity'], 'actual_quantity': None, 'sorted_quantity': None,
                   'damage_quantity': None, 'difference': None}
        if asn.status != 'completed':
            return _step('completed', 'todo', summary=summary)
        actual = sum(d.actual_quantity or 0 for d in details)
        summary.update({
            'actual_quantity': actual,
            'sorted_quantity': sum(d.sorted_quantity or 0 for d in details),
            'damage_quantity': sum(d.damage_quantity or 0 for d in details),
            'difference': actual - f['expected_quantity'],
        })
        # 分拣任务完成时 WMS 自动完成入库单（推 asn.completed）
        return _step('completed', 'done', asn.completed_at, 'system', summary)
