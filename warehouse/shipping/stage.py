"""列表用的发货阶段（shipping_stage，doc/shipping-flow.md §5），SQL 判定：DN 列表的 shipping_stage= 筛选、
DN / 发货任务列表每行的阶段、流程汇总接口的 stage 用的都是这里的同一个表达式（口径一致）。

阶段（按顺序判定，先命中先算）：
- closed：DN 已关闭 / 取消（closed）
- delivered：已签收（DN completed）
- preparing：作业中（pending / in_progress / picked）
- exception：运单结果不明（插件运单 unknown、卡住的 pending、取消中 / 取消结果不明的 cancelling）、跟踪异常
  （有效运单 tracking_status = exception）、自动步骤被挡（有效运单 tracking_problem 有值）、揽收预约结果不明
  （所带揽收 unknown 或卡住的 pending）。DN 已发货（delivered）时只看运单与跟踪
- in_transit：已交承运商、还没签收（DN delivered）
- 以下是 DN packed（打包完、还没交承运商）：
  - awaiting_number：文件方式已导出给承运商软件、还没导回运单号（file 运单 exported，当前发货任务上也没有运单号）
  - awaiting_waybill：还没有运单号（没有有效运单，当前发货任务上也没有运单号；插件建单进行中也算）
  - awaiting_pickup：有运单，承运商的揽收方式要逐票约（pickup_method api / manual），这张运单还没被有效的揽收占着
    （pending / unknown / scheduled 且揽收日没过）
  - awaiting_handover：有运单、揽收已约好或不用约（scheduled / dropoff / none），等交给承运商
「当前发货任务」= 最新的有效任务；承运商 = 当前发货任务的，没有用 DN 的（与建运单同一口径）。
卡住的判定用 carrier_services.stale_after_seconds()，「揽收日没过」按揽收所属仓库的当地日期（仓库时区，
warehouse/common/clock.py）。
"""
from datetime import timedelta

from sqlalchemy import and_, case, exists, func, literal, or_, select

from warehouse.common import clock

STAGE_PREPARING = 'preparing'
STAGE_AWAITING_WAYBILL = 'awaiting_waybill'
STAGE_AWAITING_NUMBER = 'awaiting_number'
STAGE_AWAITING_PICKUP = 'awaiting_pickup'
STAGE_AWAITING_HANDOVER = 'awaiting_handover'
STAGE_IN_TRANSIT = 'in_transit'
STAGE_EXCEPTION = 'exception'
STAGE_DELIVERED = 'delivered'
STAGE_CLOSED = 'closed'
STAGES = (STAGE_PREPARING, STAGE_AWAITING_WAYBILL, STAGE_AWAITING_NUMBER, STAGE_AWAITING_PICKUP,
          STAGE_AWAITING_HANDOVER, STAGE_IN_TRANSIT, STAGE_EXCEPTION, STAGE_DELIVERED, STAGE_CLOSED)

PICKUP_BOOKED_METHODS = ('api', 'manual')       # 要逐票约揽收的方式
TRACK_EXCEPTION = 'exception'
CANCEL_IN_PROGRESS = 'cancel_in_progress'


def _cutoffs(now=None, today=None, warehouse_id_column=None):
    """(卡住的 pending 的截止时刻, 今天)。today 不给时：按 warehouse_id_column（揽收所属仓库）取各仓库当地的今天"""
    from warehouse.dn.carrier_services import stale_after_seconds
    now = now or clock.now()
    if today is None:
        today = (clock.local_today_expression(warehouse_id_column) if warehouse_id_column is not None
                 else clock.local_today())
    return now - timedelta(seconds=stale_after_seconds()), today


def _models():
    """(DN, DeliveryTask, DNCarrierShipment, CarrierPickup, Carrier)：用时再导入（本模块的常量在 schemas 里就要用，
    那时出库模块的包还没初始化完）"""
    from warehouse.carrier.models import Carrier
    from warehouse.delivery.models import DeliveryTask
    from warehouse.dn.models import DN, CarrierPickup, DNCarrierShipment
    return DN, DeliveryTask, DNCarrierShipment, CarrierPickup, Carrier


def _current_task_id():
    """DN 的当前发货任务 id（最新的有效任务）"""
    DN, DeliveryTask, _S, _P, _C = _models()
    return (
        select(func.max(DeliveryTask.id))
        .where(DeliveryTask.dn_id == DN.id, DeliveryTask.is_active.is_(True))
        .correlate(DN)
        .scalar_subquery()
    )


def _task_column(column):
    DN, DeliveryTask, _S, _P, _C = _models()
    return select(column).where(DeliveryTask.id == _current_task_id()).correlate(DN).scalar_subquery()


def carrier_id_expression():
    """承运商 id：当前发货任务的，没有用 DN 的"""
    DN, DeliveryTask, _S, _P, _C = _models()
    return func.coalesce(_task_column(DeliveryTask.carrier_id), DN.carrier_id)


