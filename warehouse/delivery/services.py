from extensions.db import *
from extensions.error import BadRequestException
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
        """
        payload = pick_fields(data, DELIVERY_CREATE_FIELDS)
        require_fields(payload, 'dn_id', 'recipient_id', 'shipping_address', 'expected_shipping_date')
        dn_id = require_positive_int(payload['dn_id'], 'dn_id', 16044)
        dn = get_object_or_404(DN, dn_id)
        payload = DeliveryTaskService._normalize_payload(payload, dn)
        DeliveryTaskService._assert_shipping_dates(
            payload['expected_shipping_date'], payload.get('actual_shipping_date')
        )

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
        """
        delivery = DeliveryTaskService.get_task(delivery_id)

        if delivery.status in ('completed', 'signed'):
           raise BadRequestException("Cannot update a completed or signed Delivery", 16023)

        payload = pick_fields(data, DELIVERY_UPDATE_FIELDS)
        payload = DeliveryTaskService._normalize_payload(payload, delivery.dn)
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
    def delete_task(delivery_id: int):
        """
        删除指定 Delivery（可自行设定状态限制）
        """
        delivery = DeliveryTaskService.get_task(delivery_id)
        if delivery.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Delivery", 16002)

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
        """
        task = DeliveryTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in-progress Delivery", 16008)

        payload = pick_fields(data or {}, (
            'transportation_mode', 'carrier_id', 'tracking_number', 'shipping_cost', 'currency', 'remark',
        ))
        payload = DeliveryTaskService._normalize_payload(payload, task.dn)
        DeliveryTaskService._assert_shipping_dates(task.expected_shipping_date, datetime.now().date())

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