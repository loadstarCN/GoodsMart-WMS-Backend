"""全局搜索（顶栏搜索框 Ctrl K / 搜索结果页）：一个关键字同时搜单据、作业任务与主数据，按类型分组返回。

数据范围：每类的基础查询直接用该模块列表接口的 list_* 函数（只传仓库 / 公司范围，不传 keyword）——公司 / 仓库隔离、
默认只看启用的，都与列表一致；这里只在其上加搜索条件、排序与 limit。仓库范围（scope）：
- warehouse（默认）：与列表同一套 @warehouse_required() + add_warehouse_filter（X-WAREHOUSE-ID / warehouse_id；
  没给仓库时公司管理员 / 绑定公司的 API Key 按可访问的全部仓库，平台管理员不受限）
- all：不看当前仓库，调用方可访问的全部仓库（员工按 StaffService.accessible_warehouses）
权限：每类只在调用方有对应的查看权限（或 all_access / company_all_access）时才搜（与列表的 permission_required
同一判断；API Key 按 key 的权限）；一类都没有 → 空结果，不 403。

匹配（大小写不敏感；LIKE 的 % _ 已转义；q 去首尾空白后最多 MAX_QUERY_LENGTH 个字符，多的截掉）：
- 单号：复用列表的 number_conditions（DN-163 / PICK-5 / 纯数字……，前缀与列的对应同各列表）
- 运单号：去空白 / 连字符比较，与 DN 列表同一口径（完整号码精确；TRACKING_PARTIAL_MIN 位以上可以只输一段）；
  发货任务按自己的运单号或所属 DN 的当前运单号（同 DN 列表）
- 单据 / 作业任务另按明细里的商品编码（相同 / 前缀）、商品名称（包含）；命中的第一件商品另给结构化的 matched_goods
  {goods_id, code, name, quantity}（数量 = 这张单据里该商品的数量合计），副标题照旧带上编码与名称
- q 少于 MIN_FUZZY_LENGTH 个字符：只比单号与商品 / 库位编码（完全相同）
排序（rank）：0 完整单号（带前缀）/ 完整运单号 / 商品编码完全相同（exact = true）→ 1 纯数字编号 / 关联单号 / 完全相同
→ 2 前缀 → 3 包含；同级按 id 倒序。
高亮：highlights [{field, start, end}] 按返回的显示文本（code / title / subtitle / columns.<列>）计，UTF-16 码元下标
（与 JavaScript 字符串下标一致），end 不含；运单号去分隔符匹配时换算回显示文本里的位置。
"""
import math
import re
from typing import Any, NamedTuple

from flask import g
from sqlalchemy import case, func, literal, or_
from sqlalchemy.orm import joinedload, selectinload

from extensions.db import db
from extensions.error import BadRequestException, ForbiddenException, UnauthorizedException
from system.common.permissions import get_actor_context
from warehouse.common import add_warehouse_filter, warehouse_required
from warehouse.common.search import number_conditions, parse_document_number

from warehouse.adjustment.models import Adjustment, AdjustmentDetail
from warehouse.adjustment.services import AdjustmentService
from warehouse.asn.models import ASN, ASNDetail
from warehouse.asn.services import ASNService
from warehouse.company.models import Company
from warehouse.cyclecount.models import CycleCountTask, CycleCountTaskDetail
from warehouse.cyclecount.services import CycleCountTaskService
from warehouse.delivery.models import DeliveryTask
from warehouse.delivery.services import DeliveryTaskService
from warehouse.dn.carrier_services import normalize_tracking
from warehouse.dn.models import DN, DNDetail
from warehouse.dn.services import DNService, tracking_search_condition, _normalized_tracking_sql
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.goods.services import GoodsService
from warehouse.inventory.models import Inventory
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from warehouse.location.services import LocationService
from warehouse.packing.models import PackingTask
from warehouse.packing.services import PackingTaskService
from warehouse.picking.models import PickingTask
from warehouse.picking.services import PickingTaskService
from warehouse.recipient.models import Recipient
from warehouse.recipient.services import RecipientService
from warehouse.shipping.stage import shipping_rows, tracking_number_expression
from warehouse.sorting.models import SortingTask
from warehouse.sorting.services import SortingTaskService
from warehouse.staff.models import Staff
from warehouse.staff.services import StaffService
from warehouse.supplier.models import Supplier

# 类型（= url_hint.page = 前端路由 /<page>/detail/<id>），也是默认的分组顺序
SEARCH_TYPES = ('dn', 'asn', 'sorting', 'picking', 'packing', 'delivery', 'cyclecount', 'adjustment',
                'goods', 'location', 'recipient')
# 不细分模块、能看全部的权限（与各列表的 permission_required 一致）
SUPER_PERMISSIONS = frozenset({'all_access', 'company_all_access'})

SCOPE_WAREHOUSE = 'warehouse'
SCOPE_ALL = 'all'
SCOPES = (SCOPE_WAREHOUSE, SCOPE_ALL)

DEFAULT_LIMIT = 5
MAX_LIMIT = 50
MAX_QUERY_LENGTH = 100
MIN_FUZZY_LENGTH = 2
TRACKING_PARTIAL_MIN = 6    # 与 DN 列表 tracking_search_condition 一致：6 位以上可以只输一段运单号

