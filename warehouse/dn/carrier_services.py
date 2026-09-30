"""海外 DN 在承运商系统自动建运单（目前只有 FedEx Ship API）。

替代「在承运商网站手工建运单 + 保存运单号」这一步，其余流程不变：
- 前置条件（blockers）全部满足才建单：FedEx 已配置、海外 DN、packed、发货任务的承运商是 fedex、
  没有有效运单也没有手工存过运单号、有箱子（≤ 30）、报关没有错误级问题、发件人 / 收件人地址放得进 FedEx 格式。
- 建单前没有当前有效的 CI / PL（或已过期）就先用现有逻辑签发；FEDEX_ETD_ENABLED 时先把 CI 上传给 FedEx。
- 先调 FedEx，成功后才在一个事务里写库：主运单号走现有保存运单号的逻辑存到发货任务、CI / PL 带 AWB 升版本、
  面单存 dn_documents（shipping_label）、记 dn_carrier_shipments。FedEx 失败不写任何东西；
  写库失败则尝试取消刚建的运单并记日志。
- 取消：DN 未发货才可；FedEx 取消成功后记录标 cancelled、发货任务上的运单号清掉、面单作废、
  CI / PL 去掉 AWB 重新签发（条件不满足则作废）。
"""
import hashlib
import logging
import re
from datetime import date, datetime

from flask import current_app

from extensions.db import db
from extensions.error import (
    BadGatewayException, ConflictException, GatewayTimeoutException,
)
from extensions.transaction import transactional
from warehouse.common.countries import COUNTRY_NAMES_EN
from . import fedex_client
from .customs_services import CustomsService, DOC_CI, DOC_TYPES, _document_timezone, LOCKED_DN_STATUSES
from .fedex_client import FedexError
from .fedex_shipment import (
    AddressError, MAX_PACKAGES_PER_REQUEST, SUPPORTED_DUTIES_PAYMENT_TYPES, SUPPORTED_LABEL_IMAGE_TYPES,
    SHIPPER_TIN_TYPE, allocate_commodity_weights, build_ship_request, consignee_address, merge_labels,
    parse_ship_response, split_address_text, split_evenly, tin_type_for,
)
from .models import DN, DNCarrierShipment, DNDocument

logger = logging.getLogger(__name__)

CARRIER_FEDEX = 'fedex'
DOC_LABEL = 'shipping_label'
COMPLETED_TASK_STATUSES = ('completed', 'signed')
# 报关视图里已由 blockers 自己表达的问题码（不重复列）
_COVERED_PROBLEMS = ('NOT_PACKED', 'PACKAGES_MISSING')
_FILE_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


def _blocker(code, message, **extra):
    item = {'code': code, 'message': message}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _settings() -> dict:
    cfg = current_app.config
    return {
        'service_type': cfg.get('FEDEX_SERVICE_TYPE') or 'INTERNATIONAL_ECONOMY',
        'pickup_type': cfg.get('FEDEX_PICKUP_TYPE') or 'USE_SCHEDULED_PICKUP',
        'label_image_type': (cfg.get('FEDEX_LABEL_IMAGE_TYPE') or 'PDF').upper(),
        'label_stock_type': cfg.get('FEDEX_LABEL_STOCK_TYPE') or 'PAPER_4X6',
        'duties_payment_type': (cfg.get('FEDEX_DUTIES_PAYMENT_TYPE') or 'RECIPIENT').upper(),
        'etd_enabled': bool(cfg.get('FEDEX_ETD_ENABLED')),
    }


def _carrier_code(carrier) -> str:
    return ((getattr(carrier, 'code', None) or '')).strip().lower()


