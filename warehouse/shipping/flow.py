"""出库单流程汇总（GET /warehouse/dn/<id>/shipping-flow，doc/shipping-flow.md §5）：Web 和 PDA 都按它渲染，不各自推算状态。

一张出库单一条流程：下单 → 拣货 → 打包 →（要报关时）报关单证 → 运单 → 揽收 → 交承运商 → 运输中 → 签收。
每一步给 state、实际发生的时刻 at（带时区偏移）、来源 source、界面要的概要 summary 和能做的操作 actions；
规则都用已有的服务（建运单的前置条件 / 运单状态、揽收概要、跟踪结果、报关视图），这里不另起一套。

state：
- done：做完了；current：现在该做的（流程走到了这一步）；todo：还没到（前面的步骤没做完）
- warning：要人处理（运单结果不明、单证过期 / 有问题、跟踪异常、自动步骤被挡、揽收结果不明 / 揽收日已过……）
- blocked：流程走到了这一步，但前面的步骤有问题挡着（如要报关却没有报关数据时的运单步骤）
- skipped：不适用（不报关的件的 documents、pickup_method = none 的 pickup、没有跟踪的 transit、DN 取消后没做的步骤……）
source：warehouse（仓库操作）/ carrier（承运商跟踪自动）/ rule（自动视为签收）/ api（下单来自来源系统；运单步骤为
插件建的运单）/ system（WMS 自动）。
actions[].key 见 ACTION_KEYS；enabled = false 时带 reason（英文句子）和 reason_code（界面按它翻译：REASON_CODES，
或建运单 / 预约揽收的拦截码、报关问题码）。stage 与 DN 列表同一个 SQL 口径（stage.py）。
"""
from datetime import datetime

from warehouse.carrier.methods import methods_of, tracking_url as carrier_tracking_url
from warehouse.dn.carrier_services import CarrierShipmentService
from warehouse.dn.customs_services import DOC_TYPES, CustomsService
from warehouse.dn.models import CarrierPickup
from warehouse.dn.pickup_services import CarrierPickupService
from warehouse.dn.services import event_time
from warehouse.dn.tracking_services import local_naive

from extensions.db import db
from .auto_sign import auto_sign_at, tracked_by_api
from .common import current_delivery_task, is_locked, local_today
from .route import shipping_route
from .stage import stage_of
from .waybills import current_waybill

STEP_KEYS = ('ordered', 'picking', 'packing', 'documents', 'waybill', 'pickup', 'handover', 'transit', 'delivered')
STATES = ('done', 'current', 'todo', 'warning', 'blocked', 'skipped')
ACTION_KEYS = ('create_waybill', 'enter_tracking', 'cancel_waybill', 'dismiss_waybill', 'print_label', 'export_file',
               'import_numbers', 'book_pickup', 'record_pickup', 'cancel_pickup', 'complete_shipping',
               'refresh_tracking', 'open_tracking', 'sign', 'issue_documents', 'edit_packages')

# 操作不能做的原因码（actions[].reason_code）；另外也会用建运单 / 预约揽收的拦截码（blockers[].code）与报关问题码
REASON_CODES = ('DN_SHIPPED', 'NOT_PICKED', 'NOT_PACKED', 'WAYBILL_SUBMITTED', 'WAYBILL_UNRESOLVED', 'NO_OPEN_TASK',
                'API_WAYBILL_OPEN', 'CANNOT_CANCEL', 'REQUEST_IN_PROGRESS', 'WAYBILL_CANCELLED', 'NO_WAYBILL',
                'PICKUP_EXISTS', 'PICKUP_DATE_PASSED', 'WAYBILL_NOT_ACTIVE', 'NOT_HANDED_OVER', 'NOT_AVAILABLE')

PREPARING_STATUSES = ('pending', 'in_progress')
PICKED_OR_LATER = ('picked', 'packed', 'delivered', 'completed')
PACKED_OR_LATER = ('packed', 'delivered', 'completed')
SHIPPED = ('delivered', 'completed')
OPEN_TASK_STATUSES = ('pending', 'in_progress')