RANK_EXACT, RANK_EQUAL, RANK_PREFIX, RANK_CONTAINS = 0, 1, 2, 3

SEPARATOR = ' · '


def _escape_like(value: str) -> str:
    """LIKE 转义（! 作转义符，与 DN 列表的运单号搜索同一写法）"""
    return value.replace('!', '!!').replace('%', '!%').replace('_', '!_')


def _utf16(text: str, index: int) -> int:
    """Python 字符串下标 → UTF-16 码元下标（JavaScript 的字符串下标；BMP 以外的字符占 2）"""
    return len(text[:index].encode('utf-16-le')) // 2


class SearchTerm:
    """搜索关键字：SQL 条件与显示文本里的命中位置用同一套规则（高亮与实际匹配一致）"""

    def __init__(self, raw):
        text = str(raw or '').strip()[:MAX_QUERY_LENGTH].strip()
        self.text = text
        self.lower = text.lower()
        self.fuzzy = len(text) >= MIN_FUZZY_LENGTH     # 够长才做前缀 / 包含 / 运单号匹配
        self.parsed = parse_document_number(text)      # (前缀或 None, 编号) / None
        self.prefixed = bool(self.parsed and self.parsed[0])
        self.tracking = normalize_tracking(text).upper()
        escaped = _escape_like(text)
        self._contains = f'%{escaped}%'
        self._starts = f'{escaped}%'
        self._pattern = re.compile(re.escape(text), re.IGNORECASE) if text else None

    # ---------- SQL ----------
    def equals(self, column):
        return func.lower(column) == self.lower

    def starts(self, column):
        return column.ilike(self._starts, escape='!')

    def contains(self, column):
        return column.ilike(self._contains, escape='!')

    # ---------- 显示文本里的命中位置（value 内的下标 [(start, end)]）----------
    def text_spans(self, value):
        """包含（名称、订单号……）"""
        if not self.fuzzy:
            return []
        return [m.span() for m in self._pattern.finditer(value)]

    def code_spans(self, value):
        """库位编码：相同 / 前缀 / 包含；q 太短只认相同"""
        if not self.fuzzy:
            return self._full(value)
        return [m.span() for m in self._pattern.finditer(value)]

    def code_prefix_spans(self, value):
        """商品编码：相同 / 前缀；q 太短只认相同"""
        if not self.fuzzy:
            return self._full(value)
        match = self._pattern.match(value)
        return [match.span()] if match else []

    def _full(self, value):
        match = self._pattern.fullmatch(value) if self._pattern else None
        return [match.span()] if match else []

    def tracking_spans(self, value):
        """运单号：去掉空白和连字符比较（完整号码，或 TRACKING_PARTIAL_MIN 位以上的一段），位置换算回原文"""
        needle = self.tracking
        if not self.fuzzy or not needle:
            return []
        chars, positions = [], []
        for index, char in enumerate(value):
            if char.isspace() or char == '-':
                continue
            upper = char.upper()
            chars.append(upper if len(upper) == 1 else char)
            positions.append(index)
        haystack = ''.join(chars)
        if not haystack:
            return []
        if haystack == needle:
            return [(positions[0], positions[-1] + 1)]
        if len(needle) < TRACKING_PARTIAL_MIN:
            return []
        spans, start = [], haystack.find(needle)
        while start >= 0:
            spans.append((positions[start], positions[start + len(needle) - 1] + 1))
            start = haystack.find(needle, start + len(needle))
        return spans

    def number_spans(self, prefix, number, plain=True):
        """单号「PREFIX-编号」：带前缀命中整段；纯数字命中编号部分（plain=False 的列不认纯数字，同列表）"""
        def spans(value):
            if not self.parsed or number is None or self.parsed[1] != number:
                return []
            if self.parsed[0] is None:
                return [(len(value) - len(str(number)), len(value))] if plain else []
            return [(0, len(value))] if self.parsed[0] == prefix else []
        return spans


class _Text:
    """一段显示文本：由若干部分拼成（默认 ' · ' 分隔），记住每部分的位置与匹配方式，用来算高亮"""

    def __init__(self, value=None, spans=None):
        self.value = ''
        self._parts = []
        self.add(value, spans)

    def add(self, value, spans=None, sep=SEPARATOR):
        """spans：value → [(start, end)]；None = 这部分不是搜索字段（仓库名等），不画高亮"""
        if value is None or value == '':
            return self
        value = str(value)
        if self.value:
            self.value += sep
        self._parts.append((len(self.value), value, spans))
        self.value += value
        return self

    def text(self):
        return self.value or None

    def highlights(self, field):
        result = []
        for offset, value, spans in self._parts:
            if spans is None:
                continue
            for start, end in spans(value):
                result.append({'field': field, 'start': _utf16(self.value, offset + start),
                               'end': _utf16(self.value, offset + end)})
        return result


class _Match(NamedTuple):
    """一个匹配条件：matched_field、rank、SQL 条件；narrow = 已被同字段更宽的条件覆盖（只用于排序 / 标注，不进 WHERE）"""
    field: str
    rank: int
    condition: Any
    narrow: bool = False


