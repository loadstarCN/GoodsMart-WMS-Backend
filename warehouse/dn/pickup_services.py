"""上门揽收预约（承运商对接插件 supports_pickup，内置 FedEx Pickup Request API）。

没有和承运商签定期集荷时（如 FedEx 运单的 pickupType 是 CONTACT_FEDEX_TO_SCHEDULE），运单建好后要另约快递员上门。
揽收按「仓库 + 承运商 + 日期」约，一次带多张同一承运商的运单，件数 / 总重量按所带运单的箱子合计。本模块与具体承运商无关，
调承运商一律经插件（CarrierAdapter：pickup_location / pickup_availability / schedule_pickup / cancel_pickup）。
- 承运商：当前仓库所属公司里、对接插件支持上门揽收的承运商（请求可带 carrier_id，不带用第一个）。
- 待揽收运单：当前仓库、该承运商、状态 active 的自动运单，DN 未发货。被「占着」的不能再约：pending / unknown 的预约，
  或已约上（scheduled）的预约，且揽收日还没过。揽收日过了、DN 还没发货的运单可以重新预约（快递员没来 / 忘了完成发货）。
- 前置条件（blockers）：承运商有可用的对接（已启用、设置齐全）且插件支持上门揽收、揽收地址（仓库英文地址，没有则公司的，
  与建运单的发件地址同一口径）与联系电话放得进承运商的格式（插件检查）。
- 预约分三段，调承运商时不占行锁、不开事务（与建运单相同）：
  1. 锁所选运单行 → 逐张检查 → 插 pending 记录、运单 pickup_id 指向它，提交；
  2. 调承运商预约（受 CARRIER_CREATE_BUDGET_SECONDS 约束）；
  3. 约上 → scheduled（确认号、站点代码）；明确拒绝 / 没发出去 → failed，运单 pickup_id 清空；
     结果不明（超时、5xx、响应看不懂）→ unknown，运单仍被占着（免得重复预约），操作员向承运商核对后确认作废才释放。
- 取消：scheduled 且揽收日没过，用预约时的承运商（carrier_id）的对接设置（停用了也能取消）。取消成功（或回「已取消」）
  → cancelled，运单 pickup_id 清空；明确拒绝 → 保持 scheduled；结果不明 → 保持 scheduled（reason cancel_unclear），可重试取消。
- pending 超过 stale_after_seconds() 按 unknown（reason stale）对外。
- 日期 / 时间都是仓库当地的（DOCUMENT_TIMEZONE，默认 Asia/Tokyo，与运单的发货日同一口径）。
"""
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import g
from sqlalchemy.orm import lazyload

from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.carrier.integration_services import CarrierIntegrationService
from warehouse.carrier.integrations import adapter_class, display_name
from warehouse.carrier.integrations.base import STEP_PICKUP, CarrierError, clock
from warehouse.carrier.models import Carrier, CarrierIntegration
from warehouse.common.countries import COUNTRY_NAMES_EN
from warehouse.common.locks import lock_row
from .carrier_services import (
    ERROR_MESSAGE_MAX, CarrierShipmentService, _blocker, _carrier_api_exception, _unclear_reason,
    create_budget_seconds, stale_after_seconds,
)
from .customs_services import CustomsService, LOCKED_DN_STATUSES, _document_timezone
from .models import DN, CarrierPickup, DNCarrierShipment, _service_name

logger = logging.getLogger(__name__)

PICKUP_BLOCKED = 16130          # 409 前置条件不满足（details.blockers）
PICKUP_INVALID = 16131          # 400 请求参数不合法（field）
PICKUP_SHIPMENTS_INVALID = 16132    # 409 所选运单不能预约（details.shipments）
PICKUP_STATE_CONFLICT = 16133   # 409 当前状态不能取消 / 作废（details {pickup_id, status, reason}）

UNRESOLVED_STATUSES = CarrierPickup.UNRESOLVED_STATUSES     # pending / unknown
MAX_DAYS_AHEAD = 30             # 揽收日最多填到 30 天后（承运商实际只开放最近几个营业日，由承运商判断）
MAX_SHIPMENTS = 100
MAX_REMARKS = 100               # 留言列的长度；各承运商的上限另见 CarrierAdapter.pickup_max_remarks
MAX_EMAIL = 80
HISTORY_DAYS = 14               # 列表里显示揽收日在这么多天以内的预约（结果不明的一律显示）
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _maybe_booked(name) -> str:
    return (f" The pickup may have been scheduled on the {name} side: check the {name} confirmation email or ask "
            f"{name}, then dismiss the record (POST /warehouse/dn/carrier-pickups/<id>/dismiss) before "
            "scheduling the shipments again.")


