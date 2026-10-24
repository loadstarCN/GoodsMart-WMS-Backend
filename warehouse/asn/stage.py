"""列表用的入库阶段（inbound_stage，doc/shipping-flow.md §7.1），SQL 判定：ASN 列表的 inbound_stage= 筛选、列表每行的阶段、
入库流程接口（GET /asn/<id>/flow）的 stage 用的都是这里的同一个表达式（口径一致，同出库的 warehouse/shipping/stage.py）。

阶段（按顺序判定，先命中先算）：
- closed：入库单已关闭 / 取消（closed）
- putaway：入库单已完成（分拣完成时自动完成），本单的货在仓库里还有待上架的（见下）
- completed：入库单已完成，没有待上架的
- sorting：已收货（received），等分拣 / 分拣中
- 以下是待收货（pending）：
  - exception：有运单号，承运商跟踪说异常（tracking_status = exception）
  - receiving：有运单号，承运商跟踪说已送达（delivered），等仓库收货
  - in_transit：有运单号（供应商已发货；跟踪方式不是 api 时只凭运单号判断）
  - expected：还没有运单号

待上架：上架记录（putaway_records）只有商品 / 库位，不记是哪张入库单的，所以按「本单的商品在仓库里的待上架库存
（inventory.sorted_stock，分拣完成时 += 实收、上架时 -=）」估算，新完成的入库单先占：
本单某商品待上架 = clamp(该商品在本仓库的 sorted_stock − 比本单晚完成的入库单（同仓库、有效、已完成）该商品的实收合计,
0, 本单该商品的实收）。任一商品 > 0 → putaway。下架（removal）放回的货也算在 sorted_stock 里，会记到最近完成的入库单上。
"""
from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import aliased

STAGE_EXPECTED = 'expected'
STAGE_IN_TRANSIT = 'in_transit'
STAGE_RECEIVING = 'receiving'
STAGE_SORTING = 'sorting'
STAGE_PUTAWAY = 'putaway'
STAGE_COMPLETED = 'completed'
STAGE_CLOSED = 'closed'
STAGE_EXCEPTION = 'exception'
STAGES = (STAGE_EXPECTED, STAGE_IN_TRANSIT, STAGE_RECEIVING, STAGE_SORTING, STAGE_PUTAWAY, STAGE_COMPLETED,
          STAGE_CLOSED, STAGE_EXCEPTION)

TRACK_DELIVERED = 'delivered'
TRACK_EXCEPTION = 'exception'


def _models():
    """(ASN, ASNDetail, Inventory)：用时再导入（schemas 里就要用本模块的常量）"""
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.inventory.models import Inventory
    return ASN, ASNDetail, Inventory


def completed_moment(model):
    """入库单完成的时刻（旧数据没有 completed_at 时按创建时刻）：「谁更晚完成」的比较用"""
    return func.coalesce(model.completed_at, model.created_at)


def newer_actual_expression(asn_model, goods_id, exclude_id=None):
    """比 asn_model（外层查询的入库单）晚完成的、同仓库的有效已完成入库单里，该商品的实收合计（标量子查询）"""
    ASN, ASNDetail, _Inventory = _models()
    other = aliased(ASN)
    line = aliased(ASNDetail)
    mine = completed_moment(asn_model)
    theirs = completed_moment(other)
    return (
        select(func.coalesce(func.sum(line.actual_quantity), 0))
        .select_from(line)
        .join(other, other.id == line.asn_id)
        .where(
            line.goods_id == goods_id,
            other.warehouse_id == asn_model.warehouse_id,
            other.status == 'completed',
            other.is_active.is_(True),
            other.id != asn_model.id,
            or_(theirs > mine, and_(theirs == mine, other.id > asn_model.id)),
        )
        .correlate_except(line, other)
        .scalar_subquery()
    )