class _Number(NamedTuple):
    """单号列：列、界面前缀、纯数字是否也比这一列、matched_field（与该类型列表的 number_conditions 参数一一对应）"""
    column: Any
    prefix: str
    plain: bool
    field: str


def _text_matches(term, column, field, exact=True, prefix=True, contains=True):
    """文字字段：相同（rank 1）/ 前缀（rank 2）/ 包含（rank 3）；被更宽的条件覆盖的只用于排序"""
    matches = []
    if exact:
        matches.append(_Match(field, RANK_EQUAL, term.equals(column), narrow=prefix or contains))
    if prefix:
        matches.append(_Match(field, RANK_PREFIX, term.starts(column), narrow=contains))
    if contains:
        matches.append(_Match(field, RANK_CONTAINS, term.contains(column)))
    return matches


def _goods_matches(term, any_goods):
    """明细里的商品：编码相同 / 前缀、名称包含。any_goods(条件) → 「有一条明细的商品满足条件」的 SQL"""
    if not term.fuzzy:
        return []
    return [
        _Match('goods_code', RANK_EQUAL, any_goods(term.equals(Goods.code)), narrow=True),
        _Match('goods_code', RANK_PREFIX, any_goods(term.starts(Goods.code))),
        _Match('goods_name', RANK_CONTAINS, any_goods(term.contains(Goods.name))),
    ]


def _tracking_matches(term, normalized_column, contains_condition):
    """运单号：完整号码（去分隔符后相同，rank 0 / exact）；contains_condition 为可以只输一段的条件"""
    if not term.fuzzy or not term.tracking:
        return []
    matches = [_Match('tracking_number', RANK_EXACT, normalized_column == term.tracking,
                      narrow=contains_condition is not None)]
    if contains_condition is not None:
        matches.append(_Match('tracking_number', RANK_CONTAINS, contains_condition))
    return matches


def _matched_goods(term, goods_rows):
    """明细里第一个命中关键字的商品（副标题带上）：编码相同 / 前缀，或名称包含。
    goods_rows = [(商品, 数量)] → (商品, 这张单据里它的数量合计)；没有命中 → (None, None)"""
    if not term.fuzzy:
        return None, None
    rows = [(goods, quantity) for goods, quantity in goods_rows if goods is not None]
    for goods, _quantity in rows:
        if term.code_prefix_spans(goods.code or '') or term.text_spans(goods.name or ''):
            return goods, sum(quantity or 0 for other, quantity in rows if other.id == goods.id)
    return None, None


def _goods_ref(goods, quantity):
    """结构化的命中商品 matched_goods"""
    if goods is None:
        return None
    return {'goods_id': goods.id, 'code': goods.code, 'name': goods.name, 'quantity': quantity}


def _add_goods(text, term, goods):
    if goods is not None:
        text.add(goods.code, term.code_prefix_spans)
        text.add(goods.name, term.text_spans, sep=' ')
    return text


def _name(obj):
    return getattr(obj, 'name', None) if obj is not None else None


def _iso(value):
    return value.isoformat() if value is not None else None


def _code(prefix, number):
    return f'{prefix}-{number}' if number is not None else None


def _hint(page, record_id):
    return {'page': page, 'id': record_id} if record_id is not None else None


# ---------------------------------------------------------------------------------------------
# 各类型
# ---------------------------------------------------------------------------------------------

