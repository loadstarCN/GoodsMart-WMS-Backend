from datetime import datetime
from extensions.db import *
from extensions.error import BadRequestException
from extensions.transaction import transactional
from warehouse.dn.models import DN, DNDetail
from warehouse.goods.models import Goods
from warehouse.dn.services import DNService
from warehouse.inventory.services import InventoryService  
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta
from .models import DeliveryTask, DeliveryTaskStatusLog

class DeliveryTaskService:

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
        创建新的 DeliveryTask
        """
        # 若传入的 expected_shipping_date/actual_shipping_date 是字符串, 在此转换
        if 'expected_shipping_date' in data and isinstance(data['expected_shipping_date'], str):
            data['expected_shipping_date'] = datetime.strptime(data['expected_shipping_date'], '%Y-%m-%d').date()
        if 'actual_shipping_date' in data and isinstance(data['actual_shipping_date'], str):
            data['actual_shipping_date'] = datetime.strptime(data['actual_shipping_date'], '%Y-%m-%d').date()

        new_delivery = DeliveryTask(
            dn_id=data['dn_id'],
            recipient_id=data['recipient_id'],
            shipping_address=data['shipping_address'],
            expected_shipping_date=data['expected_shipping_date'],
            actual_shipping_date=data.get('actual_shipping_date'),
            transportation_mode=data.get('transportation_mode'),
            carrier_id=data.get('carrier_id'),
            tracking_number=data.get('tracking_number'),
            shipping_cost=data.get('shipping_cost', 0.0),
            order_number=data.get('order_number'),
            status=data.get('status', 'pending'),
            remark=data.get('remark'),
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
        更新指定 DeliveryTask（若要跟 Sorting 的逻辑一致，可限制仅在某些状态下可改）
        """
        delivery = DeliveryTaskService.get_task(delivery_id)

        # 如果你想严格限制只能在 'pending' 或 'in_progress' 状态下才可编辑，可自行加判断：
        if delivery.status in ('completed', 'signed'):
           raise BadRequestException("Cannot update a completed or signed Delivery", 16023)

        # 字段转换
        if 'expected_shipping_date' in data and isinstance(data['expected_shipping_date'], str):
            data['expected_shipping_date'] = datetime.strptime(data['expected_shipping_date'], '%Y-%m-%d').date()
        if 'actual_shipping_date' in data and isinstance(data['actual_shipping_date'], str):
            data['actual_shipping_date'] = datetime.strptime(data['actual_shipping_date'], '%Y-%m-%d').date()

        # 更新字段
        delivery.dn_id = data.get('dn_id', delivery.dn_id)
        delivery.recipient_id = data.get('recipient_id', delivery.recipient_id)
        delivery.shipping_address = data.get('shipping_address', delivery.shipping_address)
        delivery.expected_shipping_date = data.get('expected_shipping_date', delivery.expected_shipping_date)
        delivery.actual_shipping_date = data.get('actual_shipping_date', delivery.actual_shipping_date)
        delivery.transportation_mode = data.get('transportation_mode', delivery.transportation_mode)
        delivery.carrier_id = data.get('carrier_id', delivery.carrier_id)
        delivery.tracking_number = data.get('tracking_number', delivery.tracking_number)
        delivery.shipping_cost = data.get('shipping_cost', delivery.shipping_cost)
        delivery.order_number = data.get('order_number', delivery.order_number)
        delivery.status = data.get('status', delivery.status)
        delivery.remark = data.get('remark', delivery.remark)
        delivery.is_active = data.get('is_active', delivery.is_active)

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
        
        task = DeliveryTaskService._update_task_status(task, 'completed', operator_id)

        # 更新字段
        task.actual_shipping_date = datetime.now().date()  # 获取当前日期
        task.transportation_mode = data.get('transportation_mode', task.transportation_mode)
        task.carrier_id = data.get('carrier_id', task.carrier_id)
        task.tracking_number = data.get('tracking_number', task.tracking_number)
        task.shipping_cost = data.get('shipping_cost', task.shipping_cost)
        task.remark = data.get('remark', task.remark)

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
        
        task = DeliveryTaskService._update_task_status(task, 'signed', operator_id)

        # 更新字段
        if 'signed_at' in data and isinstance(data['signed_at'], str):
            data['signed_at'] = data['signed_at']

        task.signed_at = data.get('signed_at', task.signed_at)
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