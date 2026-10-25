"""DN 在承运商系统自动建运单（承运商对接插件，见 warehouse/carrier/integrations；内置 FedEx）。

替代「在承运商网站手工建运单 + 保存运单号」这一步，其余流程不变。本模块与具体承运商、国内外无关，调承运商一律经插件
（CarrierAdapter：check_shipment / upload_documents / create_shipment / label_archive / cancel_shipment）。
跨境要报关的件另带报关扩展（warehouse/dn/customs_extension.py：CI / PL、commodities / invoice、贸易单证 / ETD），
国内件（或同一关税同盟内）不碰报关；国内 / 跨境按 warehouse/shipping/route.py 判断。
- 用哪家：当前有效发货任务的承运商（没有用 DN 的），该承运商在承运商编辑页设置了在线对接（carrier_integrations）且已启用、
  设置齐全；没设对接的承运商照旧手工建运单、保存运单号（同时写 source = manual 的运单记录，见 warehouse/shipping/waybills.py）。
- 本模块的记录查询（active_shipment / open_shipment / unresolved_shipment ……）与锁定规则只看 source = api 的运单
  （插件建的、在承运商那边真实存在、换号要先取消）；manual / file 运单不受这些规则约束。
- 前置条件（blockers）全部满足才建单：packed、承运商有可用的对接、插件能承运这票货的范围（scopes，否则
  SCOPE_NOT_SUPPORTED）、要报关时有报关快照（CUSTOMS_DATA_MISSING）且插件能带报关（CUSTOMS_NOT_SUPPORTED）、
  没有有效运单也没有手工存过运单号、有箱子（不超过插件的箱数上限）、收件人姓名 / 电话齐全；报关件另加报关没有错误级问题；
  再加插件自己的检查（地址放不放得下、贸易条件与关税付款方是否矛盾……）。
- 每次建单请求在 dn_carrier_shipments 留一条记录（source = api，carrier = 插件代码，carrier_id = 承运商），分三段，
  调承运商时不占 DN 行锁、也不开着数据库事务：
  1. 锁 DN → 有结果不明的记录（unresolved）→ 409 16079；前置条件不满足 → 409 16072；报关件没有当前有效的 CI / PL
     （或已过期）就先用现有逻辑签发；组好交给插件的数据（含对接设置的快照）；插一条 pending 记录并提交（部分唯一索引兜底并发）。
  2. 插件开了电子单证时先上传 CI，再建单。整个建单流程受 CARRIER_CREATE_BUDGET_SECONDS 总时限约束，
     每次请求的超时按剩余时间收缩，不够就不发（worker 超时被杀前收手；真被杀了 pending 过期后按结果不明处理）。
  3. 成功：先独立提交拿到的运单号，再在一个事务里锁 DN → 记录置 active → 主运单号走现有保存运单号的逻辑存到
     发货任务 → 面单存 dn_documents（shipping_label）。CI / PL 不印运单号，数据没变时重签不出新版本。
  结果：承运商明确拒绝 / 请求没发出去 → failed；读超时、连接中断、5xx、响应看不懂 → unknown（结果不明）；
  第 3 步写库失败 → 先另开事务重读记录：已是 active 且运单号相同（提交其实成功、只是调用方收到了异常）→ 不取消，
  按成功返回；读不到 → 不取消，记 unknown（reason save_unconfirmed，带运单号）；确实没写进去 → 取消刚建的运单（补偿）：
  确认取消 → cancelled（reason compensated），取消失败 / 不确定 → unknown（reason compensation_failed，带运单号）。
  补偿结果用独立事务提交，原事务回滚后仍留痕；接口一律 502 16095（details.unresolved / compensation / error）。
- pending 超过 CARRIER_PENDING_STALE_MINUTES（不短于建单时限 + 补偿时间 + 余量）读取时按 unknown（reason stale）处理。
- 有结果不明的记录（pending / unknown / cancelling）时：不能再建单、不能保存 / 修改运单号、不能完成发货（409 16079），
  不能改报关数据 / 箱子 / 重签与运单不一致的单证（16076），不能新建 / 删除发货任务（16078）。
  带运单号的 unknown 可以 POST …/carrier-shipment/cancel 重试取消（成功 → cancelled）；确认作废
  POST …/carrier-shipment/dismiss：没有运单号的由操作员确认后直接 dismissed；带运单号的先向承运商取消，
  承运商确认已取消 / 查无此运单才 dismissed，否则 409 16094。之后才能重新建单。
- 面单打印方式（label_format）由建单请求指定：A4（普通打印机）/ THERMAL（热敏标签机），不指定用对接设置里的默认；
  各格式的文件格式 / 纸张由插件决定（CarrierAdapter.label_options）。
- 运送申告价额高于已打包货值时自动压到已打包货值（承运商拒收申告价额高于报关货值的运单；部分打包时只保实际发出的货），
  实际提交的值记在运单的 declared_value 上，响应 warnings 里说明。
- 有有效自动运单时，发货任务的运单号只能是这张运单的号码（完成发货 / 保存运单号 / 修改发货任务传了别的号码 → 409 16078），
  比对忽略空格 / 连字符，入库统一存运单上的标准写法；承运商也不能换（16078）。
  已取消（含补偿取消）的自动运单号不能再存到任务上、也不能用来完成发货（16078，details.status = cancelled）。
- 取消：DN 未发货才可；用建单时的承运商（carrier_id）的对接设置（停用了也能取消）和建单时记下的发件国。
  与建单一样分三段，调承运商时不占 DN 行锁、也不开着数据库事务：
  1. 锁 DN → 有效运单标 cancelling（reason cancel_in_progress）并提交（之后完成发货 / 存运单号 16079）；
  2. 调承运商取消（受 CARRIER_CREATE_BUDGET_SECONDS 约束）；
  3. 锁 DN → 取消成功（或回「已取消 / 查无此运单」）→ cancelled、发货任务上的运单号清掉、面单作废；
     承运商明确拒绝 / 请求没发出去 → 回到 active；
     结果不明（超时、5xx、响应看不懂）→ 停在 cancelling（reason 记原因），
     拦发货，重试取消（或确认作废）到承运商给出明确结果为止。第 3 段写库失败 / 进程被杀 → 停在 cancelling
     （save_failed / interrupted；什么都没记下的 cancel_in_progress 过期后按 stale），重试取消时承运商回「已取消」即落库。
- 取消 / 确认作废的请求可带目标标识（tracking_number / unresolved_id），与当前记录不符 → 409 16093。
"""
import hashlib
import logging
import re
from datetime import date, datetime

from flask import current_app, g
from sqlalchemy.exc import IntegrityError

from extensions.db import db
from warehouse.common import clock as wms_clock      # 时刻 / 时区（clock 是承运商时限用的单调时钟）
from extensions.error import (
    APIException, BadGatewayException, BadRequestException, ConflictException, GatewayTimeoutException,
)
from extensions.transaction import transactional
from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.integrations import display_name, portal_name
from warehouse.carrier.integrations.base import LABEL_FORMATS, STEP_CREATE, CarrierError, CarrierResponseError, clock
from warehouse.carrier.methods import tracking_url
from warehouse.carrier.models import Carrier
from warehouse.common.address_format import dn_ship_to, latin_view, warehouse_address
from warehouse.common.countries import COUNTRY_NAMES_EN
from warehouse.shipping.common import LOCKED_DN_STATUSES
from warehouse.shipping.route import scope_of, shipping_route
from .customs_extension import CustomsExtension, allocate_commodity_weights  # noqa: F401  allocate_* 旧的引用位置
from .customs_services import CustomsService
from .models import DN, DNCarrierShipment, DNDocument

logger = logging.getLogger(__name__)

DOC_LABEL = 'shipping_label'
COMPLETED_TASK_STATUSES = ('completed', 'signed')
SOURCE_API = 'api'
_FILE_SAFE = re.compile(r'[^A-Za-z0-9._-]+')

OPEN_STATUSES = DNCarrierShipment.OPEN_STATUSES              # pending / active / unknown / cancelling：同一 DN 至多一条
UNRESOLVED_STATUSES = DNCarrierShipment.UNRESOLVED_STATUSES  # pending / unknown / cancelling
LIVE_STATUSES = ('active', 'cancelling')                     # 运单在承运商那边建好了、运单号 / 面单都在（含正在取消）
CANCEL_IN_PROGRESS = 'cancel_in_progress'                    # cancelling 的 reason：正在调承运商取消

DEFAULT_CREATE_BUDGET_SECONDS = 90
DEFAULT_PENDING_STALE_MINUTES = 10
# 写库失败后的补偿取消，在建单时限之外再给的时间
COMPENSATION_EXTRA_SECONDS = 15
# pending 判定为卡住的时间下限 = 建单时限 + 补偿时间 + 这个余量（防止把还在进行中的请求当成卡住）
STALE_MARGIN_SECONDS = 60
ERROR_MESSAGE_MAX = 500


def _maybe_created(code) -> str:
    return (f" The shipment may have been created on the {display_name(code)} side: check {portal_name(code)}, "
            "cancel it there if it exists, then confirm with POST /warehouse/dn/<id>/carrier-shipment/dismiss "
            "before creating it again.")


def _cancel_retry(code) -> str:
    return (f" Cancel it from WMS (POST /warehouse/dn/<id>/carrier-shipment/cancel), or cancel it in "
            f"{portal_name(code)} and then dismiss the record (POST /warehouse/dn/<id>/carrier-shipment/dismiss; "
            f"{display_name(code)} is asked to confirm that it is cancelled).")


def _maybe_cancelled(code) -> str:
    return (f" The shipment may or may not have been cancelled on the {display_name(code)} side; the DN cannot be "
            "shipped until this is resolved: retry the cancellation (POST /warehouse/dn/<id>/carrier-shipment/cancel).")


def _blocker(code, message, **extra):
    item = {'code': code, 'message': message}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _config_number(key, default, minimum) -> float:
    value = current_app.config.get(key)
    try:
        number = float(value) if value not in (None, '') else float(default)
    except (TypeError, ValueError):
        number = float(default)
    return max(number, minimum)


def create_budget_seconds() -> float:
    """建单（含令牌 / 电子单证上传 / 建单请求）的总时限，CARRIER_CREATE_BUDGET_SECONDS（默认 90 秒）"""
    return _config_number('CARRIER_CREATE_BUDGET_SECONDS', DEFAULT_CREATE_BUDGET_SECONDS, 10)


def stale_after_seconds() -> float:
    """pending 超过这么久按结果不明（stale）处理：CARRIER_PENDING_STALE_MINUTES（默认 10 分钟），
    但不短于建单时限 + 补偿时间 + 余量"""
    configured = _config_number('CARRIER_PENDING_STALE_MINUTES', DEFAULT_PENDING_STALE_MINUTES, 1) * 60
    return max(configured, create_budget_seconds() + COMPENSATION_EXTRA_SECONDS + STALE_MARGIN_SECONDS)


def parse_label_format(value):
    """建单请求的 label_format（A4 / THERMAL，大小写不限；空 = 对接设置里的默认）→ 大写值或 None；不合法 400 16077。
    插件不支持该格式时建单那一步再报 16077。"""
    if value is not None and not isinstance(value, str):
        raise BadRequestException("label_format must be one of " + ' / '.join(LABEL_FORMATS), 16077,
                                  field='label_format')
    value = (value or '').strip().upper() or None
    if value is not None and value not in LABEL_FORMATS:
        raise BadRequestException("label_format must be one of " + ' / '.join(LABEL_FORMATS), 16077,
                                  field='label_format')
    return value