def tracking_number_expression():
    """运单号：有效 / 取消中的运单（任一来源）的号码，没有用当前发货任务上的"""
    DN, DeliveryTask, DNCarrierShipment, _P, _C = _models()
    live = (
        select(DNCarrierShipment.tracking_number)
        .where(DNCarrierShipment.dn_id == DN.id, DNCarrierShipment.status.in_(('active', 'cancelling')))
        .order_by(DNCarrierShipment.id.desc())
        .limit(1)
        .correlate(DN)
        .scalar_subquery()
    )
    return func.coalesce(live, func.nullif(_task_column(DeliveryTask.tracking_number), ''))


def stage_expression(now=None, today=None):
    """shipping_stage 的 SQL 表达式（关联到外层查询的 DN）"""
    DN, DeliveryTask, S, P, Carrier = _models()
    stale_before, today = _cutoffs(now, today, P.warehouse_id)

    unresolved = exists().where(
        S.dn_id == DN.id,
        or_(
            S.status == 'unknown',
            and_(S.status == 'pending', S.created_at < stale_before),
            and_(S.status == 'cancelling',
                 or_(S.reason.is_(None), S.reason != CANCEL_IN_PROGRESS, S.updated_at < stale_before)),
        ),
    ).correlate(DN)
    tracking_trouble = exists().where(
        S.dn_id == DN.id, S.status == 'active',
        or_(S.tracking_status == TRACK_EXCEPTION, S.tracking_problem.isnot(None)),
    ).correlate(DN)
    pickup_unclear = exists().where(
        S.dn_id == DN.id, S.status == 'active', P.id == S.pickup_id,
        or_(P.status == 'unknown', and_(P.status == 'pending', P.created_at < stale_before)),
    ).correlate(DN)
    has_waybill = or_(
        exists().where(S.dn_id == DN.id, S.status == 'active').correlate(DN),
        exists().where(
            DeliveryTask.id == _current_task_id(),
            DeliveryTask.tracking_number.isnot(None), DeliveryTask.tracking_number != '',
        ).correlate(DN),
    )
    exported = exists().where(S.dn_id == DN.id, S.source == 'file', S.status == 'exported').correlate(DN)
    pickup_held = exists().where(
        S.dn_id == DN.id, S.status == 'active', P.id == S.pickup_id,
        P.status.in_(('pending', 'unknown', 'scheduled')), P.pickup_date >= today,
    ).correlate(DN)
    books_pickups = exists().where(
        Carrier.id == carrier_id_expression(), Carrier.pickup_method.in_(PICKUP_BOOKED_METHODS),
    ).correlate(DN)

    return case(
        (DN.status == 'closed', literal(STAGE_CLOSED)),
        (DN.status == 'completed', literal(STAGE_DELIVERED)),
        (DN.status.in_(('pending', 'in_progress', 'picked')), literal(STAGE_PREPARING)),
        (and_(DN.status == 'delivered', or_(unresolved, tracking_trouble)), literal(STAGE_EXCEPTION)),
        (DN.status == 'delivered', literal(STAGE_IN_TRANSIT)),
        (or_(unresolved, tracking_trouble, pickup_unclear), literal(STAGE_EXCEPTION)),
        (and_(~has_waybill, exported), literal(STAGE_AWAITING_NUMBER)),
        (~has_waybill, literal(STAGE_AWAITING_WAYBILL)),
        (and_(books_pickups, ~pickup_held), literal(STAGE_AWAITING_PICKUP)),
        else_=literal(STAGE_AWAITING_HANDOVER),
    )


def stage_of(dn) -> str:
    """一张 DN 的阶段（与列表同一个 SQL 表达式）"""
    from extensions.db import db
    DN = _models()[0]
    return db.session.execute(select(stage_expression()).where(DN.id == dn.id)).scalar()


def shipping_rows(dn_ids) -> dict:
    """{dn_id: {'stage', 'carrier_id', 'carrier_name', 'tracking_number'}}：列表一页的阶段与承运商 / 运单号（一条查询）。
    承运商 = 当前发货任务的，没有用 DN 的（与建运单 / 文件导出同一口径）"""
    from extensions.db import db
    ids = sorted({i for i in dn_ids if i is not None})
    if not ids:
        return {}
    DN, _T, _S, _P, Carrier = _models()
    carrier_id = carrier_id_expression()
    carrier_name = select(Carrier.name).where(Carrier.id == carrier_id).correlate(DN).scalar_subquery()
    rows = db.session.execute(
        select(DN.id, stage_expression(), carrier_id, carrier_name, tracking_number_expression())
        .where(DN.id.in_(ids))
    ).all()
    return {row[0]: {'stage': row[1], 'carrier_id': row[2], 'carrier_name': row[3], 'tracking_number': row[4]}
            for row in rows}


def attach(items, dn_id_of=lambda item: item.id):
    """给列表的一页对象挂上 _shipping（{stage, carrier_id, carrier_name, tracking_number}），序列化时取（避免逐行查）"""
    items = list(items or [])
    rows = shipping_rows(dn_id_of(item) for item in items)
    for item in items:
        item._shipping = rows.get(dn_id_of(item))
    return items


def shipping_of(dn_id) -> dict:
    """序列化时没有预先挂上的（详情接口等）：单独查一次"""
    return shipping_rows([dn_id]).get(dn_id)