def _action(key, enabled=True, reason=None, code=None, **extra):
    """enabled = false 时带 reason（英文句子）与 reason_code（REASON_CODES / 拦截码 / 报关问题码）"""
    item = {'key': key, 'enabled': bool(enabled)}
    if not enabled:
        item['reason'] = reason or 'Not available now'
        item['reason_code'] = code or 'NOT_AVAILABLE'
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _step(key, state, at=None, source=None, summary=None, actions=None):
    """at：WMS 时间列（naive，服务器本地时间）或已经带偏移的 ISO 串"""
    return {'key': key, 'state': state, 'at': at if isinstance(at, str) else event_time(at), 'source': source,
            'summary': summary or {}, 'actions': actions or []}


def _problem(code, level, message, step, **extra):
    item = {'code': code, 'level': level, 'message': message, 'step': step}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _carrier_time(value):
    """承运商给的 UTC ISO 时刻 → 带服务器时区偏移的 ISO 串（与其它步骤的 at 一致）；解析不了为 None"""
    return event_time(local_naive(value)) if value else None


class ShippingFlowService:

    @staticmethod
    def build(dn) -> dict:
        task = current_delivery_task(dn)
        carrier = CarrierShipmentService.carrier_of(dn, task)
        route = shipping_route(dn)
        methods = methods_of(carrier)
        # 建运单的前置条件 / 插件运单的状态（GET carrier-shipment 同一份数据）
        ctx = CarrierShipmentService._collect(dn)
        shipment_status = CarrierShipmentService.status(dn, ctx=ctx)
        waybill = current_waybill(dn)
        tracking_number = (waybill.tracking_number if waybill is not None and waybill.tracking_number else None) \
            or ((task.tracking_number or None) if task is not None else None)
        integration = carrier.integration if carrier is not None else None
        adapter_code = waybill.carrier if waybill is not None and waybill.carrier else (
            integration.adapter if integration is not None else None)
        url = carrier_tracking_url(carrier, tracking_number, adapter_code) if methods['tracking'] != 'none' else None

        facts = {
            'dn': dn, 'task': task, 'carrier': carrier, 'route': route, 'methods': methods, 'ctx': ctx,
            'shipment_status': shipment_status, 'waybill': waybill, 'tracking_number': tracking_number,
            'tracking_url': url, 'closed': dn.status == 'closed', 'problems': [],
            'tracked': tracked_by_api(dn),
        }
        builders = (
            ShippingFlowService._ordered, ShippingFlowService._picking, ShippingFlowService._packing,
            ShippingFlowService._documents, ShippingFlowService._waybill, ShippingFlowService._pickup,
            ShippingFlowService._handover, ShippingFlowService._transit, ShippingFlowService._delivered,
        )
        steps = [builder(facts) for builder in builders]
        if facts['closed']:
            # DN 取消 / 关闭：没做完的步骤都不再适用，也没有要处理的问题了
            for step in steps:
                if step['state'] != 'done':
                    step['state'] = 'skipped'
                    step['actions'] = []
            facts['problems'] = []

        from warehouse.packing.models import PackingTask
        from warehouse.picking.models import PickingTask
        return {
            'dn_id': dn.id,
            'dn_status': dn.status,
            'order_number': dn.order_number,
            # 揽收按仓库约（界面带 X-WAREHOUSE-ID / warehouse_id）
            'warehouse_id': dn.warehouse_id,
            'route': route,
            'carrier': ShippingFlowService._carrier_payload(carrier, methods, url),
            'stage': stage_of(dn),
            'steps': steps,
            'problems': facts['problems'],
            'tasks': {
                'picking': ShippingFlowService._task_ids(PickingTask, dn),
                'packing': ShippingFlowService._task_ids(PackingTask, dn),
                'delivery': task.id if task is not None else None,
            },
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
            'methods': methods,
            'auto_sign_days': carrier.auto_sign_days,
            'tracking_url': url,
        }

    @staticmethod
    def _task_ids(model, dn) -> list:
        return [row[0] for row in db.session.query(model.id)
                .filter(model.dn_id == dn.id, model.is_active.is_(True)).order_by(model.id).all()]

    # ------------------------------------------------------------------
    # 下单 / 拣货 / 打包
    # ------------------------------------------------------------------

    @staticmethod
    def _ordered(f):
        dn = f['dn']
        return _step('ordered', 'done', dn.created_at, 'api' if dn.api_key_id else 'warehouse',
                     {'order_number': dn.order_number, 'dn_type': dn.dn_type,
                      'expected_shipping_date': (dn.expected_shipping_date.isoformat()
                                                 if dn.expected_shipping_date else None)})

    @staticmethod
    def _picking(f):
        from warehouse.picking.models import PickingTask
        dn = f['dn']
        summary = {
            'task_ids': ShippingFlowService._task_ids(PickingTask, dn),
            'picked': sum(d.picked_quantity or 0 for d in dn.details),
            'planned': sum(d.quantity or 0 for d in dn.details),
        }
        # 取消（closed）的 DN 按拣货完成时刻判断做没做完
        if dn.status in PICKED_OR_LATER or (dn.status == 'closed' and dn.picked_at is not None):
            return _step('picking', 'done', dn.picked_at, 'warehouse', summary)
        return _step('picking', 'current' if dn.status in PREPARING_STATUSES else 'todo', None, None, summary)

    @staticmethod
    def _packing(f):
        from warehouse.packing.models import PackingTask
        dn = f['dn']
        packages = CustomsService.packages_payload(dn)
        summary = {
            'task_ids': ShippingFlowService._task_ids(PackingTask, dn),
            'packed': sum(d.packed_quantity or 0 for d in dn.details),
            'planned': sum(d.quantity or 0 for d in dn.details),
            'package_count': len(packages),
            'gross_weight_kg': round(sum(p['gross_weight_kg'] or 0 for p in packages), 3),
            'packages': packages,
        }
        open_waybill = CarrierShipmentService.open_shipment(dn)
        if is_locked(dn):
            edit = _action('edit_packages', False, "The DN has been shipped; packages are locked", 'DN_SHIPPED')
        elif dn.status not in ('picked', 'packed'):
            edit = _action('edit_packages', False, "Packages can be entered once the DN is picked", 'NOT_PICKED')
        elif open_waybill is not None:
            edit = _action('edit_packages', False, "The packages were submitted with the carrier shipment; cancel "
                                                   "the shipment first", 'WAYBILL_SUBMITTED')
        else:
            edit = _action('edit_packages')
        actions = [edit]
        if dn.status in PACKED_OR_LATER or (dn.status == 'closed' and dn.packed_at is not None):
            state = 'done'
            if dn.status == 'packed' and not packages:
                state = 'warning'
                f['problems'].append(_problem('PACKAGES_MISSING', 'warning', "No packages recorded", 'packing'))
            return _step('packing', state, dn.packed_at, 'warehouse', summary, actions)
        return _step('packing', 'current' if dn.status == 'picked' else 'todo', None, None, summary, actions)

    # ------------------------------------------------------------------
    # 报关单证（要报关的件才有）
    # ------------------------------------------------------------------

    @staticmethod
    def _documents(f):
        dn, route = f['dn'], f['route']
        if not route['requires_customs']:
            return _step('documents', 'skipped', summary={'required': False})
        if dn.customs is None:
            f['problems'].append(_problem(
                'CUSTOMS_DATA_MISSING', 'error',
                f"International shipment ({route['origin_country']} → {route['destination_country']}) without "
                "customs data: the source system must send the customs snapshot", 'documents'))
            return _step('documents', 'warning' if dn.status not in SHIPPED else 'skipped',
                         summary={'required': True, 'customs': False})
        view = f['ctx']['view'] or CustomsService.build_view(dn)
        current = CustomsService.current_documents(dn)
        missing = [t for t in DOC_TYPES if t not in current]
        ci = current.get(DOC_TYPES[0])
        summary = {
            'required': True,
            'customs': True,
            'version': ci.version if ci is not None else None,
            'outdated': bool(view['documents_outdated']),
            'missing': missing,
            'ready': bool(view['ready']),
            'documents': view['current_documents'],
        }
        errors = [p for p in view['problems'] if p['level'] == 'error']
        for problem in view['problems']:
            # 打包前的 NOT_PACKED / PACKAGES_MISSING 是正常进度，不算问题；已发货（单证锁定）后报关问题也没法再处理了
            if is_locked(dn) or (dn.status != 'packed' and problem['code'] in ('NOT_PACKED', 'PACKAGES_MISSING')):
                continue
            f['problems'].append(_problem(problem['code'], problem['level'], problem['message'], 'documents',
                                          goods_code=problem.get('goods_code'), field=problem.get('field')))
        if is_locked(dn):
            action = _action('issue_documents', False, "The DN has been shipped; documents are locked", 'DN_SHIPPED')
        elif dn.status != 'packed':
            action = _action('issue_documents', False, "Documents can be issued once the DN is packed", 'NOT_PACKED')
        elif errors:
            action = _action('issue_documents', False, errors[0]['message'], errors[0]['code'])
        else:
            action = _action('issue_documents')
        at = ci.issued_at if ci is not None else None
        if not missing and not summary['outdated']:
            return _step('documents', 'done', at, 'warehouse', summary, [action])
        if dn.status in SHIPPED:
            # 发货时没出 / 已过期（完成发货不查单证）：只提示
            return _step('documents', 'warning', at, 'warehouse' if at else None, summary, [action])
        if dn.status != 'packed':
            return _step('documents', 'todo', at, None, summary, [action])
        state = 'warning' if (summary['outdated'] or errors) else 'current'
        return _step('documents', state, at, 'warehouse' if at else None, summary, [action])

    # ------------------------------------------------------------------
    # 运单
    # ------------------------------------------------------------------

    @staticmethod
    def _waybill(f):
        dn, task, carrier, methods = f['dn'], f['task'], f['carrier'], f['methods']
        status, record = f['shipment_status'], f['waybill']
        unresolved = status['unresolved']
        source = record.source if record is not None else ('manual' if f['tracking_number'] else None)
        # 当前运单（任一来源）；没有时给最近一张已取消的插件运单（界面显示「运单已取消」）
        payload = CarrierShipmentService.shipment_payload(record) if record is not None else status['shipment']
        summary = {
            'method': methods['waybill'],
            'tracking_number': f['tracking_number'],
            'source': source,
            'service': payload['service_type'] if payload else None,
            'service_name': payload['service_name'] if payload else None,
            'label_document_id': payload['label_document_id'] if payload else None,
            'label_download_path': payload['label_download_path'] if payload else None,
            'shipment': payload,
            'unresolved': unresolved,
            'can_create': status['can_create'],
            'blockers': status['blockers'],
        }
        actions = ShippingFlowService._waybill_actions(f, record, payload)
        at = record.created_at if record is not None else None
        # 运单步骤的来源：插件建的 = api，手填 / 文件导回的 = warehouse
        step_source = {'api': 'api', 'manual': 'warehouse', 'file': 'warehouse'}.get(source)

        if unresolved is not None:
            in_progress = (unresolved['status'], unresolved['reason']) in (('pending', None),
                                                                          ('cancelling', 'cancel_in_progress'))
            if in_progress:
                # 建单 / 取消请求正在进行（没卡住）：不算问题（与列表阶段同一口径）
                return _step('waybill', 'current', at, step_source, summary, actions)
            f['problems'].append(_problem(
                'WAYBILL_UNRESOLVED', 'error',
                f"The carrier shipment request is {unresolved['status']} ({unresolved['reason']}); check the carrier's "
                "website, then cancel or dismiss it", 'waybill', unresolved_id=unresolved['id']))
            return _step('waybill', 'warning', at, step_source, summary, actions)
        if f['tracking_number']:
            return _step('waybill', 'done', at, step_source, summary, actions)
        if dn.status in SHIPPED:
            return _step('waybill', 'skipped', summary=summary)
        if dn.status != 'packed':
            return _step('waybill', 'todo', summary=summary, actions=actions)
        blocked = any(b['code'] == 'CUSTOMS_DATA_MISSING' for b in status['blockers'])
        if methods['waybill'] == 'api' and status['blockers'] and task is not None and carrier is not None:
            for blocker in status['blockers']:
                f['problems'].append(_problem(blocker['code'], 'warning', blocker['message'], 'waybill',
                                              field=blocker.get('field')))
        return _step('waybill', 'blocked' if blocked and methods['waybill'] == 'api' else 'current',
                     summary=summary, actions=actions)

    @staticmethod
    def _waybill_actions(f, record, payload) -> list:
        dn, task, methods, status = f['dn'], f['task'], f['methods'], f['shipment_status']
        actions = []
        if methods['waybill'] == 'api':
            if status['can_create']:
                actions.append(_action('create_waybill'))
            elif status['blockers']:
                blocker = status['blockers'][0]
                actions.append(_action('create_waybill', False, blocker['message'], blocker['code']))
            else:
                actions.append(_action('create_waybill', False, "A previous carrier shipment request is unresolved",
                                       'WAYBILL_UNRESOLVED'))
        api_open = CarrierShipmentService.open_shipment(dn)
        if is_locked(dn):
            actions.append(_action('enter_tracking', False, "The DN has been shipped; the tracking number is locked",
                                   'DN_SHIPPED'))
        elif task is None or task.status not in OPEN_TASK_STATUSES:
            actions.append(_action('enter_tracking', False, "The DN has no open delivery task", 'NO_OPEN_TASK'))
        elif api_open is not None:
            actions.append(_action('enter_tracking', False,
                                   "The DN has a carrier shipment created through the integration; cancel it first "
                                   "to enter another tracking number", 'API_WAYBILL_OPEN'))
        else:
            actions.append(_action('enter_tracking', delivery_task_id=task.id))
        # 只列能向承运商取消的运单（有效 / 取消中 / 带运单号的结果不明）；没有运单号的结果不明记录只能确认作废
        if CarrierShipmentService.cancel_target(dn) is not None:
            actions.append(_action('cancel_waybill', status['can_cancel'],
                                   "The shipment cannot be cancelled now (shipped, or a cancellation is in progress)",
                                   'CANNOT_CANCEL'))
        if status['unresolved'] is not None:
            actions.append(_action('dismiss_waybill', status['can_dismiss'],
                                   "The request is still in progress", 'REQUEST_IN_PROGRESS'))
        if payload and payload.get('label_document_id'):
            actions.append(_action('print_label', record is not None and record.status in ('active', 'cancelling'),
                                   "The shipment has been cancelled", 'WAYBILL_CANCELLED',
                                   document_id=payload['label_document_id']))
        return actions

    # ------------------------------------------------------------------
    # 揽收
    # ------------------------------------------------------------------

    @staticmethod
    def _pickup(f):
        dn, carrier, methods, waybill = f['dn'], f['carrier'], f['methods'], f['waybill']
        method = methods['pickup']
        summary = {
            'method': method,
            'pickup': None,
            'contact': carrier.pickup_contact if carrier is not None else None,
            'schedule': carrier.pickup_schedule if carrier is not None else None,
        }
        if method == 'none':
            return _step('pickup', 'skipped', summary=summary)
        handed = dn.status in SHIPPED
        has_waybill = bool(f['tracking_number'])
        if method in ('scheduled', 'dropoff'):
            # 定期集荷 / 自送网点：不用逐票约，交承运商就算做完
            if handed:
                return _step('pickup', 'done', f['task'].completed_at if f['task'] is not None else dn.delivered_at,
                             'warehouse', summary)
            return _step('pickup', 'current' if dn.status == 'packed' and has_waybill else 'todo', summary=summary)

        record = db.session.get(CarrierPickup, waybill.pickup_id) if waybill is not None and waybill.pickup_id \
            else None
        today = local_today()
        summary['pickup'] = CarrierPickupService.payload(record, today) if record is not None else None
        actions = ShippingFlowService._pickup_actions(f, record, today)
        if record is not None:
            status, _reason = CarrierPickupService.effective_status(record)
            source = 'api' if record.source == 'api' else 'warehouse'
            if status == 'unknown':
                f['problems'].append(_problem('PICKUP_UNRESOLVED', 'warning',
                                              "The result of the pickup request is unclear; check with the carrier, "
                                              "then dismiss it", 'pickup', pickup_id=record.id))
                return _step('pickup', 'warning', record.created_at, source, summary, actions)
            if status == 'pending':
                return _step('pickup', 'current', record.created_at, source, summary, actions)
            if status == 'scheduled':
                if record.pickup_date < today and not handed:
                    f['problems'].append(_problem('PICKUP_DATE_PASSED', 'warning',
                                                  f"The pickup date {record.pickup_date.isoformat()} has passed but the "
                                                  "DN has not been handed over; book the pickup again or complete "
                                                  "the shipping", 'pickup', pickup_id=record.id))
                    return _step('pickup', 'warning', record.created_at, source, summary, actions)
                return _step('pickup', 'done', record.created_at, source, summary, actions)
        if handed:
            return _step('pickup', 'skipped', summary=summary)
        if dn.status == 'packed' and has_waybill:
            return _step('pickup', 'current', summary=summary, actions=actions)
        return _step('pickup', 'todo', summary=summary, actions=actions)

    @staticmethod
    def _pickup_actions(f, record, today) -> list:
        dn, carrier, method, waybill = f['dn'], f['carrier'], f['methods']['pickup'], f['waybill']
        actions = []
        held = CarrierPickupService.holds(record, today)
        code = None
        if dn.status != 'packed':
            if dn.status in SHIPPED:
                reason, code = "The DN has been shipped", 'DN_SHIPPED'
            else:
                reason, code = "Pickups can be booked once the DN is packed", 'NOT_PACKED'
        elif waybill is None or waybill.status != 'active':
            reason, code = "Create the waybill (or enter the tracking number) first", 'NO_WAYBILL'
        elif held:
            reason, code = "The shipment is already in a pickup", 'PICKUP_EXISTS'
        else:
            reason = None
        if method == 'api':
            api_reason, api_code = reason, code
            if api_reason is None:
                ctx = CarrierPickupService._collect(dn.warehouse, carrier)
                if ctx['blockers']:
                    api_reason, api_code = ctx['blockers'][0]['message'], ctx['blockers'][0]['code']
            actions.append(_action('book_pickup', api_reason is None, api_reason, api_code))
        actions.append(_action('record_pickup', reason is None, reason, code))
        if record is not None and record.status == 'scheduled':
            actions.append(_action('cancel_pickup', CarrierPickupService.can_cancel(record, today),
                                   "The pickup date has passed", 'PICKUP_DATE_PASSED', pickup_id=record.id))
        return actions

    # ------------------------------------------------------------------
    # 交承运商 / 运输中 / 签收
    # ------------------------------------------------------------------

    @staticmethod
    def _handover(f):
        dn, task, status = f['dn'], f['task'], f['shipment_status']
        summary = {'delivery_task_id': task.id if task is not None else None,
                   'actual_shipping_date': (task.actual_shipping_date.isoformat()
                                            if task is not None and task.actual_shipping_date else None)}
        if dn.status in SHIPPED:
            at = (task.completed_at if task is not None and task.completed_at else None) or dn.delivered_at
            source = (task.completed_source if task is not None else None) or 'warehouse'
            return _step('handover', 'done', at, source, summary)
        code = None
        if dn.status != 'packed':
            reason, code = "The DN must be packed first", 'NOT_PACKED'
        elif task is None or task.status not in OPEN_TASK_STATUSES:
            reason, code = "The DN has no open delivery task", 'NO_OPEN_TASK'
        elif status['unresolved'] is not None:
            reason = "The carrier shipment request is unresolved; cancel or dismiss it first"
            code = 'WAYBILL_UNRESOLVED'
        else:
            reason = None
        actions = [_action('complete_shipping', reason is None, reason, code,
                           delivery_task_id=task.id if task is not None else None)]
        if dn.status != 'packed':
            return _step('handover', 'todo', summary=summary, actions=actions)
        waybill = f['waybill']
        problem = waybill.tracking_problem if waybill is not None and waybill.status == 'active' else None
        if problem:
            f['problems'].append(_problem('AUTO_SHIP_BLOCKED', 'warning', problem, 'handover'))
            return _step('handover', 'warning', summary=summary, actions=actions)
        return _step('handover', 'current' if f['tracking_number'] else 'todo', summary=summary, actions=actions)

    @staticmethod
    def _transit(f):
        dn, waybill, methods = f['dn'], f['waybill'], f['methods']
        tracking = waybill.tracking_payload() if waybill is not None else None
        summary = {'tracking': tracking, 'tracking_url': f['tracking_url'], 'tracked_by_api': f['tracked']}
        actions = []
        if f['tracked']:
            actions.append(_action('refresh_tracking', waybill.status == 'active', "The shipment is not active",
                                   'WAYBILL_NOT_ACTIVE'))
        if f['tracking_url']:
            actions.append(_action('open_tracking', url=f['tracking_url']))
        if methods['tracking'] == 'none':
            return _step('transit', 'skipped', summary=summary, actions=actions)
        if not f['tracking_number']:
            # 还没有运单号：以后能查（API 跟踪 / 有查询页模板）就是 todo；已经发货了还没有号码 → 不适用
            trackable = methods['tracking'] == 'api' or ShippingFlowService._has_url_template(f)
            state = 'todo' if trackable and dn.status not in SHIPPED else 'skipped'
            return _step('transit', state, summary=summary, actions=actions)
        if not f['tracked'] and not f['tracking_url']:
            # 既不经 API 查、也没有公开查询页
            return _step('transit', 'skipped', summary=summary, actions=actions)
        # 这一步的时刻 = 承运商最新一条扫描的时刻
        events = (tracking or {}).get('events') or []
        at = _carrier_time(events[0].get('time')) if events else None
        source = 'carrier' if tracking else None
        if dn.status == 'completed':
            return _step('transit', 'done', at, source, summary, actions)
        if dn.status != 'delivered':
            return _step('transit', 'todo', summary=summary, actions=actions)
        state = 'current'
        if tracking and (tracking.get('status') == 'exception' or tracking.get('error')):
            state = 'warning'
            f['problems'].append(_problem(
                'TRACKING_EXCEPTION' if tracking.get('status') == 'exception' else 'TRACKING_ERROR', 'warning',
                tracking.get('error') or tracking.get('status_text') or "The carrier reports an exception",
                'transit'))
        return _step('transit', state, at, source, summary, actions)

    @staticmethod
    def _has_url_template(f) -> bool:
        """有公开查询页模板（承运商设置的，或在线对接插件内置的）"""
        carrier = f['carrier']
        if carrier is None:
            return False
        if (carrier.tracking_url_template or '').strip():
            return True
        from warehouse.carrier.methods import plugin_of
        cls = plugin_of(carrier)
        return bool(cls is not None and cls.tracking_url_template)

    @staticmethod
    def _delivered(f):
        dn, task, carrier, waybill = f['dn'], f['task'], f['carrier'], f['waybill']
        tracking = waybill.tracking_payload() if waybill is not None else None
        days = carrier.auto_sign_days if carrier is not None else None
        rule_at = auto_sign_at(task, days) if not f['tracked'] else None
        summary = {
            'estimated_delivery': (tracking or {}).get('estimated_delivery'),
            'auto_sign_days': days if not f['tracked'] else None,
            'auto_sign_at': event_time(rule_at) if rule_at is not None and dn.status == 'delivered' else None,
        }
        if dn.status == 'completed':
            at = (task.signed_at if task is not None and task.signed_at else None) or dn.completed_at
            source = (task.signed_source if task is not None else None) or 'warehouse'
            return _step('delivered', 'done', at, source, summary)
        can_sign = dn.status == 'delivered' and task is not None and task.status == 'completed'
        actions = [_action('sign', can_sign, "The DN has not been handed over to the carrier yet", 'NOT_HANDED_OVER',
                           delivery_task_id=task.id if task is not None else None)]
        if dn.status == 'delivered' and tracking and tracking.get('status') == 'delivered':
            # 承运商说送达了、自动签收没做成（原因在 tracking.problem）
            if tracking.get('problem'):
                f['problems'].append(_problem('AUTO_SIGN_BLOCKED', 'warning', tracking['problem'], 'delivered'))
            return _step('delivered', 'warning' if tracking.get('problem') else 'current', summary=summary,
                         actions=actions)
        if dn.status == 'delivered' and rule_at is not None and rule_at <= datetime.now():
            return _step('delivered', 'current', summary=summary, actions=actions)
        return _step('delivered', 'todo', summary=summary, actions=actions)

