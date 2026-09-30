"""海外 DN 在承运商系统自动建运单（目前只有 FedEx Ship API）。

替代「在承运商网站手工建运单 + 保存运单号」这一步，其余流程不变：
- 前置条件（blockers）全部满足才建单：FedEx 已配置且 DN 所属公司在 FEDEX_ALLOWED_COMPANY_IDS 里、海外 DN、
  packed、发货任务的承运商是 fedex、没有有效运单也没有手工存过运单号、有箱子（≤ 30）、报关没有错误级问题、
  贸易条件 DDP 时关税付款方配置是 SENDER、发件人 / 收件人地址放得进 FedEx 格式。
- 每次建单请求在 dn_carrier_shipments 留一条记录，分三段，调 FedEx 时不占 DN 行锁、也不开着数据库事务：
  1. 锁 DN → 有结果不明的记录（unresolved）→ 409 16079；前置条件不满足 → 409 16072；没有当前有效的 CI / PL
     （或已过期）就先用现有逻辑签发；组好请求数据；插一条 pending 记录并提交（部分唯一索引兜底并发）。
  2. FEDEX_ETD_ENABLED 时先上传 CI，再调 FedEx 建单。整个建单流程受 FEDEX_CREATE_BUDGET_SECONDS 总时限约束，
     每次请求的超时按剩余时间收缩，不够就不发（worker 超时被杀前收手；真被杀了 pending 过期后按结果不明处理）。
  3. 成功：先独立提交拿到的运单号，再在一个事务里锁 DN → 记录置 active → 主运单号走现有保存运单号的逻辑存到
     发货任务 → CI / PL 带 AWB 升版本 → 面单存 dn_documents（shipping_label）。
  结果：FedEx 明确拒绝 / 请求没发出去 → failed；读超时、连接中断、5xx、响应看不懂 → unknown（结果不明）；
  第 3 步写库失败 → 取消刚建的运单（补偿）：确认取消 → cancelled（reason compensated），
  取消失败 / 不确定 → unknown（reason compensation_failed，带运单号）。补偿结果用独立事务提交，原事务回滚后仍留痕。
- pending 超过 FEDEX_PENDING_STALE_MINUTES（不短于建单时限 + 补偿时间 + 余量）读取时按 unknown（reason stale）处理。
- 有结果不明的记录时：不能再建单、不能保存 / 修改运单号（409 16079），不能改报关数据 / 箱子（16076），
  不能新建 / 删除发货任务（16078）。操作员在 FedEx Ship Manager 确认没有这张运单（或已手工取消）后
  POST …/carrier-shipment/dismiss 标 dismissed，才能重新建单。
- 面单打印方式（label_format）由建单请求指定：A4（激光打印机）/ THERMAL（4 英寸面单机），不指定用配置默认；
  各格式的 imageType / 纸张见 fedex_shipment.label_settings。
- 运送申告价额高于已打包货值时自动压到已打包货值（FedEx 拒收申告价额高于报关货值的运单；部分打包时只保实际发出的货），
  实际提交的值记在运单的 declared_value 上，响应 warnings 里说明。
- 有有效自动运单时，发货任务的运单号只能是这张运单的号码（完成发货 / 保存运单号 / 修改发货任务传了别的号码 → 409 16078）。
- 取消：DN 未发货才可；用建单时记下的发件国；FedEx 取消成功（或回「已取消 / 查无此运单」）后记录标 cancelled、
  发货任务上的运单号清掉、面单作废、CI / PL 去掉 AWB 重新签发（条件不满足则作废）。
"""
import hashlib
import logging
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR

from flask import current_app, g
from sqlalchemy.exc import IntegrityError

from extensions.db import db
from extensions.error import (
    BadGatewayException, BadRequestException, ConflictException, GatewayTimeoutException,
)
from extensions.transaction import transactional
from warehouse.common.countries import COUNTRY_NAMES_EN
from . import fedex_client
from .customs_services import CustomsService, DOC_CI, DOC_TYPES, _document_timezone, LOCKED_DN_STATUSES
from .fedex_client import FedexError
from .fedex_shipment import (
    AddressError, LABEL_FORMATS, MAX_PACKAGES_PER_REQUEST, SUPPORTED_DUTIES_PAYMENT_TYPES,
    SUPPORTED_LABEL_IMAGE_TYPES, SHIPPER_TIN_TYPE, allocate_commodity_weights, build_label_archive,
    build_ship_request, consignee_address, label_settings, parse_ship_response, split_address_text,
    split_evenly, tin_type_for,
)
from .models import DN, DNCarrierShipment, DNDocument

logger = logging.getLogger(__name__)

CARRIER_FEDEX = 'fedex'
DOC_LABEL = 'shipping_label'
COMPLETED_TASK_STATUSES = ('completed', 'signed')
# 报关视图里已由 blockers 自己表达的问题码（不重复列）
_COVERED_PROBLEMS = ('NOT_PACKED', 'PACKAGES_MISSING')
_FILE_SAFE = re.compile(r'[^A-Za-z0-9._-]+')

OPEN_STATUSES = DNCarrierShipment.OPEN_STATUSES              # pending / active / unknown：同一 DN 至多一条
UNRESOLVED_STATUSES = DNCarrierShipment.UNRESOLVED_STATUSES  # pending / unknown

DEFAULT_CREATE_BUDGET_SECONDS = 90
DEFAULT_PENDING_STALE_MINUTES = 10
# 写库失败后的补偿取消，在建单时限之外再给的时间
COMPENSATION_EXTRA_SECONDS = 15
# pending 判定为卡住的时间下限 = 建单时限 + 补偿时间 + 这个余量（防止把还在进行中的请求当成卡住）
STALE_MARGIN_SECONDS = 60
ERROR_MESSAGE_MAX = 500

# FedEx 取消接口回「已取消 / 查无此运单」时视为取消成功（取消后本地写库失败再点取消、补偿取消撞上已取消的运单）。
# 依据：FedEx Ship API（REST）公开文档没有单列这两种情况的错误码，本仓库也没有在 sandbox 实测过（按约定不调用 FedEx），
# 所以只按错误码（不看 message，message 会随 X-locale 变）判断：
#   - 码里同时有 ALREADY 与 CANCEL / DELETE（已取消）；
#   - 码里同时有 TRACKING / SHIPMENT 与 NOT FOUND / NOT EXIST（查无此运单）；
#   - 精确码 ALREADY_CANCELLED_CODES：旧版 FedEx Web Services 的 8159
#     「Shipment Delete was requested for a tracking number already in a deleted state」。
# 命中时日志记原始错误码（WARNING）；正式环境遇到没覆盖的码，补到 ALREADY_CANCELLED_CODES。
ALREADY_CANCELLED_CODES = frozenset({'8159'})

