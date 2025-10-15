from datetime import datetime
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.cyclecount.services import CycleCountTaskService
from warehouse.goods.services import GoodsLocationService
from warehouse.inventory.services import InventoryService
from .models import Adjustment, AdjustmentDetail
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta

class AdjustmentService:

    @staticmethod
    def _get_instance(task_or_id: int | Adjustment) -> Adjustment:
        """
        根据传入参数返回 PickingTask 实例。
        如果参数为 int，则调用 get_task 获取 PickingTask 实例；
        否则直接返回传入的 PickingTask 实例。
        """
        if isinstance(task_or_id, int):
            return AdjustmentService.get_adjustment(task_or_id)
        return task_or_id

    @staticmethod
    def list_adjustments(filters: dict):
        """
        根据过滤条件，返回 Adjustment 的查询对象。

        :param filters: dict，包含可能的过滤字段
        :return: 一个已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = Adjustment.query.order_by(Adjustment.id.desc())

        if filters.get('adjustment_reason'):
            query = query.filter(Adjustment.adjustment_reason.ilike(f"%{filters['adjustment_reason']}%"))
        if filters.get('status'):
            query = query.filter(Adjustment.status == filters['status'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(Adjustment.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Adjustment.is_active == filters['is_active'])

        if filters.get('created_by'):
            query = query.filter(Adjustment.created_by == filters['created_by'])
        
        if filters.get('warehouse_id'):
            query = query.filter(Adjustment.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Adjustment.warehouse_id.in_(filters['warehouse_ids']))

        return query

    @staticmethod
    @transactional
    def create_adjustment(data: dict, created_by_id: int) -> Adjustment:
        """
        创建新的 Inventory Adjustment，并可选地创建其详情 (AdjustmentDetail)

        :param data: 包含请求中的调整任务数据
        :param created_by_id: 创建者用户 ID
        :return: 新创建的 Adjustment 对象
        """
        new_adjustment = Adjustment(
            warehouse_id=data['warehouse_id'],
            adjustment_reason=data.get('adjustment_reason'),
            status=data.get('status', 'pending'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_adjustment)
        db.session.flush()  # 确保新创建的 Adjustment ID 可用
        # db.session.commit()

        # 如果有调整明细，创建并保存
        if data.get('details'):
            for detail_data in data['details']:
                AdjustmentService.create_adjustment_detail(new_adjustment.id, detail_data, created_by_id)

        return new_adjustment

    @staticmethod
    def get_adjustment(adjustment_id: int) -> Adjustment:
        """
        根据 adjustment_id 获取单个 Adjustment，不存在时抛出 404
        """
        return get_object_or_404(Adjustment, adjustment_id)

    @staticmethod
    @transactional
    def update_adjustment(adjustment_id: int, data: dict) -> Adjustment:
        """
        更新指定 Adjustment（仅当其状态为 pending 时）

        :param adjustment_id: Adjustment 的 ID
        :param data: 要更新的字段
        :return: 更新后的 Adjustment 对象
        :raises ValueError: 如果 Adjustment 不处于 pending 状态
        """
        adjustment = AdjustmentService.get_adjustment(adjustment_id)
        if adjustment.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Adjustment", 16001)

        adjustment.adjustment_reason = data.get('adjustment_reason', adjustment.adjustment_reason)
        adjustment.status = data.get('status', adjustment.status)
        adjustment.is_active = data.get('is_active', adjustment.is_active)

        # db.session.commit()
        return adjustment

    @staticmethod
    @transactional
    def delete_adjustment(adjustment_id: int):
        """
        删除指定 Adjustment（仅当其状态为 pending 时）
        """
        adjustment = AdjustmentService.get_adjustment(adjustment_id)
        if adjustment.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Adjustment", 16002)

        db.session.delete(adjustment)
        # db.session.commit()

    @staticmethod
    def search_adjustment_details(filters: dict):
        """
        根据过滤条件，返回 AdjustmentDetail 的查询对象。

        :param filters: dict，包含可能的过滤字段
        :return: 一个已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = AdjustmentDetail.query.order_by(AdjustmentDetail.id.desc())

        if filters.get('goods_id'):
            query = query.filter(AdjustmentDetail.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(AdjustmentDetail.location_id == filters['location_id'])

        if any(key in filters for key in ['warehouse_id','warehouse_ids']):
            query = query.join(Adjustment, AdjustmentDetail.adjustment_id == Adjustment.id)      
            
        if filters.get('warehouse_id'):
            query = query.filter(Adjustment.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Adjustment.warehouse_id.in_(filters['warehouse_ids']))       

        return query


    @staticmethod
    def list_adjustment_details(adjustment_id: int):
        """
        获取指定 Adjustment 下所有的 AdjustmentDetail
        """
        adjustment = AdjustmentService.get_adjustment(adjustment_id)
        return adjustment.details

    @staticmethod
    @transactional
    def create_adjustment_detail(adjustment_id: int, data: dict, created_by_id: int) -> AdjustmentDetail:
        """
        创建新的 AdjustmentDetail

        :param adjustment_id: 所属的 Adjustment ID
        :param data: 包含请求中的调整明细数据
        :param created_by_id: 创建者用户 ID
        :return: 新创建的 AdjustmentDetail 对象
        """
        new_detail = AdjustmentDetail(
            adjustment_id=adjustment_id,
            goods_id=data['goods_id'],
            location_id=data.get('location_id'),
            system_quantity=data['system_quantity'],
            actual_quantity=data['actual_quantity'],
            adjustment_quantity=data['adjustment_quantity'],
            remark=data.get('remark')
        )
        db.session.add(new_detail)
        db.session.flush()  # 确保新创建的 AdjustmentDetail ID 可用
        # db.session.commit()
        return new_detail

    @staticmethod
    def get_adjustment_detail(adjustment_id: int, detail_id: int) -> AdjustmentDetail:
        """
        根据 detail_id 获取单个 AdjustmentDetail，并校验其 adjustment_id 是否匹配
        """
        detail = get_object_or_404(AdjustmentDetail, detail_id)
        if detail.adjustment_id != adjustment_id:
            raise NotFoundException(f"AdjustmentDetail (id={detail_id}) not found in Adjustment (id={adjustment_id}).",13001)
        return detail

    @staticmethod
    @transactional
    def update_adjustment_detail(adjustment_id: int, detail_id: int, data: dict) -> AdjustmentDetail:
        """
        更新指定 AdjustmentDetail

        :param adjustment_id: Adjustment 的 ID
        :param detail_id: AdjustmentDetail 的 ID
        :param data: 要更新的字段
        :return: 更新后的 AdjustmentDetail 对象
        """
        detail = AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)

        detail.goods_id = data.get('goods_id', detail.goods_id)
        detail.location_id = data.get('location_id', detail.location_id)
        detail.system_quantity = data.get('system_quantity', detail.system_quantity)
        detail.actual_quantity = data.get('actual_quantity', detail.actual_quantity)
        detail.adjustment_quantity = data.get('adjustment_quantity', detail.adjustment_quantity)
        detail.remark = data.get('remark', detail.remark)

        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_adjustment_detail(adjustment_id: int, detail_id: int):
        """
        删除指定的 AdjustmentDetail
        """
        detail = AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)
        db.session.delete(detail)
        # db.session.commit()

    @staticmethod
    @transactional
    def _update_adjustment_status(adjustment: Adjustment, new_status: str, operator_id: int) -> Adjustment:
        """
        更新 Adjustment 的状态
        """
        if new_status not in Adjustment.AdjustmentStatuses:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        if adjustment.status == 'pending' and new_status == 'approved':
            adjustment.approved_at = datetime.now()
            adjustment.approved_by = operator_id
        if adjustment.status == 'approved' and new_status == 'completed':
            adjustment.completed_at = datetime.now()
            adjustment.operator_id = operator_id
        
        adjustment.status = new_status
        db.session.flush()

        # db.session.commit()
        return adjustment
    
    @staticmethod
    @transactional
    def approve_adjustment(task_or_id: int | Adjustment, operator_id: int) -> Adjustment:
        """
        更新 Adjustment 的状态为 approved
        """
        adjustment = AdjustmentService._get_instance(task_or_id)
        if adjustment.status != 'pending':
            raise BadRequestException("Cannot approve an Adjustment that is not pending", 16018)

        return AdjustmentService._update_adjustment_status(adjustment, 'approved',operator_id)

    @staticmethod
    @transactional
    def complete_adjustment(task_or_id: int | Adjustment, operator_id: int) -> Adjustment:
        """
        更新 Adjustment 的状态为 completed
        """
        adjustment = AdjustmentService._get_instance(task_or_id)
        if adjustment.status != 'approved':
            raise BadRequestException("Cannot complete an Adjustment that is not approved", 16019)

        adjustment = AdjustmentService._update_adjustment_status(adjustment, 'completed',operator_id)

        # 修改库位信息
        for detail in adjustment.details:
            goods_location_record = GoodsLocationService.get_goods_location_record(detail.goods_id, detail.location_id)
            if goods_location_record is None:
                raise NotFoundException(f"GoodsLocation not found for goods_id={detail.goods_id} and location_id={detail.location_id}", 13003)
            goods_location_record.quantity += detail.adjustment_quantity
            
            if goods_location_record.quantity < 0:
                raise BadRequestException(f"Insufficient stock for goods_id={detail.goods_id} in location_id={detail.location_id}", 15002)
            elif goods_location_record.quantity == 0:
                db.session.delete(goods_location_record)
            else:
                db.session.add(goods_location_record)
            db.session.flush()

        # 更新库存
        for detail in adjustment.details:
            InventoryService.update_and_calculate_stock(detail.goods_id, adjustment.warehouse_id)

        return adjustment

    @staticmethod
    @transactional
    def create_adjustment_from_cyclecount(cyclecount_id: int, created_by_id: int) -> Adjustment:
        """
        创建一个新的 Adjustment 任务，并将其与指定的 CycleCount 关联
        """
        cyclecount = CycleCountTaskService.get_task(cyclecount_id)
        
        if len(cyclecount.task_details) == 0:
            raise BadRequestException("CycleCountTask has no details to create an Adjustment", 16016)
        
        # 遍历task_details，删选出difference不为零的记录
        task_details = [detail for detail in cyclecount.task_details if detail.difference != 0]
        if len(task_details) == 0:
            raise BadRequestException("No differences found in CycleCountTask details", 16020)
                
        adjustment_details = []
        for detail in task_details:
            adjustment_detail = AdjustmentDetail(
                goods_id=detail.goods_id,
                location_id=detail.location_id,
                system_quantity=detail.system_quantity,
                actual_quantity=detail.actual_quantity,
                adjustment_quantity=detail.difference
            )
            adjustment_details.append(adjustment_detail)

        adjustment = Adjustment(
            warehouse_id=cyclecount.warehouse_id,
            adjustment_reason="Adjustment by CycleCountTask ID: {}".format(cyclecount_id),
            status='pending',
            is_active=True,
            details=adjustment_details,
            created_by=created_by_id
        )
        db.session.add(adjustment)
        db.session.flush()

        return adjustment
    
    @staticmethod
    def get_cyclecount_monthly_stats(months=6, filters=None):
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
        query = Adjustment.query.with_entities(
            extract('year', Adjustment.created_at).label('year'),
            extract('month', Adjustment.created_at).label('month'),
            func.sum(
                case((Adjustment.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((Adjustment.status == 'approved', 1), else_=0)
            ).label('approved'),
            func.sum(
                case((Adjustment.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            Adjustment.created_at >= start_date,
            Adjustment.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:
            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(Adjustment.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(Adjustment.warehouse_id.in_(filters['warehouse_ids'])) 

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
        status_order = ['pending', 'approved', 'completed']
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
                    db.extract('year', Adjustment.created_at) == target_year,
                    db.extract('month', Adjustment.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = Adjustment.query.with_entities(
            Adjustment.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            Adjustment.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(Adjustment.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(Adjustment.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(Adjustment.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in Adjustment.AdjustmentStatuses]