class _SearchType:
    """一类搜索对象。子类给出：权限、模型、单号列、基础查询（各模块的 list_*）、其它匹配条件、结果的显示文本与列表列"""
    name = None
    permission = None
    model = None
    numbers = ()

    def base_query(self, scope, company_id):
        raise NotImplementedError

    def matches(self, term):
        """单号以外的匹配条件 [_Match]"""
        return []

    def prepare(self, objects, scope, company_id, with_columns):
        """整页一次查好的附加数据（运单号、库存……），传给 build"""
        return None

    def build(self, obj, term, ctx, with_columns):
        """→ (code: _Text, title: _Text, subtitle: _Text, status, warehouse_id, columns, column_spans, matched_goods)"""
        raise NotImplementedError

    # ---------------------------------------------------------------------
    def _number_matches(self, term):
        matches = []
        for number in self.numbers:
            conditions = number_conditions(term.text, {number.prefix: [number.column]},
                                           [number.column] if number.plain else [])
            # 带前缀命中本单的单号 = 完整单号（exact）；纯数字 / 关联单号排第二档
            own = number.field == 'number'
            rank = RANK_EXACT if own and term.prefixed else RANK_EQUAL
            matches += [_Match(number.field, rank, condition) for condition in conditions]
        return matches

    def search(self, term, scope, company_id, offset, size, with_columns):
        """→ (total, items)"""
        matches = self._number_matches(term) + self.matches(term)
        if not matches:
            return 0, []
        query = self.base_query(dict(scope), company_id)
        query = query.filter(or_(*[m.condition for m in matches if not m.narrow]))

        ordered = sorted(matches, key=lambda m: m.rank)   # CASE 取第一个成立的：最好的档在前
        rank = case(*[(m.condition, m.rank) for m in ordered], else_=RANK_CONTAINS + 1)
        field = case(*[(m.condition, literal(m.field)) for m in ordered], else_=None)
        rows = (
            query.add_columns(rank.label('search_rank'), field.label('search_field'))
            .order_by(None)
            .order_by(rank, self.model.id.desc())
            .offset(offset)
            .limit(size)
            .all()
        )
        # 第一页没取满就是全部，不用再数（各列表查询的 joined 预加载都是多对一 / 一对一；盘点明细是顶层集合，
        # SQLAlchemy 会先包子查询再 LIMIT，取回的行数 = 记录数）
        total = len(rows) if offset == 0 and len(rows) < size else query.order_by(None).count()

        objects = [row[0] for row in rows]
        ctx = self.prepare(objects, scope, company_id, with_columns)
        return total, [self._item(obj, row_rank, row_field, term, ctx, with_columns)
                       for obj, row_rank, row_field in rows]

    def _item(self, obj, rank, field, term, ctx, with_columns):
        code, title, subtitle, status, warehouse_id, columns, column_spans, matched_goods = self.build(
            obj, term, ctx, with_columns)
        highlights = code.highlights('code') + title.highlights('title') + subtitle.highlights('subtitle')
        if with_columns:
            for key, spans in (column_spans or {}).items():
                value = columns.get(key)
                if isinstance(value, str) and value:
                    highlights += _Text(value, spans).highlights(f'columns.{key}')
        return {
            'type': self.name,
            'id': obj.id,
            'code': code.text(),
            'title': title.text(),
            'subtitle': subtitle.text(),
            'status': status,
            'warehouse_id': warehouse_id,
            'url_hint': _hint(self.name, obj.id),
            'matched_field': field,
            'exact': rank == RANK_EXACT,
            'highlights': highlights,
            # 单据 / 作业任务按明细商品命中时：第一个命中的明细商品 {goods_id, code, name, quantity}；其余为 null
            'matched_goods': matched_goods,
            'columns': columns if with_columns else None,
        }


class _DNSearch(_SearchType):
    """发货单：DN 编号、订单号、当前运单号（与 DN 列表同一口径）、收货方名称、明细商品"""
    name, permission, model = 'dn', 'dn_read', DN
    numbers = (_Number(DN.id, 'DN', True, 'number'),)

    def base_query(self, scope, company_id):
        return DNService.list_dns(scope)

    def matches(self, term):
        if not term.fuzzy:
            return []
        return (
            _text_matches(term, DN.order_number, 'order_number')
            + _tracking_matches(term, _normalized_tracking_sql(tracking_number_expression()),
                                tracking_search_condition(term.text))
            + [_Match('recipient_name', RANK_CONTAINS, DN.recipient.has(term.contains(Recipient.name)))]
            + _goods_matches(term, lambda c: DN.details.any(DNDetail.goods.has(c)))
        )

    def prepare(self, objects, scope, company_id, with_columns):
        # 当前运单号 / 承运商 / 发货阶段：与 DN 列表同一条查询（warehouse/shipping/stage.py）
        return shipping_rows(dn.id for dn in objects)

    def build(self, dn, term, ctx, with_columns):
        shipping = (ctx or {}).get(dn.id) or {}
        tracking = shipping.get('tracking_number')
        recipient_name = _name(dn.recipient)
        goods, quantity = _matched_goods(term, ((detail.goods, detail.quantity) for detail in dn.details))
        code = _Text(_code('DN', dn.id), term.number_spans('DN', dn.id))
        title = _Text(recipient_name, term.text_spans)
        subtitle = _Text(dn.order_number, term.text_spans).add(tracking, term.tracking_spans)
        _add_goods(subtitle, term, goods).add(_name(dn.warehouse))
        columns = {
            'recipient_name': recipient_name,
            'carrier_name': shipping.get('carrier_name'),
            'tracking_number': tracking,
            'status': dn.status,
            'shipping_stage': shipping.get('stage'),
            'expected_shipping_date': _iso(dn.expected_shipping_date),
            'order_number': dn.order_number,
            'warehouse_name': _name(dn.warehouse),
        } if with_columns else None
        spans = {'recipient_name': term.text_spans, 'order_number': term.text_spans,
                 'tracking_number': term.tracking_spans}
        return code, title, subtitle, dn.status, dn.warehouse_id, columns, spans, _goods_ref(goods, quantity)