_MAYBE_CREATED = (" The shipment may have been created on the FedEx side: check FedEx Ship Manager, cancel it there "
                  "if it exists, then confirm with POST /warehouse/dn/<id>/carrier-shipment/dismiss before "
                  "creating it again.")


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
    """建单（含 OAuth / ETD 上传 / 建单请求）的总时限，FEDEX_CREATE_BUDGET_SECONDS（默认 90 秒）"""
    return _config_number('FEDEX_CREATE_BUDGET_SECONDS', DEFAULT_CREATE_BUDGET_SECONDS, 10)


def stale_after_seconds() -> float:
    """pending 超过这么久按结果不明（stale）处理：FEDEX_PENDING_STALE_MINUTES（默认 10 分钟），
    但不短于建单时限 + 补偿时间 + 余量"""
    configured = _config_number('FEDEX_PENDING_STALE_MINUTES', DEFAULT_PENDING_STALE_MINUTES, 1) * 60
    return max(configured, create_budget_seconds() + COMPENSATION_EXTRA_SECONDS + STALE_MARGIN_SECONDS)


def allowed_company_ids():
    """FEDEX_ALLOWED_COMPANY_IDS（逗号分隔的公司 ID）→ (ID 集合, 不合法的项)。
    不设 / 空 = 空集合 = 所有公司都不允许（运费记在同一个 FedEx 账号上，不能让任何公司都用）。"""
    raw = current_app.config.get('FEDEX_ALLOWED_COMPANY_IDS')
    items = raw if isinstance(raw, (list, tuple, set, frozenset)) else str(raw or '').split(',')
    ids, invalid = set(), []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if text.isdigit() and int(text) > 0:
            ids.add(int(text))
        else:
            invalid.append(text)
    return ids, invalid


def company_allowed(dn: DN) -> bool:
    return dn.warehouse.company_id in allowed_company_ids()[0]


def _settings() -> dict:
    cfg = current_app.config
    return {
        'service_type': cfg.get('FEDEX_SERVICE_TYPE') or 'INTERNATIONAL_ECONOMY',
        'pickup_type': cfg.get('FEDEX_PICKUP_TYPE') or 'USE_SCHEDULED_PICKUP',
        'duties_payment_type': (cfg.get('FEDEX_DUTIES_PAYMENT_TYPE') or 'RECIPIENT').upper(),
        'etd_enabled': bool(cfg.get('FEDEX_ETD_ENABLED')),
        'default_label_format': label_settings(cfg)['label_format'],
        'label_formats': {fmt: label_settings(cfg, fmt) for fmt in LABEL_FORMATS},
    }


def resolve_label_format(value) -> dict:
    """建单请求的 label_format（A4 / THERMAL，大小写不限；空 = 配置默认）→ label_settings。
    请求里的值不合法 400 16077；配置默认不合法由 blockers（FEDEX_CONFIG_INVALID）报。"""
    if value is not None and not isinstance(value, str):
        raise BadRequestException("label_format must be one of " + ' / '.join(LABEL_FORMATS), 16077,
                                  field='label_format')
    value = (value or '').strip() or None
    settings = label_settings(current_app.config, value)
    if value is not None and settings['label_format'] not in LABEL_FORMATS:
        raise BadRequestException("label_format must be one of " + ' / '.join(LABEL_FORMATS), 16077,
                                  field='label_format')
    return settings


def _carrier_code(carrier) -> str:
    return ((getattr(carrier, 'code', None) or '')).strip().lower()


def _already_cancelled(items) -> bool:
    """FedEx 错误 / 提示里有「已取消 / 查无此运单」类的码（依据见 ALREADY_CANCELLED_CODES 上方说明）"""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip().upper()
        if not code:
            continue
        if code in ALREADY_CANCELLED_CODES:
            return True
        compact = re.sub(r'[^A-Z0-9]', '', code)
        if 'ALREADY' in compact and ('CANCEL' in compact or 'DELETE' in compact):
            return True
        if ('TRACKING' in compact or 'SHIPMENT' in compact) and ('NOTFOUND' in compact or 'NOTEXIST' in compact):
            return True
    return False


def _codes(items) -> str:
    return ', '.join(str(i.get('code')) for i in items or [] if isinstance(i, dict)) or '-'


def _unclear_reason(err: FedexError) -> str:
    """结果不明的原因：timeout / server_error / bad_response / connection_error"""
    if err.timeout:
        return 'timeout'
    if err.status_code and err.status_code >= 500:
        return 'server_error'
    if err.status_code:
        return 'bad_response'
    return 'connection_error'


def _fedex_api_exception(err: FedexError, action: str, unresolved=None, with_unresolved=False):
    """FedexError → 502 16073 / 504 16074；details 带 FedEx errors 与 transactionId（不带凭证）、
    maybe_processed（结果不明）；建单时再带 unresolved（结果不明的记录，没有则 null）。"""
    details = {
        'carrier': CARRIER_FEDEX,
        'action': action,
        'http_status': err.status_code,
        'transaction_id': err.transaction_id,
        'errors': err.errors,
        'maybe_processed': err.maybe_processed,
    }
    if with_unresolved:
        details['unresolved'] = unresolved
    maybe_created = _MAYBE_CREATED if err.maybe_processed and action == 'create' else ''
    if err.timeout:
        if err.budget_exhausted:
            details['budget_exhausted'] = True
            message = (f"FedEx request was not sent ({action}): the time allowed for creating a shipment "
                       "(FEDEX_CREATE_BUDGET_SECONDS) ran out. Try again.")
        else:
            message = f"FedEx did not respond in time ({action})." + maybe_created
        return GatewayTimeoutException(message, 16074, details=details)
    if err.permission_denied:
        details['permission_denied'] = True
        message = (f"FedEx refused the request ({action}, HTTP {err.status_code}): check the API credentials "
                   f"and that the FedEx project has the required API enabled. {err.message}")
    elif err.maybe_processed:
        message = f"FedEx request failed with an unclear result ({action}): {err.message}." + maybe_created
    else:
        message = f"FedEx rejected the request ({action}): {err.message}"
    return BadGatewayException(message, 16073, details=details)