def putaway_pending_expression():
    """本单（外层的 ASN）还有待上架的货：某个实收 > 0 的商品，仓库的 sorted_stock 大于比本单晚完成的入库单的实收合计"""
    ASN, ASNDetail, Inventory = _models()
    return exists().where(
        ASNDetail.asn_id == ASN.id,
        ASNDetail.actual_quantity > 0,
        Inventory.goods_id == ASNDetail.goods_id,
        Inventory.warehouse_id == ASN.warehouse_id,
        Inventory.sorted_stock > newer_actual_expression(ASN, ASNDetail.goods_id),
    ).correlate(ASN)


def stage_expression():
    """inbound_stage 的 SQL 表达式（关联到外层查询的 ASN）"""
    ASN, _ASNDetail, _Inventory = _models()
    has_number = and_(ASN.tracking_number.isnot(None), func.trim(ASN.tracking_number) != '')
    return case(
        (ASN.status == 'closed', literal(STAGE_CLOSED)),
        (and_(ASN.status == 'completed', putaway_pending_expression()), literal(STAGE_PUTAWAY)),
        (ASN.status == 'completed', literal(STAGE_COMPLETED)),
        (ASN.status == 'received', literal(STAGE_SORTING)),
        (and_(has_number, ASN.tracking_status == TRACK_EXCEPTION), literal(STAGE_EXCEPTION)),
        (and_(has_number, ASN.tracking_status == TRACK_DELIVERED), literal(STAGE_RECEIVING)),
        (has_number, literal(STAGE_IN_TRANSIT)),
        else_=literal(STAGE_EXPECTED),
    )


def stage_rows(asn_ids) -> dict:
    """{asn_id: stage}：列表一页的阶段（一条查询）"""
    from extensions.db import db
    ids = sorted({i for i in asn_ids if i is not None})
    if not ids:
        return {}
    ASN = _models()[0]
    rows = db.session.execute(select(ASN.id, stage_expression()).where(ASN.id.in_(ids))).all()
    return {row[0]: row[1] for row in rows}


def stage_of(asn) -> str:
    """一张入库单的阶段（与列表同一个 SQL 表达式）"""
    return stage_rows([asn.id]).get(asn.id)


def attach(items):
    """给列表的一页入库单挂上 _inbound_stage，序列化时取（避免逐行查）"""
    items = list(items or [])
    rows = stage_rows(item.id for item in items)
    for item in items:
        item._inbound_stage = rows.get(item.id)
    return items


def inbound_stage_of(asn):
    """序列化时用：列表接口预先挂上的，没挂的（单张）单独查"""
    if hasattr(asn, '_inbound_stage'):
        return asn._inbound_stage
    return stage_of(asn) if getattr(asn, 'id', None) else None


def putaway_pending(asn) -> list:
    """本单逐商品的待上架估算（与 putaway_pending_expression 同一口径）→
    [{goods_id, quantity（本单实收）, sorted_stock（仓库的待上架库存）, newer（晚完成的入库单的实收）, pending}]。
    只列实收 > 0 的商品（同一商品多行的旧单据按商品合并）"""
    from extensions.db import db
    ASN, ASNDetail, Inventory = _models()
    quantities = {}
    for detail in asn.details:
        if (detail.actual_quantity or 0) > 0:
            quantities[detail.goods_id] = quantities.get(detail.goods_id, 0) + detail.actual_quantity
    if not quantities:
        return []
    stocks = dict(db.session.query(Inventory.goods_id, Inventory.sorted_stock).filter(
        Inventory.warehouse_id == asn.warehouse_id, Inventory.goods_id.in_(list(quantities))).all())
    outer = aliased(ASN)
    result = []
    for goods_id in sorted(quantities):
        newer = db.session.execute(
            select(newer_actual_expression(outer, literal(goods_id))).select_from(outer).where(outer.id == asn.id)
        ).scalar() or 0
        stock = stocks.get(goods_id) or 0
        quantity = quantities[goods_id]
        result.append({'goods_id': goods_id, 'quantity': quantity, 'sorted_stock': stock, 'newer': int(newer),
                       'pending': max(0, min(quantity, stock - int(newer)))})
    return result
