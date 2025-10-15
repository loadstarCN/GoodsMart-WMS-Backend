from werkzeug.exceptions import NotFound
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from .models import (
    CycleCountTask, 
    CycleCountTaskDetail, 
    CycleCountTaskStatusLog
)
from warehouse.goods.services import GoodsLocationService, GoodsService
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta

class CycleCountTaskService:

    @staticmethod
    def _get_instance(task_or_id: int | CycleCountTask) -> CycleCountTask:
        """
        根据传入参数返回 PickingTask 实例。
        如果参数为 int，则调用 get_task 获取 PickingTask 实例；
        否则直接返回传入的 PickingTask 实例。
        """
        if isinstance(task_or_id, int):
            return CycleCountTaskService.get_task(task_or_id)
        return task_or_id

    @staticmethod
    def list_tasks(filters: dict):
        """
        根据过滤条件，返回 CycleCountTask 的查询对象。
        """
        query = CycleCountTask.query.order_by(CycleCountTask.id.desc())

        if filters.get('task_name'):
            query = query.filter(CycleCountTask.task_name.ilike(f"%{filters['task_name']}%"))
        if filters.get('status'):
            query = query.filter(CycleCountTask.status == filters['status'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(CycleCountTask.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(CycleCountTask.is_active == filters['is_active'])

        if filters.get('scheduled_start_date'):
            query = query.filter(CycleCountTask.scheduled_date >= filters['scheduled_start_date'])
        if filters.get('scheduled_end_date'):
            query = query.filter(CycleCountTask.scheduled_date <= filters['scheduled_end_date'])

        # 搜索id，task_name，在detail中的goods.code，在detail中的location.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                task_id = int(keyword)
                conditions.append(CycleCountTask.id == task_id)
            except ValueError:
                pass
            conditions.append(CycleCountTask.task_name.ilike(f"%{keyword}%"))
            conditions.append(CycleCountTask.task_details.any(CycleCountTaskDetail.goods.has(code=keyword)))
            conditions.append(CycleCountTask.task_details.any(CycleCountTaskDetail.location.has(code=keyword)))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id'):
            query = query.filter(CycleCountTask.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(CycleCountTask.warehouse_id.in_(filters['warehouse_ids']))

        return query

    @staticmethod
    @transactional
    def create_task(data: dict, created_by_id: int) -> CycleCountTask:
        """
        创建新的 Cycle Count Task（仅创建主任务，不带明细）。
        """
        new_task = CycleCountTask(
            task_name=data['task_name'],
            warehouse_id=data['warehouse_id'],
            scheduled_date=data.get('scheduled_date'),
            status=data.get('status', 'pending'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )

        if 'task_details' in data:
            for detail_data in data['task_details']:
                new_detail = CycleCountTaskDetail(
                    goods_id=detail_data['goods_id'],
                    location_id=detail_data['location_id'],
                    actual_quantity=0
                )
                new_task.task_details.append(new_detail)

        db.session.add(new_task)
        # db.session.commit()
        return new_task

    @staticmethod
    def get_task(task_id: int) -> CycleCountTask:
        """
        根据 task_id 获取单个 CycleCountTask，不存在时抛出 404
        """
        return get_object_or_404(CycleCountTask, task_id)

    @staticmethod
    @transactional
    def update_task(task_id: int, data: dict) -> CycleCountTask:
        """
        更新指定 CycleCountTask（仅当其状态为 pending 时）
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot update a non-pending CycleCountTask", 16001)

        task.task_name = data.get('task_name', task.task_name)
        task.scheduled_date = data.get('scheduled_date', task.scheduled_date)
        task.status = data.get('status', task.status)
        task.is_active = data.get('is_active', task.is_active)

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def delete_task(task_id: int):
        """
        删除指定 CycleCountTask（仅当其状态为 pending 时）
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending CycleCountTask", 16002)

        db.session.delete(task)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # Task Detail 相关
    # -------------------------------------------------------------------------
    def search_task_details(filters:dict):
        """
        根据过滤条件，返回 CycleCountTaskDetail 的查询对象。
        """
        query = CycleCountTaskDetail.query.order_by(CycleCountTaskDetail.id.desc())


        if filters.get('goods_id'):
            query = query.filter(CycleCountTaskDetail.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(CycleCountTaskDetail.location_id == filters['location_id'])
        if filters.get('operator_id'):
            query = query.filter(CycleCountTaskDetail.operator_id == filters['operator_id'])
        if filters.get('status'):
            query = query.filter(CycleCountTaskDetail.status == filters['status'])

        if any(key in filters for key in ['warehouse_id','warehouse_ids']):
            query = query.join(CycleCountTask, CycleCountTaskDetail.task_id == CycleCountTask.id)
            
        if filters.get('warehouse_id'):
            query = query.filter(CycleCountTask.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(CycleCountTask.warehouse_id.in_(filters['warehouse_ids']))      

        return query 



    @staticmethod
    def list_task_details(task_id: int):
        """
        获取指定 CycleCountTask 下所有的 CycleCountTaskDetail
        """
        task = CycleCountTaskService.get_task(task_id)
        return task.task_details

    @staticmethod
    @transactional
    def create_task_detail(task_id: int, data: dict, created_by_id: int) -> CycleCountTaskDetail:
        """
        创建新的 CycleCountTaskDetail（仅当所属的 CycleCountTask 为 pending）
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot add CycleCountTaskDetail to a non-pending CycleCountTask", 16003)

        new_detail = CycleCountTaskDetail(
            task_id=task_id,
            goods_id=data['goods_id'],
            location_id=data['location_id'],
            actual_quantity=0
        )
        db.session.add(new_detail)
        # db.session.commit()
        print(new_detail)
        return new_detail

    @staticmethod
    def get_task_detail(task_id: int, detail_id: int) -> CycleCountTaskDetail:
        """
        根据 detail_id 获取单个 CycleCountTaskDetail，并校验其 task_id 是否匹配
        """
        detail = get_object_or_404(CycleCountTaskDetail, detail_id)
        if detail.task_id != task_id:
            raise NotFoundException(
                f"CycleCountTaskDetail (id={detail_id}) not found in CycleCountTask (id={task_id}).", 13001
            )
        return detail

    @staticmethod
    @transactional
    def update_task_detail(task_id: int, detail_id: int, data: dict) -> CycleCountTaskDetail:
        """
        更新指定 CycleCountTaskDetail（仅当所属的 CycleCountTask 为 pending）
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot update detail in a non-pending CycleCountTask", 16005)

        detail = CycleCountTaskService.get_task_detail(task_id, detail_id)

        # 获取库存数据，计算差额
        # goods_in_location_count = GoodsLocationService.get_quantity(detail.goods_id, detail.location_id)

        detail.goods_id = data.get('goods_id', detail.goods_id)
        detail.location_id = data.get('location_id', detail.location_id)
        detail.actual_quantity = data.get('actual_quantity', detail.actual_quantity)
        # detail.difference = detail.actual_quantity - detail.system_quantity
        detail.operator_id = data.get('operator_id', detail.operator_id)
        detail.status = data.get('status', detail.status)

        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_task_detail(task_id: int, detail_id: int):
        """
        删除指定的 CycleCountTaskDetail（仅当所属的 CycleCountTask 状态为 pending）
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot delete detail from a non-pending CycleCountTask", 16005)

        detail = CycleCountTaskService.get_task_detail(task_id, detail_id)
        db.session.delete(detail)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # 状态流转 & 状态日志（与 SortingTask 对齐）
    # -------------------------------------------------------------------------
    @staticmethod
    @transactional
    def _update_task_status(task: CycleCountTask, new_status: str, operator_id: int) -> CycleCountTask:
        """
        内部方法：更新 CycleCountTask 的状态，记录状态变更日志，并维护 started_at/completed_at。
        """
        if new_status not in CycleCountTask.CYCLE_COUNT_TASK_STATUSES:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        old_status = task.status
        now = datetime.now()

        # 设置新状态
        task.status = new_status

        # 若 pending -> in_progress，则记录 started_at
        if old_status == 'pending' and new_status == 'in_progress':
            task.started_at = now
            # 遍历task_detail，获取goodslocation的数量更新到task_detail中的system_quantity
            for detail in task.task_details:
                goods_location = GoodsLocationService.get_quantity(detail.goods_id, detail.location_id)
                detail.system_quantity = goods_location
                # 计算差异
                detail.difference = detail.actual_quantity - goods_location

        # 若 in_progress -> completed，则记录 completed_at
        elif old_status == 'in_progress' and new_status == 'completed':
            task.completed_at = now

        # 写入状态变更日志
        status_log = CycleCountTaskStatusLog(
            task_id=task.id,
            old_status=old_status,
            new_status=new_status,
            operator_id=operator_id,
            changed_at=now
        )
        db.session.add(status_log)

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def process_task(task_or_id: int | CycleCountTask, operator_id: int) -> CycleCountTask:
        """
        将CycleCountTask从 pending 切换到 in_progress
        """
        task = CycleCountTaskService._get_instance(task_or_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot process a non-pending CycleCountTask", 16007)

        return CycleCountTaskService._update_task_status(task, 'in_progress', operator_id)

    @staticmethod
    @transactional
    def complete_task(task_or_id: int | CycleCountTask, operator_id: int) -> CycleCountTask:
        """
        将CycleCountTask从 in_progress 切换到 completed
        """
        task = CycleCountTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in_progress CycleCountTask", 16008)

        return CycleCountTaskService._update_task_status(task, 'completed', operator_id)

    # -------------------------------------------------------------------------
    # 若需要对明细也做状态日志，可以增加类似的方法:
    # -------------------------------------------------------------------------
    # 1. 私有方法 _update_task_detail_status
    @staticmethod
    @transactional
    def _update_task_detail_status(detail: CycleCountTaskDetail, new_status: str, operator_id: int) -> CycleCountTaskDetail:
        """
        内部方法：更新 CycleCountTaskDetail 的状态并写入日志 (如果你在 Detail 也有状态日志表)。
        这里仅示范，若需要在 detail 层面也维护日志，请新建类似 'CycleCountTaskDetailStatusLog' 模型。
        """
        if new_status not in CycleCountTaskDetail.CYCLE_COUNT_TASK_DETAIL_STATUSES:
            raise BadRequestException(f"Invalid detail status value: {new_status}", 14001)

        old_status = detail.status
        now = datetime.now()
        
        detail.status = new_status
        detail.operator_id = operator_id  # 也可记录最后一次操作人
        if old_status == 'pending' and new_status == 'completed':
            detail.completed_at = now

        # 如果你有 CycleCountTaskDetailStatusLog 表，可以在这里插入一条记录
        # log = CycleCountTaskDetailStatusLog(
        #    detail_id=detail.id,
        #    old_status=old_status,
        #    new_status=new_status,
        #    operator_id=operator_id,
        #    changed_at=now
        # )
        # db.session.add(log)

        # db.session.commit()
        return detail

    # 2. 公共方法 complete_task_detail
    @staticmethod
    @transactional
    def complete_task_detail(task_id: int, detail_id: int, operator_id: int) -> CycleCountTaskDetail:
        """
        将CycleCountTaskDetail从 pending 切换到 completed
        """
        # 先检查父任务
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete detail in a task that is not in_progress", 16008)

        detail = CycleCountTaskService.get_task_detail(task_id, detail_id)
        if detail.status == 'completed':
            raise BadRequestException("Detail is already completed", 16017)

        return CycleCountTaskService._update_task_detail_status(detail, 'completed', operator_id)
    
    @staticmethod
    @transactional
    def batch_save_task_details(task_id: int, details: list, operator_id: int) -> CycleCountTask:
        """
        批量保存 CycleCountTaskDetail
        """
        task = CycleCountTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update details in a non-in_progress CycleCountTask", 16010)
        
        for detail_data in details:
            detail = CycleCountTaskService.get_task_detail(task_id, detail_data['id'])
            detail.system_quantity = detail.system_quantity or 0  # 将 None 转为 0
            detail.actual_quantity = int(detail_data.get('actual_quantity', 0))  # 默认值为 0
            detail.operator_id = operator_id
            detail.difference = detail.actual_quantity - detail.system_quantity

        # db.session.commit()
        return task

    # -------------------------------------------------------------------------
    # 其他辅助方法
    # -------------------------------------------------------------------------
    @staticmethod
    @transactional
    def create_cycle_count_tasks_from_goods_list(goods_ids: list, warehouse_id: int, created_by_id: int) -> CycleCountTask:
        """
        示例：根据一组 goods_id 自动创建一个 CycleCountTask，并生成对应的明细。
        """
        task = CycleCountTaskService.create_task({
            'task_name': f"Cycle Count Task - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            'warehouse_id': warehouse_id,
            'status': 'pending',
            'is_active': True
        }, created_by_id=created_by_id)

        details = []
        for goods_id in goods_ids:
            goods = get_object_or_404(Goods, goods_id)

            # 如果你有 'goods.locations' 结构，需要过滤出同一warehouse的货位
            for storage_record in goods.storage_records:
                if storage_record.location.warehouse_id != warehouse_id:
                    continue
                location_id = storage_record.location_id

                # 创建明细
                detail_obj = CycleCountTaskService.create_task_detail(
                    task.id,
                    {
                        'goods_id': goods_id,
                        'location_id': location_id,
                        'actual_quantity': 0
                    },
                    created_by_id=created_by_id
                )
                details.append(detail_obj)

        task.task_details = details
        db.session.add(task)
        # db.session.commit()

        return task
    

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
        query = CycleCountTask.query.with_entities(
            extract('year', CycleCountTask.created_at).label('year'),
            extract('month', CycleCountTask.created_at).label('month'),
            func.sum(
                case((CycleCountTask.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((CycleCountTask.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((CycleCountTask.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            CycleCountTask.created_at >= start_date,
            CycleCountTask.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:
            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(CycleCountTask.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(CycleCountTask.warehouse_id.in_(filters['warehouse_ids'])) 

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
                    db.extract('year', CycleCountTask.created_at) == target_year,
                    db.extract('month', CycleCountTask.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = CycleCountTask.query.with_entities(
            CycleCountTask.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            CycleCountTask.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(CycleCountTask.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(CycleCountTask.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(CycleCountTask.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in CycleCountTask.CYCLE_COUNT_TASK_STATUSES]

