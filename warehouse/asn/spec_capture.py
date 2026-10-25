"""收货通知的规格采集（README「Spec capture on receiving」；取值与设置校验见 warehouse/common/spec_capture.py）

- 请求（单据级）：asn.spec_capture_fields（weight / dimensions / origin_country 的子集）与 asn.spec_capture_mode
  （missing 只补商品资料里缺的项〔默认〕/ remeasure 全部重新测量）。接口上是对象 spec_capture {fields, mode}，建单与修改
  （pending）时可传；建单时没传的部分取仓库的默认值（default_spec_capture_fields / default_spec_capture_mode），是单据上的
  快照，之后改仓库设置不影响已建的单。仓库默认不采集（空列表），现有流程不变
- 结果（明细级）：spec_captured_at / spec_captured_by = 最近一次采集或跳过的时刻与人；spec_capture_note 非空 = 跳过
  （其余没采的项）及原因
- 待采集项（spec_pending）不落库，按商品当前资料、单据的采集项与模式算出（line_spec_pending）：
  - 单据已关闭、已完成的单据上实收为 0 的行（货没到）、填了跳过原因的行 → 没有待采集
  - remeasure：还没采集过 → 全部采集项；采集过 → 无（采集时要么填齐全部采集项，要么填跳过原因）
  - missing：采集项里商品资料缺的（goods_spec_missing：重量为空或 ≤ 0、长宽高任一为空或 ≤ 0、原产国为空），
    补上了就不再待采集（不论是在这里采集的，还是商品编辑 / 导入 / 别的单据补上的）
  单据的 spec_pending_count = 有待采集项的行数；列表 spec_pending=true 用同一口径的 SQL（pending_condition）
- 分拣完成（SortingTaskService.complete_task）只看本单分拣到的商品（合格 + 破损 > 0），按仓库当前的
  spec_capture_enforcement：warn → 照常完成，分拣任务响应的 spec_pending 列出未采集的行；block → 还有未采集的行
  （跳过并填了原因的不算）→ 409 17021，details {asn_id, enforcement, lines}
- 采集：PUT /asn/<id>/details/<detail_id>/spec（SpecCaptureService.capture），收货通知为 received 或 completed（完成后补采）
  时可用，测到的值写回商品主数据（GoodsService.update_goods，来源 spec_source 默认 receiving，可显式传 station 等；规格有变化
  照常推 goods.spec_updated）
"""
from flask import g
from sqlalchemy import Text, and_, cast, exists, func, or_

from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common import clock
from warehouse.common.locks import lock_row
from warehouse.common.spec_capture import (
    DEFAULT_SPEC_CAPTURE_ENFORCEMENT, DEFAULT_SPEC_CAPTURE_MODE, SPEC_CAPTURE_BLOCKED, SPEC_CAPTURE_FIELDS,
    SPEC_CAPTURE_NOT_ALLOWED, SPEC_CAPTURE_REQUEST_INVALID, SPEC_CAPTURE_SETTINGS_INVALID, normalize_spec_fields,
    normalize_spec_mode,
)
from warehouse.goods.models import Goods
from warehouse.goods.services import (
    SPEC_SOURCES, GoodsService, clean_goods_numbers, clean_origin_country, goods_spec_missing,
    goods_spec_missing_condition,
)

from .models import ASN, ASNDetail

DIMENSION_ATTRS = ('length', 'width', 'height')
NOTE_MAX_LENGTH = 255
DEFAULT_SOURCE = 'receiving'


# ---------------------------------------------------------------------------
# 单据上的采集请求
# ---------------------------------------------------------------------------

def asn_spec_fields(asn) -> list:
    """单据要采集的项（按固定顺序；存量单据为 null = 不采集）"""
    stored = asn.spec_capture_fields if isinstance(asn.spec_capture_fields, list) else []
    return [name for name in SPEC_CAPTURE_FIELDS if name in stored]


def asn_spec_mode(asn) -> str:
    return asn.spec_capture_mode or DEFAULT_SPEC_CAPTURE_MODE


def spec_capture_payload(asn) -> dict:
    """接口输出的 spec_capture：{fields, mode}"""
    return {'fields': asn_spec_fields(asn), 'mode': asn_spec_mode(asn)}


def resolve_spec_capture(value, warehouse, current=None) -> tuple:
    """请求里的 spec_capture → (fields, mode)。

    - 建单（current 为 None）：没传 / null，或对象里没给的键，取仓库的默认值
    - 修改（current = 单据现有的 (fields, mode)）：对象里没给的键保持不变
    - 不是对象、fields 不是采集项列表、mode 不是 missing / remeasure → 400 17020
    """
    if current is None:
        current = (normalize_spec_fields(getattr(warehouse, 'default_spec_capture_fields', None)),
                   getattr(warehouse, 'default_spec_capture_mode', None) or DEFAULT_SPEC_CAPTURE_MODE)
    if value is None:
        return current
    if not isinstance(value, dict):
        raise BadRequestException("spec_capture must be an object {fields, mode}", SPEC_CAPTURE_SETTINGS_INVALID,
                                  'spec_capture')
    fields_ = normalize_spec_fields(value['fields']) if 'fields' in value else current[0]
    mode = normalize_spec_mode(value['mode']) if value.get('mode') is not None else current[1]
    return fields_, mode