class CarrierShipmentService:

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def enabled(dn: DN = None) -> bool:
        """FedEx 已配置；给了 DN 时还要求 DN 所属公司在 FEDEX_ALLOWED_COMPANY_IDS 里"""
        if not fedex_client.fedex_configured():
            return False
        return dn is None or company_allowed(dn)

    @staticmethod
    def _latest(dn: DN, statuses):
        return (
            DNCarrierShipment.query
            .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.status.in_(statuses))
            .order_by(DNCarrierShipment.id.desc())
            .first()
        )

    @staticmethod
    def active_shipment(dn: DN):
        """有效运单（status = active）"""
        return CarrierShipmentService._latest(dn, ('active',))

    @staticmethod
    def open_shipment(dn: DN):
        """进行中 / 有效 / 结果不明的记录（pending / active / unknown，同一 DN 至多一条）"""
        return CarrierShipmentService._latest(dn, OPEN_STATUSES)

    @staticmethod
    def unresolved_shipment(dn: DN):
        """进行中 / 结果不明的记录（pending / unknown）"""
        return CarrierShipmentService._latest(dn, UNRESOLVED_STATUSES)

    @staticmethod
    def latest_shipment(dn: DN):
        """GET 里的 shipment：有效运单优先，否则最近一张已取消的（failed / unknown / dismissed 不算运单）"""
        return CarrierShipmentService.active_shipment(dn) or CarrierShipmentService._latest(dn, ('cancelled',))

    @staticmethod
    def has_active_shipment(dn: DN) -> bool:
        """有 pending / active / unknown 的记录（改箱子 16076 用：进行中或结果不明时箱子可能已随运单提交）"""
        return CarrierShipmentService.open_shipment(dn) is not None

    @staticmethod
    def is_stale(record: DNCarrierShipment) -> bool:
        if record is None or record.status != 'pending' or record.created_at is None:
            return False
        return (datetime.now() - record.created_at).total_seconds() > stale_after_seconds()

    @staticmethod
    def effective_status(record: DNCarrierShipment):
        """卡住的 pending 按 unknown（reason stale）对外"""
        if CarrierShipmentService.is_stale(record):
            return 'unknown', 'stale'
        return record.status, record.reason

    @staticmethod
    def unresolved_payload(record: DNCarrierShipment):
        """unresolved：{id, status: pending | unknown, reason, tracking_number, transaction_id, created_at, updated_at}"""
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
        """409 16079：有结果不明（或进行中）的建单记录"""
        payload = CarrierShipmentService.unresolved_payload(record)
        if message is None:
            if payload and payload['status'] == 'pending':
                message = "A FedEx shipment request for this DN is still in progress; wait for it to finish."
            else:
                message = ("The result of the previous FedEx shipment request for this DN is unclear." + _MAYBE_CREATED)
        return ConflictException(message, 16079, details={'unresolved': payload})

    # ------------------------------------------------------------------
    # 前置条件与建单数据
    # ------------------------------------------------------------------

    @staticmethod
    def _sender_country(dn: DN) -> str:
        """发件国：仓库有英文地址用仓库的国家（没有则公司的），否则公司的国家（与 _shipper 同一口径）"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            return (warehouse.country_code or company.country_code or '').upper()
        return (company.country_code or '').upper()

    @staticmethod
    def _shipper(dn: DN, exporter: dict) -> dict:
        """发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）+ 发货地址 = 仓库英文地址（没有用公司的）"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            text, postal = warehouse.address_en, warehouse.zip_code
        else:
            text, postal = company.address_en, company.zip_code
        country = CarrierShipmentService._sender_country(dn)
        address = split_address_text(text, country, postal, COUNTRY_NAMES_EN.get(country))
        tins = [{'number': company.tax_id, 'tinType': SHIPPER_TIN_TYPE}] if company.tax_id else []
        return {
            'company_name': exporter['legal_name_en'],
            'person_name': company.export_contact_name or warehouse.contact_name_en,
            'phone': exporter['phone'],
            'email': company.email,
            'address': address,
            'tins': tins,
        }

    @staticmethod
    def _recipient(customs, consignee: dict, country: str) -> dict:
        address = consignee_address(consignee, country)
        tins = []
        if customs.recipient_tax_id:
            tins.append({'number': customs.recipient_tax_id, 'tinType': tin_type_for(customs.recipient_tax_id_type)})
        return {
            'company_name': consignee.get('company'),
            'person_name': consignee.get('name') or consignee.get('company'),
            'phone': consignee.get('phone'),
            'email': None,
            'address': address,
            'tins': tins,
        }

    @staticmethod
    def _collect(dn: DN) -> dict:
        """前置条件检查 + 建单要用的数据：{blockers, view, task, shipper, recipient, settings}"""
        blockers = []
        settings = _settings()
        ctx = {'blockers': blockers, 'warnings': [], 'declared_value': None, 'settings': settings,
               'view': None, 'task': None,
               'shipper': None, 'recipient': None}

        if not CarrierShipmentService.enabled():
            blockers.append(_blocker('FEDEX_NOT_CONFIGURED',
                                     "FedEx is not configured (FEDEX_API_KEY / FEDEX_SECRET_KEY / FEDEX_ACCOUNT_NUMBER)"))
        else:
            company_ids, invalid = allowed_company_ids()
            if invalid:
                blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                         "FEDEX_ALLOWED_COMPANY_IDS must be a comma-separated list of company IDs "
                                         f"(invalid: {', '.join(invalid)})"))
            if dn.warehouse.company_id not in company_ids:
                blockers.append(_blocker('FEDEX_COMPANY_NOT_ALLOWED',
                                         "FedEx shipments are not enabled for this company "
                                         "(FEDEX_ALLOWED_COMPANY_IDS)"))
        if settings['default_label_format'] not in LABEL_FORMATS:
            blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                     f"FEDEX_DEFAULT_LABEL_FORMAT must be one of {', '.join(LABEL_FORMATS)}"))
        for fmt, label in settings['label_formats'].items():
            if label['image_type'] not in SUPPORTED_LABEL_IMAGE_TYPES:
                blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                         f"FEDEX_LABEL_{fmt}_IMAGE_TYPE must be one of "
                                         f"{', '.join(SUPPORTED_LABEL_IMAGE_TYPES)}"))
        if settings['duties_payment_type'] not in SUPPORTED_DUTIES_PAYMENT_TYPES:
            blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                     "FEDEX_DUTIES_PAYMENT_TYPE must be one of "
                                     f"{', '.join(SUPPORTED_DUTIES_PAYMENT_TYPES)}"))

        customs = dn.customs
        if customs is None:
            blockers.append(_blocker('NOT_EXPORT', "DN is not an export shipment (no customs data)"))
            return ctx

        # DDP = 发件人付关税；关税付款方配置成收件人付，会被 FedEx 向收件人收关税，与贸易条件矛盾
        incoterm = (customs.incoterm or '').strip().upper()
        if (incoterm == 'DDP' and settings['duties_payment_type'] in SUPPORTED_DUTIES_PAYMENT_TYPES
                and settings['duties_payment_type'] != 'SENDER'):
            blockers.append(_blocker(
                'INCOTERM_DUTIES_MISMATCH',
                f"Incoterm DDP means the shipper pays duties and taxes, but FEDEX_DUTIES_PAYMENT_TYPE is "
                f"{settings['duties_payment_type']}; use another incoterm (e.g. DAP) or set "
                "FEDEX_DUTIES_PAYMENT_TYPE=SENDER", field='incoterm'))

        if dn.status in LOCKED_DN_STATUSES:
            blockers.append(_blocker('DN_SHIPPED', f"DN has already been shipped (status: {dn.status})"))
        elif dn.status != 'packed':
            blockers.append(_blocker('NOT_PACKED', f"DN must be packed (current: {dn.status})"))

        task = CustomsService._delivery_task(dn)
        ctx['task'] = task
        if task is None:
            blockers.append(_blocker('DELIVERY_TASK_MISSING', "DN has no active delivery task"))
        else:
            if _carrier_code(task.carrier or dn.carrier) != CARRIER_FEDEX:
                carrier = task.carrier or dn.carrier
                blockers.append(_blocker(
                    'CARRIER_NOT_FEDEX',
                    f"Carrier of the delivery task is not FedEx (carrier code: {_carrier_code(carrier) or 'none'})"))
            if task.status in COMPLETED_TASK_STATUSES:
                blockers.append(_blocker('DELIVERY_TASK_COMPLETED', "Delivery task has already been completed"))

        active = CarrierShipmentService.active_shipment(dn)
        if active is not None:
            blockers.append(_blocker('SHIPMENT_EXISTS',
                                     f"An active carrier shipment already exists ({active.tracking_number})"))
        elif task is not None and task.tracking_number:
            blockers.append(_blocker(
                'TRACKING_NUMBER_EXISTS',
                f"Delivery task already has a tracking number ({task.tracking_number}); clear it first "
                "if the shipment should be created in FedEx"))

        packages = list(dn.packages)
        if not packages:
            blockers.append(_blocker('PACKAGES_MISSING', "No packages recorded"))
        elif len(packages) > MAX_PACKAGES_PER_REQUEST:
            blockers.append(_blocker('TOO_MANY_PACKAGES',
                                     f"At most {MAX_PACKAGES_PER_REQUEST} packages per FedEx shipment "
                                     f"(current: {len(packages)})"))
        for package in packages:
            if not package.gross_weight_kg or not (package.length_mm and package.width_mm and package.height_mm):
                blockers.append(_blocker('PACKAGE_INCOMPLETE',
                                         f"Package {package.package_no} has no weight or dimensions"))

        view = CustomsService.build_view(dn)
        ctx['view'] = view
        for problem in view['problems']:
            if problem['level'] != 'error' or problem['code'] in _COVERED_PROBLEMS:
                continue
            blockers.append(_blocker(problem['code'], problem['message'],
                                     goods_code=problem.get('goods_code'), field=problem.get('field')))

        ctx['declared_value'], warning = CarrierShipmentService._declared_value(customs, view)
        if warning and active is None:
            ctx['warnings'].append(warning)

        exporter = view['exporter']
        if exporter['legal_name_en'] and (exporter['address_en'] or (dn.warehouse.address_en or '').strip()):
            try:
                ctx['shipper'] = CarrierShipmentService._shipper(dn, exporter)
            except AddressError as exc:
                blockers.append(_blocker('SHIPPER_ADDRESS_INVALID',
                                         f"Ship-from address cannot be used for FedEx: {exc}", field='address_en'))

        consignee = view['consignee'] or {}
        country = view['recipient_country']
        if country:
            try:
                ctx['recipient'] = CarrierShipmentService._recipient(customs, consignee, country)
            except AddressError as exc:
                blockers.append(_blocker('RECIPIENT_ADDRESS_INVALID',
                                         f"Consignee address cannot be used for FedEx: {exc}", field='consignee'))
        if not (consignee.get('name') or consignee.get('company')):
            blockers.append(_blocker('RECIPIENT_NAME_MISSING', "Consignee name and company are both missing",
                                     field='consignee.name'))
        if not re.sub(r'\D', '', str(consignee.get('phone') or '')):
            blockers.append(_blocker('RECIPIENT_PHONE_MISSING', "Consignee phone number is missing",
                                     field='consignee.phone'))
        return ctx

    @staticmethod
    def _declared_value(customs, view):
        """随运单提交的申告价额（整数，报关币种）与说明：高于已打包货值时压到已打包货值（取整数部分）。
        FedEx 拒收申告价额高于报关货值的运单（TOTALCARRIAGEVALUE.EXCEEDS.CUSTOMSVALUE，sandbox 实测）。"""
        requested = customs.declared_value_carriage
        if not requested:
            return None, None
        goods_value = Decimal(str(view['totals']['goods_value'] or 0)).to_integral_value(rounding=ROUND_FLOOR)
        if requested <= goods_value:
            return requested, None
        capped = int(goods_value) or None
        return capped, {
            'code': 'DECLARED_VALUE_CAPPED',
            'message': (f"Declared value for carriage {requested} exceeds the customs value of the packed goods "
                        f"({goods_value}); {capped or 0} is submitted to FedEx instead"),
            'requested': requested,
            'applied': capped,
        }

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
        """保存 / 修改发货任务运单号前的检查（调用方应先锁 DN 行）：
        - 有结果不明 / 进行中的建单记录：给了运单号 → 409 16079（防止在 FedEx 上又手工建一张）；空值放行
        - 有有效自动运单：运单号只能是它的号码（忽略空白差异），不同 → 409 16078。
          allow_empty=True：空值视为「不改」由调用方跳过；False：清空也算不一致（要清空请取消运单）。"""
        given = ''.join(str(tracking_number or '').split())
        unresolved = CarrierShipmentService.unresolved_shipment(dn)
        if unresolved is not None:
            if given:
                raise CarrierShipmentService._unresolved_conflict(
                    unresolved,
                    "The result of the automatic FedEx shipment request for this DN is unclear (or still in "
                    "progress); a tracking number cannot be saved until it is resolved. Check FedEx Ship Manager, "
                    "then dismiss the record (POST /warehouse/dn/<id>/carrier-shipment/dismiss).")
            return
        shipment = CarrierShipmentService.active_shipment(dn)
        if shipment is None:
            return
        if not given and allow_empty:
            return
        if given != ''.join(shipment.tracking_number.split()):
            raise ConflictException(
                f"This DN has an active {shipment.carrier} shipment {shipment.tracking_number}; the tracking number "
                "cannot be changed. Cancel the carrier shipment first.", 16078,
                details={'tracking_number': shipment.tracking_number, 'carrier': shipment.carrier},
            )

    @staticmethod
    def assert_delivery_tasks_editable(dn: DN, action: str):
        """有 pending / active / unknown 的自动运单时，不能新建 / 删除发货任务 → 409 16078
        （新任务会成为「当前发货任务」，绕过运单号锁定）。调用方应先锁 DN 行。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is None:
            return
        summary = CarrierShipmentService._record_summary(record)
        hint = ("cancel the carrier shipment first" if summary['status'] == 'active'
                else "resolve the carrier shipment first (check FedEx Ship Manager, then dismiss it)")
        raise ConflictException(
            f"This DN has a {record.carrier} shipment ({summary['status']}"
            f"{' ' + record.tracking_number if record.tracking_number else ''}); delivery tasks cannot be "
            f"{action}; {hint}.", 16078,
            details={'tracking_number': record.tracking_number, 'carrier': record.carrier,
                     'status': summary['status']},
        )

    @staticmethod
    def assert_customs_editable(dn: DN):
        """有 pending / active / unknown 的自动运单时不能改报关数据（已随运单提交给承运商）→ 409 16076。
        调用方应先锁 DN 行。"""
        record = CarrierShipmentService.open_shipment(dn)
        if record is None:
            return
        summary = CarrierShipmentService._record_summary(record)
        hint = ("cancel the shipment first" if summary['status'] == 'active'
                else "resolve it first (check FedEx Ship Manager, then dismiss it)")
        raise ConflictException(
            f"Customs data cannot be changed while the DN has a {record.carrier} shipment "
            f"({summary['status']}); the data was submitted with the shipment — {hint}.", 16076,
            details={'reason': 'CARRIER_SHIPMENT_OPEN', 'carrier': record.carrier, 'carrier_shipment': summary},
        )

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @staticmethod
    def status(dn: DN, ctx=None, alerts=None, warnings=None) -> dict:
        """GET /dn/<id>/carrier-shipment"""
        ctx = ctx or CarrierShipmentService._collect(dn)
        shipment = CarrierShipmentService.latest_shipment(dn)
        unresolved = CarrierShipmentService.unresolved(dn)
        customs = dn.customs
        payload = {
            'enabled': CarrierShipmentService.enabled(dn),
            'carrier': CARRIER_FEDEX,
            'can_create': not ctx['blockers'] and unresolved is None,
            'blockers': ctx['blockers'],
            # 结果不明 / 进行中的建单记录（有它时不能建单）；unknown 时可以确认作废（dismiss）
            'unresolved': unresolved,
            'can_dismiss': unresolved is not None and unresolved['status'] == 'unknown',
            'etd_enabled': ctx['settings']['etd_enabled'],
            'default_label_format': ctx['settings']['default_label_format'],
            'label_formats': {
                fmt: {'image_type': label['image_type'], 'stock_type': label['stock_type']}
                for fmt, label in ctx['settings']['label_formats'].items()
            },
            'declared_value_carriage': customs.declared_value_carriage if customs else None,
            'delivery_task_id': ctx['task'].id if ctx['task'] is not None else None,
            'shipment': shipment.to_dict() if shipment else None,
            # 建单时会自动做的调整（如申告价额压到已打包货值）；建单响应里是这次实际做了的
            'warnings': ctx['warnings'] if warnings is None else warnings,
        }
        if alerts is not None:
            payload['alerts'] = alerts
        return payload

    # ------------------------------------------------------------------
    # 建单
    # ------------------------------------------------------------------

    @staticmethod
    def _ship_request_args(dn: DN, ctx: dict, label: dict) -> dict:
        """build_ship_request 的参数（纯数据，不引用 ORM 对象；etd_document_id 调 FedEx 时再补）"""
        view = ctx['view']
        customs = dn.customs
        packages = list(dn.packages)
        declared_total = ctx['declared_value']
        declared_split = split_evenly(declared_total, len(packages)) if declared_total else [None] * len(packages)
        package_items = [{
            'gross_weight_kg': p.gross_weight_kg,
            'length_mm': p.length_mm,
            'width_mm': p.width_mm,
            'height_mm': p.height_mm,
            'declared_value': value,
        } for p, value in zip(packages, declared_split)]

        lines = [line for line in view['lines'] if line['packed_quantity'] > 0]
        weights = allocate_commodity_weights(
            [{'quantity': l['packed_quantity'], 'unit_weight_kg': l['unit_weight_kg']} for l in lines],
            view['totals']['gross_weight_kg'],
        )
        commodities = [{
            'description': line['description_en'],
            'origin_country': line['origin_country'],
            'hs_code': line['hs_code'],
            'quantity': line['packed_quantity'],
            'quantity_unit': line['quantity_unit'],
            'unit_value': line['unit_value'],
            'amount': line['amount'],
            'weight_kg': weight,
            'part_number': line['goods_code'],
        } for line, weight in zip(lines, weights)]

        totals = view['totals']
        invoice = {
            'invoice_number': view['invoice_number'],
            'currency': customs.currency,
            'incoterm': customs.incoterm,
            'export_reason': customs.export_reason,
            'freight': customs.freight_charge or 0,
            'insurance': customs.insurance_charge or 0,
            'goods_value': totals['goods_value'],
            'declared_value': declared_total if declared_total else None,
        }
        return {
            'account_number': current_app.config.get('FEDEX_ACCOUNT_NUMBER'),
            'settings': ctx['settings'],
            'label': label,
            'ship_date': datetime.now(_document_timezone()).date(),
            'shipper': ctx['shipper'],
            'recipient': ctx['recipient'],
            'packages': package_items,
            'commodities': commodities,
            'invoice': invoice,
            'reference': dn.order_number,
        }

    @staticmethod
    def create_shipment(dn: DN, user_id, label_format=None) -> dict:
        """POST /dn/<id>/carrier-shipment（三段，见模块说明）。

        各段自己提交，不能在外层事务里调用（否则 pending 记录不会先落库、调 FedEx 时还占着锁）。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("create_shipment must not run inside another transaction")
        label = resolve_label_format(label_format)
        deadline = fedex_client.clock() + create_budget_seconds()
        dn_id = dn.id
        try:
            plan = CarrierShipmentService._reserve(dn, user_id, label)
        except IntegrityError as exc:
            # 部分唯一索引兜底：并发请求刚插了同一 DN 的 pending（正常情况下 DN 行锁已让它们排队）
            db.session.rollback()
            conflict = CarrierShipmentService._conflict_after_race(dn_id)
            if conflict is None:
                raise
            raise conflict from exc

        state = {'create_sent': False, 'created': None, 'finalized': False}
        try:
            return CarrierShipmentService._request_and_save(plan, user_id, label, deadline, state)
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
                "FedEx shipment cannot be created; resolve the listed blockers first.", 16072,
                details={'blockers': [_blocker('SHIPMENT_EXISTS', "An active carrier shipment already exists "
                                                                  f"({active.tracking_number})")]},
            )
        return None

    @staticmethod
    @transactional
    def _reserve(dn: DN, user_id, label: dict) -> dict:
        """第 1 段：锁 DN、检查、必要时签发 CI / PL、插 pending 记录，随本事务提交（释放 DN 行锁）。"""
        dn = CustomsService._lock(dn)
        unresolved = CarrierShipmentService.unresolved_shipment(dn)
        if unresolved is not None:
            raise CarrierShipmentService._unresolved_conflict(unresolved)
        ctx = CarrierShipmentService._collect(dn)
        if ctx['blockers']:
            raise ConflictException(
                "FedEx shipment cannot be created; resolve the listed blockers first.", 16072,
                details={'blockers': ctx['blockers']},
            )

        # CI / PL 必须先签发（没有或已过期 → 用现有逻辑签发；随 pending 记录一起提交）
        current = CustomsService.current_documents(dn)
        if any(t not in current for t in DOC_TYPES) or ctx['view']['documents_outdated']:
            CustomsService.issue_documents(dn, user_id)
            current = CustomsService.current_documents(dn)

        settings = ctx['settings']
        sender_country = ctx['shipper']['address']['countryCode']
        etd = None
        if settings['etd_enabled']:
            ci = current[DOC_CI]
            etd = {'content': ci.content, 'file_name': ci.file_name}

        now = datetime.now()
        record = DNCarrierShipment(
            dn_id=dn.id,
            carrier=CARRIER_FEDEX,
            status='pending',
            service_type=settings['service_type'],
            package_count=len(dn.packages),
            declared_value=ctx['declared_value'],
            label_format=label['label_format'],
            image_type=label['image_type'],
            label_stock_type=label['stock_type'],
            sender_country=sender_country,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(record)
        db.session.flush()
        return {
            'record_id': record.id,
            'dn_id': dn.id,
            'request_args': CarrierShipmentService._ship_request_args(dn, ctx, label),
            'etd': etd,
            'sender_country': sender_country,
            'recipient_country': ctx['recipient']['address']['countryCode'],
            'warnings': list(ctx['warnings']),
        }

    @staticmethod
    def _request_and_save(plan: dict, user_id, label: dict, deadline: float, state: dict) -> dict:
        """第 2 段（调 FedEx，不开事务）+ 第 3 段（写库）。任何出口都把记录落到最终状态。"""
        record_id, dn_id = plan['record_id'], plan['dn_id']

        etd_document_id = None
        if plan['etd']:
            try:
                uploaded = fedex_client.upload_etd_document(
                    plan['etd']['content'], plan['etd']['file_name'], plan['sender_country'],
                    plan['recipient_country'], deadline=deadline)
            except FedexError as exc:
                # 运单还没请求：结果确定
                CarrierShipmentService._finish(record_id, 'failed', state, reason='etd_upload_failed',
                                               error_message=exc.message, transaction_id=exc.transaction_id)
                raise _fedex_api_exception(exc, 'etd_upload', unresolved=None, with_unresolved=True)
            etd_document_id = ((uploaded.get('output') or {}).get('meta') or {}).get('docId')
            if not etd_document_id:
                CarrierShipmentService._finish(record_id, 'failed', state, reason='etd_upload_failed',
                                               error_message="FedEx trade document upload returned no docId")
                raise BadGatewayException("FedEx trade document upload returned no docId", 16073,
                                          details={'carrier': CARRIER_FEDEX, 'action': 'etd_upload',
                                                   'errors': [], 'transaction_id': None, 'http_status': None,
                                                   'maybe_processed': False, 'unresolved': None})

        request = build_ship_request(**plan['request_args'], etd_document_id=etd_document_id)
        state['create_sent'] = True
        try:
            body = fedex_client.create_shipment(request, deadline=deadline)
        except FedexError as exc:
            if exc.maybe_processed:
                reason = _unclear_reason(exc)
                logger.error("DN %s: FedEx create shipment result unclear (%s, transactionId %s); the shipment "
                             "may exist on the FedEx side", dn_id, reason, exc.transaction_id)
                unresolved = CarrierShipmentService._finish(
                    record_id, 'unknown', state, reason=reason, error_message=exc.message,
                    transaction_id=exc.transaction_id, etd_document_id=etd_document_id)
            else:
                # rejected = FedEx 对建单请求本身回了 4xx；not_sent = 请求没发出去（token 失败、连不上等）
                if exc.budget_exhausted:
                    reason = 'budget_exhausted'
                elif exc.what == fedex_client.CREATE_SHIPMENT and exc.status_code:
                    reason = 'rejected'
                else:
                    reason = 'not_sent'
                unresolved = CarrierShipmentService._finish(
                    record_id, 'failed', state, reason=reason, error_message=exc.message,
                    transaction_id=exc.transaction_id, etd_document_id=etd_document_id)
            raise _fedex_api_exception(exc, 'create', unresolved=unresolved, with_unresolved=True)

        transaction_id = body.get('transactionId')
        try:
            result = parse_ship_response(body)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            logger.error("DN %s: unexpected FedEx ship response (transactionId %s): %s", dn_id, transaction_id, exc)
            tracking = CarrierShipmentService._loose_tracking_number(body)
            if tracking:
                # 看不懂但有运单号：按写库失败处理，取消它
                state['created'] = {'tracking_number': tracking, 'transaction_id': transaction_id}
                unresolved = CarrierShipmentService._compensate(plan, state, deadline, user_id)
            else:
                unresolved = CarrierShipmentService._finish(
                    record_id, 'unknown', state, reason='bad_response', error_message=f"Unexpected response: {exc}",
                    transaction_id=transaction_id, etd_document_id=etd_document_id)
            raise BadGatewayException(
                f"Unexpected FedEx response: {exc}." + (_MAYBE_CREATED if unresolved else
                                                        " The shipment was cancelled in FedEx."),
                16073, details={'carrier': CARRIER_FEDEX, 'action': 'create', 'errors': [],
                                'transaction_id': transaction_id, 'http_status': 200,
                                'maybe_processed': True, 'unresolved': unresolved})

        # 从这里起 FedEx 上已有运单：先把运单号独立提交（第 3 段失败 / 进程被杀也留得下号码），之后任何失败都补偿取消
        tracking = result['tracking_number']
        state['created'] = {'tracking_number': tracking, 'transaction_id': result['transaction_id'] or transaction_id}
        CarrierShipmentService._note_created(record_id, tracking, state['created']['transaction_id'],
                                             etd_document_id)
        try:
            response = CarrierShipmentService._save(plan, user_id, label, result, etd_document_id)
        except Exception:
            logger.exception("DN %s: FedEx shipment %s was created but saving it in WMS failed", dn_id, tracking)
            CarrierShipmentService._compensate(plan, state, deadline, user_id)
            raise
        state['finalized'] = True
        return response

    @staticmethod
    def _loose_tracking_number(body):
        """解析失败的响应里尽量找出主运单号（用来补偿取消）"""
        try:
            shipment = ((body.get('output') or {}).get('transactionShipments') or [None])[0] or {}
            value = shipment.get('masterTrackingNumber') or (
                ((shipment.get('completedShipmentDetail') or {}).get('masterTrackingId') or {}).get('trackingNumber'))
            if not value:
                pieces = [p for p in shipment.get('pieceResponses') or [] if isinstance(p, dict)]
                if pieces:
                    value = pieces[0].get('masterTrackingNumber') or pieces[0].get('trackingNumber')
        except (AttributeError, TypeError, IndexError):
            return None
        value = str(value or '').strip()[:100]
        return value or None

    @staticmethod
    def _note_created(record_id, tracking, transaction_id, etd_document_id):
        """FedEx 建单成功后立即独立提交运单号（失败只记日志，后面照常写库 / 补偿）"""
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
        record.updated_at = datetime.now()
        db.session.flush()

    @staticmethod
    @transactional
    def _save(plan: dict, user_id, label: dict, result: dict, etd_document_id) -> dict:
        """第 3 段：锁 DN → 记录置 active → 运单号 / CI / PL / 面单，一个事务。"""
        from warehouse.delivery.services import DeliveryTaskService

        dn = CustomsService._lock(db.session.get(DN, plan['dn_id']))
        record = db.session.get(DNCarrierShipment, plan['record_id'])
        if record is None or record.status != 'pending':
            raise RuntimeError(f"carrier shipment record {plan['record_id']} is no longer pending "
                               f"({record.status if record is not None else 'deleted'})")
        tracking = result['tracking_number']
        archive = build_label_archive(result['documents'], label['image_type'])
        task = CustomsService._delivery_task(dn)
        if task is None:
            raise RuntimeError(f"DN {dn.id} has no active delivery task any more")

        # 先把记录置 active（带运单号）：保存运单号的校验（assert_tracking_matches）认的就是这张运单
        record.status = 'active'
        record.reason = None
        record.tracking_number = tracking
        record.updated_at = datetime.now()
        db.session.flush()

        # 运单号：复用现有保存运单号的逻辑；CI / PL 带 AWB 升版本
        tracking_payload = {'tracking_number': tracking}
        if task.carrier_id is None and dn.carrier_id is not None:
            tracking_payload['carrier_id'] = dn.carrier_id
        DeliveryTaskService.save_tracking(task, tracking_payload)
        CustomsService.issue_documents(dn, user_id)

        ship_date = None
        if result.get('ship_date'):
            try:
                ship_date = date.fromisoformat(str(result['ship_date'])[:10])
            except ValueError:
                ship_date = None
        ship_date = ship_date or datetime.now(_document_timezone()).date()

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
        record.transaction_id = result['transaction_id'] or record.transaction_id
        db.session.flush()
        db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
        return CarrierShipmentService.status(dn, alerts=result['alerts'], warnings=plan['warnings'])

    @staticmethod
    def _cancel_confirmed(body: dict, strict: bool):
        """取消响应 → (是否确认取消, 没取消的原因)。
        cancelledShipment 为 False 且提示里没有「已取消 / 查无此运单」→ 没取消；
        strict（补偿取消）时 cancelledShipment 必须明确为 True。"""
        output = body.get('output') or {}
        flag = output.get('cancelledShipment')
        alerts = [a for a in output.get('alerts') or [] if isinstance(a, dict)]
        if flag is True or (flag is not False and not strict):
            return True, None
        if _already_cancelled(alerts):
            return True, None
        text = output.get('message') or '; '.join(f"{a.get('code')}: {a.get('message')}" for a in alerts)
        return False, text or ('no reason given' if flag is False else 'cancellation not confirmed')

    @staticmethod
    def _compensate(plan: dict, state: dict, deadline: float, user_id):
        """第 3 段失败（或响应看不懂）后取消 FedEx 上刚建的运单，结果独立提交到记录：
        确认取消 → cancelled（reason compensated）；失败 / 不确定 → unknown（reason compensation_failed）。
        返回 unresolved 形状（unknown）或 None（已取消）。"""
        record_id, dn_id = plan['record_id'], plan['dn_id']
        created = state['created']
        tracking = created['tracking_number']
        logger.error("DN %s: FedEx shipment %s was created (transactionId %s) but saving it in WMS failed; "
                     "cancelling it", dn_id, tracking, created.get('transaction_id'))
        cancelled, cancel_transaction_id, problem = False, None, None
        try:
            body = fedex_client.cancel_shipment(tracking, plan['sender_country'],
                                                deadline=deadline + COMPENSATION_EXTRA_SECONDS)
            cancel_transaction_id = body.get('transactionId')
            cancelled, problem = CarrierShipmentService._cancel_confirmed(body, strict=True)
        except FedexError as exc:
            cancel_transaction_id = exc.transaction_id
            if _already_cancelled(exc.errors):
                logger.warning("DN %s: FedEx reports shipment %s as already cancelled / not found (%s)",
                               dn_id, tracking, _codes(exc.errors))
                cancelled = True
            else:
                problem = exc.message
        except Exception as exc:  # noqa: BLE001 — 补偿本身出错也要留痕
            problem = f"{type(exc).__name__}: {exc}"

        if cancelled:
            logger.error("DN %s: FedEx shipment %s cancelled after the WMS failure", dn_id, tracking)
            return CarrierShipmentService._finish(
                record_id, 'cancelled', state, reason='compensated', tracking_number=tracking,
                transaction_id=created.get('transaction_id'), cancel_transaction_id=cancel_transaction_id,
                cancelled_by=user_id)
        logger.error("DN %s: cancelling FedEx shipment %s FAILED (%s); cancel it in FedEx Ship Manager, then "
                     "dismiss the record", dn_id, tracking, problem)
        return CarrierShipmentService._finish(
            record_id, 'unknown', state, reason='compensation_failed', tracking_number=tracking,
            transaction_id=created.get('transaction_id'), error_message=problem)

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
        now = datetime.now()
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
            issued_at=datetime.now(),
            issued_by=user_id,
        )
        db.session.add(doc)
        db.session.flush()
        return doc

    # ------------------------------------------------------------------
    # 确认作废结果不明的记录
    # ------------------------------------------------------------------

    @staticmethod
    @transactional
    def dismiss(dn: DN, user_id, confirm) -> dict:
        """POST /dn/<id>/carrier-shipment/dismiss：操作员确认 FedEx 上没有这张运单（或已手工取消），
        把结果不明的记录（unknown / 卡住的 pending）标 dismissed，之后可以重新建单。不调用 FedEx。"""
        if confirm is not True:
            raise BadRequestException(
                'Set "confirm": true to confirm that FedEx has no such shipment (or that it has been cancelled in '
                'FedEx Ship Manager).', 40000, field='confirm')
        dn = CustomsService._lock(dn)
        record = CarrierShipmentService.unresolved_shipment(dn)
        payload = CarrierShipmentService.unresolved_payload(record)
        if payload is None or payload['status'] != 'unknown':
            raise ConflictException(
                "There is no carrier shipment with an unclear result to dismiss"
                + (" (the FedEx request is still in progress)." if payload else "."), 16075,
                details={'unresolved': payload})
        now = datetime.now()
        record.reason = payload['reason']            # 卡住的 pending 记下 stale
        record.status = 'dismissed'
        record.dismissed_at = now
        record.dismissed_by = user_id
        record.updated_at = now
        db.session.flush()
        logger.warning("DN %s: carrier shipment record %s (%s, tracking %s) dismissed by user %s",
                       dn.id, record.id, record.reason, record.tracking_number, user_id)
        db.session.expire(dn, ['carrier_shipments'])
        return CarrierShipmentService.status(dn)

    # ------------------------------------------------------------------
    # 取消
    # ------------------------------------------------------------------

    @staticmethod
    @transactional
    def cancel_shipment(dn: DN, user_id) -> dict:
        """POST /dn/<id>/carrier-shipment/cancel"""
        from warehouse.delivery.services import DeliveryTaskService

        dn = CustomsService._lock(dn)
        CustomsService._assert_not_locked(dn)
        shipment = CarrierShipmentService.active_shipment(dn)
        if shipment is None:
            raise ConflictException("DN has no active carrier shipment to cancel.", 16075)
        task = CustomsService._delivery_task(dn)
        if task is not None and task.status in COMPLETED_TASK_STATUSES:
            raise ConflictException("The shipment has been completed; it cannot be cancelled.", 16065)
        # 只要求凭证齐全（公司后来被移出 FEDEX_ALLOWED_COMPANY_IDS 也能取消已建的运单）
        if not CarrierShipmentService.enabled():
            raise ConflictException(
                "FedEx is not configured; the shipment cannot be cancelled from WMS.", 16072,
                details={'blockers': [_blocker('FEDEX_NOT_CONFIGURED', "FedEx is not configured")]},
            )

        # 发件国用建单时记下的（与建单一致）；老记录没有就按建单同一口径推
        sender_country = shipment.sender_country or CarrierShipmentService._sender_country(dn)
        deadline = fedex_client.clock() + create_budget_seconds()
        reason = None
        try:
            body = fedex_client.cancel_shipment(shipment.tracking_number, sender_country, deadline=deadline)
        except FedexError as exc:
            if not _already_cancelled(exc.errors):
                raise _fedex_api_exception(exc, 'cancel')
            # 例：上次取消在 FedEx 成功了但本地写库失败，再点取消 FedEx 回「已取消」
            logger.warning("DN %s: FedEx reports shipment %s as already cancelled / not found (%s); "
                           "marking it cancelled", dn.id, shipment.tracking_number, _codes(exc.errors))
            body, reason = {'transactionId': exc.transaction_id, 'output': {'cancelledShipment': True}}, \
                'already_cancelled'
        output = body.get('output') or {}
        confirmed, problem = CarrierShipmentService._cancel_confirmed(body, strict=False)
        if not confirmed:
            raise BadGatewayException(
                f"FedEx did not cancel the shipment: {problem}", 16073,
                details={'carrier': CARRIER_FEDEX, 'action': 'cancel', 'http_status': 200,
                         'transaction_id': body.get('transactionId'), 'errors': [],
                         'alerts': output.get('alerts') or []},
            )
        if output.get('cancelledShipment') is False:
            logger.warning("DN %s: FedEx reports shipment %s as already cancelled (%s)", dn.id,
                           shipment.tracking_number, _codes(output.get('alerts')))
            reason = 'already_cancelled'

        try:
            now = datetime.now()
            shipment.status = 'cancelled'
            shipment.reason = reason
            shipment.cancelled_at = now
            shipment.cancelled_by = user_id
            shipment.updated_at = now
            shipment.cancel_transaction_id = body.get('transactionId')
            if shipment.label_document_id:
                label = db.session.get(DNDocument, shipment.label_document_id)
                if label is not None and label.status == 'issued':
                    label.status = 'void'
                    label.voided_at = now
                    label.void_reason = 'shipment_cancelled'
            if task is not None and task.tracking_number == shipment.tracking_number:
                DeliveryTaskService.save_tracking(task, {'tracking_number': None})
            db.session.flush()

            # CI / PL 上印着这张运单号：去掉 AWB 重新签发；条件不满足（数据已变）就作废，出库前再签
            if CustomsService.current_documents(dn):
                view = CustomsService.build_view(dn)
                if view['documents_outdated']:
                    if any(p['level'] == 'error' for p in view['problems']):
                        CustomsService._void_current_documents(dn, 'shipment_cancelled')
                    else:
                        CustomsService.issue_documents(dn, user_id)
            db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
            return CarrierShipmentService.status(dn)
        except Exception:
            logger.error("DN %s: FedEx shipment %s was cancelled on FedEx but updating WMS failed; cancelling "
                         "again marks it cancelled (FedEx answers already cancelled)",
                         dn.id, shipment.tracking_number)
            raise