def _carrier_code(carrier) -> str:
    return ((getattr(carrier, 'code', None) or '')).strip().lower()


def normalize_tracking(value) -> str:
    """运单号比对用的规范化写法：去掉所有空白和连字符（面单分组写法「7946 0000 0001」/「7946-0000-0001」
    与运单号 794600000001 视为同一个）。只用于比较，入库仍存原值或运单上的标准写法。"""
    return ''.join(str(value or '').split()).replace('-', '')


def parse_target_tracking(value):
    """POST …/cancel 请求体里的 tracking_number（操作员看到的、要取消的运单号）：不带 / null / 空串 = 不校验；
    字符串或数字照收（去首尾空白）；其它类型 → 400 40000（field tracking_number）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BadRequestException("tracking_number must be a string", 40000, field='tracking_number')
    return str(value).strip() or None


def parse_target_id(value):
    """POST …/dismiss 请求体里的 unresolved_id（操作员核对过的那条记录的 id）：不带 / null = 不校验；
    正整数（或正整数字符串）照收；其它 → 400 40000（field unresolved_id）。"""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BadRequestException("unresolved_id must be a positive integer", 40000, field='unresolved_id')
    return value


def _target_mismatch(message: str, expected, actual) -> ConflictException:
    """409 16093：请求里带的目标（expected = 请求里的值）与当前记录（actual = 服务端现在的值，没有为 null）不符"""
    return ConflictException(message, 16093, details={'expected': expected, 'actual': actual})


def _unclear_reason(err: CarrierError) -> str:
    """结果不明的原因：timeout / server_error / bad_response / connection_error"""
    if err.timeout:
        return 'timeout'
    if err.status_code and err.status_code >= 500:
        return 'server_error'
    if err.status_code:
        return 'bad_response'
    return 'connection_error'


def split_evenly(total: int, count: int) -> list:
    """整数 total 均分成 count 份，余数从第一份起每份 +1；合计等于 total（申告价额按箱分）"""
    if count <= 0:
        return []
    base, remainder = divmod(int(total), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _carrier_api_exception(err: CarrierError, action: str, code: str, unresolved=None, with_unresolved=False):
    """CarrierError → 502 16073 / 504 16074；details 带承运商的 errors 与 transactionId（不带凭证）、
    maybe_processed（结果不明）；建单时再带 unresolved（结果不明的记录，没有则 null）。"""
    name = display_name(code)
    details = {
        'carrier': code,
        'action': action,
        'http_status': err.status_code,
        'transaction_id': err.transaction_id,
        'errors': err.errors,
        'maybe_processed': err.maybe_processed,
    }
    if with_unresolved:
        details['unresolved'] = unresolved
    maybe_created = ''
    if err.maybe_processed and action == 'create':
        maybe_created = _maybe_created(code)
    elif err.maybe_processed and action == 'cancel':
        maybe_created = _maybe_cancelled(code)
    if err.timeout:
        if err.budget_exhausted:
            details['budget_exhausted'] = True
            what = 'creating a shipment' if action in ('create', 'etd_upload') else f'the {name} request'
            message = (f"{name} request was not sent ({action}): the time allowed for {what} "
                       "(CARRIER_CREATE_BUDGET_SECONDS) ran out. Try again.")
        else:
            message = f"{name} did not respond in time ({action})." + maybe_created
        return GatewayTimeoutException(message, 16074, details=details)
    if err.permission_denied:
        details['permission_denied'] = True
        message = (f"{name} refused the request ({action}, HTTP {err.status_code}): check the API keys in the carrier "
                   f"settings and that the {name} account has the required API enabled. {err.message}")
    elif err.maybe_processed:
        message = f"{name} request failed with an unclear result ({action}): {err.message}." + maybe_created
    else:
        message = f"{name} rejected the request ({action}): {err.message}"
    return BadGatewayException(message, 16073, details=details)


class CarrierShipmentService:

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def carrier_of(dn: DN, task=None):
        """建单用的承运商：当前有效发货任务的承运商，没有则 DN 的（与印在 CI 上的同一口径）"""
        if task is None:
            task = CustomsService._delivery_task(dn)
        return (task.carrier if task is not None and task.carrier is not None else None) or dn.carrier

    @staticmethod
    def integration_of(dn: DN, task=None):
        """→ (承运商, 对接设置快照 | None, 适配器 | None)。承运商不属于 DN 的公司时按没有对接处理。"""
        carrier = CarrierShipmentService.carrier_of(dn, task)
        snapshot = None
        if carrier is not None and carrier.company_id == dn.warehouse.company_id:
            snapshot = CarrierIntegrationService.for_carrier(carrier)
        return carrier, snapshot, CarrierIntegrationService.build(snapshot)

    @staticmethod
    def enabled(dn: DN) -> bool:
        """DN 的承运商有可用的对接（已启用、设置齐全），可以自动建运单"""
        _carrier, snapshot, adapter = CarrierShipmentService.integration_of(dn)
        return snapshot is not None and not CarrierIntegrationService.problems(snapshot, adapter)

    @staticmethod
    def is_shipment_carrier(record: DNCarrierShipment, carrier) -> bool:
        """carrier 是不是这张自动运单的承运商：记录有 carrier_id 时比 id；旧记录比承运商的对接插件或 code"""
        if carrier is None:
            return False
        if record.carrier_id is not None:
            return carrier.id == record.carrier_id
        code = (record.carrier or '').strip().lower()
        integration = carrier.integration
        return _carrier_code(carrier) == code or (integration is not None and integration.adapter == code)

    @staticmethod
    def _latest(dn: DN, statuses):
        """最新一条插件建的运单记录（source = api）：本模块的查询与锁定规则只对 api 运单生效。
        与来源无关的当前运单见 warehouse/shipping/waybills.py（current_waybill / active_waybill）。"""
        return (
            DNCarrierShipment.query
            .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.source == SOURCE_API,
                    DNCarrierShipment.status.in_(statuses))
            .order_by(DNCarrierShipment.id.desc())
            .first()
        )

    @staticmethod
    def active_shipment(dn: DN):
        """有效运单（status = active）"""
        return CarrierShipmentService._latest(dn, ('active',))

    @staticmethod
    def live_shipment(dn: DN):
        """有效运单或正在取消 / 取消结果不明的运单（active / cancelling）：运单号与面单都还在"""
        return CarrierShipmentService._latest(dn, LIVE_STATUSES)

    @staticmethod
    def open_shipment(dn: DN):
        """进行中 / 有效 / 结果不明 / 取消中的记录（pending / active / unknown / cancelling，同一 DN 至多一条）"""
        return CarrierShipmentService._latest(dn, OPEN_STATUSES)

    @staticmethod
    def unresolved_shipment(dn: DN):
        """进行中 / 结果不明 / 取消中的记录（pending / unknown / cancelling）"""
        return CarrierShipmentService._latest(dn, UNRESOLVED_STATUSES)

    @staticmethod
    def latest_shipment(dn: DN):
        """GET 里的 shipment：有效 / 取消中的运单优先，否则最近一张已取消的（failed / unknown / dismissed 不算运单）"""
        return CarrierShipmentService.live_shipment(dn) or CarrierShipmentService._latest(dn, ('cancelled',))

    @staticmethod
    def has_active_shipment(dn: DN) -> bool:
        """有 pending / active / unknown / cancelling 的记录（进行中或结果不明时箱子可能已随运单提交）"""
        return CarrierShipmentService.open_shipment(dn) is not None

    @staticmethod
    def _expired(timestamp) -> bool:
        return timestamp is not None and (wms_clock.now() - timestamp).total_seconds() > stale_after_seconds()

    @staticmethod
    def is_stale(record: DNCarrierShipment) -> bool:
        """卡住的记录：pending 自建单起 / cancelling（cancel_in_progress）自标记起超过 stale_after_seconds()"""
        if record is None:
            return False
        if record.status == 'pending':
            return CarrierShipmentService._expired(record.created_at)
        if record.status == 'cancelling' and record.reason == CANCEL_IN_PROGRESS:
            return CarrierShipmentService._expired(record.updated_at)
        return False

    @staticmethod
    def effective_status(record: DNCarrierShipment):
        """卡住的 pending 按 unknown（reason stale）、卡住的 cancel_in_progress 按 cancelling（reason stale）对外"""
        if CarrierShipmentService.is_stale(record):
            return ('unknown' if record.status == 'pending' else record.status), 'stale'
        return record.status, record.reason

    @staticmethod
    def cancel_in_progress(record: DNCarrierShipment) -> bool:
        """有请求正在调承运商取消这张运单（cancelling + cancel_in_progress，未过期）"""
        return (record is not None and record.status == 'cancelling'
                and CarrierShipmentService.effective_status(record)[1] == CANCEL_IN_PROGRESS)

    @staticmethod
    def dismissable(record: DNCarrierShipment) -> bool:
        """可以确认作废：结果不明（unknown，含卡住的 pending）或取消结果不明（cancelling，没有请求正在取消）"""
        if record is None or record.status not in UNRESOLVED_STATUSES:
            return False
        status, reason = CarrierShipmentService.effective_status(record)
        return status == 'unknown' or (status == 'cancelling' and reason != CANCEL_IN_PROGRESS)

    @staticmethod
    def cancel_target(dn: DN):
        """可以向承运商取消的记录：有效运单（active）、取消中 / 取消结果不明（cancelling）、
        带运单号的结果不明记录（unknown，含卡住的 pending，例如补偿取消失败）。没有返回 None。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is None:
            return None
        status, _reason = CarrierShipmentService.effective_status(record)
        if status in LIVE_STATUSES or (status == 'unknown' and record.tracking_number):
            return record
        return None

    @staticmethod
    def shipment_payload(record: DNCarrierShipment):
        """GET 里的 shipment（to_dict；cancelling 的 reason 按 effective_status，过期的 cancel_in_progress 为 stale）"""
        if record is None:
            return None
        payload = record.to_dict()
        payload['status'], payload['reason'] = CarrierShipmentService.effective_status(record)
        # 揽收预约概要（运单还有效时才有意义）；揽收服务依赖本模块，延迟导入
        from .pickup_services import CarrierPickupService
        payload['pickup'] = (CarrierPickupService.summary_for_shipment(record)
                             if record.status in LIVE_STATUSES else None)
        # 公开查询页：承运商设置的模板，没有用插件内置的；跟踪方式 none 时不给
        carrier = db.session.get(Carrier, record.carrier_id) if record.carrier_id else None
        payload['tracking_url'] = (
            tracking_url(carrier, record.tracking_number, record.carrier)
            if record.tracking_number and getattr(carrier, 'tracking_method', None) != 'none' else None)
        return payload

    @staticmethod
    def unresolved_payload(record: DNCarrierShipment):
        """unresolved：{id, status: pending | unknown | cancelling, reason, tracking_number, transaction_id,
        created_at, updated_at}"""
        if record is None or record.status not in UNRESOLVED_STATUSES:
            return None
        status, reason = CarrierShipmentService.effective_status(record)
        return {
            'id': record.id,
            'status': status,
            'reason': reason,
            'tracking_number': record.tracking_number,
            'transaction_id': record.transaction_id,
            'created_at': record.created_at.isoformat() if record.created_at else None,
            'updated_at': record.updated_at.isoformat() if record.updated_at else None,
        }

    @staticmethod
    def unresolved(dn: DN):
        return CarrierShipmentService.unresolved_payload(CarrierShipmentService.unresolved_shipment(dn))

    @staticmethod
    def _unresolved_conflict(record, message=None):
        """409 16079：有结果不明（或进行中）的建单记录，或运单正在取消 / 取消结果不明"""
        payload = CarrierShipmentService.unresolved_payload(record)
        code = record.carrier if record is not None else None
        name = display_name(code)
        if message is None:
            if payload and payload['status'] == 'pending':
                message = f"A {name} shipment request for this DN is still in progress; wait for it to finish."
            elif payload and payload['status'] == 'cancelling':
                message = CarrierShipmentService._cancelling_message(record)
            elif payload and payload['tracking_number']:
                message = (f"{name} shipment {payload['tracking_number']} of this DN was not saved in WMS and may "
                           f"still exist on the {name} side." + _cancel_retry(code))
            else:
                message = (f"The result of the previous {name} shipment request for this DN is unclear."
                           + _maybe_created(code))
        return ConflictException(message, 16079, details={'unresolved': payload})

    @staticmethod
    def _cancelling_message(record) -> str:
        name = display_name(record.carrier)
        if CarrierShipmentService.cancel_in_progress(record):
            return (f"{name} shipment {record.tracking_number} of this DN is being cancelled; wait for the "
                    "cancellation to finish.")
        return (f"The result of cancelling {name} shipment {record.tracking_number} of this DN is unclear; the DN "
                "cannot be shipped and the tracking number cannot be changed until it is resolved: retry the "
                "cancellation (POST /warehouse/dn/<id>/carrier-shipment/cancel).")

    # ------------------------------------------------------------------
    # 前置条件与建单数据
    # ------------------------------------------------------------------

    # 报关件的发件人 / 收件人 / 申告价额搬到了报关扩展（customs_extension.py），这里保留旧名
    _sender_country = staticmethod(CustomsExtension.sender_country)
    _shipper = staticmethod(CustomsExtension.shipper)
    _recipient = staticmethod(CustomsExtension.recipient)
    _declared_value = staticmethod(CustomsExtension.declared_value)

    @staticmethod
    def _collect(dn: DN) -> dict:
        """前置条件检查 + 建单要用的数据：
        {blockers, warnings, declared_value, view（报关视图，报关件才有）, task, carrier, integration（快照）, adapter,
        route, customs（带报关扩展）, shipment（交给插件的数据，不含 ship_date / documents）}"""
        blockers = []
        ctx = {'blockers': blockers, 'warnings': [], 'declared_value': None, 'view': None, 'task': None,
               'carrier': None, 'integration': None, 'adapter': None, 'route': None, 'customs': False,
               'shipment': None}

        task = CustomsService._delivery_task(dn)
        carrier, snapshot, adapter = CarrierShipmentService.integration_of(dn, task)
        route = shipping_route(dn)
        customs = CustomsExtension.applies(dn, route)
        ctx.update(task=task, carrier=carrier, integration=snapshot, adapter=adapter, route=route, customs=customs)

        if dn.status in LOCKED_DN_STATUSES:
            blockers.append(_blocker('DN_SHIPPED', f"DN has already been shipped (status: {dn.status})"))
        elif dn.status != 'packed':
            blockers.append(_blocker('NOT_PACKED', f"DN must be packed (current: {dn.status})"))

        if task is None:
            blockers.append(_blocker('DELIVERY_TASK_MISSING', "DN has no active delivery task"))
        if carrier is None:
            blockers.append(_blocker('CARRIER_NOT_SET', "Choose the carrier of the delivery task first"))
        elif snapshot is None:
            blockers.append(_blocker(
                'CARRIER_NOT_INTEGRATED',
                f"Carrier {carrier.name} has no online integration: create the shipment on the carrier's website "
                "and save the tracking number, or set up the integration in the carrier settings",
                carrier_id=carrier.id))
        else:
            for item in CarrierIntegrationService.problems(snapshot, adapter):
                item.setdefault('carrier_id', carrier.id)
                blockers.append(item)
        # 国内 / 跨境：插件能不能承运这票货、要报关时有没有报关数据、插件能不能带报关（取代以前的 NOT_EXPORT）
        route_blockers = CarrierShipmentService._route_blockers(dn, route, adapter)
        blockers.extend(route_blockers)
        if task is not None and task.status in COMPLETED_TASK_STATUSES:
            blockers.append(_blocker('DELIVERY_TASK_COMPLETED', "Delivery task has already been completed"))

        active = CarrierShipmentService.live_shipment(dn)
        if active is not None:
            blockers.append(_blocker(
                'SHIPMENT_EXISTS',
                f"An active carrier shipment already exists ({active.tracking_number})" if active.status == 'active'
                else f"Carrier shipment {active.tracking_number} is being cancelled (or the result of cancelling it "
                     "is unclear)"))
        elif task is not None and task.tracking_number:
            blockers.append(_blocker(
                'TRACKING_NUMBER_EXISTS',
                f"Delivery task already has a tracking number ({task.tracking_number}); clear it first "
                "if the shipment should be created automatically"))

        packages = list(dn.packages)
        limit = adapter.max_packages if adapter is not None else None
        if not packages:
            blockers.append(_blocker('PACKAGES_MISSING', "No packages recorded"))
        elif limit and len(packages) > limit:
            blockers.append(_blocker('TOO_MANY_PACKAGES',
                                     f"At most {limit} packages per {adapter.name} shipment "
                                     f"(current: {len(packages)})", limit=limit))
        for package in packages:
            if not package.gross_weight_kg or not (package.length_mm and package.width_mm and package.height_mm):
                blockers.append(_blocker('PACKAGE_INCOMPLETE',
                                         f"Package {package.package_no} has no weight or dimensions"))

        if customs:
            # 报关件：报关问题并入 blockers、英文的发件人 / 收件人、commodities / invoice、申告价额
            parties = CustomsExtension.collect(dn, ctx, has_shipment=active is not None)
        else:
            parties = CarrierShipmentService._local_parties(dn, route, blockers)

        shipment = CarrierShipmentService._shipment_data(dn, route, ctx['declared_value'], parties, carrier)
        ctx['shipment'] = shipment
        # 插件本来就承运不了 / 带不了报关的件不再交给插件检查（地址格式等对不上号）
        if adapter is not None and not route_blockers:
            blockers.extend(adapter.check_shipment(shipment))
        return ctx

    @staticmethod
    def _route_blockers(dn: DN, route: dict, adapter) -> list:
        """国内 / 跨境相关的拦截：
        - CUSTOMS_DATA_MISSING：要报关（跨境且不在同一关税同盟）却没有报关快照
        - WAYBILL_NOT_SUPPORTED：插件不能建运单（只用来预约揽收 / 查跟踪）
        - SCOPE_NOT_SUPPORTED：插件的 scopes 不含这票货的范围（如 FedEx 只接跨境件）
        - CUSTOMS_NOT_SUPPORTED：要报关而插件不能带报关扩展"""
        blockers = []
        if route['requires_customs'] and dn.customs is None:
            blockers.append(_blocker(
                'CUSTOMS_DATA_MISSING',
                f"This is an international shipment ({route['origin_country']} → {route['destination_country']}) "
                "that needs customs data, but the DN has none: the source system must send the customs snapshot "
                "(PUT /warehouse/dn/<id>/customs)", field='customs'))
        if adapter is None:
            return blockers
        if not adapter.supports_waybill:
            blockers.append(_blocker('WAYBILL_NOT_SUPPORTED',
                                     f"The {adapter.name} integration cannot create shipments; enter the tracking "
                                     "number by hand"))
        scope = scope_of(route)
        scopes = tuple(adapter.scopes or ())
        if scope not in scopes:
            blockers.append(_blocker(
                'SCOPE_NOT_SUPPORTED',
                f"{adapter.name} only carries {' / '.join(scopes) or 'no'} shipments; this DN is {scope} "
                f"({route['origin_country'] or '?'} → {route['destination_country'] or '?'}). Choose another "
                "carrier or enter the tracking number by hand", scope=scope))
        if route['requires_customs'] and not adapter.supports_customs:
            blockers.append(_blocker('CUSTOMS_NOT_SUPPORTED',
                                     f"The {adapter.name} integration cannot submit customs data, which this "
                                     "international shipment needs"))
        return blockers

    @staticmethod
    def _local_parties(dn: DN, route: dict, blockers: list) -> dict:
        """不报关的件（国内 / 同一关税同盟内）的发件人 / 收件人：
        - 发件人：公司名（本地，没有用英文名）、仓库地址（本地，没有用英文；仓库都没有用公司的）、电话（仓库优先）
        - 收件人：DN 有结构化收件地址（ship_to）时按它（address_line1 / 2、city = locality、state = admin_area）；
          没有时 DN 收件人（名称 / 联系人 / 电话 / 邮箱 / 邮编）+ DN 的收件地址（一个字符串）
        两边另带 local（结构化地址，本地文字）与 latin（拉丁转写）。缺地址 / 姓名 / 电话 → blockers。
        commodities / invoice 为 None。"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address or '').strip() or (warehouse.address_en or '').strip():
            text, postal = warehouse.address or warehouse.address_en, warehouse.zip_code
        else:
            text, postal = company.address or company.address_en, company.zip_code
        origin = route['origin_country']
        shipper = {
            'company_name': company.name or company.legal_name_en,
            'person_name': warehouse.contact_name_en or company.export_contact_name,
            'phone': warehouse.phone or company.phone,
            'email': company.email,
            'address_text': (text or '').strip() or None,
            'postal_code': postal,
            'country_code': origin,
            'country_name': COUNTRY_NAMES_EN.get(origin or ''),
            'tax_id': company.tax_id,
        }
        shipper_local = warehouse_address(warehouse)
        shipper['local'] = shipper_local
        shipper['latin'] = latin_view(shipper_local)
        if not shipper['address_text']:
            blockers.append(_blocker('SHIPPER_ADDRESS_MISSING', "Neither the warehouse nor the company has an address",
                                     field='warehouse.address'))

        person = dn.recipient
        address = (dn.shipping_address or '').strip() or None
        destination = route['destination_country'] or origin       # 收件国未知按国内（与发货国相同）
        recipient = {
            'company_name': getattr(person, 'name', None),
            'person_name': getattr(person, 'contact', None) or getattr(person, 'name', None),
            'phone': getattr(person, 'phone', None),
            'email': getattr(person, 'email', None),
            'address_text': address,
            'address_line1': address,
            'address_line2': None,
            'city': None,
            'state': None,
            'postal_code': getattr(person, 'zip_code', None),
            'country_code': destination,
            'tax_id': None,
            'tax_id_type': None,
        }
        ship_to = dn_ship_to(dn)
        if dn.ship_to:
            # 结构化收件地址快照：姓名 / 电话 / 地址按它（没带的联系方式在 dn_ship_to 里已用收件人的补上）
            recipient.update({
                'company_name': ship_to.get('organization') or recipient['company_name'],
                'person_name': ship_to.get('name') or recipient['person_name'],
                'phone': ship_to.get('phone') or recipient['phone'],
                'email': ship_to.get('email') or recipient['email'],
                'address_line1': ' '.join(v for v in (ship_to.get('dependent_locality'), ship_to.get('address_line1'))
                                          if v) or address,
                'address_line2': ship_to.get('address_line2'),
                'city': ship_to.get('locality'),
                'state': ship_to.get('admin_area'),
                'postal_code': ship_to.get('postal_code') or recipient['postal_code'],
            })
        recipient['local'] = ship_to
        recipient['latin'] = latin_view(ship_to)
        if not (recipient['person_name'] or recipient['company_name']):
            blockers.append(_blocker('RECIPIENT_NAME_MISSING', "Recipient name is missing", field='recipient.name'))
        if not re.sub(r'\D', '', str(recipient['phone'] or '')):
            blockers.append(_blocker('RECIPIENT_PHONE_MISSING', "Recipient phone number is missing",
                                     field='recipient.phone'))
        if not address:
            blockers.append(_blocker('RECIPIENT_ADDRESS_MISSING', "DN shipping address is missing",
                                     field='shipping_address'))
        return {'shipper': shipper, 'recipient': recipient, 'commodities': None, 'invoice': None}

    @staticmethod
    def blockers(dn: DN) -> list:
        return CarrierShipmentService._collect(dn)['blockers']

    # ------------------------------------------------------------------
    # 其它模块的守卫
    # ------------------------------------------------------------------

    @staticmethod
    def _record_summary(record: DNCarrierShipment) -> dict:
        status, _reason = CarrierShipmentService.effective_status(record)
        return {'id': record.id, 'status': status, 'tracking_number': record.tracking_number}

    @staticmethod
    def assert_tracking_matches(dn: DN, tracking_number, allow_empty=False):
        """保存 / 修改发货任务运单号、完成发货前的检查（调用方应先锁 DN 行），返回应写入任务的运单号：
        - 有结果不明 / 进行中的建单记录：给了运单号 → 409 16079（防止在承运商网站上又手工建一张）；空值放行
        - 运单正在取消 / 取消结果不明（cancelling）：给了运单号（含同一个号码）→ 409 16079；
          空值且 allow_empty=False（清空任务上的运单号）也 16079（取消没落定前运单号与面单都要留着）
        - 有有效自动运单：运单号只能是它的号码（按 normalize_tracking 比，忽略空格 / 连字符差异），
          不同 → 409 16078；相同 → 返回运单上的标准写法 shipment.tracking_number（入库统一用它，
          取消运单时才能按号码清掉）。
          allow_empty=True：空值视为「不改」由调用方跳过；False：清空也算不一致（要清空请取消运单）。
        - 没有有效运单时，给的是本 DN 已取消（含补偿取消）的自动运单号 → 409 16078，
          details {tracking_number: null, cancelled_tracking_number, carrier, status: 'cancelled'}
          （tracking_number 与其它 16078 一样表示「只能用的号码」，这里没有，所以为 null）
        - 其它情况原样返回 tracking_number。"""
        given = normalize_tracking(tracking_number)
        unresolved = CarrierShipmentService.unresolved_shipment(dn)
        if unresolved is not None:
            if unresolved.status == 'cancelling':
                if given or not allow_empty:
                    raise CarrierShipmentService._unresolved_conflict(unresolved)
                return tracking_number
            if given:
                raise CarrierShipmentService._unresolved_conflict(
                    unresolved,
                    f"The result of the automatic {display_name(unresolved.carrier)} shipment request for this DN is "
                    "unclear (or still in progress); a tracking number cannot be saved until it is resolved. Check "
                    f"{portal_name(unresolved.carrier)}, then cancel or dismiss the record "
                    "(POST /warehouse/dn/<id>/carrier-shipment/cancel or /dismiss).")
            return tracking_number
        shipment = CarrierShipmentService.active_shipment(dn)
        if shipment is None:
            if given:
                CarrierShipmentService._assert_not_cancelled(dn, given)
            return tracking_number
        if not given and allow_empty:
            return tracking_number
        if given != normalize_tracking(shipment.tracking_number):
            raise ConflictException(
                f"This DN has an active {display_name(shipment.carrier)} shipment {shipment.tracking_number}; the "
                "tracking number cannot be changed. Cancel the carrier shipment first.", 16078,
                details={'tracking_number': shipment.tracking_number, 'carrier': shipment.carrier,
                         'shipment_carrier_id': shipment.carrier_id},
            )
        return shipment.tracking_number

    @staticmethod
    def _assert_not_cancelled(dn: DN, given: str):
        """given（已规范化）是本 DN 已取消（cancelled，含补偿取消 compensated）或已确认作废（dismissed）
        的自动运单号 → 409 16078。例：取消后 PDA 完成发货表单还停在旧的预填运单号。
        details.status 统一为 'cancelled'（前端按它显示「运单已取消」），record_status 是记录的实际状态。"""
        cancelled = (
            DNCarrierShipment.query
            .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.source == SOURCE_API,
                    DNCarrierShipment.status.in_(('cancelled', 'dismissed')),
                    DNCarrierShipment.tracking_number.isnot(None))
            .order_by(DNCarrierShipment.id.desc())
            .all()
        )
        for record in cancelled:
            if normalize_tracking(record.tracking_number) == given:
                raise ConflictException(
                    f"{display_name(record.carrier)} shipment {record.tracking_number} of this DN has been "
                    f"{record.status}; its tracking number cannot be used any more. Create a new shipment or enter "
                    "another tracking number.", 16078,
                    details={'tracking_number': None, 'cancelled_tracking_number': record.tracking_number,
                             'carrier': record.carrier, 'shipment_carrier_id': record.carrier_id,
                             'status': 'cancelled', 'record_status': record.status},
                )

    @staticmethod
    def _resolve_hint(status: str, code=None) -> str:
        """「先处理运单」的提示（按记录的对外状态；code 是记录的插件代码）"""
        if status == 'active':
            return "cancel the carrier shipment first"
        if status == 'pending':
            return "wait for the carrier shipment request to finish"
        if status == 'cancelling':
            return ("the carrier shipment is being cancelled: wait for it, or retry the cancellation "
                    "(POST /warehouse/dn/<id>/carrier-shipment/cancel)")
        return (f"resolve the carrier shipment first (check {portal_name(code)}, then cancel or dismiss it "
                "via POST /warehouse/dn/<id>/carrier-shipment/cancel or /dismiss)")

    @staticmethod
    def assert_delivery_tasks_editable(dn: DN, action: str):
        """有 pending / active / unknown / cancelling 的自动运单时，不能新建 / 删除发货任务 → 409 16078
        （新任务会成为「当前发货任务」，绕过运单号锁定）。调用方应先锁 DN 行。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is None:
            return
        summary = CarrierShipmentService._record_summary(record)
        hint = CarrierShipmentService._resolve_hint(summary['status'], record.carrier)
        raise ConflictException(
            f"This DN has a {display_name(record.carrier)} shipment ({summary['status']}"
            f"{' ' + record.tracking_number if record.tracking_number else ''}); delivery tasks cannot be "
            f"{action}; {hint}.", 16078,
            details={'tracking_number': record.tracking_number, 'carrier': record.carrier,
                     'shipment_carrier_id': record.carrier_id,
                     'status': summary['status']},
        )

    @staticmethod
    def open_shipment_conflict(record: DNCarrierShipment, what: str,
                               reason: str = 'CARRIER_SHIPMENT_OPEN') -> ConflictException:
        """409 16076（改箱子 / 改报关快照 / 签发与运单不一致的单证）：
        details {reason, status, carrier, carrier_shipment: {id, status, tracking_number}}。
        reason：CARRIER_SHIPMENT_OPEN（有未结束的运单时改箱子 / 报关数据）/
        DOCUMENT_DATA_CHANGED（单证数据与随运单提交的不一致）。status 与 carrier_shipment.status 相同。"""
        summary = CarrierShipmentService._record_summary(record)
        hint = CarrierShipmentService._resolve_hint(summary['status'], record.carrier)
        return ConflictException(
            f"{what} while the DN has a {display_name(record.carrier)} shipment ({summary['status']}"
            f"{' ' + record.tracking_number if record.tracking_number else ''}); the data was submitted with the "
            f"shipment — {hint}.", 16076,
            details={'reason': reason, 'status': summary['status'], 'carrier': record.carrier,
                     'carrier_shipment': summary},
        )

    @staticmethod
    def assert_customs_editable(dn: DN):
        """有 pending / active / unknown / cancelling 的自动运单时不能改报关数据（已随运单提交给承运商）
        → 409 16076（reason CARRIER_SHIPMENT_OPEN）。调用方应先锁 DN 行。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is not None:
            raise CarrierShipmentService.open_shipment_conflict(record, "Customs data cannot be changed")

    @staticmethod
    def assert_packages_editable(dn: DN):
        """有 pending / active / unknown / cancelling 的自动运单时不能改箱子（箱数 / 重量 / 尺寸已随运单提交）
        → 409 16076（reason CARRIER_SHIPMENT_OPEN）。调用方应先锁 DN 行。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is not None:
            raise CarrierShipmentService.open_shipment_conflict(record, "Packages cannot be changed")

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @staticmethod
    def _carrier_payload(carrier):
        if carrier is None:
            return None
        return {'id': carrier.id, 'name': carrier.name, 'code': carrier.code}

    @staticmethod
    def _integration_payload(snapshot, adapter, blockers):
        """GET 里的 integration：DN 的承运商的对接（没有为 null）"""
        if snapshot is None:
            return None
        cls = adapter or None
        problems = {'CARRIER_INTEGRATION_UNAVAILABLE', 'CARRIER_INTEGRATION_DISABLED', 'CARRIER_NOT_CONFIGURED',
                    'CARRIER_CONFIG_INVALID'}
        return {
            'code': snapshot['adapter'],
            'name': display_name(snapshot['adapter']),
            'portal_name': cls.portal_name if cls else None,
            'portal_url': cls.portal_url if cls else None,
            'enabled': snapshot['enabled'],
            'ready': cls is not None and not any(b['code'] in problems for b in blockers),
            # 建好的运单要另约上门揽收（GoodsMart：/warehouse/dn/carrier-pickups）
            'books_pickups': bool(cls is not None and cls.books_pickups()),
            # 能查跟踪：揽收后自动完成发货、送达后自动签收（warehouse/dn/tracking_services.py）
            'tracks_shipments': bool(cls is not None and cls.supports_tracking),
        }

    @staticmethod
    def status(dn: DN, ctx=None, alerts=None, warnings=None) -> dict:
        """GET /dn/<id>/carrier-shipment"""
        ctx = ctx or CarrierShipmentService._collect(dn)
        shipment = CarrierShipmentService.latest_shipment(dn)
        unresolved_record = CarrierShipmentService.unresolved_shipment(dn)
        unresolved = CarrierShipmentService.unresolved_payload(unresolved_record)
        target = CarrierShipmentService.cancel_target(dn)
        customs = dn.customs
        adapter = ctx['adapter']
        integration = CarrierShipmentService._integration_payload(ctx['integration'], adapter, ctx['blockers'])
        label_formats = {}
        if adapter is not None:
            for fmt in adapter.label_formats:
                label = adapter.label_options(fmt)
                label_formats[fmt] = {'image_type': label['image_type'], 'stock_type': label['stock_type']}
        payload = {
            # DN 的承运商有可用的对接（启用、设置齐全）：能不能建单另看 can_create / blockers
            'enabled': bool(integration and integration['ready']),
            # DN 当前的承运商（发货任务的，没有则 DN 的）与它的对接
            'carrier': CarrierShipmentService._carrier_payload(ctx['carrier']),
            'integration': integration,
            'can_create': not ctx['blockers'] and unresolved is None,
            'blockers': ctx['blockers'],
            # 结果不明 / 进行中的建单记录、正在取消 / 取消结果不明的运单（有它时不能建单、不能发货）
            'unresolved': unresolved,
            # unknown（含卡住的 pending）、取消结果不明的 cancelling 可以确认作废（带运单号的先向承运商确认已取消）
            'can_dismiss': CarrierShipmentService.dismissable(unresolved_record),
            # 有效运单、取消结果不明的运单、带运单号的结果不明记录可以（重试）取消；DN 已发货 / 正在取消时不行
            'can_cancel': (target is not None and not CarrierShipmentService.cancel_in_progress(target)
                           and not CustomsService.is_locked(dn)),
            'etd_enabled': bool(adapter is not None and adapter.documents_enabled()),
            'regulatory_check_enabled': bool(adapter is not None and adapter.trade_requirements_enabled()),
            'default_label_format': adapter.default_label_format() if adapter is not None else None,
            'label_formats': label_formats,
            # 国内 / 跨境（{origin_country, destination_country, international, requires_customs}）
            'route': ctx['route'],
            'declared_value_carriage': customs.declared_value_carriage if customs else None,
            'delivery_task_id': ctx['task'].id if ctx['task'] is not None else None,
            # 发货任务上现在的运单号（自动建的或手工填的）
            'tracking_number': ctx['task'].tracking_number if ctx['task'] is not None else None,
            'shipment': CarrierShipmentService.shipment_payload(shipment),
            # 建单时会自动做的调整（如申告价额压到已打包货值）；建单响应里是这次实际做了的
            'warnings': ctx['warnings'] if warnings is None else warnings,
        }
        if alerts is not None:
            payload['alerts'] = alerts
        return payload

    # 贸易单证计划搬到了报关扩展（customs_extension.py），这里保留旧名
    _trade_document_plan = staticmethod(CustomsExtension.trade_document_plan)

    @staticmethod
    def trade_requirements_preview(dn: DN) -> dict:
        """Read-only preflight; create queries again so changed goods or destinations are rechecked.
        只对带报关扩展的件查（国内件 / 同一关税同盟内没有贸易单证要求）。"""
        ctx = CarrierShipmentService._collect(dn)
        if ctx['blockers']:
            raise ConflictException('Carrier shipment conditions are not met', 16072,
                                    details={'blockers': ctx['blockers']})
        adapter = ctx['adapter']
        if not ctx['customs'] or not adapter.trade_requirements_enabled():
            return {'enabled': False, 'documents': [], 'missing_documents': [], 'paper_documents': []}
        shipment = dict(ctx['shipment'])
        shipment['ship_date'] = wms_clock.local_today(dn)        # 发货日 = 仓库当地的今天
        try:
            regulatory = adapter.trade_requirements(shipment, deadline=clock() + 20)
        except CarrierError as exc:
            raise _carrier_api_exception(exc, 'regulatory', adapter.code,
                                         unresolved=None, with_unresolved=True) from exc
        return {**regulatory, **CustomsExtension.trade_document_plan(regulatory, adapter.documents_enabled()),
                'enabled': True}

    # ------------------------------------------------------------------
    # 建单
    # ------------------------------------------------------------------

    @staticmethod
    def _shipment_data(dn: DN, route: dict, declared_total, parties: dict, carrier=None) -> dict:
        """交给插件的一票货（纯数据，不引用 ORM 对象；ship_date / documents 建单时再补），形状见 CarrierAdapter。
        parties：{shipper, recipient, commodities, invoice}（报关件由报关扩展给，国内件 commodities / invoice 为 None）；
        options：DN 的运单服务选项（只含插件声明了的键，warehouse/shipping/options.py）"""
        from warehouse.shipping.options import for_plugin, schema_for
        packages = list(dn.packages)
        declared_split = split_evenly(declared_total, len(packages)) if declared_total else [None] * len(packages)
        package_items = [{
            'gross_weight_kg': p.gross_weight_kg,
            'length_mm': p.length_mm,
            'width_mm': p.width_mm,
            'height_mm': p.height_mm,
            'declared_value': value,
        } for p, value in zip(packages, declared_split)]
        return {
            'reference': dn.order_number,
            'ship_date': None,
            'route': dict(route),
            'shipper': parties['shipper'],
            'recipient': parties['recipient'],
            'packages': package_items,
            'commodities': parties.get('commodities'),
            'invoice': parties.get('invoice'),
            'documents': None,
            'options': for_plugin(dn, schema_for(carrier)) if carrier is not None else {},
        }

    @staticmethod
    def create_shipment(dn: DN, user_id, label_format=None, supplemental_documents=None) -> dict:
        """POST /dn/<id>/carrier-shipment（三段，见模块说明）。

        各段自己提交，不能在外层事务里调用（否则 pending 记录不会先落库、调承运商时还占着锁）。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("create_shipment must not run inside another transaction")
        label_format = parse_label_format(label_format)
        deadline = clock() + create_budget_seconds()
        dn_id = dn.id
        try:
            plan = CarrierShipmentService._reserve(dn, user_id, label_format)
        except IntegrityError as exc:
            # 部分唯一索引兜底：并发请求刚插了同一 DN 的 pending（正常情况下 DN 行锁已让它们排队）
            db.session.rollback()
            conflict = CarrierShipmentService._conflict_after_race(dn_id)
            if conflict is None:
                raise
            raise conflict from exc

        state = {'create_sent': False, 'created': None, 'finalized': False}
        plan['supplemental_documents'] = supplemental_documents or []
        try:
            return CarrierShipmentService._request_and_save(plan, user_id, deadline, state)
        except BaseException as exc:
            # worker 超时被杀（gunicorn 抛 SystemExit）等：尽力把记录落到 unknown / failed；落不了就等 stale
            if not isinstance(exc, Exception) and not state['finalized']:
                CarrierShipmentService._record_interrupted(plan['record_id'], state)
            raise

    @staticmethod
    def _conflict_after_race(dn_id):
        dn = db.session.get(DN, dn_id)
        if dn is None:
            return None
        record = CarrierShipmentService.unresolved_shipment(dn)
        if record is not None:
            return CarrierShipmentService._unresolved_conflict(record)
        active = CarrierShipmentService.active_shipment(dn)
        if active is not None:
            return ConflictException(
                "The carrier shipment cannot be created; resolve the listed blockers first.", 16072,
                details={'blockers': [_blocker('SHIPMENT_EXISTS', "An active carrier shipment already exists "
                                                                  f"({active.tracking_number})")]},
            )
        return None

    @staticmethod
    @transactional
    def _reserve(dn: DN, user_id, label_format) -> dict:
        """第 1 段：锁 DN、检查、必要时签发 CI / PL、插 pending 记录，随本事务提交（释放 DN 行锁）。"""
        dn = CustomsService._lock(dn)
        unresolved = CarrierShipmentService.unresolved_shipment(dn)
        if unresolved is not None:
            raise CarrierShipmentService._unresolved_conflict(unresolved)
        ctx = CarrierShipmentService._collect(dn)
        if ctx['blockers']:
            raise ConflictException(
                "The carrier shipment cannot be created; resolve the listed blockers first.", 16072,
                details={'blockers': ctx['blockers']},
            )
        adapter = ctx['adapter']
        if label_format is not None and label_format not in adapter.label_formats:
            raise BadRequestException(f"{adapter.name} does not support label_format {label_format} "
                                      f"(supported: {' / '.join(adapter.label_formats)})", 16077, field='label_format')
        label = adapter.label_options(label_format)

        shipment = dict(ctx['shipment'])
        shipment['ship_date'] = wms_clock.local_today(dn)        # 发货日 = 仓库当地的今天
        if ctx['customs']:
            # 报关件：CI / PL 必须先签发（没有或已过期 → 用现有逻辑签发；随 pending 记录一起提交），
            # 开了电子单证时交给插件
            CustomsExtension.prepare_documents(dn, user_id, ctx['view'], adapter, shipment)

        now = wms_clock.now()
        record = DNCarrierShipment(
            dn_id=dn.id,
            source=SOURCE_API,
            carrier=adapter.code,
            carrier_id=ctx['carrier'].id,
            status='pending',
            service_type=adapter.service_type(),
            package_count=len(dn.packages),
            declared_value=ctx['declared_value'],
            label_format=label['label_format'],
            image_type=label['image_type'],
            label_stock_type=label['stock_type'],
            sender_country=shipment['shipper']['country_code'],
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(record)
        db.session.flush()
        return {
            'record_id': record.id,
            'dn_id': dn.id,
            'carrier_id': ctx['carrier'].id,
            'integration': ctx['integration'],
            'label': label,
            'shipment': shipment,
            'sender_country': shipment['shipper']['country_code'],
            'warnings': list(ctx['warnings']),
            'customs': ctx['customs'],              # 带报关扩展（贸易单证要求、建单后重签 CI / PL）
        }

    @staticmethod
    def _request_and_save(plan: dict, user_id, deadline: float, state: dict) -> dict:
        """第 2 段（调承运商，不开事务）+ 第 3 段（写库）。任何出口都把记录落到最终状态。"""
        record_id, dn_id = plan['record_id'], plan['dn_id']
        adapter = CarrierIntegrationService.build(plan['integration'])
        code, name = adapter.code, adapter.name

        if plan['customs'] and adapter.trade_requirements_enabled():
            # 报关件：查目的地和货物要的贸易单证（报关扩展），缺的拦下、补传的随电子单证上传
            try:
                regulatory = adapter.trade_requirements(plan['shipment'], deadline=deadline)
            except CarrierError as exc:
                CarrierShipmentService._finish(record_id, 'failed', state, reason='regulatory_lookup_failed',
                                               error_message=exc.message, transaction_id=exc.transaction_id)
                raise _carrier_api_exception(exc, 'regulatory', code, unresolved=None, with_unresolved=True)
            CustomsExtension.apply_trade_requirements(
                plan, adapter, regulatory,
                lambda reason: CarrierShipmentService._finish(record_id, 'failed', state, reason=reason))

        document_ref = None
        if plan['shipment'].get('documents'):
            try:
                document_ref = adapter.upload_documents(plan['shipment'], deadline=deadline)
            except CarrierError as exc:
                # 运单还没请求：结果确定
                CarrierShipmentService._finish(record_id, 'failed', state, reason='etd_upload_failed',
                                               error_message=exc.message, transaction_id=exc.transaction_id)
                raise _carrier_api_exception(exc, 'etd_upload', code, unresolved=None, with_unresolved=True)

        primary_document_ref = (document_ref.get('commercial_invoice') if isinstance(document_ref, dict)
                                else document_ref)

        state['create_sent'] = True
        try:
            result = adapter.create_shipment(plan['shipment'], plan['label'], deadline=deadline,
                                             document_ref=document_ref)
        except CarrierResponseError as exc:
            logger.error("DN %s: unexpected %s ship response (transactionId %s): %s", dn_id, name,
                         exc.transaction_id, exc.message)
            if exc.tracking_number:
                # 看不懂但有运单号：按写库失败处理，取消它
                state['created'] = {'tracking_number': exc.tracking_number, 'transaction_id': exc.transaction_id}
                unresolved, _compensation = CarrierShipmentService._compensate(plan, adapter, state, deadline, user_id)
            else:
                unresolved = CarrierShipmentService._finish(
                    record_id, 'unknown', state, reason='bad_response', error_message=exc.message,
                    transaction_id=exc.transaction_id, etd_document_id=primary_document_ref)
            raise BadGatewayException(
                f"{exc.message}." + (_maybe_created(code) if unresolved else f" The shipment was cancelled in {name}."),
                16073, details={'carrier': code, 'action': 'create', 'errors': [],
                                'transaction_id': exc.transaction_id, 'http_status': 200,
                                'maybe_processed': True, 'unresolved': unresolved})
        except CarrierError as exc:
            if exc.maybe_processed:
                reason = _unclear_reason(exc)
                logger.error("DN %s: %s create shipment result unclear (%s, transactionId %s); the shipment "
                             "may exist on the %s side", dn_id, name, reason, exc.transaction_id, name)
                unresolved = CarrierShipmentService._finish(
                    record_id, 'unknown', state, reason=reason, error_message=exc.message,
                    transaction_id=exc.transaction_id, etd_document_id=primary_document_ref)
            else:
                # rejected = 承运商对建单请求本身回了 4xx；not_sent = 请求没发出去（令牌失败、连不上等）
                if exc.budget_exhausted:
                    reason = 'budget_exhausted'
                elif exc.step == STEP_CREATE and exc.status_code:
                    reason = 'rejected'
                else:
                    reason = 'not_sent'
                unresolved = CarrierShipmentService._finish(
                    record_id, 'failed', state, reason=reason, error_message=exc.message,
                    transaction_id=exc.transaction_id, etd_document_id=primary_document_ref)
            raise _carrier_api_exception(exc, 'create', code, unresolved=unresolved, with_unresolved=True)

        # 从这里起承运商那边已有运单：先把运单号独立提交（第 3 段失败 / 进程被杀也留得下号码），之后任何失败都补偿取消
        tracking = result['tracking_number']
        if plan.get('regulatory_requirements') or document_ref:
            result['trade_documents'] = {
                **(result.get('trade_documents') or {}),
                'regulatory_requirements': plan.get('regulatory_requirements'),
                'uploaded_documents': (document_ref if isinstance(document_ref, dict)
                                       else {'commercial_invoice': document_ref} if document_ref else {}),
            }
        state['created'] = {'tracking_number': tracking, 'transaction_id': result['transaction_id']}
        CarrierShipmentService._note_created(record_id, tracking, state['created']['transaction_id'], primary_document_ref)
        try:
            response = CarrierShipmentService._save(plan, adapter, user_id, result, primary_document_ref)
        except Exception as exc:
            logger.exception("DN %s: %s shipment %s was created but saving it in WMS failed", dn_id, name, tracking)
            # 补偿取消前另开事务重读记录：提交其实成功（应答丢失等）时记录已是 active —— 不能取消有效运单
            saved = CarrierShipmentService._saved_despite_error(record_id, tracking)
            if saved:
                logger.warning("DN %s: saving %s shipment %s raised but the record is active (the commit went "
                               "through); not cancelling it", dn_id, name, tracking)
                state['finalized'] = True
                return CarrierShipmentService.status(db.session.get(DN, dn_id), alerts=result['alerts'],
                                                     warnings=plan['warnings'])
            if saved is None:
                # 读不到记录（数据库不可用等），不知道提交了没有：不取消，记录（还是 pending 时）落 unknown 带运单号，
                # 之后由操作员重试取消 / 确认作废；已经是 active 的不会被改写
                unresolved = CarrierShipmentService._finish(
                    record_id, 'unknown', state, reason='save_unconfirmed', tracking_number=tracking,
                    transaction_id=state['created']['transaction_id'],
                    error_message=f"Saving in WMS failed and could not be verified: {type(exc).__name__}")
                compensation = {'attempted': False, 'cancelled': False, 'cancel_transaction_id': None,
                                'error': 'whether the shipment was saved could not be verified'}
            else:
                unresolved, compensation = CarrierShipmentService._compensate(plan, adapter, state, deadline, user_id)
            raise CarrierShipmentService._save_failed_exception(
                exc, code, tracking, state['created']['transaction_id'], unresolved, compensation) from exc
        state['finalized'] = True
        return response

    @staticmethod
    def _saved_despite_error(record_id, tracking):
        """第 3 段报错后另开事务重读记录：已是 active 且运单号相同 → True（提交其实成功了）；
        不是 → False；读不到（数据库不可用等）→ None。"""
        try:
            db.session.rollback()
            row = (db.session.query(DNCarrierShipment.status, DNCarrierShipment.tracking_number)
                   .filter(DNCarrierShipment.id == record_id).first())
            db.session.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("carrier shipment record %s could not be re-read after the save failed", record_id)
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return None
        if row is None:
            return False
        return row[0] == 'active' and normalize_tracking(row[1]) == normalize_tracking(tracking)

    @staticmethod
    def _save_failed_exception(exc, code, tracking, transaction_id, unresolved, compensation) -> BadGatewayException:
        """承运商建好运单后 WMS 写库失败（含业务异常，如期间 DN 已发货 16065、单证数据变了 16076）→ 502 16095：
        details {carrier, action: create, tracking_number, transaction_id, error: {code, message},
        compensation: {attempted, cancelled, cancel_transaction_id, error}, unresolved, maybe_processed}。
        unresolved 为 null = 运单已在承运商那边取消；否则是带运单号的结果不明记录（重试取消 / 确认作废）。"""
        name = display_name(code)
        if isinstance(exc, APIException):
            error = {'code': exc.biz_code, 'message': exc.message}
        else:
            error = {'code': None, 'message': type(exc).__name__}
        if compensation['cancelled']:
            outcome = f"the shipment has been cancelled in {name}; create it again once the problem is fixed."
        elif compensation['attempted']:
            outcome = f"cancelling it in {name} failed ({compensation['error']})." + _cancel_retry(code)
        else:
            outcome = "whether it was saved could not be verified, so it was not cancelled." + _cancel_retry(code)
        return BadGatewayException(
            f"{name} created shipment {tracking} but saving it in WMS failed ({error['message']}); {outcome}", 16095,
            details={
                'carrier': code, 'action': 'create', 'tracking_number': tracking,
                'transaction_id': transaction_id, 'error': error, 'compensation': compensation,
                'unresolved': unresolved, 'maybe_processed': unresolved is not None,
            })

    @staticmethod
    def _note_created(record_id, tracking, transaction_id, etd_document_id):
        """承运商建单成功后立即独立提交运单号（失败只记日志，后面照常写库 / 补偿）"""
        try:
            CarrierShipmentService._note_created_tx(record_id, tracking, transaction_id, etd_document_id)
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception("carrier shipment record %s: could not note tracking number %s", record_id, tracking)

    @staticmethod
    @transactional
    def _note_created_tx(record_id, tracking, transaction_id, etd_document_id):
        record = db.session.get(DNCarrierShipment, record_id)
        if record is None or record.status != 'pending':
            return
        record.tracking_number = tracking
        record.transaction_id = transaction_id or record.transaction_id
        record.etd_document_id = etd_document_id or record.etd_document_id
        record.updated_at = wms_clock.now()
        db.session.flush()

    @staticmethod
    @transactional
    def _save(plan: dict, adapter, user_id, result: dict, etd_document_id) -> dict:
        """第 3 段：锁 DN → 记录置 active → 运单号 / CI / PL / 面单，一个事务。"""
        from warehouse.delivery.services import DeliveryTaskService

        dn = CustomsService._lock(db.session.get(DN, plan['dn_id']))
        record = db.session.get(DNCarrierShipment, plan['record_id'])
        if record is None or record.status != 'pending':
            raise RuntimeError(f"carrier shipment record {plan['record_id']} is no longer pending "
                               f"({record.status if record is not None else 'deleted'})")
        tracking = result['tracking_number']
        archive = adapter.label_archive(result['documents'], plan['label'])
        task = CustomsService._delivery_task(dn)
        if task is None:
            raise RuntimeError(f"DN {dn.id} has no active delivery task any more")

        # 先把记录置 active（带运单号）：保存运单号的校验（assert_tracking_matches）认的就是这张运单
        record.status = 'active'
        record.reason = None
        record.tracking_number = tracking
        record.updated_at = wms_clock.now()
        db.session.flush()

        # 运单号：复用现有保存运单号的逻辑
        tracking_payload = {'tracking_number': tracking}
        if task.carrier_id is None:
            tracking_payload['carrier_id'] = plan['carrier_id']
        DeliveryTaskService.save_tracking(task, tracking_payload)
        if plan['customs']:
            # CI / PL 不印运单号，数据没变时这里不出新版本；建单期间单证数据被改了（如公司出口资料）→ 16076，
            # 按写库失败补偿取消（承运商拿到的申报与现在的数据对不上）
            CustomsExtension.reissue_after_create(dn, user_id)

        ship_date = None
        if result.get('ship_date'):
            try:
                ship_date = date.fromisoformat(str(result['ship_date'])[:10])
            except ValueError:
                ship_date = None
        ship_date = ship_date or wms_clock.local_today(dn)        # 发货日 = 仓库当地的今天

        label_doc = CarrierShipmentService._store_label(dn, tracking, ship_date, archive, user_id)
        record.package_tracking_numbers = result['package_tracking_numbers']
        record.service_type = result['service_type'] or record.service_type
        record.ship_date = ship_date
        record.package_count = len(dn.packages)
        record.net_charge = result['net_charge']
        record.currency = result['currency']
        record.label_parts = archive['parts']
        record.label_document_id = label_doc.id
        record.etd_document_id = etd_document_id
        record.trade_documents = result.get('trade_documents')
        record.transaction_id = result['transaction_id'] or record.transaction_id
        db.session.flush()
        db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
        return CarrierShipmentService.status(dn, alerts=result['alerts'], warnings=plan['warnings'])

    @staticmethod
    def _compensate(plan: dict, adapter, state: dict, deadline: float, user_id):
        """第 3 段失败（或响应看不懂）后取消承运商那边刚建的运单，结果独立提交到记录：
        确认取消 → cancelled（reason compensated）；失败 / 不确定 → unknown（reason compensation_failed）。
        返回 (unresolved 形状（unknown）或 None（已取消）, compensation {attempted, cancelled, cancel_transaction_id,
        error})。"""
        record_id, dn_id = plan['record_id'], plan['dn_id']
        created = state['created']
        tracking = created['tracking_number']
        name = adapter.name
        logger.error("DN %s: %s shipment %s was created (transactionId %s) but saving it in WMS failed; "
                     "cancelling it", dn_id, name, tracking, created.get('transaction_id'))
        cancelled, cancel_transaction_id, problem = False, None, None
        try:
            outcome = adapter.cancel_shipment(tracking, sender_country=plan['sender_country'],
                                              deadline=deadline + COMPENSATION_EXTRA_SECONDS)
            cancel_transaction_id = outcome.get('transaction_id')
            cancelled = outcome['result'] == 'confirmed'
            problem = None if cancelled else outcome.get('problem')
        except CarrierError as exc:
            cancel_transaction_id = exc.transaction_id
            problem = exc.message
        except Exception as exc:  # noqa: BLE001 — 补偿本身出错也要留痕
            problem = f"{type(exc).__name__}: {exc}"

        compensation = {'attempted': True, 'cancelled': cancelled, 'cancel_transaction_id': cancel_transaction_id,
                        'error': problem}
        if cancelled:
            logger.error("DN %s: %s shipment %s cancelled after the WMS failure", dn_id, name, tracking)
            return CarrierShipmentService._finish(
                record_id, 'cancelled', state, reason='compensated', tracking_number=tracking,
                transaction_id=created.get('transaction_id'), cancel_transaction_id=cancel_transaction_id,
                cancelled_by=user_id), compensation
        logger.error("DN %s: cancelling %s shipment %s FAILED (%s); retry the cancellation (or cancel it in "
                     "%s, then dismiss the record)", dn_id, name, tracking, problem, adapter.portal_name)
        return CarrierShipmentService._finish(
            record_id, 'unknown', state, reason='compensation_failed', tracking_number=tracking,
            transaction_id=created.get('transaction_id'), cancel_transaction_id=cancel_transaction_id,
            error_message=problem), compensation

    @staticmethod
    def _finish(record_id, status: str, state=None, **fields):
        """把建单记录从 pending 落到最终状态（独立事务提交）。返回 unresolved 形状（unknown 时）或 None。
        提交失败只记日志：记录停在 pending，过期后按结果不明（stale）处理。"""
        try:
            payload = CarrierShipmentService._finish_tx(record_id, status, fields)
        except Exception:  # noqa: BLE001
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("carrier shipment record %s could not be marked %s (%s)",
                             record_id, status, fields.get('reason'))
            return {'id': record_id, 'status': 'pending', 'reason': fields.get('reason'),
                    'tracking_number': fields.get('tracking_number'),
                    'transaction_id': fields.get('transaction_id'), 'created_at': None, 'updated_at': None}
        if state is not None:
            state['finalized'] = True
        return payload

    @staticmethod
    @transactional
    def _finish_tx(record_id, status: str, fields: dict):
        record = db.session.get(DNCarrierShipment, record_id)
        if record is None:
            return None
        if record.status != 'pending':
            # 已经落过（或被确认作废）：不覆盖
            logger.warning("carrier shipment record %s is %s, not pending; not changing it to %s",
                           record_id, record.status, status)
            return CarrierShipmentService.unresolved_payload(record)
        now = wms_clock.now()
        record.status = status
        for key, value in fields.items():
            if value is None:
                continue
            if key == 'error_message':
                value = str(value)[:ERROR_MESSAGE_MAX]
            setattr(record, key, value)
        if status == 'cancelled':
            record.cancelled_at = now
        record.updated_at = now
        db.session.flush()
        return CarrierShipmentService.unresolved_payload(record)

    @staticmethod
    def _record_interrupted(record_id, state: dict):
        """进程被中断（SystemExit 等）时尽力落状态：建单请求可能已发出 → unknown，否则 failed（reason interrupted）"""
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        created = state.get('created') or {}
        CarrierShipmentService._finish(
            record_id, 'unknown' if state.get('create_sent') else 'failed', state, reason='interrupted',
            tracking_number=created.get('tracking_number'), transaction_id=created.get('transaction_id'))

    @staticmethod
    def _store_label(dn: DN, tracking: str, ship_date, archive: dict, user_id) -> DNDocument:
        max_version = (
            db.session.query(db.func.max(DNDocument.version))
            .filter(DNDocument.dn_id == dn.id, DNDocument.doc_type == DOC_LABEL)
            .scalar()
        )
        content = archive['content']
        digest = hashlib.sha256(content).hexdigest()
        safe = _FILE_SAFE.sub('_', tracking).strip('_')[:80] or 'LABEL'
        doc = DNDocument(
            dn_id=dn.id,
            doc_type=DOC_LABEL,
            version=(max_version or 0) + 1,
            document_number=tracking[:80],
            invoice_date=ship_date,
            status='issued',
            sha256=digest,
            data_sha256=digest,
            size_bytes=len(content),
            file_name=f"LABEL_{safe}.{archive['extension']}",
            content=content,
            issued_at=wms_clock.now(),
            issued_by=user_id,
        )
        db.session.add(doc)
        db.session.flush()
        return doc

    # ------------------------------------------------------------------
    # 取消 / 确认作废（三段：锁 DN 标记并提交 → 不持锁调承运商 → 锁 DN 落最终状态）
    # ------------------------------------------------------------------

    @staticmethod
    def cancel_shipment(dn: DN, user_id, tracking_number=None) -> dict:
        """POST /dn/<id>/carrier-shipment/cancel：取消有效运单（active）、重试取消结果不明的运单（cancelling），
        或取消带运单号的结果不明记录（unknown，例如补偿取消失败）。
        tracking_number（可选）：操作员要取消的运单号，与当前记录（规范化后）不符 → 409 16093。
        各段自己提交，不能在外层事务里调用（否则调承运商时还占着 DN 行锁）。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("cancel_shipment must not run inside another transaction")
        expected = parse_target_tracking(tracking_number)
        plan = CarrierShipmentService._begin_cancel(dn, user_id, expected)
        return CarrierShipmentService._run_cancel(plan, user_id)

    @staticmethod
    def dismiss(dn: DN, user_id, confirm, unresolved_id=None) -> dict:
        """POST /dn/<id>/carrier-shipment/dismiss：确认作废结果不明的记录（unknown / 卡住的 pending / 取消结果不明的
        cancelling），之后可以重新建单。
        - 没有运单号：操作员确认承运商那边没有这张运单（或已手工取消）后直接 dismissed，不调承运商；
        - 带运单号：先向承运商取消（三段，同取消），承运商取消成功或回「已取消 / 查无此运单」才作废
          （unknown → dismissed；cancelling → cancelled，面单 / 运单号 / CI 一并处理），否则 409 16094。
        unresolved_id（可选）：操作员核对过的记录 id，与当前结果不明的记录不符 → 409 16093。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("dismiss must not run inside another transaction")
        if confirm is not True:
            raise BadRequestException(
                'Set "confirm": true to confirm that the carrier has no such shipment (or that it has been cancelled '
                'on the carrier\'s website).', 40000, field='confirm')
        expected_id = parse_target_id(unresolved_id)
        plan = CarrierShipmentService._begin_dismiss(dn, user_id, expected_id)
        if 'payload' in plan:
            return plan['payload']
        return CarrierShipmentService._run_cancel(plan, user_id)

    @staticmethod
    def _cancel_integration(dn: DN, record: DNCarrierShipment) -> dict:
        """取消 / 确认作废要用的对接设置快照：建单时的承运商（carrier_id）的对接；旧记录没有 carrier_id 时用 DN 当前承运商的
        （插件要与记录一致）。只要求凭证齐全，停用了也能取消已建的运单。没有 → 409 16072（details.blockers）。"""
        carrier = (db.session.get(Carrier, record.carrier_id) if record.carrier_id is not None
                   else CarrierShipmentService.carrier_of(dn))
        snapshot = CarrierIntegrationService.for_carrier(carrier)
        name = display_name(record.carrier)
        if snapshot is None or snapshot['adapter'] != record.carrier:
            blockers = [_blocker('CARRIER_NOT_INTEGRATED',
                                 f"The {name} integration of the carrier that created this shipment is not set up "
                                 "any more", carrier_id=carrier.id if carrier is not None else None)]
        else:
            blockers = CarrierIntegrationService.problems(
                snapshot, CarrierIntegrationService.build(snapshot), for_create=False)
        if blockers:
            raise ConflictException(
                f"{name} is not configured; the shipment cannot be cancelled from WMS.", 16072,
                details={'blockers': blockers},
            )
        return snapshot

    @staticmethod
    @transactional
    def _begin_cancel(dn: DN, user_id, expected) -> dict:
        """取消第 1 段：锁 DN → 检查 → 有效运单标 cancelling（cancel_in_progress）→ 提交。"""
        dn = CustomsService._lock(dn)
        CustomsService._assert_not_locked(dn)
        record = CarrierShipmentService.cancel_target(dn)
        if record is None:
            unresolved = CarrierShipmentService.unresolved(dn)
            message = "DN has no active carrier shipment to cancel."
            if unresolved is not None and unresolved['status'] == 'pending':
                message += " A shipment request is still in progress; wait for it to finish."
            elif unresolved is not None:
                message += (" The unclear shipment request has no tracking number: check the carrier's website, then "
                            "dismiss it (POST /warehouse/dn/<id>/carrier-shipment/dismiss).")
            raise ConflictException(message, 16075, details={'unresolved': unresolved})
        if expected is not None and normalize_tracking(expected) != normalize_tracking(record.tracking_number):
            raise _target_mismatch(
                f"The carrier shipment of this DN is now {record.tracking_number}, not {expected}; reload it and "
                "check again before cancelling.", expected, record.tracking_number)
        if CarrierShipmentService.cancel_in_progress(record):
            raise CarrierShipmentService._unresolved_conflict(record)
        task = CustomsService._delivery_task(dn)
        if task is not None and task.status in COMPLETED_TASK_STATUSES:
            raise ConflictException("The shipment has been completed; it cannot be cancelled.", 16065)
        integration = CarrierShipmentService._cancel_integration(dn, record)
        return CarrierShipmentService._mark_cancelling(dn, record, user_id, 'cancel', integration)

    @staticmethod
    @transactional
    def _begin_dismiss(dn: DN, user_id, expected_id) -> dict:
        """确认作废第 1 段：锁 DN → 检查；没有运单号的直接作废（返回 {'payload': ...}），
        带运单号的标记后（cancelling 才标）提交，交给 _run_cancel 向承运商确认。"""
        dn = CustomsService._lock(dn)
        record = CarrierShipmentService.unresolved_shipment(dn)
        payload = CarrierShipmentService.unresolved_payload(record)
        if expected_id is not None and record is not None and record.id != expected_id:
            raise _target_mismatch(
                f"The unresolved carrier shipment record of this DN is now #{record.id}, not #{expected_id}; "
                "reload it and check it again before dismissing.", expected_id, record.id)
        if not CarrierShipmentService.dismissable(record):
            if payload is None:
                detail = "."
            elif payload['status'] == 'pending':
                detail = " (the carrier request is still in progress)."
            else:
                detail = " (the cancellation is still in progress)."
            raise ConflictException("There is no carrier shipment with an unclear result to dismiss" + detail, 16075,
                                    details={'unresolved': payload})
        if record.tracking_number:
            # 带运单号：承运商那边可能真有这张运单 —— 先向承运商取消，确认已取消 / 查无此运单才作废
            integration = CarrierShipmentService._cancel_integration(dn, record)
            return CarrierShipmentService._mark_cancelling(dn, record, user_id, 'dismiss', integration)

        now = wms_clock.now()
        record.reason = payload['reason']            # 卡住的 pending 记下 stale
        record.status = 'dismissed'
        record.dismissed_at = now
        record.dismissed_by = user_id
        record.updated_at = now
        db.session.flush()
        logger.warning("DN %s: carrier shipment record %s (%s, no tracking number) dismissed by user %s",
                       dn.id, record.id, record.reason, user_id)
        db.session.expire(dn, ['carrier_shipments'])
        return {'payload': CarrierShipmentService.status(dn)}

    @staticmethod
    def _mark_cancelling(dn: DN, record: DNCarrierShipment, user_id, mode: str, integration: dict) -> dict:
        """第 1 段的标记（DN 已锁）：active / cancelling → cancelling（cancel_in_progress，记下请求人），
        之后完成发货 / 存运单号 16079；unknown / 卡住的 pending 本来就拦着一切，不改状态。返回调承运商的计划。"""
        origin_status, origin_reason = CarrierShipmentService.effective_status(record)
        live = record.status in LIVE_STATUSES
        if live:
            now = wms_clock.now()
            record.status = 'cancelling'
            record.reason = CANCEL_IN_PROGRESS
            record.cancelled_by = user_id
            record.updated_at = now
            db.session.flush()
        logger.warning("DN %s: %s %s shipment %s (record %s, %s) requested by user %s", dn.id,
                       'cancelling' if mode == 'cancel' else 'dismissing', display_name(record.carrier),
                       record.tracking_number, record.id, origin_status, user_id)
        return {
            'mode': mode,                              # cancel / dismiss
            'live': live,                              # 运单建好过（active / cancelling）：成功后作废面单、清运单号
            'record_id': record.id,
            'dn_id': dn.id,
            'carrier': record.carrier,                 # 插件代码
            'integration': integration,                # 对接设置快照（调承运商时不碰数据库）
            'record_status': record.status,            # 第 3 段时记录应仍是这个状态（否则已被别的请求处理）
            'marked_at': record.updated_at,            # live 时第 3 段据此判断是不是本请求的标记
            'origin_reason': origin_reason,
            'tracking_number': record.tracking_number,
            # 发件国用建单时记下的（与建单一致）；老记录没有就按建单同一口径推
            'sender_country': record.sender_country or CarrierShipmentService._sender_country(dn),
        }

    @staticmethod
    def _run_cancel(plan: dict, user_id) -> dict:
        """第 2 段（调承运商，不开事务）+ 第 3 段（写库）。第 3 段报错 / 进程被杀时记录停在 cancelling（尽力记下原因），
        重试取消时承运商回「已取消」即落库。"""
        name = display_name(plan['carrier'])
        try:
            outcome = CarrierShipmentService._request_cancel(plan)
        except BaseException:
            # worker 超时被杀（SystemExit）等：取消请求可能已发出 —— 尽力标 interrupted（之后可以直接重试取消）
            CarrierShipmentService._note_cancel_problem(plan, 'interrupted', f"Interrupted while calling {name}")
            raise
        try:
            result = CarrierShipmentService._finish_cancel(plan, outcome, user_id)
        except Exception:
            if outcome['result'] == 'confirmed':
                logger.exception("DN %s: %s shipment %s was cancelled on the carrier side but updating WMS failed; "
                                 "cancelling again marks it cancelled (the carrier answers already cancelled)",
                                 plan['dn_id'], name, plan['tracking_number'])
            else:
                logger.exception("DN %s: recording the %s cancel result of shipment %s failed",
                                 plan['dn_id'], name, plan['tracking_number'])
            CarrierShipmentService._note_cancel_problem(
                plan, 'save_failed', f"Updating WMS failed after the {name} cancel request "
                                     f"(result: {outcome['result']})")
            raise
        if result['error'] is not None:
            raise result['error']
        return result['payload']

    @staticmethod
    def _request_cancel(plan: dict) -> dict:
        """第 2 段：调承运商取消 → outcome {result: confirmed | refused | unclear, reason, problem, transaction_id,
        carrier_error（CarrierError 时）, alerts（承运商回了响应时）}。
        - confirmed：已取消，或承运商回「已取消 / 查无此运单」（reason already_cancelled）
        - refused：承运商明确没取消（4xx、明确回了没取消），或请求没发出去 —— 运单仍有效
        - unclear：读超时、连接中断、5xx、响应看不懂 —— 可能取消了也可能没有"""
        tracking = plan['tracking_number']
        adapter = CarrierIntegrationService.build(plan['integration'])
        deadline = clock() + create_budget_seconds()
        try:
            return adapter.cancel_shipment(tracking, sender_country=plan['sender_country'], deadline=deadline)
        except CarrierError as exc:
            return {'result': 'unclear' if exc.maybe_processed else 'refused',
                    'reason': _unclear_reason(exc) if exc.maybe_processed else None,
                    'problem': exc.message, 'transaction_id': exc.transaction_id, 'carrier_error': exc}
        except Exception as exc:  # noqa: BLE001 — 调用本身出了意外：不知道请求发没发出去，按结果不明
            logger.exception("DN %s: cancelling %s shipment %s failed unexpectedly", plan['dn_id'], adapter.name,
                             tracking)
            return {'result': 'unclear', 'reason': 'error', 'problem': f"{type(exc).__name__}: {exc}",
                    'transaction_id': None}

    @staticmethod
    @transactional
    def _finish_cancel(plan: dict, outcome: dict, user_id) -> dict:
        """第 3 段：锁 DN → 按承运商的结果落记录 → {'payload': GET 形状 | None, 'error': 要抛的异常 | None}。
        错误也在这里组好、提交之后再由调用方抛（记录状态的变化要留下）。"""
        dn = CustomsService._lock(db.session.get(DN, plan['dn_id']))
        record = db.session.get(DNCarrierShipment, plan['record_id'])
        if record is not None:
            db.session.refresh(record)
        if record is None or record.status != plan['record_status'] \
                or normalize_tracking(record.tracking_number) != normalize_tracking(plan['tracking_number']):
            # 期间别的请求已经处理了这条记录（例如两人同时点了取消）：已取消就按成功返回，否则只报这次的结果
            logger.warning("DN %s: carrier shipment record %s changed while cancelling (now %s)", plan['dn_id'],
                           plan['record_id'], record.status if record is not None else 'deleted')
            db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
            if outcome['result'] == 'confirmed':
                return {'payload': CarrierShipmentService.status(dn), 'error': None}
            return {'payload': None, 'error': CarrierShipmentService._cancel_error(
                plan, outcome, CarrierShipmentService.unresolved(dn))}

        now = wms_clock.now()
        ours = not plan['live'] or record.updated_at == plan['marked_at']
        if outcome['result'] == 'confirmed':
            if plan['live'] or plan['mode'] == 'cancel':
                record.status = 'cancelled'
                record.reason = outcome.get('reason')        # None / already_cancelled
                record.cancelled_at = now
                record.cancelled_by = user_id
            else:
                # 确认作废带运单号的结果不明记录：承运商已确认取消 / 查无此运单
                record.status = 'dismissed'
                record.reason = plan['origin_reason']        # 卡住的 pending 记下 stale
                record.dismissed_at = now
                record.dismissed_by = user_id
            record.cancel_transaction_id = outcome.get('transaction_id') or record.cancel_transaction_id
            record.updated_at = now
            db.session.flush()
            CarrierShipmentService._cleanup_after_cancel(dn, record, user_id)
            logger.warning("DN %s: %s shipment %s (record %s) %s by user %s", dn.id, display_name(record.carrier),
                           record.tracking_number, record.id, record.status, user_id)
            db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
            return {'payload': CarrierShipmentService.status(dn), 'error': None}

        problem = str(outcome.get('problem') or '')[:ERROR_MESSAGE_MAX - 30]
        if outcome.get('transaction_id'):
            record.cancel_transaction_id = outcome['transaction_id']
        if plan['live'] and ours:
            if outcome['result'] == 'refused':
                # 承运商明确没取消（或请求没发出去）：运单仍有效
                record.status = 'active'
                record.reason = None
                record.cancelled_by = None
                record.error_message = f"Cancel refused: {problem}"
            else:
                record.reason = outcome['reason']             # timeout / server_error / bad_response / …
                record.error_message = f"Cancel result unclear: {problem}"
            record.updated_at = now
        elif not plan['live']:
            record.error_message = f"Cancel {'refused' if outcome['result'] == 'refused' else 'unclear'}: {problem}"
            record.updated_at = now
        db.session.flush()
        logger.error("DN %s: cancelling %s shipment %s (record %s): %s (%s)", dn.id, display_name(record.carrier),
                     plan['tracking_number'], record.id, outcome['result'], problem)
        db.session.expire(dn, ['carrier_shipments'])
        return {'payload': None, 'error': CarrierShipmentService._cancel_error(
            plan, outcome, CarrierShipmentService.unresolved_payload(record))}

    @staticmethod
    def _cleanup_after_cancel(dn: DN, record: DNCarrierShipment, user_id):
        """运单在承运商那边取消后（记录已落 cancelled / dismissed）：面单作废、发货任务上的这张运单号清掉。"""
        from warehouse.delivery.services import DeliveryTaskService
        now = wms_clock.now()
        if record.label_document_id:
            label = db.session.get(DNDocument, record.label_document_id)
            if label is not None and label.status == 'issued':
                label.status = 'void'
                label.voided_at = now
                label.void_reason = 'shipment_cancelled'
        task = CustomsService._delivery_task(dn)
        # 按规范化后的号码比（存量数据可能存成了「7946 0000 0001」这类分组写法），一致才清空
        if task is not None and task.tracking_number and record.tracking_number \
                and normalize_tracking(task.tracking_number) == normalize_tracking(record.tracking_number):
            DeliveryTaskService.save_tracking(task, {'tracking_number': None})
        db.session.flush()

    @staticmethod
    def _cancel_error(plan: dict, outcome: dict, unresolved):
        """取消 / 确认作废没成功时的异常（第 3 段提交之后抛）：
        - 确认作废（dismiss）：一律 409 16094（details {unresolved, carrier, tracking_number, result, http_status,
          transaction_id, errors, alerts, maybe_processed}）
        - 取消：承运商报错 → 502 16073 / 504 16074（_carrier_api_exception，带 unresolved）；
          承运商回了响应但没取消 / 看不懂 → 502 16073（details.alerts / maybe_processed / unresolved）"""
        err = outcome.get('carrier_error')
        unclear = outcome['result'] == 'unclear'
        tracking = plan['tracking_number']
        code = plan['carrier']
        name = display_name(code)
        problem = outcome.get('problem') or ''
        # 承运商回了响应（没取消 / 看不懂）时带 alerts；调用本身出意外时两者都没有
        http_status = err.status_code if err is not None else (200 if 'alerts' in outcome else None)
        if plan['mode'] == 'dismiss':
            return ConflictException(
                f"{name} did not confirm that shipment {tracking} is cancelled ({problem}); the record cannot be "
                f"dismissed. Cancel it in {portal_name(code)} (or try again later), then dismiss it again.", 16094,
                details={
                    'unresolved': unresolved, 'carrier': code, 'tracking_number': tracking,
                    'result': outcome['result'],
                    'http_status': http_status,
                    'transaction_id': outcome.get('transaction_id'),
                    'errors': err.errors if err is not None else [],
                    'alerts': outcome.get('alerts') or [],
                    'maybe_processed': unclear,
                })
        if err is not None:
            return _carrier_api_exception(err, 'cancel', code, unresolved=unresolved, with_unresolved=True)
        if unclear:
            message = f"{name} did not confirm the cancellation of shipment {tracking}: {problem}." + _maybe_cancelled(code)
        else:
            message = f"{name} did not cancel the shipment: {problem}"
        return BadGatewayException(
            message, 16073,
            details={'carrier': code, 'action': 'cancel', 'http_status': http_status,
                     'transaction_id': outcome.get('transaction_id'), 'errors': [],
                     'alerts': outcome.get('alerts') or [], 'maybe_processed': unclear, 'unresolved': unresolved})

    @staticmethod
    def _note_cancel_problem(plan: dict, reason: str, message: str):
        """第 2 / 3 段出意外时（独立事务）把还标着 cancel_in_progress 的记录改记原因（仍是 cancelling），
        不用等过期就能重试取消。只处理本请求标记的记录；落不了只记日志（过期后按 stale）。"""
        if not plan.get('live'):
            return
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            CarrierShipmentService._note_cancel_problem_tx(plan, reason, message)
        except Exception:  # noqa: BLE001
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("carrier shipment record %s: could not note cancel problem %s",
                             plan['record_id'], reason)

    @staticmethod
    @transactional
    def _note_cancel_problem_tx(plan: dict, reason: str, message: str):
        dn = db.session.get(DN, plan['dn_id'])
        if dn is None:
            return
        CustomsService._lock(dn)
        record = db.session.get(DNCarrierShipment, plan['record_id'])
        if record is None:
            return
        db.session.refresh(record)
        if record.status != 'cancelling' or record.reason != CANCEL_IN_PROGRESS \
                or record.updated_at != plan['marked_at']:
            return
        record.reason = reason
        record.error_message = str(message or '')[:ERROR_MESSAGE_MAX]
        record.updated_at = wms_clock.now()
        db.session.flush()