def apply_spec_capture(asn, value, warehouse, creating: bool):
    """把 spec_capture 写到单据上（建单时没传也写：快照仓库的默认值）"""
    current = None if creating else (asn_spec_fields(asn), asn_spec_mode(asn))
    if not creating and value is None:
        return
    asn.spec_capture_fields, asn.spec_capture_mode = resolve_spec_capture(value, warehouse, current)


# ---------------------------------------------------------------------------
# 待采集项
# ---------------------------------------------------------------------------

def line_spec_pending(detail, asn=None) -> list:
    """一行明细还待采集的项（按固定顺序；空列表 = 没有待采集）"""
    asn = asn if asn is not None else detail.asn
    fields_ = asn_spec_fields(asn)
    if not fields_ or asn.status == 'closed' or detail.spec_capture_note:
        return []
    if asn.status == 'completed' and not (detail.actual_quantity or 0):
        return []
    if asn_spec_mode(asn) == 'remeasure':
        return [] if detail.spec_captured_at is not None else fields_
    if detail.goods is None:
        return []
    missing = goods_spec_missing(detail.goods)
    return [name for name in fields_ if name in missing]


def asn_spec_pending_count(asn) -> int:
    """有待采集项的明细行数"""
    if not asn_spec_fields(asn):
        return 0
    return sum(1 for detail in asn.details if line_spec_pending(detail, asn))


def _line_ref(detail, pending) -> dict:
    goods = detail.goods
    return {
        'asn_detail_id': detail.id,
        'goods_id': detail.goods_id,
        'goods_code': goods.code if goods is not None else None,
        'goods_name': goods.name if goods is not None else None,
        'spec_pending': pending,
    }


def sorted_goods_ids(asn) -> set:
    """本单有效分拣任务里分拣到的商品（合格 + 破损 > 0）"""
    from warehouse.sorting.models import SortingTask, SortingTaskDetail
    rows = (
        db.session.query(SortingTaskDetail.goods_id)
        .join(SortingTask, SortingTaskDetail.sorting_task_id == SortingTask.id)
        .filter(SortingTask.asn_id == asn.id, SortingTask.is_active.is_(True))
        .group_by(SortingTaskDetail.goods_id)
        .having(func.coalesce(func.sum(SortingTaskDetail.sorted_quantity + SortingTaskDetail.damage_quantity), 0) > 0)
        .all()
    )
    return {goods_id for goods_id, in rows}


def sorted_pending_lines(asn) -> list:
    """本单分拣到的商品里还有待采集项的明细（分拣完成时提示 / 拦截的就是这些）：
    [{asn_detail_id, goods_id, goods_code, goods_name, spec_pending}]"""
    if not asn_spec_fields(asn):
        return []
    goods_ids = sorted_goods_ids(asn)
    lines = []
    for detail in sorted(asn.details, key=lambda d: d.id):
        if detail.goods_id not in goods_ids:
            continue
        pending = line_spec_pending(detail, asn)
        if pending:
            lines.append(_line_ref(detail, pending))
    return lines


def assert_capturable_on_complete(asn) -> list:
    """分拣完成前：仓库为 block 时还有未采集的行 → 409 17021；warn 返回清单（照常完成）"""
    lines = sorted_pending_lines(asn)
    enforcement = getattr(asn.warehouse, 'spec_capture_enforcement', None) or DEFAULT_SPEC_CAPTURE_ENFORCEMENT
    if lines and enforcement == 'block':
        raise ConflictException(
            f"{len(lines)} sorted line(s) of ASN {asn.id} still need specs captured; capture them or record a skip "
            f"reason before completing sorting.",
            SPEC_CAPTURE_BLOCKED, details={'asn_id': asn.id, 'enforcement': enforcement, 'lines': lines},
        )
    return lines


def pending_condition():
    """SQL：单据有待采集的行（与 line_spec_pending 同一口径；列表 spec_pending=true / false 用）"""
    fields_text = cast(ASN.spec_capture_fields, Text)

    def requested(name):
        return fields_text.like(f'%"{name}"%')

    remeasure = func.coalesce(ASN.spec_capture_mode, DEFAULT_SPEC_CAPTURE_MODE) == 'remeasure'
    line_pending = and_(
        or_(ASNDetail.spec_capture_note.is_(None), ASNDetail.spec_capture_note == ''),
        or_(ASN.status != 'completed', func.coalesce(ASNDetail.actual_quantity, 0) > 0),
        or_(
            and_(remeasure, ASNDetail.spec_captured_at.is_(None), or_(*[requested(n) for n in SPEC_CAPTURE_FIELDS])),
            and_(~remeasure, or_(*[and_(requested(n), goods_spec_missing_condition(n)) for n in SPEC_CAPTURE_FIELDS])),
        ),
    )
    return and_(
        ASN.status != 'closed',
        exists().where(ASNDetail.asn_id == ASN.id, Goods.id == ASNDetail.goods_id, line_pending),
    )


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------