class _ASNSearch(_SearchType):
    """收货通知：ASN 编号、外部订单号、运单号（去分隔符比较）、供应商名称、明细商品"""
    name, permission, model = 'asn', 'asn_read', ASN
    numbers = (_Number(ASN.id, 'ASN', True, 'number'),)

    def base_query(self, scope, company_id):
        return ASNService.list_asns(scope)

    def matches(self, term):
        if not term.fuzzy:
            return []
        normalized = _normalized_tracking_sql(ASN.tracking_number)
        partial = (normalized.like(f'%{_escape_like(term.tracking)}%', escape='!')
                   if len(term.tracking) >= TRACKING_PARTIAL_MIN else None)
        return (
            _text_matches(term, ASN.order_number, 'order_number')
            + _tracking_matches(term, normalized, partial)
            + [_Match('supplier_name', RANK_CONTAINS, ASN.supplier.has(term.contains(Supplier.name)))]
            + _goods_matches(term, lambda c: ASN.details.any(ASNDetail.goods.has(c)))
        )

    def build(self, asn, term, ctx, with_columns):
        supplier_name = _name(asn.supplier)
        goods, quantity = _matched_goods(term, ((detail.goods, detail.quantity) for detail in asn.details))
        code = _Text(_code('ASN', asn.id), term.number_spans('ASN', asn.id))
        title = _Text(supplier_name, term.text_spans)
        subtitle = _Text(asn.order_number, term.text_spans).add(asn.tracking_number, term.tracking_spans)
        _add_goods(subtitle, term, goods).add(_name(asn.warehouse))
        columns = {
            'supplier_name': supplier_name,
            'status': asn.status,
            'expected_arrival_date': _iso(asn.expected_arrival_date),
            'order_number': asn.order_number,
            'tracking_number': asn.tracking_number,
            'warehouse_name': _name(asn.warehouse),
        } if with_columns else None
        spans = {'supplier_name': term.text_spans, 'order_number': term.text_spans,
                 'tracking_number': term.tracking_spans}
        return code, title, subtitle, asn.status, asn.warehouse_id, columns, spans, _goods_ref(goods, quantity)


class _TaskSearch(_SearchType):
    """作业任务的公共部分：编号（含关联单号，同列表）、明细商品；列表列 = 类型、关联单号、状态"""
    code_prefix = None
    related_page = None     # 关联单据的页面类型（asn / dn / cyclecount），没有为 None

    def related(self, task):
        """→ (关联单据 id, 任务所在仓库)"""
        raise NotImplementedError

    def goods_of(self, task):
        """明细商品 [(商品, 数量)]（matched_goods 的数量：计划量；盘点为系统数量，调整为调整量）"""
        return []

    def describe(self, task, term, related_code, related_spans, ctx=None):
        """→ (标题, 副标题的开头部分)：默认标题 = 关联单号"""
        return _Text(related_code, related_spans), _Text()

    def build(self, task, term, ctx, with_columns):
        related_id, warehouse = self.related(task)
        # 关联单号列（numbers 里 matched_field 不是 number 的那一列）：前缀与纯数字规则同列表
        related = next((n for n in self.numbers if n.field != 'number'), None)
        related_code = _code(related.prefix, related_id) if related else None
        related_spans = term.number_spans(related.prefix, related_id, related.plain) if related else None
        code = _Text(_code(self.code_prefix, task.id), term.number_spans(self.code_prefix, task.id))
        title, subtitle = self.describe(task, term, related_code, related_spans, ctx)
        goods, quantity = _matched_goods(term, self.goods_of(task))
        _add_goods(subtitle, term, goods).add(_name(warehouse))
        columns = {
            'task_type': self.name,
            'related_code': related_code,
            'related_url_hint': _hint(self.related_page, related_id) if self.related_page else None,
            'status': task.status,
            'warehouse_name': _name(warehouse),
        } if with_columns else None
        spans = {'related_code': related_spans} if related else {}
        return (code, title, subtitle, task.status, getattr(warehouse, 'id', None), columns, spans,
                _goods_ref(goods, quantity))


class _SortingSearch(_TaskSearch):
    name, permission, model = 'sorting', 'sorting_read', SortingTask
    code_prefix, related_page = 'SORT', 'asn'
    numbers = (_Number(SortingTask.id, 'SORT', True, 'number'),
               _Number(SortingTask.asn_id, 'ASN', True, 'asn_number'))

    def base_query(self, scope, company_id):
        return SortingTaskService.list_tasks(scope)

    def matches(self, term):
        # 按所属 ASN 的商品明细（还没分拣的任务也搜得到）
        return _goods_matches(term, lambda c: SortingTask.asn.has(ASN.details.any(ASNDetail.goods.has(c))))

    def related(self, task):
        return task.asn_id, getattr(task.asn, 'warehouse', None)

    def goods_of(self, task):
        return [(detail.goods, detail.quantity) for detail in task.asn.details] if task.asn else []

    def describe(self, task, term, related_code, related_spans, ctx=None):
        return _Text(related_code, related_spans), _Text(_name(task.asn.supplier) if task.asn else None)


class _DNTaskSearch(_TaskSearch):
    """拣货 / 打包 / 发货任务：关联 DN，按所属 DN 的商品明细匹配（同拣货 / 打包列表）"""
    related_page = 'dn'

    def matches(self, term):
        model = self.model
        return _goods_matches(term, lambda c: model.dn.has(DN.details.any(DNDetail.goods.has(c))))

    def related(self, task):
        return task.dn_id, getattr(task.dn, 'warehouse', None)

    def goods_of(self, task):
        return [(detail.goods, detail.quantity) for detail in task.dn.details] if task.dn else []

    def describe(self, task, term, related_code, related_spans, ctx=None):
        subtitle = _Text()
        if task.dn is not None:
            subtitle.add(_name(task.dn.recipient)).add(task.dn.order_number)
        return _Text(related_code, related_spans), subtitle