def _maybe_cancelled(name) -> str:
    return (f" The pickup may or may not have been cancelled on the {name} side: retry the cancellation "
            "(POST /warehouse/dn/carrier-pickups/<id>/cancel).")


def _tz():
    return _document_timezone()


def local_today() -> date:
    return datetime.now(_tz()).date()


def _invalid(message, field):
    return BadRequestException(message, PICKUP_INVALID, field=field)


def parse_hhmm(value):
    """'HH:MM' / 'HH:MM:SS' → time；不合法返回 None"""
    text = str(value or '').strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _parse_id(value, field):
    """正整数 id（也认数字字符串）；不合法 400 16131"""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _invalid(f"{field} must be a positive integer", field)
    return value


class CarrierPickupService:

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @staticmethod
    def is_stale(record: CarrierPickup) -> bool:
        return (record is not None and record.status == 'pending' and record.created_at is not None
                and (datetime.now() - record.created_at).total_seconds() > stale_after_seconds())

    @staticmethod
    def effective_status(record: CarrierPickup):
        """卡住的 pending 按 unknown（reason stale）对外"""
        if CarrierPickupService.is_stale(record):
            return 'unknown', 'stale'
        return record.status, record.reason

    @staticmethod
    def holds(record: CarrierPickup, today: date = None) -> bool:
        """这条预约还占着所带的运单：pending / unknown / scheduled，且揽收日没过"""
        if record is None or record.status not in ('pending', 'unknown', 'scheduled'):
            return False
        return record.pickup_date >= (today or local_today())

    @staticmethod
    def can_cancel(record: CarrierPickup, today: date = None) -> bool:
        return (record is not None and record.status == 'scheduled' and bool(record.confirmation_code)
                and record.pickup_date >= (today or local_today()))

    @staticmethod
    def can_dismiss(record: CarrierPickup) -> bool:
        return record is not None and CarrierPickupService.effective_status(record)[0] == 'unknown'

    @staticmethod
    def payload(record: CarrierPickup, today: date = None) -> dict:
        today = today or local_today()
        data = record.to_dict()
        data['status'], data['reason'] = CarrierPickupService.effective_status(record)
        data['past'] = record.pickup_date < today
        data['holds_shipments'] = CarrierPickupService.holds(record, today)
        data['can_cancel'] = CarrierPickupService.can_cancel(record, today)
        data['can_dismiss'] = CarrierPickupService.can_dismiss(record)
        return data

    @staticmethod
    def summary(record: CarrierPickup, today: date = None):
        """运单上显示的揽收概要（GET carrier-shipment 的 shipment.pickup）；没有预约为 None"""
        if record is None:
            return None
        today = today or local_today()
        status, reason = CarrierPickupService.effective_status(record)
        return {
            'id': record.id,
            'status': status,
            'reason': reason,
            'pickup_date': record.pickup_date.isoformat() if record.pickup_date else None,
            'ready_time': record.ready_time,
            'close_time': record.close_time,
            'confirmation_code': record.confirmation_code,
            'past': record.pickup_date < today,
            'holds_shipments': CarrierPickupService.holds(record, today),
        }

    @staticmethod
    def summary_for_shipment(shipment: DNCarrierShipment):
        if shipment is None or not shipment.pickup_id:
            return None
        return CarrierPickupService.summary(db.session.get(CarrierPickup, shipment.pickup_id))

    # ------------------------------------------------------------------
    # 承运商、前置条件与揽收地点
    # ------------------------------------------------------------------

    @staticmethod
    def pickup_carriers(company_id) -> list:
        """公司里对接插件支持上门揽收的承运商（对接启用的在前，再按名称）"""
        rows = (
            Carrier.query
            .join(CarrierIntegration, CarrierIntegration.carrier_id == Carrier.id)
            .filter(Carrier.company_id == company_id)
            .all()
        )
        carriers = [c for c in rows
                    if adapter_class(c.integration.adapter) is not None
                    and adapter_class(c.integration.adapter).supports_pickup]
        return sorted(carriers, key=lambda c: (not c.integration.enabled, not c.is_active, (c.name or '').lower(), c.id))

    @staticmethod
    def _carrier(warehouse, carrier_id=None):
        """请求里的 carrier_id → 承运商（要是本公司、支持上门揽收的）；不带 → 第一个，没有为 None"""
        carriers = CarrierPickupService.pickup_carriers(warehouse.company_id)
        if carrier_id is None:
            return carriers[0] if carriers else None
        carrier_id = _parse_id(carrier_id, 'carrier_id')
        for carrier in carriers:
            if carrier.id == carrier_id:
                return carrier
        raise _invalid("carrier_id is not a carrier of this company that can book pickups", 'carrier_id')

    @staticmethod
    def _carrier_payload(carrier):
        if carrier is None:
            return None
        integration = carrier.integration
        return {'id': carrier.id, 'name': carrier.name, 'code': carrier.code,
                'adapter': integration.adapter if integration else None,
                'enabled': bool(integration and integration.enabled)}

    @staticmethod
    def _place(warehouse) -> dict:
        """揽收地点（原始资料）：地址 = 仓库英文地址（没有用公司的），联系人 = 仓库联系人（没有用公司出口联系人），
        电话 = 仓库电话（没有用公司的），公司名 = 公司英文名"""
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            text, postal = warehouse.address_en, warehouse.zip_code
            country = (warehouse.country_code or company.country_code or '').upper()
        else:
            text, postal = company.address_en, company.zip_code
            country = (company.country_code or '').upper()
        return {
            'company_name': company.legal_name_en,
            'person_name': warehouse.contact_name_en or company.export_contact_name,
            'phone': warehouse.phone or company.phone,
            'address_text': (text or '').strip() or None,
            'postal_code': postal,
            'country_code': country,
            'country_name': COUNTRY_NAMES_EN.get(country),
        }

    @staticmethod
    def _collect(warehouse, carrier, for_create=True) -> dict:
        """→ {carrier, snapshot, adapter, blockers, location}；location 是插件转好的揽收地点（有问题为 None）"""
        blockers, snapshot, adapter, location = [], None, None, None
        if carrier is None:
            blockers.append(_blocker('PICKUP_CARRIER_MISSING',
                                     "No carrier of this company has an online integration that can book pickups"))
        else:
            snapshot = CarrierIntegrationService.for_carrier(carrier)
            adapter = CarrierIntegrationService.build(snapshot)
            if snapshot is None:
                blockers.append(_blocker('CARRIER_NOT_INTEGRATED',
                                         f"Carrier {carrier.name} has no online integration", carrier_id=carrier.id))
            else:
                blockers.extend(CarrierIntegrationService.problems(snapshot, adapter, for_create=for_create))
                if adapter is not None and not adapter.supports_pickup:
                    blockers.append(_blocker('PICKUP_NOT_SUPPORTED',
                                             f"The {display_name(snapshot['adapter'])} integration cannot book pickups"))
        if adapter is not None and adapter.supports_pickup:
            place = CarrierPickupService._place(warehouse)
            if not place['address_text']:
                blockers.append(_blocker('PICKUP_ADDRESS_MISSING',
                                         "Neither the warehouse nor the company has an English address",
                                         field='address_en'))
            location, problems = adapter.pickup_location(place)
            blockers.extend(problems)
            if not place['address_text']:
                location = None
        return {'carrier': carrier, 'snapshot': snapshot, 'adapter': adapter, 'blockers': blockers,
                'location': location}

    @staticmethod
    def _name(ctx) -> str:
        if ctx.get('snapshot'):
            return display_name(ctx['snapshot']['adapter'])
        return ctx['carrier'].name if ctx.get('carrier') is not None else 'Carrier'

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def _carrier_filter(model, carrier, adapter_code):
        """属于这个承运商的运单 / 预约：carrier_id 相同；旧记录没有 carrier_id 时按插件代码"""
        return db.or_(model.carrier_id == carrier.id,
                      db.and_(model.carrier_id.is_(None), model.carrier == adapter_code))

    @staticmethod
    def _open_shipments(warehouse_id, carrier, adapter_code):
        """当前仓库、该承运商、active、DN 未发货的自动运单（新的在前）"""
        return (
            DNCarrierShipment.query
            .join(DN, DN.id == DNCarrierShipment.dn_id)
            .filter(DN.warehouse_id == warehouse_id,
                    CarrierPickupService._carrier_filter(DNCarrierShipment, carrier, adapter_code),
                    DNCarrierShipment.status == 'active',
                    DN.status.notin_(LOCKED_DN_STATUSES))
            .order_by(DNCarrierShipment.id.desc())
            .all()
        )

    @staticmethod
    def _weight_kg(dn: DN) -> Decimal:
        return sum((Decimal(str(p.gross_weight_kg or 0)) for p in dn.packages), Decimal(0))

    @staticmethod
    def _shipment_row(shipment: DNCarrierShipment, pickups: dict, today: date) -> dict:
        dn = shipment.dn
        task = CustomsService._delivery_task(dn)
        pickup = pickups.get(shipment.pickup_id) if shipment.pickup_id else None
        return {
            'shipment_id': shipment.id,
            'dn_id': dn.id,
            'order_number': dn.order_number,
            'delivery_task_id': task.id if task is not None else None,
            'tracking_number': shipment.tracking_number,
            'service_type': shipment.service_type,
            'service_name': _service_name(shipment.carrier, shipment.service_type),
            'ship_date': shipment.ship_date.isoformat() if shipment.ship_date else None,
            'package_count': shipment.package_count or len(dn.packages),
            'weight_kg': float(CarrierPickupService._weight_kg(dn)),
            'created_at': shipment.created_at.isoformat() if shipment.created_at else None,
            'pickup': CarrierPickupService.summary(pickup, today),
            'can_schedule': not CarrierPickupService.holds(pickup, today),
        }

    @staticmethod
    def overview(warehouse, carrier_id=None) -> dict:
        """GET /dn/carrier-pickups"""
        today = local_today()
        carrier = CarrierPickupService._carrier(warehouse, carrier_id)
        ctx = CarrierPickupService._collect(warehouse, carrier)
        integration = None
        shipments, recent = [], []
        if ctx['snapshot'] is not None:
            code = ctx['snapshot']['adapter']
            cls = adapter_class(code)
            integration = {'code': code, 'name': display_name(code),
                           'portal_name': cls.portal_name if cls else None,
                           'portal_url': cls.portal_url if cls else None}
            shipments = CarrierPickupService._open_shipments(warehouse.id, carrier, code)
            recent = (
                CarrierPickup.query
                .filter(CarrierPickup.warehouse_id == warehouse.id,
                        CarrierPickupService._carrier_filter(CarrierPickup, carrier, code),
                        db.or_(CarrierPickup.pickup_date >= today - timedelta(days=HISTORY_DAYS),
                               CarrierPickup.status.in_(UNRESOLVED_STATUSES)))
                .order_by(CarrierPickup.pickup_date.desc(), CarrierPickup.id.desc())
                .limit(100)
                .all()
            )
        pickup_ids = {s.pickup_id for s in shipments if s.pickup_id}
        pickups = {p.id: p for p in recent}
        missing = pickup_ids - set(pickups)
        if missing:
            pickups.update({p.id: p for p in CarrierPickup.query.filter(CarrierPickup.id.in_(missing)).all()})
        rows = [CarrierPickupService._shipment_row(s, pickups, today) for s in shipments]
        integration_problems = {'PICKUP_CARRIER_MISSING', 'CARRIER_NOT_INTEGRATED', 'PICKUP_NOT_SUPPORTED',
                                'CARRIER_INTEGRATION_UNAVAILABLE', 'CARRIER_INTEGRATION_DISABLED',
                                'CARRIER_NOT_CONFIGURED', 'CARRIER_CONFIG_INVALID'}
        adapter = ctx['adapter']
        return {
            # 承运商的对接可以预约揽收（启用、设置齐全）；能不能约另看 can_create / blockers
            'enabled': not any(b['code'] in integration_problems for b in ctx['blockers']),
            'carrier': CarrierPickupService._carrier_payload(carrier),
            'carriers': [CarrierPickupService._carrier_payload(c)
                         for c in CarrierPickupService.pickup_carriers(warehouse.company_id)],
            'integration': integration,
            'warehouse_id': warehouse.id,
            'today': today.isoformat(),
            'timezone': str(_tz()),
            'can_create': not ctx['blockers'] and any(r['can_schedule'] for r in rows),
            'blockers': ctx['blockers'],
            'pickup_location': ctx['location'],
            'max_remarks': min(adapter.pickup_max_remarks, MAX_REMARKS) if adapter is not None else MAX_REMARKS,
            'shipments': rows,
            'pickups': [CarrierPickupService.payload(p, today) for p in recent],
        }

    @staticmethod
    def _blocked(blockers, name='Carrier'):
        return ConflictException(f"{name} pickup cannot be scheduled; resolve the listed blockers first.",
                                 PICKUP_BLOCKED, details={'blockers': blockers})

    @staticmethod
    def availability(warehouse, carrier_id=None) -> dict:
        """GET /dn/carrier-pickups/availability：承运商返回的可约日期与时间（只查询，不预约）"""
        carrier = CarrierPickupService._carrier(warehouse, carrier_id)
        ctx = CarrierPickupService._collect(warehouse, carrier)
        if ctx['blockers']:
            raise CarrierPickupService._blocked(ctx['blockers'], CarrierPickupService._name(ctx))
        deadline = clock() + create_budget_seconds()
        try:
            result = ctx['adapter'].pickup_availability(ctx['location'], deadline=deadline)
        except CarrierError as exc:
            raise _carrier_api_exception(exc, 'pickup_availability', ctx['snapshot']['adapter'])
        result.update({'today': local_today().isoformat(), 'timezone': str(_tz())})
        return result

    # ------------------------------------------------------------------
    # 预约
    # ------------------------------------------------------------------

    @staticmethod
    def parse_request(data) -> dict:
        """POST 请求体 → {carrier_id, shipment_ids, pickup_date, ready, close, remarks, notification_email}；
        不合法 400 16131"""
        if not isinstance(data, dict):
            raise _invalid("Request body must be a JSON object", None)
        carrier_id = data.get('carrier_id')
        if carrier_id is not None:
            carrier_id = _parse_id(carrier_id, 'carrier_id')
        raw_ids = data.get('shipment_ids')
        if not isinstance(raw_ids, list) or not raw_ids:
            raise _invalid("shipment_ids must be a non-empty list of carrier shipment ids", 'shipment_ids')
        ids = []
        for value in raw_ids:
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise _invalid("shipment_ids must be positive integers", 'shipment_ids')
            if value not in ids:
                ids.append(value)
        if len(ids) > MAX_SHIPMENTS:
            raise _invalid(f"At most {MAX_SHIPMENTS} shipments per pickup", 'shipment_ids')

        today = local_today()
        try:
            pickup_date = date.fromisoformat(str(data.get('pickup_date') or '').strip())
        except ValueError:
            raise _invalid("pickup_date must be YYYY-MM-DD", 'pickup_date')
        if pickup_date < today:
            raise _invalid(f"pickup_date is in the past (today: {today.isoformat()})", 'pickup_date')
        if pickup_date > today + timedelta(days=MAX_DAYS_AHEAD):
            raise _invalid(f"pickup_date must be within {MAX_DAYS_AHEAD} days", 'pickup_date')

        ready = parse_hhmm(data.get('ready_time'))
        if ready is None:
            raise _invalid("ready_time must be HH:MM", 'ready_time')
        close = parse_hhmm(data.get('close_time'))
        if close is None:
            raise _invalid("close_time must be HH:MM", 'close_time')
        if close <= ready:
            raise _invalid("close_time must be later than ready_time", 'close_time')

        remarks = data.get('remarks')
        if remarks is not None and not isinstance(remarks, str):
            raise _invalid("remarks must be a string", 'remarks')
        remarks = ' '.join((remarks or '').split()) or None
        if remarks and len(remarks) > MAX_REMARKS:
            raise _invalid(f"remarks must be at most {MAX_REMARKS} characters", 'remarks')

        email = data.get('notification_email')
        if email is not None and not isinstance(email, str):
            raise _invalid("notification_email must be a string", 'notification_email')
        email = (email or '').strip() or None
        if email and (len(email) > MAX_EMAIL or not _EMAIL_RE.match(email)):
            raise _invalid("notification_email is not a valid email address", 'notification_email')
        return {'carrier_id': carrier_id, 'shipment_ids': ids, 'pickup_date': pickup_date, 'ready': ready,
                'close': close, 'remarks': remarks, 'notification_email': email}

    @staticmethod
    def create(warehouse, user_id, data) -> dict:
        """POST /dn/carrier-pickups（三段，见模块说明）。各段自己提交，不能在外层事务里调用。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("create pickup must not run inside another transaction")
        request = CarrierPickupService.parse_request(data)
        deadline = clock() + create_budget_seconds()
        plan = CarrierPickupService._reserve(warehouse.id, user_id, request)
        state = {'sent': False, 'finalized': False}
        try:
            return CarrierPickupService._request_and_save(plan, deadline, state)
        except BaseException as exc:
            # worker 超时被杀（gunicorn 抛 SystemExit）等：尽力落 unknown / failed；落不了就等 stale
            if not isinstance(exc, Exception) and not state['finalized']:
                try:
                    db.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                CarrierPickupService._finish(plan['record_id'], 'unknown' if state['sent'] else 'failed', state,
                                             reason='interrupted')
            raise

    @staticmethod
    @transactional
    def _reserve(warehouse_id, user_id, request: dict) -> dict:
        """第 1 段：锁所选运单行、检查、插 pending 记录并占住运单，随本事务提交。"""
        from warehouse.warehouse.models import Warehouse

        warehouse = db.session.get(Warehouse, warehouse_id)
        carrier = CarrierPickupService._carrier(warehouse, request['carrier_id'])
        ctx = CarrierPickupService._collect(warehouse, carrier)
        if ctx['blockers']:
            raise CarrierPickupService._blocked(ctx['blockers'], CarrierPickupService._name(ctx))
        adapter = ctx['adapter']
        if request['remarks'] and len(request['remarks']) > adapter.pickup_max_remarks:
            raise _invalid(f"remarks must be at most {adapter.pickup_max_remarks} characters", 'remarks')

        today = local_today()
        ids = request['shipment_ids']
        rows = (
            DNCarrierShipment.query
            .options(lazyload('*'))
            .filter(DNCarrierShipment.id.in_(ids))
            .order_by(DNCarrierShipment.id)
            .with_for_update()
            .all()
        )
        found = {s.id: s for s in rows}
        problems, shipments = [], []
        for shipment_id in ids:
            shipment = found.get(shipment_id)
            dn = db.session.get(DN, shipment.dn_id) if shipment is not None else None
            if shipment is None or dn is None or dn.warehouse_id != warehouse_id:
                problems.append({'shipment_id': shipment_id, 'tracking_number': None, 'reason': 'NOT_FOUND'})
                continue
            item = {'shipment_id': shipment_id, 'tracking_number': shipment.tracking_number}
            holder = db.session.get(CarrierPickup, shipment.pickup_id) if shipment.pickup_id else None
            if not CarrierShipmentService.is_shipment_carrier(shipment, carrier):
                problems.append({**item, 'reason': 'OTHER_CARRIER', 'carrier': shipment.carrier})
            elif shipment.status != 'active':
                problems.append({**item, 'reason': 'NOT_ACTIVE', 'status': shipment.status})
            elif dn.status in LOCKED_DN_STATUSES:
                problems.append({**item, 'reason': 'DN_SHIPPED', 'dn_status': dn.status})
            elif CarrierPickupService.holds(holder, today):
                problems.append({**item, 'reason': 'ALREADY_SCHEDULED',
                                 'pickup': CarrierPickupService.summary(holder, today)})
            else:
                shipments.append((shipment, dn))
        if problems:
            raise ConflictException(
                "Some of the selected shipments cannot be included in a pickup; reload the list and try again.",
                PICKUP_SHIPMENTS_INVALID, details={'shipments': problems})

        package_count = sum(s.package_count or len(dn.packages) for s, dn in shipments)
        weights = {s.id: CarrierPickupService._weight_kg(dn) for s, dn in shipments}
        total_weight = sum(weights.values(), Decimal(0))
        location = ctx['location']
        now = datetime.now()
        record = CarrierPickup(
            warehouse_id=warehouse_id,
            carrier=ctx['snapshot']['adapter'],
            carrier_id=carrier.id,
            status='pending',
            pickup_date=request['pickup_date'],
            ready_time=request['ready'].strftime('%H:%M'),
            close_time=request['close'].strftime('%H:%M'),
            package_count=package_count,
            total_weight_kg=total_weight,
            shipments=[{
                'shipment_id': s.id, 'dn_id': dn.id, 'order_number': dn.order_number,
                'tracking_number': s.tracking_number, 'package_count': s.package_count or len(dn.packages),
                'weight_kg': float(weights[s.id]),
            } for s, dn in shipments],
            pickup_location=location,
            remarks=request['remarks'],
            notification_email=request['notification_email'],
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(record)
        db.session.flush()
        for shipment, _dn in shipments:
            shipment.pickup_id = record.id
        db.session.flush()
        return {
            'record_id': record.id,
            'snapshot': ctx['snapshot'],
            'location': location,
            'pickup': {
                'pickup_date': request['pickup_date'], 'ready': request['ready'], 'close': request['close'],
                'same_day': request['pickup_date'] == today, 'tz': _tz(),
                'package_count': package_count, 'total_weight_kg': total_weight,
                'remarks': request['remarks'], 'notification_email': request['notification_email'],
            },
        }

    @staticmethod
    def _request_and_save(plan: dict, deadline: float, state: dict) -> dict:
        """第 2 段（调承运商，不开事务）+ 第 3 段（写库）。任何出口都把记录落到最终状态。"""
        record_id = plan['record_id']
        code = plan['snapshot']['adapter']
        name = display_name(code)
        adapter = CarrierIntegrationService.build(plan['snapshot'])
        state['sent'] = True
        try:
            result = adapter.schedule_pickup(plan['location'], plan['pickup'], deadline=deadline)
        except CarrierError as exc:
            if exc.maybe_processed:
                reason = _unclear_reason(exc)
                logger.error("pickup %s: %s create pickup result unclear (%s, transactionId %s)",
                             record_id, name, reason, exc.transaction_id)
                pickup = CarrierPickupService._finish(record_id, 'unknown', state, reason=reason,
                                                      error_message=exc.message, transaction_id=exc.transaction_id)
            else:
                if exc.budget_exhausted:
                    reason = 'budget_exhausted'
                elif exc.step == STEP_PICKUP and exc.status_code:
                    reason = 'rejected'
                else:
                    reason = 'not_sent'
                pickup = CarrierPickupService._finish(record_id, 'failed', state, reason=reason,
                                                      error_message=exc.message, transaction_id=exc.transaction_id)
            error = _carrier_api_exception(exc, 'pickup', code)
            if exc.maybe_processed:
                error.message += _maybe_booked(name)
            error.details['pickup'] = pickup
            raise error

        pickup = CarrierPickupService._finish(
            record_id, 'scheduled', state, confirmation_code=str(result['confirmation_code'])[:40],
            location_code=(result.get('location_code') or '')[:20] or None,
            transaction_id=result.get('transaction_id'))
        if pickup is not None and pickup['status'] != 'scheduled':
            # 确认号没能写进库：承运商那边已约上，记录停在 pending → 过期后按结果不明处理
            logger.error("pickup %s: %s scheduled it (confirmation %s, location %s) but saving failed",
                         record_id, name, result['confirmation_code'], result.get('location_code'))
        return {'pickup': pickup, 'alerts': result.get('alerts') or [], 'message': result.get('message')}

    @staticmethod
    def _finish(record_id, status: str, state=None, **fields):
        """把预约记录从 pending 落到最终状态（独立事务提交）。返回记录的 payload。
        提交失败只记日志：记录停在 pending，过期后按结果不明（stale）处理。"""
        try:
            payload = CarrierPickupService._finish_tx(record_id, status, fields)
        except Exception:  # noqa: BLE001
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("carrier pickup %s could not be marked %s (%s)", record_id, status, fields.get('reason'))
            return {'id': record_id, 'status': 'pending', 'reason': fields.get('reason'),
                    'confirmation_code': fields.get('confirmation_code'),
                    'transaction_id': fields.get('transaction_id')}
        if state is not None:
            state['finalized'] = True
        return payload

    @staticmethod
    @transactional
    def _finish_tx(record_id, status: str, fields: dict):
        record = db.session.get(CarrierPickup, record_id)
        if record is None:
            return None
        if record.status != 'pending':
            logger.warning("carrier pickup %s is %s, not pending; not changing it to %s",
                           record_id, record.status, status)
            return CarrierPickupService.payload(record)
        record.status = status
        for key, value in fields.items():
            if value is None:
                continue
            if key == 'error_message':
                value = str(value)[:ERROR_MESSAGE_MAX]
            setattr(record, key, value)
        record.updated_at = datetime.now()
        if status == 'failed':
            CarrierPickupService._release(record)
        db.session.flush()
        return CarrierPickupService.payload(record)

    @staticmethod
    def _release(record: CarrierPickup):
        """放开这条预约占着的运单（之后可以重新预约）"""
        DNCarrierShipment.query.filter(DNCarrierShipment.pickup_id == record.id).update(
            {DNCarrierShipment.pickup_id: None}, synchronize_session='fetch')

    # ------------------------------------------------------------------
    # 取消 / 确认作废
    # ------------------------------------------------------------------

    @staticmethod
    def _state_conflict(record: CarrierPickup, message: str) -> ConflictException:
        status, reason = CarrierPickupService.effective_status(record)
        return ConflictException(message, PICKUP_STATE_CONFLICT,
                                 details={'pickup_id': record.id, 'status': status, 'reason': reason,
                                          'pickup_date': record.pickup_date.isoformat()})

    @staticmethod
    def cancel(record: CarrierPickup, user_id) -> dict:
        """POST /dn/carrier-pickups/<id>/cancel：检查（锁记录）→ 调承运商（不开事务）→ 锁记录写结果"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("cancel pickup must not run inside another transaction")
        plan = CarrierPickupService._prepare_cancel(record.id)
        code = plan['snapshot']['adapter']
        name = display_name(code)
        adapter = CarrierIntegrationService.build(plan['snapshot'])
        deadline = clock() + create_budget_seconds()
        try:
            result = adapter.cancel_pickup(plan['pickup'], deadline=deadline)
        except CarrierError as exc:
            reason = _unclear_reason(exc) if exc.maybe_processed else None
            pickup = CarrierPickupService._save_cancel(
                record.id, user_id, 'scheduled', reason='cancel_unclear' if exc.maybe_processed else 'cancel_refused',
                error_message=f"{reason + ': ' if reason else ''}{exc.message}",
                cancel_transaction_id=exc.transaction_id)
            error = _carrier_api_exception(exc, 'pickup_cancel', code)
            if exc.maybe_processed:
                error.message += _maybe_cancelled(name)
            error.details['pickup'] = pickup
            raise error
        pickup = CarrierPickupService._save_cancel(record.id, user_id, 'cancelled', reason=result.get('reason'),
                                                   cancel_transaction_id=result.get('transaction_id'))
        return {'pickup': pickup, 'alerts': result.get('alerts') or [], 'message': result.get('message')}

    @staticmethod
    def _cancel_snapshot(record: CarrierPickup):
        """取消要用的对接设置快照：预约时的承运商（carrier_id）的对接；旧记录没有 carrier_id 时用本公司插件代码相同的
        承运商。只要求设置齐全，停用了也能取消。没有 → 409 16130（details.blockers）。"""
        from warehouse.warehouse.models import Warehouse

        carrier = db.session.get(Carrier, record.carrier_id) if record.carrier_id is not None else None
        if carrier is None and record.carrier_id is None:
            warehouse = db.session.get(Warehouse, record.warehouse_id)
            carrier = next((c for c in CarrierPickupService.pickup_carriers(warehouse.company_id)
                            if c.integration.adapter == record.carrier), None)
        snapshot = CarrierIntegrationService.for_carrier(carrier)
        name = display_name(record.carrier)
        if snapshot is None or snapshot['adapter'] != record.carrier:
            blockers = [_blocker('CARRIER_NOT_INTEGRATED',
                                 f"The {name} integration of the carrier that booked this pickup is not set up any more",
                                 carrier_id=carrier.id if carrier is not None else None)]
        else:
            blockers = CarrierIntegrationService.problems(
                snapshot, CarrierIntegrationService.build(snapshot), for_create=False)
        if blockers:
            raise ConflictException(f"{name} is not configured; the pickup cannot be cancelled from WMS.",
                                    PICKUP_BLOCKED, details={'blockers': blockers})
        return snapshot

    @staticmethod
    @transactional
    def _prepare_cancel(record_id) -> dict:
        record = lock_row(db.session.get(CarrierPickup, record_id))
        if not CarrierPickupService.can_cancel(record):
            raise CarrierPickupService._state_conflict(
                record, "Only a scheduled pickup whose date has not passed can be cancelled.")
        return {
            'snapshot': CarrierPickupService._cancel_snapshot(record),
            'pickup': {'confirmation_code': record.confirmation_code, 'location_code': record.location_code,
                       'pickup_date': record.pickup_date},
        }

    @staticmethod
    @transactional
    def _save_cancel(record_id, user_id, status: str, reason=None, error_message=None, cancel_transaction_id=None):
        """取消结果落库：只改仍是 scheduled 的记录（并发的另一次取消已落定的不覆盖）"""
        record = lock_row(db.session.get(CarrierPickup, record_id))
        if record.status == 'scheduled':
            now = datetime.now()
            record.status = status
            record.reason = reason
            record.error_message = str(error_message)[:ERROR_MESSAGE_MAX] if error_message else None
            record.cancel_transaction_id = cancel_transaction_id or record.cancel_transaction_id
            record.updated_at = now
            if status == 'cancelled':
                record.cancelled_at = now
                record.cancelled_by = user_id
                CarrierPickupService._release(record)
            db.session.flush()
        return CarrierPickupService.payload(record)

    @staticmethod
    @transactional
    def dismiss(record: CarrierPickup, user_id, confirm) -> dict:
        """POST /dn/carrier-pickups/<id>/dismiss {confirm: true}：结果不明的预约经操作员核对后作废，放开运单"""
        if confirm is not True:
            raise _invalid("confirm must be true (the operator checked with the carrier)", 'confirm')
        record = lock_row(record)
        if not CarrierPickupService.can_dismiss(record):
            raise CarrierPickupService._state_conflict(
                record, "Only a pickup request whose result is unclear can be dismissed.")
        now = datetime.now()
        record.status = 'dismissed'
        record.dismissed_at = now
        record.dismissed_by = user_id
        record.updated_at = now
        CarrierPickupService._release(record)
        db.session.flush()
        return {'pickup': CarrierPickupService.payload(record)}