# 能采集的收货通知状态：收货后、分拣中（received），以及完成后补采（completed）；pending / closed → 409 17023
CAPTURABLE_STATUSES = ('received', 'completed')

def _invalid(message, field=None, details=None):
    return BadRequestException(message, SPEC_CAPTURE_REQUEST_INVALID, field, details=details)


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _captured_items(values: dict) -> set:
    items = set()
    if 'weight' in values:
        items.add('weight')
    if all(attr in values for attr in DIMENSION_ATTRS):
        items.add('dimensions')
    if 'origin_country' in values:
        items.add('origin_country')
    return items


class SpecCaptureService:

    @staticmethod
    def parse(data) -> tuple:
        """采集请求 → (写回商品的值 {weight, length, width, height, origin_country}, 跳过原因, 来源)。

        - 没给 / null / 空串的值不写（采集不会清空商品资料）；数值同商品接口（400 16100），原产国同商品接口（400 10014）
        - 长宽高要三项一起给（400 17022 field dimensions）
        - note：跳过原因（文字，≤ 255，首尾空白去掉，空串 = 没填）
        - 既没有值也没有跳过原因 → 400 17022
        - spec_source：SPEC_SOURCES 之一（station / receiving / ……）照用，没传或不认识 → receiving
        """
        if not isinstance(data, dict):
            raise _invalid("Request body must be a JSON object")
        values = {attr: data[attr] for attr in ('weight', *DIMENSION_ATTRS) if attr in data and not _blank(data[attr])}
        clean_goods_numbers(values)
        given_dims = [attr for attr in DIMENSION_ATTRS if attr in values]
        if given_dims and len(given_dims) != len(DIMENSION_ATTRS):
            raise _invalid("length, width and height must be given together", 'dimensions',
                           details={'missing': [attr for attr in DIMENSION_ATTRS if attr not in values]})
        origin = clean_origin_country(data.get('origin_country'))
        if origin:
            values['origin_country'] = origin

        note = data.get('note')
        if note is not None and not isinstance(note, str):
            raise _invalid("note must be a string", 'note')
        note = note.strip() if note else None
        if note and len(note) > NOTE_MAX_LENGTH:
            raise _invalid(f"note must be at most {NOTE_MAX_LENGTH} characters", 'note')
        note = note or None
        if not values and note is None:
            raise _invalid("Give at least one spec value (weight, length / width / height, origin_country) or a "
                           "skip reason (note)")

        requested = data.get('spec_source')
        source = requested.strip().lower() if isinstance(requested, str) else None
        return values, note, source if source in SPEC_SOURCES else DEFAULT_SOURCE

    @staticmethod
    @transactional
    def capture(asn, detail_id: int, data) -> ASNDetail:
        """采集（或跳过）一行明细的规格：收货通知为 received，或已完成后补采（completed）；其他状态 409 17023。
        补采与收货时采集相同：写回商品、推 goods.spec_updated、记采集时刻与人（asn.completed 已推送过，不再重推）。

        - remeasure 模式没填跳过原因时，单据的采集项要全部给出（400 17022，details {missing}）
        - 有值 → 写回商品主数据（规格有变化推 goods.spec_updated，source 见 parse）
        - 明细记 spec_captured_at / spec_captured_by（当前用户；没绑用户的 API Key 为空），spec_capture_note = 这次的
          跳过原因（没填 = 清空：重新采集后不再算跳过）
        """
        from .services import ASNService

        # 先锁单据并重读状态：与分拣完成串行（block 模式下完成时要么看到这次采集，要么这次在完成之后、算补采）
        asn = lock_row(asn)
        if asn.status not in CAPTURABLE_STATUSES:
            raise ConflictException(
                f"Specs can only be captured while the ASN is received or completed (ASN {asn.id} is {asn.status})",
                SPEC_CAPTURE_NOT_ALLOWED, details={'asn_id': asn.id, 'status': asn.status})
        detail = ASNService.get_asn_detail(asn.id, detail_id)
        values, note, source = SpecCaptureService.parse(data)

        if asn_spec_mode(asn) == 'remeasure' and note is None:
            missing = [name for name in asn_spec_fields(asn) if name not in _captured_items(values)]
            if missing:
                raise _invalid("Remeasure mode: give every requested spec or a skip reason (note)", 'spec_capture',
                               details={'missing': missing})

        if values:
            GoodsService.update_goods(detail.goods_id, values, spec_source=source)
        user = g.get('current_user')
        detail.spec_captured_at = clock.now()
        detail.spec_captured_by = user.id if user is not None else None
        detail.spec_capture_note = note
        db.session.add(detail)
        db.session.flush()
        return detail