def _fedex_api_exception(err: FedexError, action: str):
    """FedexError → 502 16073 / 504 16074；details 带 FedEx errors 与 transactionId（不带凭证）"""
    details = {
        'carrier': CARRIER_FEDEX,
        'action': action,
        'http_status': err.status_code,
        'transaction_id': err.transaction_id,
        'errors': err.errors,
    }
    if err.timeout:
        details['maybe_processed'] = err.maybe_processed
        message = f"FedEx did not respond in time ({action})."
        if err.maybe_processed and action == 'create':
            message += (" The shipment may have been created on the FedEx side; check FedEx Ship Manager "
                        "before trying again to avoid a duplicate shipment.")
        return GatewayTimeoutException(message, 16074, details=details)
    if err.permission_denied:
        details['permission_denied'] = True
        message = (f"FedEx refused the request ({action}, HTTP {err.status_code}): check the API credentials "
                   f"and that the FedEx project has the required API enabled. {err.message}")
    else:
        message = f"FedEx rejected the request ({action}): {err.message}"
    return BadGatewayException(message, 16073, details=details)


class CarrierShipmentService:

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def enabled() -> bool:
        return fedex_client.fedex_configured()

    @staticmethod
    def active_shipment(dn: DN):
        return (
            DNCarrierShipment.query
            .filter(DNCarrierShipment.dn_id == dn.id, DNCarrierShipment.status == 'active')
            .order_by(DNCarrierShipment.id.desc())
            .first()
        )

    @staticmethod
    def latest_shipment(dn: DN):
        return CarrierShipmentService.active_shipment(dn) or (
            DNCarrierShipment.query
            .filter(DNCarrierShipment.dn_id == dn.id)
            .order_by(DNCarrierShipment.id.desc())
            .first()
        )

    @staticmethod
    def has_active_shipment(dn: DN) -> bool:
        return CarrierShipmentService.active_shipment(dn) is not None

    # ------------------------------------------------------------------
    # 前置条件与建单数据
    # ------------------------------------------------------------------

    @staticmethod
    def _shipper(dn: DN, exporter: dict) -> dict:
        """发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）+ 发货地址 = 仓库英文地址（没有用公司的）"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            text, postal = warehouse.address_en, warehouse.zip_code
            country = (warehouse.country_code or company.country_code or '').upper()
        else:
            text, postal = company.address_en, company.zip_code
            country = (company.country_code or '').upper()
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
        ctx = {'blockers': blockers, 'settings': settings, 'view': None, 'task': None,
               'shipper': None, 'recipient': None}

        if not CarrierShipmentService.enabled():
            blockers.append(_blocker('FEDEX_NOT_CONFIGURED',
                                     "FedEx is not configured (FEDEX_API_KEY / FEDEX_SECRET_KEY / FEDEX_ACCOUNT_NUMBER)"))
        if settings['label_image_type'] not in SUPPORTED_LABEL_IMAGE_TYPES:
            blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                     f"FEDEX_LABEL_IMAGE_TYPE must be one of {', '.join(SUPPORTED_LABEL_IMAGE_TYPES)}"))
        if settings['duties_payment_type'] not in SUPPORTED_DUTIES_PAYMENT_TYPES:
            blockers.append(_blocker('FEDEX_CONFIG_INVALID',
                                     "FEDEX_DUTIES_PAYMENT_TYPE must be one of "
                                     f"{', '.join(SUPPORTED_DUTIES_PAYMENT_TYPES)}"))

        customs = dn.customs
        if customs is None:
            blockers.append(_blocker('NOT_EXPORT', "DN is not an export shipment (no customs data)"))
            return ctx

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

        # FedEx 拒绝申告价额高于报关货值（TOTALCARRIAGEVALUE.EXCEEDS.CUSTOMSVALUE，sandbox 实测）
        goods_value = view['totals']['goods_value'] or 0
        if customs.declared_value_carriage and customs.declared_value_carriage > goods_value:
            blockers.append(_blocker(
                'DECLARED_VALUE_EXCEEDS_CUSTOMS_VALUE',
                f"Declared value for carriage ({customs.declared_value_carriage}) exceeds the customs value of the "
                f"packed goods ({goods_value}); FedEx does not accept this", field='declared_value_carriage'))

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
    def blockers(dn: DN) -> list:
        return CarrierShipmentService._collect(dn)['blockers']

    @staticmethod
    def status(dn: DN, ctx=None, alerts=None) -> dict:
        """GET /dn/<id>/carrier-shipment"""
        ctx = ctx or CarrierShipmentService._collect(dn)
        shipment = CarrierShipmentService.latest_shipment(dn)
        customs = dn.customs
        payload = {
            'enabled': CarrierShipmentService.enabled(),
            'carrier': CARRIER_FEDEX,
            'can_create': not ctx['blockers'],
            'blockers': ctx['blockers'],
            'etd_enabled': ctx['settings']['etd_enabled'],
            'declared_value_carriage': customs.declared_value_carriage if customs else None,
            'delivery_task_id': ctx['task'].id if ctx['task'] is not None else None,
            'shipment': shipment.to_dict() if shipment else None,
        }
        if alerts is not None:
            payload['alerts'] = alerts
        return payload

    # ------------------------------------------------------------------
    # 建单
    # ------------------------------------------------------------------

    @staticmethod
    def _ship_request(dn: DN, ctx: dict, etd_document_id=None) -> dict:
        view = ctx['view']
        customs = dn.customs
        packages = list(dn.packages)
        declared_total = customs.declared_value_carriage
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
        return build_ship_request(
            account_number=current_app.config.get('FEDEX_ACCOUNT_NUMBER'),
            settings=ctx['settings'],
            ship_date=datetime.now(_document_timezone()).date(),
            shipper=ctx['shipper'],
            recipient=ctx['recipient'],
            packages=package_items,
            commodities=commodities,
            invoice=invoice,
            reference=dn.order_number,
            etd_document_id=etd_document_id,
        )

    @staticmethod
    def create_shipment(dn: DN, user_id) -> dict:
        """POST /dn/<id>/carrier-shipment。写库失败时尝试取消刚建的 FedEx 运单（补偿）。"""
        outcome = {}
        try:
            return CarrierShipmentService._create(dn, user_id, outcome)
        except Exception:
            if outcome.get('tracking_number'):
                CarrierShipmentService._compensate(dn.id, outcome)
            raise

    @staticmethod
    def _compensate(dn_id, outcome):
        tracking = outcome['tracking_number']
        logger.error("DN %s: FedEx shipment %s was created (transactionId %s) but saving it in WMS failed; "
                     "cancelling it", dn_id, tracking, outcome.get('transaction_id'))
        try:
            fedex_client.cancel_shipment(tracking, outcome.get('sender_country'))
            logger.error("DN %s: FedEx shipment %s cancelled after the WMS failure", dn_id, tracking)
        except Exception as exc:  # noqa: BLE001 — 补偿失败只能记日志，原异常照常抛出
            logger.error("DN %s: cancelling FedEx shipment %s FAILED (%s); cancel it in FedEx Ship Manager",
                         dn_id, tracking, exc)

    @staticmethod
    @transactional
    def _create(dn: DN, user_id, outcome: dict) -> dict:
        from warehouse.delivery.services import DeliveryTaskService

        dn = CustomsService._lock(dn)
        ctx = CarrierShipmentService._collect(dn)
        if ctx['blockers']:
            raise ConflictException(
                "FedEx shipment cannot be created; resolve the listed blockers first.", 16072,
                details={'blockers': ctx['blockers']},
            )

        # CI / PL 必须先签发（没有或已过期 → 用现有逻辑签发；随本事务一起提交或回滚）
        current = CustomsService.current_documents(dn)
        if any(t not in current for t in DOC_TYPES) or ctx['view']['documents_outdated']:
            CustomsService.issue_documents(dn, user_id)
            current = CustomsService.current_documents(dn)

        settings = ctx['settings']
        sender_country = ctx['shipper']['address']['countryCode']
        recipient_country = ctx['recipient']['address']['countryCode']
        etd_document_id = None
        if settings['etd_enabled']:
            ci = current[DOC_CI]
            try:
                uploaded = fedex_client.upload_etd_document(ci.content, ci.file_name, sender_country,
                                                           recipient_country)
            except FedexError as exc:
                raise _fedex_api_exception(exc, 'etd_upload')
            etd_document_id = ((uploaded.get('output') or {}).get('meta') or {}).get('docId')
            if not etd_document_id:
                raise BadGatewayException("FedEx trade document upload returned no docId", 16073,
                                          details={'carrier': CARRIER_FEDEX, 'action': 'etd_upload',
                                                   'errors': [], 'transaction_id': None, 'http_status': None})

        request = CarrierShipmentService._ship_request(dn, ctx, etd_document_id)
        try:
            body = fedex_client.create_shipment(request)
        except FedexError as exc:
            if exc.timeout and exc.maybe_processed:
                logger.error("DN %s: FedEx create shipment timed out; the shipment may exist on the FedEx side", dn.id)
            raise _fedex_api_exception(exc, 'create')

        outcome['transaction_id'] = body.get('transactionId')
        outcome['sender_country'] = sender_country
        try:
            result = parse_ship_response(body)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.error("DN %s: unexpected FedEx ship response (transactionId %s): %s",
                         dn.id, body.get('transactionId'), exc)
            raise BadGatewayException(f"Unexpected FedEx response: {exc}", 16073, details={
                'carrier': CARRIER_FEDEX, 'action': 'create', 'errors': [],
                'transaction_id': body.get('transactionId'), 'http_status': 200})
        # 从这里起 FedEx 上已有运单：之后任何失败都会触发补偿取消
        outcome['tracking_number'] = result['tracking_number']

        label_pdf = merge_labels(result['labels'], settings['label_image_type'])
        tracking = result['tracking_number']

        # 运单号：复用现有保存运单号的逻辑；CI / PL 带 AWB 升版本
        task = ctx['task']
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

        label_doc = CarrierShipmentService._store_label(dn, tracking, ship_date, label_pdf, user_id)
        customs = dn.customs
        shipment = DNCarrierShipment(
            dn_id=dn.id,
            carrier=CARRIER_FEDEX,
            tracking_number=tracking,
            package_tracking_numbers=result['package_tracking_numbers'],
            service_type=result['service_type'] or settings['service_type'],
            status='active',
            ship_date=ship_date,
            package_count=len(dn.packages),
            net_charge=result['net_charge'],
            currency=result['currency'],
            declared_value=customs.declared_value_carriage or None,
            label_document_id=label_doc.id,
            etd_document_id=etd_document_id,
            transaction_id=result['transaction_id'],
            created_by=user_id,
        )
        db.session.add(shipment)
        db.session.flush()
        db.session.expire(dn, ['carrier_shipments', 'customs_documents'])
        return CarrierShipmentService.status(dn, alerts=result['alerts'])

    @staticmethod
    def _store_label(dn: DN, tracking: str, ship_date, content: bytes, user_id) -> DNDocument:
        max_version = (
            db.session.query(db.func.max(DNDocument.version))
            .filter(DNDocument.dn_id == dn.id, DNDocument.doc_type == DOC_LABEL)
            .scalar()
        )
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
            file_name=f"LABEL_{safe}.pdf",
            content=content,
            issued_at=datetime.now(),
            issued_by=user_id,
        )
        db.session.add(doc)
        db.session.flush()
        return doc

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
        if not CarrierShipmentService.enabled():
            raise ConflictException(
                "FedEx is not configured; the shipment cannot be cancelled from WMS.", 16072,
                details={'blockers': [_blocker('FEDEX_NOT_CONFIGURED', "FedEx is not configured")]},
            )

        sender_country = (dn.warehouse.country_code or dn.warehouse.company.country_code or '').upper()
        try:
            body = fedex_client.cancel_shipment(shipment.tracking_number, sender_country)
        except FedexError as exc:
            raise _fedex_api_exception(exc, 'cancel')
        output = body.get('output') or {}
        if output.get('cancelledShipment') is False:
            alerts = '; '.join(f"{a.get('code')}: {a.get('message')}" for a in output.get('alerts') or []
                               if isinstance(a, dict))
            raise BadGatewayException(
                f"FedEx did not cancel the shipment: {output.get('message') or alerts or 'no reason given'}", 16073,
                details={'carrier': CARRIER_FEDEX, 'action': 'cancel', 'http_status': 200,
                         'transaction_id': body.get('transactionId'), 'errors': [],
                         'alerts': output.get('alerts') or []},
            )

        try:
            now = datetime.now()
            shipment.status = 'cancelled'
            shipment.cancelled_at = now
            shipment.cancelled_by = user_id
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
            logger.error("DN %s: FedEx shipment %s was cancelled on FedEx but updating WMS failed",
                         dn.id, shipment.tracking_number)
            raise
