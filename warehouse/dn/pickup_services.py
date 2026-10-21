"""FedEx 上门揽收预约（Pickup Request API）。

自动建运单时 pickupType 是 CONTACT_FEDEX_TO_SCHEDULE（没有和 FedEx 签定期集荷），运单建好后要另约快递员上门。
揽收按「仓库 + 日期」约，一次带多张运单，件数 / 总重量按所带运单的箱子合计。
- 待揽收运单：当前仓库、FedEx、状态 active 的自动运单，DN 未发货。被「占着」的不能再约：pending / unknown 的预约，
  或已约上（scheduled）的预约，且揽收日还没过。揽收日过了、DN 还没发货的运单可以重新预约（快递员没来 / 忘了完成发货）。
- 前置条件（blockers）：FedEx 已配置、仓库所属公司在 FEDEX_ALLOWED_COMPANY_IDS 里、揽收地址（仓库英文地址，
  没有则公司的，与建运单的发件地址同一口径）放得进 FedEx 格式、联系电话 8~13 位数字。
- 预约分三段，调 FedEx 时不占行锁、不开事务（与建运单相同）：
  1. 锁所选运单行 → 逐张检查 → 插 pending 记录、运单 pickup_id 指向它，提交；
  2. 调 FedEx 预约（受 FEDEX_CREATE_BUDGET_SECONDS 约束）；
  3. 约上 → scheduled（确认号、站点代码）；明确拒绝 / 没发出去 → failed，运单 pickup_id 清空；
     结果不明（超时、5xx、响应看不懂）→ unknown，运单仍被占着（免得重复预约），操作员向 FedEx 核对后确认作废才释放。
- 取消：scheduled 且揽收日没过。FedEx 取消成功（或回「已取消」）→ cancelled，运单 pickup_id 清空；
  明确拒绝 → 保持 scheduled；结果不明 → 保持 scheduled（reason cancel_unclear），可重试取消。
- pending 超过 stale_after_seconds() 按 unknown（reason stale）对外。
- 日期 / 时间都是仓库当地的（DOCUMENT_TIMEZONE，默认 Asia/Tokyo，与运单的发货日同一口径）。
"""
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import current_app, g
from sqlalchemy.orm import lazyload

from extensions.db import db
from extensions.error import BadGatewayException, BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common.countries import COUNTRY_NAMES_EN
from warehouse.common.locks import lock_row
from . import fedex_client
from .carrier_services import (
    CARRIER_FEDEX, ERROR_MESSAGE_MAX, _already_cancelled, _blocker, _codes, _fedex_api_exception, _unclear_reason,
    allowed_company_ids, create_budget_seconds, stale_after_seconds,
)
from .customs_services import CustomsService, LOCKED_DN_STATUSES, _document_timezone
from .fedex_client import FedexError
from .fedex_pickup import (
    MAX_REMARKS, PickupRequestError, build_availability_request, build_cancel_request, build_create_request,
    parse_availability_response, parse_cancel_response, parse_create_response, parse_hhmm, pickup_contact,
)
from .fedex_shipment import MAX_EMAIL, AddressError, split_address_text
from .models import DN, CarrierPickup, DNCarrierShipment

logger = logging.getLogger(__name__)

PICKUP_BLOCKED = 16130          # 409 前置条件不满足（details.blockers）
PICKUP_INVALID = 16131          # 400 请求参数不合法（field）
PICKUP_SHIPMENTS_INVALID = 16132    # 409 所选运单不能预约（details.shipments）
PICKUP_STATE_CONFLICT = 16133   # 409 当前状态不能取消 / 作废（details {pickup_id, status, reason}）

UNRESOLVED_STATUSES = CarrierPickup.UNRESOLVED_STATUSES     # pending / unknown
MAX_DAYS_AHEAD = 30             # 揽收日最多填到 30 天后（FedEx 实际只开放最近几个营业日，由 FedEx 判断）
MAX_SHIPMENTS = 100
HISTORY_DAYS = 14               # 列表里显示揽收日在这么多天以内的预约（结果不明的一律显示）
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

_MAYBE_BOOKED = (" The pickup may have been scheduled on the FedEx side: check the FedEx confirmation email or ask "
                 "FedEx, then dismiss the record (POST /warehouse/dn/carrier-pickups/<id>/dismiss) before "
                 "scheduling the shipments again.")
_MAYBE_CANCELLED = (" The pickup may or may not have been cancelled on the FedEx side: retry the cancellation "
                    "(POST /warehouse/dn/carrier-pickups/<id>/cancel).")


def _tz():
    return _document_timezone()


def local_today() -> date:
    return datetime.now(_tz()).date()


