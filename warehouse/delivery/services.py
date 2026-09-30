from extensions.db import *
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.dn.models import DN, DNDetail
from warehouse.goods.models import Goods
from warehouse.common import require_positive_int, require_fields
from warehouse.dn.services import (
    DNService, pick_fields, parse_date_value, parse_datetime_value,
)
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta
from .models import DeliveryTask, DeliveryTaskStatusLog

# 请求体白名单：dn_id 只在创建时接受；status / is_active / created_by / *_at 只经流程端点变更
DELIVERY_UPDATE_FIELDS = (
    'recipient_id', 'shipping_address', 'expected_shipping_date', 'actual_shipping_date',
    'transportation_mode', 'carrier_id', 'tracking_number', 'shipping_cost', 'currency',
    'order_number', 'remark',
)
DELIVERY_CREATE_FIELDS = ('dn_id',) + DELIVERY_UPDATE_FIELDS


class DeliveryTaskService:

    @staticmethod
    def _normalize_payload(payload: dict, dn: DN):
        """日期 / 枚举 / 运费统一校验并转换；收货人、承运商必须与 DN 仓库同公司。"""
        for field in ('expected_shipping_date', 'actual_shipping_date'):
            if field in payload:
                payload[field] = parse_date_value(payload[field], field)
        mode = payload.get('transportation_mode')
        if mode is not None and mode not in DeliveryTask.DELIVERY_TASK_TRANSPORTATION_MODES:
            raise BadRequestException(f"Invalid transportation_mode: {mode}", 16048)
        cost = payload.get('shipping_cost')
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
                raise BadRequestException("shipping_cost must be a non-negative number", 16033)
        DNService.assert_company_master_data(
            dn.warehouse.company_id,
            recipient_id=payload.get('recipient_id'),
            carrier_id=payload.get('carrier_id'),
        )
        return payload

    @staticmethod
    def _assert_shipping_dates(expected, actual):
        """与表上 chk_shipping_date 约束对齐：实际发货日不得早于计划发货日（否则 500）"""
        if expected is not None and actual is not None and actual < expected:
            raise BadRequestException(
                "actual_shipping_date must not be earlier than expected_shipping_date", 16061
            )

    @staticmethod
    def _guard_carrier_tracking(task: DeliveryTask, payload: dict):
        """DN 有进行中 / 有效 / 结果不明的自动运单（承运商对接建的）时：
        - 请求里的运单号为空视为不改；有效运单时与它不同 → 409 16078（要换运单号先取消自动运单）；
          进行中 / 结果不明时带了运单号 → 409 16079（防止在 FedEx 网站又手工建一张）
        - 有效运单时：请求里的 carrier_id 为 null 视为不改；承运商与运单的不同 → 409 16078
          （details {tracking_number, carrier, carrier_id}，carrier_id 为请求里的值）"""
        if 'tracking_number' not in payload and 'carrier_id' not in payload:
            return
        from warehouse.dn.carrier_services import CarrierShipmentService
        shipment = CarrierShipmentService.open_shipment(task.dn)
        if shipment is None:
            return
        if 'tracking_number' in payload:
            if not str(payload['tracking_number'] or '').strip():
                payload.pop('tracking_number')
            else:
                CarrierShipmentService.assert_tracking_matches(task.dn, payload['tracking_number'])
        if 'carrier_id' in payload and shipment.status == 'active':
            DeliveryTaskService._assert_shipment_carrier(shipment, payload)

    @staticmethod
    def _assert_shipment_carrier(shipment, payload: dict):
        """请求里的承运商必须是自动运单的承运商（按承运商 code 比，与建单前置条件同口径）。"""
        from warehouse.carrier.models import Carrier
        from warehouse.dn.carrier_services import _carrier_code
        carrier_id = payload.get('carrier_id')
        if carrier_id is None:
            payload.pop('carrier_id', None)
            return
        carrier = db.session.get(Carrier, carrier_id)
        if _carrier_code(carrier) != (shipment.carrier or '').strip().lower():
            tracking = shipment.tracking_number
            raise ConflictException(
                f"This DN has an active {shipment.carrier} shipment"
                f"{' ' + tracking if tracking else ''}; the carrier cannot be changed. "
                "Cancel the carrier shipment first.", 16078,
                details={'tracking_number': tracking, 'carrier': shipment.carrier, 'carrier_id': carrier_id},
            )

    @staticmethod
    def _clean_complete_tracking(payload: dict):
        """完成发货的 tracking_number：空串 / null / 不传 → 从 payload 去掉（不改已存值）；
        数字按字符串收；其它类型或超过 100 字 → 400。"""
        if 'tracking_number' not in payload:
            return
        tracking = payload['tracking_number']
        if tracking is None:
            payload.pop('tracking_number')
            return
        if isinstance(tracking, bool) or not isinstance(tracking, (str, int)):
            raise BadRequestException("tracking_number must be a string", 40000, field='tracking_number')
        tracking = str(tracking).strip()
        if not tracking:
            payload.pop('tracking_number')
            return
        if len(tracking) > 100:
            raise BadRequestException("tracking_number must not exceed 100 characters", 40000,
                                      field='tracking_number')
        payload['tracking_number'] = tracking

    @staticmethod
    def _guard_document_awb(ship_state: dict, payload: dict):
        """海外件：当前有效 CI 已印 AWB 时，完成发货只能用这个号码（忽略空白差异）。
        不同 → 409 16080（先保存运单号并重出单证）；相同 → 不改已存值（保持与 CI 一字不差）。
        CI 没印 AWB 时照常写入请求里的号码。"""
        document_awb = ship_state.get('awb')
        requested = payload.get('tracking_number')
        if not document_awb or not requested:
            return
        if ''.join(requested.split()) != ''.join(document_awb.split()):
            raise ConflictException(
                f"The current commercial invoice shows AWB {document_awb} but the request has {requested}. "
                "Save the tracking number and issue the documents again before shipping.", 16080,
                details={'document_tracking_number': document_awb, 'tracking_number': requested},
            )
        payload.pop('tracking_number')

    @staticmethod
    def _get_instance(task_or_id: int | DeliveryTask) -> DeliveryTask:
        """
        根据传入参数返回 DeliveryTask 实例。
        如果参数为 int，则调用 get_task 获取 DeliveryTask 实例；
        否则直接返回传入的 DeliveryTask 实例。
        """
        if isinstance(task_or_id, int):
            return DeliveryTaskService.get_task(task_or_id)
        return task_or_id

    @staticmethod
    def list_tasks(filters: dict):
        """
        根据过滤条件，返回 DeliveryTask 的查询对象。
        """
        query = DeliveryTask.query.order_by(DeliveryTask.id.desc())

        if filters.get('dn_id'):
            query = query.filter(DeliveryTask.dn_id == filters['dn_id'])
        if filters.get('recipient_id'):
            query = query.filter(DeliveryTask.recipient_id == filters['recipient_id'])
        if filters.get('carrier_id'):
            query = query.filter(DeliveryTask.carrier_id == filters['carrier_id'])
        # 注：DeliveryTask 本身没有 operator_id 字段，如需按照操作人过滤，请结合日志表或者修改模型
        if filters.get('shipping_address'):
            query = query.filter(DeliveryTask.shipping_address.ilike(f"%{filters['shipping_address']}%"))
        if filters.get('tracking_number'):
            query = query.filter(DeliveryTask.tracking_number.ilike(f"%{filters['tracking_number']}%"))
        if filters.get('order_number'):
            query = query.filter(DeliveryTask.order_number.ilike(f"%{filters['order_number']}%"))
        if filters.get('transportation_mode'):
            query = query.filter(DeliveryTask.transportation_mode == filters['transportation_mode'])

        if filters.get('expected_shipping_date'):
            query = query.filter(DeliveryTask.expected_shipping_date >= filters['expected_shipping_date'])
        if filters.get('actual_shipping_date'):
            query = query.filter(DeliveryTask.actual_shipping_date >= filters['actual_shipping_date'])

        if filters.get('status'):
            query = query.filter(DeliveryTask.status == filters['status'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(DeliveryTask.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(DeliveryTask.is_active == filters['is_active'])

        # 搜索id,DN.id ，或者在dn.details中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                task_id = int(keyword)
                conditions.append(DeliveryTask.id == task_id)
                conditions.append(DeliveryTask.dn_id == task_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 DN.id 过滤条件
                pass

            conditions.append(DeliveryTask.dn.has(DN.details.any(DNDetail.goods.has(Goods.code.ilike(f"%{keyword}%")))))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id') or filters.get('warehouse_ids'):
            query = query.join(DN, DeliveryTask.dn_id == DN.id)

        if filters.get('warehouse_id'):
            query = query.filter(DN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query

    @staticmethod
    @transactional
    def create_task(data: dict, created_by_id: int) -> DeliveryTask:
        """
        创建新的 DeliveryTask。只接受白名单字段；status 固定 pending、is_active 固定 True。
        缺 dn_id / recipient_id / shipping_address / expected_shipping_date → 400。
        DN 有进行中 / 有效 / 结果不明的自动运单（承运商对接）时不能新建（新任务会成为当前发货任务，
        绕过运单号锁定）→ 409 16078；带的运单号也按自动运单校验（结果不明 16079 / 不一致 16078）。
        """
        from warehouse.dn.carrier_services import CarrierShipmentService
        from warehouse.dn.customs_services import CustomsService

        payload = pick_fields(data, DELIVERY_CREATE_FIELDS)
        require_fields(payload, 'dn_id', 'recipient_id', 'shipping_address', 'expected_shipping_date')
        dn_id = require_positive_int(payload['dn_id'], 'dn_id', 16044)
        dn = get_object_or_404(DN, dn_id)
        # 先锁 DN 行：与自动建运单 / 取消串行
        dn = CustomsService._lock(dn)
        payload = DeliveryTaskService._normalize_payload(payload, dn)
        DeliveryTaskService._assert_shipping_dates(
            payload['expected_shipping_date'], payload.get('actual_shipping_date')
        )
        CarrierShipmentService.assert_tracking_matches(dn, payload.get('tracking_number'), allow_empty=True)
        CarrierShipmentService.assert_delivery_tasks_editable(dn, 'added')

        new_delivery = DeliveryTask(
            dn_id=dn_id,
            recipient_id=payload['recipient_id'],
            shipping_address=payload['shipping_address'],
            expected_shipping_date=payload['expected_shipping_date'],
            actual_shipping_date=payload.get('actual_shipping_date'),
            transportation_mode=payload.get('transportation_mode'),
            carrier_id=payload.get('carrier_id'),
            tracking_number=payload.get('tracking_number'),
            shipping_cost=payload.get('shipping_cost', 0.0),
            currency=payload.get('currency') or 'JPY',
            order_number=payload.get('order_number'),
            status='pending',
            remark=payload.get('remark'),
            created_by=created_by_id
        )
        db.session.add(new_delivery)
        # db.session.commit()
        return new_delivery

    @staticmethod
    def get_task(delivery_id: int) -> DeliveryTask:
        """
        根据 delivery_id 获取单个 DeliveryTask，不存在时抛出 404
        """
        return get_object_or_404(DeliveryTask, delivery_id)

    @staticmethod
    @transactional
    def update_task(delivery_id: int, data: dict) -> DeliveryTask:
        """
        更新指定 DeliveryTask（completed / signed 后不可改）。
        只接受白名单字段：dn_id 不可改；status / is_active / created_by / *_at
        只经 process / complete / sign 流转，客户端传入一律忽略。
        运单号：DN 的自动运单结果不明 / 进行中时不能存号码（409 16079）；有有效自动运单时只能是它的号码（16078）。
        """
        from warehouse.dn.carrier_services import CarrierShipmentService
        from warehouse.dn.customs_services import CustomsService

        delivery = DeliveryTaskService.get_task(delivery_id)

        if delivery.status in ('completed', 'signed'):
           raise BadRequestException("Cannot update a completed or signed Delivery", 16023)

        payload = pick_fields(data, DELIVERY_UPDATE_FIELDS)
        payload = DeliveryTaskService._normalize_payload(payload, delivery.dn)
        # 先锁 DN 行再查自动运单：与自动建运单 / 取消串行
        CustomsService._lock(delivery.dn)
        if 'tracking_number' in payload:
            CarrierShipmentService.assert_tracking_matches(delivery.dn, payload['tracking_number'], allow_empty=True)
        DeliveryTaskService._guard_carrier_tracking(delivery, payload)
        DeliveryTaskService._assert_shipping_dates(
            payload.get('expected_shipping_date', delivery.expected_shipping_date),
            payload.get('actual_shipping_date', delivery.actual_shipping_date),
        )

        # NOT NULL 字段：传 None 视为不改
        for field in ('recipient_id', 'shipping_address', 'expected_shipping_date'):
            if payload.get(field) is not None:
                setattr(delivery, field, payload[field])
        # 可空字段：显式传 None 允许清空
        for field in ('actual_shipping_date', 'transportation_mode', 'carrier_id', 'tracking_number',
                      'shipping_cost', 'currency', 'order_number', 'remark'):
            if field in payload:
                setattr(delivery, field, payload[field])

        # db.session.commit()
        return delivery

    @staticmethod
    @transactional
    def save_tracking(task_or_id: int | DeliveryTask, data: dict) -> DeliveryTask:
        """
        发货前单独保存运单号（与承运商）：打包录箱子 → 在承运商系统建运单拿到运单号 → 保存 →
        出单证（商业发票印 AWB No.）→ 完成发货。
        - 任务 pending / in_progress 可存；DN 已发货或任务已完成 → 409 16065
        - 已签发的出口单证不自动作废：运单号计入单证数据指纹，重新 issue 时升版本
        - DN 有有效的自动运单（承运商对接）时，只能存它的号码（清空 / 改成别的 → 409 16078，先取消自动运单）
        - DN 的自动运单结果不明 / 进行中时不能存号码（409 16079，防止又在承运商网站手工建一张）
        """
        from warehouse.dn.customs_services import CustomsService

        task = DeliveryTaskService._get_instance(task_or_id)
        # 先锁 DN 行再查自动运单：与自动建运单 / 取消串行（建单流程里调用时 DN 已锁，重复加锁无妨）
        CustomsService._lock(task.dn)
        if task.status in ('completed', 'signed') or task.dn.status in ('delivered', 'completed'):
            raise ConflictException("The shipment has been completed; tracking number is locked.", 16065)

        payload = pick_fields(data or {}, ('tracking_number', 'carrier_id'))
        if 'tracking_number' in payload:
            tracking = payload['tracking_number']
            if tracking is not None and not isinstance(tracking, str):
                raise BadRequestException("tracking_number must be a string", 40000, field='tracking_number')
            tracking = (tracking or '').strip() or None
            if tracking is not None and len(tracking) > 100:
                raise BadRequestException("tracking_number must not exceed 100 characters", 40000,
                                          field='tracking_number')
            from warehouse.dn.carrier_services import CarrierShipmentService
            CarrierShipmentService.assert_tracking_matches(task.dn, tracking)
            task.tracking_number = tracking
        if payload.get('carrier_id') is not None:
            carrier_id = require_positive_int(payload['carrier_id'], 'carrier_id', 16044)
            DNService.assert_company_master_data(task.dn.warehouse.company_id, carrier_id=carrier_id)
            task.carrier_id = carrier_id
        db.session.flush()
        return task

    @staticmethod
    @transactional
    def delete_task(delivery_id: int):
        """
        删除指定 Delivery（只能删 pending 的）。
        DN 有进行中 / 有效 / 结果不明的自动运单时不能删（运单号在任务上，删了等于绕过锁定）→ 409 16078。
        """
        from warehouse.dn.carrier_services import CarrierShipmentService
        from warehouse.dn.customs_services import CustomsService

        delivery = DeliveryTaskService.get_task(delivery_id)
        if delivery.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Delivery", 16002)
        # 先锁 DN 行：与自动建运单 / 取消串行
        dn = CustomsService._lock(delivery.dn)
        CarrierShipmentService.assert_delivery_tasks_editable(dn, 'deleted')

        db.session.delete(delivery)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # 下面是与 SortingTask 类似的“状态流转 & 日志”处理
    # -------------------------------------------------------------------------
    @staticmethod
    @transactional
    def _update_task_status(delivery: DeliveryTask, new_status: str, operator_id: int) -> DeliveryTask:
        """
        内部方法：更新 DeliveryTask 状态，记录状态日志，并维护里程碑时间（started_at / completed_at / signed_at）。
        """
        if new_status not in DeliveryTask.DELIVERY_TASK_STATUSES:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        old_status = delivery.status
        now = datetime.now()

        # 设置新状态
        delivery.status = new_status

        # 根据状态流转，更新不同的时间字段
        if old_status == 'pending' and new_status == 'in_progress':
            delivery.started_at = now
        elif old_status == 'in_progress' and new_status == 'completed':
            delivery.completed_at = now
            # 这里也可同步将 actual_shipping_date 设置为当前日期/时间, 视业务而定:
            delivery.actual_shipping_date = now
        elif old_status == 'completed' and new_status == 'signed':
            delivery.signed_at = now

        # 写入状态变更日志
        status_log = DeliveryTaskStatusLog(
            task_id=delivery.id,
            old_status=old_status,
            new_status=new_status,
            operator_id=operator_id,
            changed_at=now
        )
        db.session.add(delivery)
        db.session.add(status_log)
        db.session.flush()

        # db.session.commit()
        return delivery

    @staticmethod
    @transactional
    def process_task(task_or_id: int | DeliveryTask, operator_id: int) -> DeliveryTask:
        """
        将 DeliveryTask 从 pending 切换为 in_progress
        """
        delivery = DeliveryTaskService._get_instance(task_or_id)
        if delivery.status != 'pending':
            raise BadRequestException("Cannot process a non-pending Delivery", 16007)

        return DeliveryTaskService._update_task_status(delivery, 'in_progress', operator_id)

    @staticmethod
    @transactional
    def complete_task(task_or_id: int | DeliveryTask, data:dict, operator_id: int) -> DeliveryTask:
        """
        将 DeliveryTask 从 in_progress 切换为 completed
        数据格式：
        {
            "transportation_mode": "air",
            "carrier_id": 1,
            "tracking_number": "123456",
            "shipping_cost": 100.0,
            "remark": "Delivery completed."
        }
        - tracking_number 为空串 / null / 不传 → 不改已存的运单号（国内件也一样）
        - 有有效的自动运单时运单号 / 承运商与运单不一致 → 409 16078
        - 海外件（DN 带报关快照）按写入前的已存状态检查单证：缺 CI / PL → 409 16069
          {missing_documents: [...], outdated: false}；已过期 → 409 16069 {missing_documents: [], outdated: true}
        - 海外件当前 CI 已印 AWB、请求带了不同的运单号 → 409 16080 {document_tracking_number, tracking_number}；
          CI 没印 AWB 时照常写入请求里的运单号
        """
        from warehouse.dn.customs_services import CustomsService
        task = DeliveryTaskService._get_instance(task_or_id)
        # 先锁 DN 行（与改箱子 / 改报关快照 / 出单证 / 建运单串行），防止检查通过后单证被并发作废仍出货；
        # 拿到锁后重读任务，并发的重复完成在下面的状态检查处挡下
        CustomsService._lock(task.dn)
        db.session.refresh(task)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in-progress Delivery", 16008)

        payload = pick_fields(data or {}, (
            'transportation_mode', 'carrier_id', 'tracking_number', 'shipping_cost', 'currency', 'remark',
        ))
        DeliveryTaskService._clean_complete_tracking(payload)
        payload = DeliveryTaskService._normalize_payload(payload, task.dn)
        # 有有效的自动运单时，完成发货传了别的运单号 / 承运商 → 409 16078（相同或不传照常）
        DeliveryTaskService._guard_carrier_tracking(task, payload)
        DeliveryTaskService._assert_shipping_dates(task.expected_shipping_date, datetime.now().date())

        # 海外件：单证缺失 / 过期 → 409 16069（按写入前的状态）；CI 上的 AWB 与请求不同 → 409 16080
        ship_state = CustomsService.assert_ready_to_ship(task.dn)
        DeliveryTaskService._guard_document_awb(ship_state, payload)

        task = DeliveryTaskService._update_task_status(task, 'completed', operator_id)

        # 更新字段
        task.actual_shipping_date = datetime.now().date()  # 获取当前日期
        for field in ('transportation_mode', 'carrier_id', 'tracking_number', 'shipping_cost', 'currency', 'remark'):
            if field in payload:
                setattr(task, field, payload[field])

        # 若有需要在此更新库存或者做别的业务处理
        DNService.delivery_dn(task.dn_id)

        return task

    @staticmethod
    @transactional
    def sign_task(task_or_id: int | DeliveryTask,data:dict, operator_id: int) -> DeliveryTask:
        """
        将 DeliveryTask 从 completed 切换为 signed
        数据格式：
        {
            "signed_at": "2021-08-01"
        }
        """
        task = DeliveryTaskService._get_instance(task_or_id)

        if task.status != 'completed':
            raise BadRequestException("Cannot sign a non-completed Delivery", 16024)
        
        signed_at = parse_datetime_value((data or {}).get('signed_at'), 'signed_at')

        task = DeliveryTaskService._update_task_status(task, 'signed', operator_id)

        # 客户端给了签收时间就用它，否则用状态流转时刻
        if signed_at is not None:
            task.signed_at = signed_at
        # db.session.commit()
        
        DNService.complete_dn(task.dn)

        return task

    @staticmethod
    @transactional
    def create_delivery_task_from_dn(dn_id: int, created_by_id: int) -> DeliveryTask:
        """
        根据 DN 自动生成一个 DeliveryTask
        """
        dn = DNService.get_dn(dn_id)
        if not dn.details:
            raise BadRequestException("DN has no details to create a Delivery.", 16016)

        transportation_mode = dn.transportation_mode
        if transportation_mode not in DeliveryTask.DELIVERY_TASK_TRANSPORTATION_MODES:
            transportation_mode = None

        # 这里演示从 DN 复制部分字段
        delivery = DeliveryTask(
            dn_id=dn_id,
            recipient_id=dn.recipient_id,       
            shipping_address=dn.shipping_address or "",  
            expected_shipping_date=dn.expected_shipping_date,
            transportation_mode=transportation_mode,
            carrier_id=dn.carrier_id,
            status='pending',
            created_by=created_by_id
        )
        db.session.add(delivery)
        # db.session.commit()
        return delivery

    @staticmethod
    def get_delivery_monthly_stats(months=6, filters=None):
        """获取最近N个月各状态Picking统计（支持仓库过滤）
        Args:
            months (int): 统计月份数（默认6个月）
            filters (dict): 过滤条件字典，可包含：
                - warehouse_id: 单个仓库ID
                - warehouse_ids: 多个仓库ID列表
        Returns:
            list: 符合前端图表要求的数据序列
        """
        # 计算时间范围
        end_date = datetime.now()
        start_date = end_date - relativedelta(months=months-1)
        start_date = start_date.replace(day=1, hour=0, minute=0, second=0)

        # 构建基础查询
        query = DeliveryTask.query.with_entities(
            extract('year', DeliveryTask.created_at).label('year'),
            extract('month', DeliveryTask.created_at).label('month'),
            func.sum(
                case((DeliveryTask.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((DeliveryTask.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((DeliveryTask.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            DeliveryTask.created_at >= start_date,
            DeliveryTask.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, DeliveryTask.dn_id == DN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(DN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 分组和排序保持不变
        query = query.group_by('year', 'month').order_by('year', 'month')

        # 执行查询并格式化为字典
        raw_data = {
            f"{int(row.year)}-{int(row.month):02d}": {
                'pending': row.pending or 0,
                'in_progress': row.in_progress or 0,
                'completed': row.completed or 0
            } for row in query.all()
        }

        # 生成完整月份序列（处理空数据月份）
        date_series = []
        current = start_date.replace(day=1)
        while current <= end_date:
            date_series.append(current.strftime("%Y-%m"))
            current += relativedelta(months=1)

        # 按前端要求构建数据结构
        status_order = ['pending', 'in_progress', 'completed']
        return [{
            "name": status.capitalize(),
            "data": [
                raw_data.get(month, {}).get(status, 0)
                for month in date_series[-months:]  # 取最近N个月
            ]
        } for status in status_order]

    @staticmethod
    def get_status_overview(filters=None):
        """获取各状态ASN在当前月、前一个月和去年同月的统计"""
        now = datetime.now()
        current_year = now.year
        current_month = now.month

        # 时间范围计算（优化闰月处理）
        prev_month_date = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
        prev_month_year = prev_month_date.year
        prev_month_month = prev_month_date.month

        # 构建动态条件生成器（复用代码）
        def build_case(target_year, target_month):
            return case(
                (and_(
                    db.extract('year', DeliveryTask.created_at) == target_year,
                    db.extract('month', DeliveryTask.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = DeliveryTask.query.with_entities(
            DeliveryTask.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            DeliveryTask.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('sorting_type'):
                query = query.filter(DeliveryTask.sorting_type == filters['sorting_type'])

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, DeliveryTask.dn_id == DN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(DN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(DeliveryTask.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in DeliveryTask.DELIVERY_TASK_STATUSES]