class _PickingSearch(_DNTaskSearch):
    name, permission, model = 'picking', 'picking_read', PickingTask
    code_prefix = 'PICK'
    numbers = (_Number(PickingTask.id, 'PICK', True, 'number'),
               _Number(PickingTask.dn_id, 'DN', True, 'dn_number'))

    def base_query(self, scope, company_id):
        return PickingTaskService.list_tasks(scope)


class _PackingSearch(_DNTaskSearch):
    name, permission, model = 'packing', 'packing_read', PackingTask
    code_prefix = 'PACK'
    numbers = (_Number(PackingTask.id, 'PACK', True, 'number'),
               _Number(PackingTask.dn_id, 'DN', True, 'dn_number'))

    def base_query(self, scope, company_id):
        # 副标题要用 DN 明细的商品：整页预加载
        return PackingTaskService.list_tasks(scope).options(joinedload(PackingTask.dn).selectinload(DN.details))


class _DeliverySearch(_DNTaskSearch):
    name, permission, model = 'delivery', 'delivery_read', DeliveryTask
    code_prefix = 'DEL'
    numbers = (_Number(DeliveryTask.id, 'DEL', True, 'number'),
               _Number(DeliveryTask.dn_id, 'DN', True, 'dn_number'))

    def base_query(self, scope, company_id):
        return DeliveryTaskService.list_tasks(scope).options(joinedload(DeliveryTask.dn).selectinload(DN.details))

    def matches(self, term):
        """明细商品之外，按运单号：任务自己的，或所属 DN 的当前运单号（与 DN 列表同一口径：去空白 / 连字符，完整号码 exact，
        TRACKING_PARTIAL_MIN 位以上可以只输一段）"""
        matches = super().matches(term)
        if not term.fuzzy or not term.tracking:
            return matches
        own = _normalized_tracking_sql(DeliveryTask.tracking_number)
        current = _normalized_tracking_sql(tracking_number_expression())
        exact = or_(own == term.tracking, DeliveryTask.dn.has(current == term.tracking))
        partial = None
        if len(term.tracking) >= TRACKING_PARTIAL_MIN:
            partial = or_(own.like(f'%{_escape_like(term.tracking)}%', escape='!'),
                          DeliveryTask.dn.has(tracking_search_condition(term.text)))
        matches.append(_Match('tracking_number', RANK_EXACT, exact, narrow=partial is not None))
        if partial is not None:
            matches.append(_Match('tracking_number', RANK_CONTAINS, partial))
        return matches

    def prepare(self, objects, scope, company_id, with_columns):
        # 任务上还没有运单号（自动运单 / 文件方式导回之前）时副标题显示 DN 的当前运单号
        return shipping_rows(task.dn_id for task in objects)

    def describe(self, task, term, related_code, related_spans, ctx=None):
        title, subtitle = super().describe(task, term, related_code, related_spans, ctx)
        tracking = task.tracking_number or ((ctx or {}).get(task.dn_id) or {}).get('tracking_number')
        return title, subtitle.add(tracking, term.tracking_spans)


class _CycleCountSearch(_TaskSearch):
    """盘点：CC- 编号、任务名、明细商品"""
    name, permission, model = 'cyclecount', 'cycle_count_read', CycleCountTask
    code_prefix = 'CC'
    numbers = (_Number(CycleCountTask.id, 'CC', True, 'number'),)

    def base_query(self, scope, company_id):
        return CycleCountTaskService.list_tasks(scope)

    def matches(self, term):
        if not term.fuzzy:
            return []
        return ([_Match('task_name', RANK_CONTAINS, term.contains(CycleCountTask.task_name))]
                + _goods_matches(term, lambda c: CycleCountTask.task_details.any(CycleCountTaskDetail.goods.has(c))))

    def related(self, task):
        return None, task.warehouse

    def goods_of(self, task):
        return [(detail.goods, detail.system_quantity) for detail in task.task_details]

    def describe(self, task, term, related_code, related_spans, ctx=None):
        return _Text(task.task_name, term.text_spans), _Text()


class _AdjustmentSearch(_TaskSearch):
    """库存调整：ADJ- 编号、来源盘点单 CC-（同调整单列表：CC 只认带前缀的）、明细商品"""
    name, permission, model = 'adjustment', 'adjustment_read', Adjustment
    code_prefix, related_page = 'ADJ', 'cyclecount'
    numbers = (_Number(Adjustment.id, 'ADJ', True, 'number'),
               _Number(Adjustment.cyclecount_id, 'CC', False, 'cyclecount_number'))

    def base_query(self, scope, company_id):
        return AdjustmentService.list_adjustments(scope).options(selectinload(Adjustment.details))

    def matches(self, term):
        return _goods_matches(term, lambda c: Adjustment.details.any(AdjustmentDetail.goods.has(c)))

    def related(self, task):
        return task.cyclecount_id, task.warehouse

    def goods_of(self, task):
        return [(detail.goods, detail.adjustment_quantity) for detail in task.details]

    def describe(self, task, term, related_code, related_spans, ctx=None):
        # 标题 = 调整原因；没填原因时用来源盘点单号
        if not task.adjustment_reason:
            return _Text(related_code, related_spans), _Text()
        return _Text(task.adjustment_reason), _Text(related_code, related_spans)