def _invalid(message, field):
    return BadRequestException(message, PICKUP_INVALID, field=field)


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
    # 前置条件与揽收地点
    # ------------------------------------------------------------------

    @staticmethod
    def _location(warehouse, blockers: list):
        """揽收地点 {contact, address}：地址 = 仓库英文地址（没有用公司的），联系人 = 仓库联系人（没有用公司出口联系人），
        电话 = 仓库电话（没有用公司的），公司名 = 公司英文名。放不进 FedEx 格式的记进 blockers，返回 None。"""
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            text, postal = warehouse.address_en, warehouse.zip_code
            country = (warehouse.country_code or company.country_code or '').upper()
        else:
            text, postal = company.address_en, company.zip_code
            country = (company.country_code or '').upper()
        location = {'contact': None, 'address': None}
        if not (text or '').strip():
            blockers.append(_blocker('PICKUP_ADDRESS_MISSING',
                                     "Neither the warehouse nor the company has an English address",
                                     field='address_en'))
        else:
            try:
                location['address'] = split_address_text(text, country, postal, COUNTRY_NAMES_EN.get(country))
            except AddressError as exc:
                blockers.append(_blocker('PICKUP_ADDRESS_INVALID',
                                         f"Pickup address cannot be used for FedEx: {exc}", field='address_en'))
        try:
            location['contact'] = pickup_contact(company.legal_name_en,
                                                 warehouse.contact_name_en or company.export_contact_name,
                                                 warehouse.phone or company.phone)
        except PickupRequestError as exc:
            blockers.append(_blocker('PICKUP_PHONE_INVALID', f"Pickup contact phone: {exc}", field='phone'))
        if location['address'] is None or location['contact'] is None:
            return None
        return location

    @staticmethod
    def _collect(warehouse) -> dict:
        blockers = []
        if not fedex_client.fedex_configured():
            blockers.append(_blocker('FEDEX_NOT_CONFIGURED',
                                     "FedEx is not configured (FEDEX_API_KEY / FEDEX_SECRET_KEY / FEDEX_ACCOUNT_NUMBER)"))
        else:
            company_ids, invalid = allowed_company_ids()
            if invalid:
                blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                         "FEDEX_ALLOWED_COMPANY_IDS must be a comma-separated list of company IDs "
                                         f"(invalid: {', '.join(invalid)})"))
            if warehouse.company_id not in company_ids:
                blockers.append(_blocker('FEDEX_COMPANY_NOT_ALLOWED',
                                         "FedEx is not enabled for this company (FEDEX_ALLOWED_COMPANY_IDS)"))
        location = CarrierPickupService._location(warehouse, blockers)
        return {'blockers': blockers, 'location': location}

    @staticmethod
    def enabled(warehouse) -> bool:
        return fedex_client.fedex_configured() and warehouse.company_id in allowed_company_ids()[0]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def _open_shipments(warehouse_id):
        """当前仓库、FedEx、active、DN 未发货的自动运单（新的在前）"""
        return (
            DNCarrierShipment.query
            .join(DN, DN.id == DNCarrierShipment.dn_id)
            .filter(DN.warehouse_id == warehouse_id,
                    DNCarrierShipment.carrier == CARRIER_FEDEX,
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
            'ship_date': shipment.ship_date.isoformat() if shipment.ship_date else None,
            'package_count': shipment.package_count or len(dn.packages),
            'weight_kg': float(CarrierPickupService._weight_kg(dn)),
            'created_at': shipment.created_at.isoformat() if shipment.created_at else None,
            'pickup': CarrierPickupService.summary(pickup, today),
            'can_schedule': not CarrierPickupService.holds(pickup, today),
        }

    @staticmethod
    def overview(warehouse) -> dict:
        """GET /dn/carrier-pickups"""
        today = local_today()
        ctx = CarrierPickupService._collect(warehouse)
        shipments = CarrierPickupService._open_shipments(warehouse.id)
        pickup_ids = {s.pickup_id for s in shipments if s.pickup_id}
        recent = (
            CarrierPickup.query
            .filter(CarrierPickup.warehouse_id == warehouse.id,
                    db.or_(CarrierPickup.pickup_date >= today - timedelta(days=HISTORY_DAYS),
                           CarrierPickup.status.in_(UNRESOLVED_STATUSES)))
            .order_by(CarrierPickup.pickup_date.desc(), CarrierPickup.id.desc())
            .limit(100)
            .all()
        )
        pickups = {p.id: p for p in recent}
        missing = pickup_ids - set(pickups)
        if missing:
            pickups.update({p.id: p for p in CarrierPickup.query.filter(CarrierPickup.id.in_(missing)).all()})
        rows = [CarrierPickupService._shipment_row(s, pickups, today) for s in shipments]
        return {
            'enabled': CarrierPickupService.enabled(warehouse),
            'carrier': CARRIER_FEDEX,
            'warehouse_id': warehouse.id,
            'today': today.isoformat(),
            'timezone': str(_tz()),
            'can_create': not ctx['blockers'] and any(r['can_schedule'] for r in rows),
            'blockers': ctx['blockers'],
            'pickup_location': ctx['location'],
            'max_remarks': MAX_REMARKS,
            'shipments': rows,
            'pickups': [CarrierPickupService.payload(p, today) for p in recent],
        }

    @staticmethod
    def _blocked(blockers):
        return ConflictException("FedEx pickup cannot be scheduled; resolve the listed blockers first.",
                                 PICKUP_BLOCKED, details={'blockers': blockers})

    @staticmethod
    def availability(warehouse) -> dict:
        """GET /dn/carrier-pickups/availability：FedEx 返回的可约日期与时间（只查询，不预约）"""
        ctx = CarrierPickupService._collect(warehouse)
        if ctx['blockers']:
            raise CarrierPickupService._blocked(ctx['blockers'])
        deadline = fedex_client.clock() + create_budget_seconds()
        try:
            body = fedex_client.pickup_availability(build_availability_request(ctx['location']['address']),
                                                    deadline=deadline)
        except FedexError as exc:
            raise _fedex_api_exception(exc, 'pickup_availability')
        result = parse_availability_response(body)
        result.update({'today': local_today().isoformat(), 'timezone': str(_tz()),
                       'transaction_id': body.get('transactionId')})
        return result

    # ------------------------------------------------------------------
    # 预约
    # ------------------------------------------------------------------

    @staticmethod
    def parse_request(data) -> dict:
        """POST 请求体 → {shipment_ids, pickup_date, ready, close, remarks, notification_email}；不合法 400 16131"""
        if not isinstance(data, dict):
            raise _invalid("Request body must be a JSON object", None)
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
        return {'shipment_ids': ids, 'pickup_date': pickup_date, 'ready': ready, 'close': close,
                'remarks': remarks, 'notification_email': email}

    @staticmethod
    def create(warehouse, user_id, data) -> dict:
        """POST /dn/carrier-pickups（三段，见模块说明）。各段自己提交，不能在外层事务里调用。"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("create pickup must not run inside another transaction")
        request = CarrierPickupService.parse_request(data)
        deadline = fedex_client.clock() + create_budget_seconds()
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
        ctx = CarrierPickupService._collect(warehouse)
        if ctx['blockers']:
            raise CarrierPickupService._blocked(ctx['blockers'])

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
            if shipment.carrier != CARRIER_FEDEX or shipment.status != 'active':
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
            carrier=CARRIER_FEDEX,
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
        payload = build_create_request(
            account_number=current_app.config.get('FEDEX_ACCOUNT_NUMBER'),
            contact=location['contact'], address=location['address'],
            pickup_date=request['pickup_date'], ready=request['ready'], close=request['close'],
            same_day=request['pickup_date'] == today, tz=_tz(),
            package_count=package_count, total_weight=total_weight,
            remarks=request['remarks'], notification_email=request['notification_email'],
        )
        return {'record_id': record.id, 'request': payload}

    @staticmethod
    def _request_and_save(plan: dict, deadline: float, state: dict) -> dict:
        """第 2 段（调 FedEx，不开事务）+ 第 3 段（写库）。任何出口都把记录落到最终状态。"""
        record_id = plan['record_id']
        state['sent'] = True
        try:
            body = fedex_client.create_pickup(plan['request'], deadline=deadline)
        except FedexError as exc:
            if exc.maybe_processed:
                reason = _unclear_reason(exc)
                logger.error("pickup %s: FedEx create pickup result unclear (%s, transactionId %s)",
                             record_id, reason, exc.transaction_id)
                pickup = CarrierPickupService._finish(record_id, 'unknown', state, reason=reason,
                                                      error_message=exc.message, transaction_id=exc.transaction_id)
            else:
                if exc.budget_exhausted:
                    reason = 'budget_exhausted'
                elif exc.what == fedex_client.CREATE_PICKUP and exc.status_code:
                    reason = 'rejected'
                else:
                    reason = 'not_sent'
                pickup = CarrierPickupService._finish(record_id, 'failed', state, reason=reason,
                                                      error_message=exc.message, transaction_id=exc.transaction_id)
            error = _fedex_api_exception(exc, 'pickup')
            if exc.maybe_processed:
                error.message += _MAYBE_BOOKED
            error.details['pickup'] = pickup
            raise error

        transaction_id = body.get('transactionId')
        try:
            result = parse_create_response(body)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.error("pickup %s: unexpected FedEx create pickup response (transactionId %s): %s",
                         record_id, transaction_id, exc)
            pickup = CarrierPickupService._finish(record_id, 'unknown', state, reason='bad_response',
                                                  error_message=f"Unexpected response: {exc}",
                                                  transaction_id=transaction_id)
            raise BadGatewayException(
                f"Unexpected FedEx response: {exc}." + _MAYBE_BOOKED, 16073,
                details={'carrier': CARRIER_FEDEX, 'action': 'pickup', 'errors': [], 'http_status': 200,
                         'transaction_id': transaction_id, 'maybe_processed': True, 'pickup': pickup})

        pickup = CarrierPickupService._finish(
            record_id, 'scheduled', state, confirmation_code=result['confirmation_code'][:40],
            location_code=(result['location'] or '')[:20] or None, transaction_id=transaction_id)
        if pickup is not None and pickup['status'] != 'scheduled':
            # 确认号没能写进库：FedEx 上已约上，记录停在 pending → 过期后按结果不明处理
            logger.error("pickup %s: FedEx scheduled it (confirmation %s, location %s) but saving failed",
                         record_id, result['confirmation_code'], result['location'])
        return {'pickup': pickup, 'alerts': result['alerts'], 'message': result['message']}

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
        """POST /dn/carrier-pickups/<id>/cancel：检查（锁记录）→ 调 FedEx（不开事务）→ 锁记录写结果"""
        if getattr(g, 'transaction_depth', 0):
            raise RuntimeError("cancel pickup must not run inside another transaction")
        plan = CarrierPickupService._prepare_cancel(record.id)
        deadline = fedex_client.clock() + create_budget_seconds()
        try:
            body = fedex_client.cancel_pickup(plan['request'], deadline=deadline)
        except FedexError as exc:
            if not exc.maybe_processed and _already_cancelled(exc.errors):
                logger.warning("pickup %s: FedEx says it is already cancelled (codes %s)", record.id,
                               _codes(exc.errors))
                return {'pickup': CarrierPickupService._save_cancel(
                    record.id, user_id, 'cancelled', reason='already_cancelled',
                    cancel_transaction_id=exc.transaction_id), 'alerts': []}
            reason = _unclear_reason(exc) if exc.maybe_processed else None
            pickup = CarrierPickupService._save_cancel(
                record.id, user_id, 'scheduled', reason='cancel_unclear' if exc.maybe_processed else 'cancel_refused',
                error_message=f"{reason + ': ' if reason else ''}{exc.message}",
                cancel_transaction_id=exc.transaction_id)
            error = _fedex_api_exception(exc, 'pickup_cancel')
            if exc.maybe_processed:
                error.message += _MAYBE_CANCELLED
            error.details['pickup'] = pickup
            raise error
        transaction_id = body.get('transactionId')
        try:
            result = parse_cancel_response(body)
        except (ValueError, TypeError, AttributeError) as exc:
            pickup = CarrierPickupService._save_cancel(
                record.id, user_id, 'scheduled', reason='cancel_unclear',
                error_message=f"Unexpected response: {exc}", cancel_transaction_id=transaction_id)
            raise BadGatewayException(
                f"Unexpected FedEx response: {exc}." + _MAYBE_CANCELLED, 16073,
                details={'carrier': CARRIER_FEDEX, 'action': 'pickup_cancel', 'errors': [], 'http_status': 200,
                         'transaction_id': transaction_id, 'maybe_processed': True, 'pickup': pickup})
        pickup = CarrierPickupService._save_cancel(record.id, user_id, 'cancelled', reason=None,
                                                   cancel_transaction_id=transaction_id)
        return {'pickup': pickup, 'alerts': result['alerts'], 'message': result['message']}

    @staticmethod
    @transactional
    def _prepare_cancel(record_id) -> dict:
        record = lock_row(db.session.get(CarrierPickup, record_id))
        if not CarrierPickupService.can_cancel(record):
            raise CarrierPickupService._state_conflict(
                record, "Only a scheduled pickup whose date has not passed can be cancelled.")
        return {'request': build_cancel_request(
            account_number=current_app.config.get('FEDEX_ACCOUNT_NUMBER'),
            confirmation_code=record.confirmation_code, pickup_date=record.pickup_date,
            location=record.location_code)}

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
            raise _invalid("confirm must be true (the operator checked with FedEx)", 'confirm')
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