class _GoodsSearch(_SearchType):
    """商品：编码（完全相同 = exact，大小写不敏感；其次前缀）、名称（包含）。公司级主数据，范围同商品列表（company_id，不按仓库）"""
    name, permission, model = 'goods', 'goods_read', Goods

    def base_query(self, scope, company_id):
        return GoodsService.list_goods({**scope, 'company_id': company_id})

    def matches(self, term):
        exact = _Match('goods_code', RANK_EXACT, term.equals(Goods.code), narrow=term.fuzzy)
        if not term.fuzzy:
            return [exact]
        return ([exact, _Match('goods_code', RANK_PREFIX, term.starts(Goods.code))]
                + _text_matches(term, Goods.name, 'name', exact=False, prefix=False))

    def prepare(self, objects, scope, company_id, with_columns):
        if not with_columns or not objects:
            return None
        # 可售 / 在库：调用方范围内（当前仓库 / 可访问仓库）的库存行合计，范围用库存列表的同一个函数
        query = InventoryService.list_inventories({**scope, 'company_id': company_id})
        stock = {}
        for inventory in query.filter(Inventory.goods_id.in_([goods.id for goods in objects])).all():
            total = stock.setdefault(inventory.goods_id, {'available_stock_for_sale': 0, 'onhand_stock': 0})
            total['available_stock_for_sale'] += inventory.available_stock_for_sale or 0
            total['onhand_stock'] += inventory.onhand_stock or 0
        return stock

    def build(self, goods, term, ctx, with_columns):
        code = _Text(goods.code, term.code_prefix_spans)
        title = _Text(goods.name, term.text_spans)
        subtitle = _Text(goods.brand).add(goods.category)
        columns = None
        if with_columns:
            stock = (ctx or {}).get(goods.id) or {'available_stock_for_sale': 0, 'onhand_stock': 0}
            columns = {'code': goods.code, 'name': goods.name, 'brand': goods.brand,
                       'thumbnail_url': goods.thumbnail_url, **stock}
        spans = {'code': term.code_prefix_spans, 'name': term.text_spans}
        return code, title, subtitle, None, None, columns, spans, None


class _LocationSearch(_SearchType):
    """库位：库位编码（相同 / 前缀 / 包含）"""
    name, permission, model = 'location', 'location_read', Location

    def base_query(self, scope, company_id):
        return LocationService.list_locations(scope)

    def matches(self, term):
        if not term.fuzzy:
            return [_Match('location_code', RANK_EQUAL, term.equals(Location.code))]
        return _text_matches(term, Location.code, 'location_code')

    def prepare(self, objects, scope, company_id, with_columns):
        if not with_columns or not objects:
            return None
        # 商品种数 / 件数（库位上数量 > 0 的商品）
        rows = (
            db.session.query(GoodsLocation.location_id, func.count(GoodsLocation.goods_id.distinct()),
                             func.coalesce(func.sum(GoodsLocation.quantity), 0))
            .filter(GoodsLocation.location_id.in_([location.id for location in objects]), GoodsLocation.quantity > 0)
            .group_by(GoodsLocation.location_id)
            .all()
        )
        return {location_id: (kinds, int(quantity)) for location_id, kinds, quantity in rows}

    def build(self, location, term, ctx, with_columns):
        code = _Text(location.code, term.code_spans)
        title = _Text(location.description)
        subtitle = _Text(_name(location.warehouse))
        columns = None
        if with_columns:
            kinds, quantity = (ctx or {}).get(location.id, (0, 0))
            columns = {'code': location.code, 'location_type': location.location_type,
                       'warehouse_name': _name(location.warehouse), 'goods_count': kinds, 'quantity': quantity}
        return code, title, subtitle, None, location.warehouse_id, columns, {'code': term.code_spans}, None


class _RecipientSearch(_SearchType):
    """收货方：名称、公司名。只返回 id、名称、所在城市级别信息（国家 / 州省 / 城市），不返回电话、邮箱、详细地址"""
    name, permission, model = 'recipient', 'recipient_read', Recipient

    def base_query(self, scope, company_id):
        return RecipientService.list_recipients({'company_id': company_id})

    def matches(self, term):
        if not term.fuzzy:
            return []
        return (_text_matches(term, Recipient.name, 'name')
                + [_Match('organization', RANK_CONTAINS, term.contains(Recipient.organization))])

    def build(self, recipient, term, ctx, with_columns):
        title = _Text(recipient.name, term.text_spans)
        subtitle = _Text(recipient.locality).add(recipient.admin_area).add(recipient.country)
        columns = {'name': recipient.name, 'locality': recipient.locality, 'admin_area': recipient.admin_area,
                   'country': recipient.country} if with_columns else None
        return _Text(), title, subtitle, None, None, columns, {'name': term.text_spans}, None


_TYPES = {kind.name: kind for kind in (
    _DNSearch(), _ASNSearch(), _SortingSearch(), _PickingSearch(), _PackingSearch(), _DeliverySearch(),
    _CycleCountSearch(), _AdjustmentSearch(), _GoodsSearch(), _LocationSearch(), _RecipientSearch(),
)}


# ---------------------------------------------------------------------------------------------
# 仓库范围
# ---------------------------------------------------------------------------------------------

@warehouse_required()
def _current_warehouse_scope():
    """scope=warehouse：与各列表接口同一套（@warehouse_required() 认 X-WAREHOUSE-ID / warehouse_id，
    add_warehouse_filter 转成 warehouse_id / warehouse_ids）"""
    return add_warehouse_filter({})


def _all_warehouses_scope():
    """scope=all：不看当前仓库，调用方可访问的全部启用仓库。
    员工（含绑定员工的 API Key）按 StaffService.accessible_warehouses：公司管理员 = 本公司全部仓库，其余 = 分配给自己的；
    绑定公司的 API Key = 该公司全部仓库（与 _enforce_api_key_warehouse_access 同一规则）；平台管理员 / 超级密钥不受限。
    一个仓库都没有 → 403（同列表）"""
    user = g.get('current_user')
    if user is not None:
        if user.type == 'staff':
            staff = db.session.get(Staff, user.id)
            if staff is None:
                raise ForbiddenException("Current user is not a staff member", 12005)
            g.accessible_warehouses = StaffService.accessible_warehouses(staff)
    elif getattr(g, 'current_system', None):
        company_id = getattr(g, 'api_key_company_id', None)
        if company_id:
            company = db.session.get(Company, company_id)
            g.accessible_warehouses = [w for w in (company.warehouses if company else []) if w.is_active]
    else:
        raise UnauthorizedException("No current user found", 11004)
    g.warehouse_id = None
    g.pop('warehouse', None)
    return add_warehouse_filter({})


# ---------------------------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------------------------

class GlobalSearchService:

    @staticmethod
    def parse_types(values) -> list:
        """types 参数（逗号分隔或重复给）→ 按默认顺序去重；不给 = 全部；不认识的类型 → 400 40000（field types）"""
        requested = []
        for value in values or []:
            requested += [part.strip().lower() for part in str(value).split(',') if part.strip()]
        if not requested:
            return list(SEARCH_TYPES)
        unknown = sorted({name for name in requested if name not in _TYPES})
        if unknown:
            raise BadRequestException(f"Unknown search type(s): {', '.join(unknown)}", 40000, field='types',
                                      details={'unknown': unknown, 'allowed': list(SEARCH_TYPES)})
        return [name for name in SEARCH_TYPES if name in requested]

    @staticmethod
    def parse_scope(value) -> str:
        scope = str(value or SCOPE_WAREHOUSE).strip().lower()
        if scope not in SCOPES:
            raise BadRequestException(f"scope must be one of: {', '.join(SCOPES)}", 40000, field='scope')
        return scope

    @staticmethod
    def readable_types(types) -> list:
        """调用方有查看权限的类型（权限判断同 permission_required：JWT 用户看生效角色，API Key 看 key 的权限）"""
        _, permissions, _ = get_actor_context()
        if permissions & SUPER_PERMISSIONS:
            return list(types)
        return [name for name in types if _TYPES[name].permission in permissions]

    @staticmethod
    def resolve_scope(scope) -> dict:
        """→ add_warehouse_filter 的结果（{} / {'warehouse_id'} / {'warehouse_ids'}），各列表的 list_* 直接认"""
        return _all_warehouses_scope() if scope == SCOPE_ALL else _current_warehouse_scope()

    @staticmethod
    def search(q, types, warehouse_scope: dict, company_id, scope=SCOPE_WAREHOUSE,
               limit=None, page=None, per_page=None, single=None) -> dict:
        """types 已按权限过滤。single（请求里只指定了一类；默认看 types）：按 page / per_page 分页并带列表列（columns），
        否则每类取前 limit 条"""
        term = SearchTerm(q)
        single = len(types) == 1 if single is None else single and len(types) == 1
        size = per_page if single and per_page else limit
        size = max(1, min(size or DEFAULT_LIMIT, MAX_LIMIT))
        page = max(1, page or 1) if single else 1
        offset = (page - 1) * size

        groups = []
        for name in types:
            total, items = (_TYPES[name].search(term, warehouse_scope, company_id, offset, size, single)
                            if term.text else (0, []))
            groups.append({'type': name, 'total': total, 'page': page, 'per_page': size,
                           'pages': math.ceil(total / size) if total else 0, 'items': items})

        # 有完整单号 / 运单号命中的组排前面（组内 exact 已按 rank 排在最前）
        groups.sort(key=lambda group: not any(item['exact'] for item in group['items']))
        return {
            'q': term.text,
            'scope': scope,
            'types': list(types),
            'counts': {group['type']: group['total'] for group in sorted(groups, key=lambda g_: types.index(g_['type']))},
            'exact_matches': [item for group in groups for item in group['items'] if item['exact']],
            'groups': groups,
        }
