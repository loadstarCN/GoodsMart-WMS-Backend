from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.dn.models import DN
from .models import (
    PackingTask, 
    PackingTaskDetail, 
    PackingBatch, 
    PackingTaskStatusLog
)

from warehouse.dn.services import DNService
from warehouse.inventory.services import InventoryService
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta

class PackingTaskService:

    # -------------------------------------------------------------------------
    # 1. 基础查询 & CRUD
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_instance(task_or_id: int | PackingTask) -> PackingTask:
        """
        根据传入参数返回 PackingTask 实例。
        如果参数为 int，则调用 get_task 获取 PackingTask 实例；
        否则直接返回传入的 PackingTask 实例。
        """
        if isinstance(task_or_id, int):
            return PackingTaskService.get_task(task_or_id)
        return task_or_id


    @staticmethod
    def list_tasks(filters: dict):
        """
        根据过滤条件，返回 PackingTask 的查询对象。
        """
        query = PackingTask.query.order_by(PackingTask.id.desc())

        if filters.get('dn_id'):
            query = query.filter(PackingTask.dn_id == filters['dn_id'])
        if filters.get('status'):
            query = query.filter(PackingTask.status == filters['status'])
        
        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(PackingTask.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(PackingTask.is_active == filters['is_active'])

        # 搜索id,DN.id ，或者在detail中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                task_id = int(keyword)
                conditions.append(PackingTask.id == task_id)
                conditions.append(PackingTask.dn_id == task_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 DN.id 过滤条件
                pass

            conditions.append(PackingTask.task_details.any(PackingTaskDetail.goods.has(code=keyword)))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id') or filters.get('warehouse_ids'):
            query = query.join(DN, PackingTask.dn_id == DN.id)

        if filters.get('warehouse_id'):
            query = query.filter(DN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query

    @staticmethod
    @transactional
    def create_task(data: dict, created_by_id: int) -> PackingTask:
        """
        创建新的 Packing Task
        """
        new_task = PackingTask(
            dn_id=data['dn_id'],
            status=data.get('status', 'pending'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_task)
        # db.session.commit()
        return new_task

    @staticmethod
    def get_task(task_id: int) -> PackingTask:
        """
        根据 task_id 获取单个 PackingTask，不存在时抛出 404
        """
        return get_object_or_404(PackingTask, task_id)

    @staticmethod
    @transactional
    def update_task(task_id: int, data: dict) -> PackingTask:
        """
        更新指定 PackingTask（仅当其 status 为 pending 时）
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Packing Task", 16001)

        task.dn_id = data.get('dn_id', task.dn_id)
        task.status = data.get('status', task.status)
        task.is_active = data.get('is_active', task.is_active)

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def delete_task(task_id: int):
        """
        删除指定 PackingTask（仅当其 status 为 pending 时）
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Packing Task", 16002)

        db.session.delete(task)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # 2. Packing Task Detail 相关 (增删改查)
    # -------------------------------------------------------------------------
    @staticmethod
    def list_task_details(task_id: int):
        """
        获取指定 PackingTask 下所有的 PackingTaskDetail
        """
        task = PackingTaskService.get_task(task_id)
        return task.task_details

    @staticmethod
    @transactional
    def create_task_detail(task_id: int, data: dict, operator_id: int) -> PackingTaskDetail:
        """
        创建新的 PackingTaskDetail（仅当所属的 PackingTask 为 pending）
        
        如果业务需要绑定到某个 batch_id，也可在 data 中添加 batch_id，
        并在这里进行赋值： `batch_id=data['batch_id']` (若已定义非 nullable)
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot add Packing Task Detail to a non-pending Packing Task", 16003)

        new_detail = PackingTaskDetail(
            packing_task_id=task_id,
            # 如果你的模型里 batch_id 是非空，需要这里指定:
            # batch_id=data['batch_id'],
            goods_id=data['goods_id'],
            packed_quantity=data.get('packed_quantity', 0),
            packing_time=data.get('packing_time', datetime.now()),
            operator_id=operator_id
        )
        db.session.add(new_detail)
        # db.session.commit()
        return new_detail

    @staticmethod
    def get_task_detail(task_id: int, detail_id: int) -> PackingTaskDetail:
        """
        根据 detail_id 获取单个 PackingTaskDetail，并校验其 packing_task_id 是否匹配
        """
        detail = get_object_or_404(PackingTaskDetail, detail_id)
        if detail.packing_task_id != task_id:
            raise NotFoundException(f"Packing Task Detail (id={detail_id}) not found in Packing Task (id={task_id}).", 13001)
        return detail

    @staticmethod
    @transactional
    def update_task_detail(task_id: int, detail_id: int, data: dict) -> PackingTaskDetail:
        """
        更新指定 PackingTaskDetail（仅当所属的 PackingTask 为 in_progress
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update Packing Task Detail in a non-in_progress Packing Task", 16010)

        detail = PackingTaskService.get_task_detail(task_id, detail_id)
        # 如果你的模型里 batch_id 是非空，需要这里更新:
        if 'batch_id' in data:
            detail.batch_id = data['batch_id']
        detail.goods_id = data.get('goods_id', detail.goods_id)
        detail.packed_quantity = data.get('packed_quantity', detail.packed_quantity)
        detail.packing_time = data.get('packing_time', detail.packing_time)
        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_task_detail(task_id: int, detail_id: int):
        """
        删除指定的 PackingTaskDetail（仅当所属的 PackingTask 状态为 in_progress
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete Packing Task Detail from a non-in_progress Packing Task", 16011)

        detail = PackingTaskService.get_task_detail(task_id, detail_id)
        db.session.delete(detail)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # 3. Packing Batch 相关 (增删改查)
    # -------------------------------------------------------------------------
    @staticmethod
    def list_batches(task_id: int):
        """
        获取指定 PackingTask 下所有的 PackingBatch
        """
        task = PackingTaskService.get_task(task_id)
        return task.batches  # 或者再加 .all()，取决于你的 relationship lazy 属性

    @staticmethod
    @transactional
    def create_batch(task_or_id: int | PackingTask, data: dict, operator_id: int):
        """
        同时适用于：
        1) 只创建批次
        2) 创建批次并批量添加 PackingTaskDetail

        统一规则：仅当 PackingTask.status == 'in_progress' 才允许操作。
        
        data 示例:
            {
                "packing_task_id":<int:task_id>,
                "operation_time": "2025-02-01T08:00:00",
                "remark": "备注信息",
                "details": [
                    {
                        "goods_id": 123,
                        "packed_quantity": 10,
                        "packing_time": "2025-02-01T09:30:00"
                        ...
                    },
                    ...
                ]
            }
        """
        task = PackingTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot create a batch or details in a non-in-progress Packing Task", 16009)

        op_time = data.get('operation_time')
        if isinstance(op_time, str):
            # 假设你的日期字符串是标准ISO8601
            op_time = datetime.fromisoformat(op_time)

        # 1) 创建批次
        new_batch = PackingBatch(
            packing_task_id=task.id,
            operator_id=operator_id,
            operation_time=op_time or datetime.now(),
            remark=data.get('remark', '')
        )
        db.session.add(new_batch)
        # 使用 flush() 以获取 new_batch.id
        db.session.flush()

        # 2) 如果有 details，就批量创建
        details_data = data.get('details')
        if details_data:
            if not isinstance(details_data, list):
                raise BadRequestException("'details' must be a list if provided", 16015)

            for item in details_data:
                detail = PackingTaskDetail(
                    packing_task_id=task.id,
                    batch_id=new_batch.id,
                    goods_id=item['goods_id'],
                    packed_quantity=item.get('packed_quantity', 0),
                    packing_time=item.get('packing_time', datetime.now()),
                    operator_id=operator_id
                )
                db.session.add(detail)
        
        # db.session.commit()
        return new_batch

    @staticmethod
    def get_batch(task_id: int, batch_id: int) -> PackingBatch:
        """
        根据 batch_id 获取单个 PackingBatch，并验证其 packing_task_id 是否匹配
        """
        batch = get_object_or_404(PackingBatch, batch_id)
        if batch.packing_task_id != task_id:
            raise NotFoundException(f"PackingBatch (id={batch_id}) not found in Packing Task (id={task_id}).", 13001)
        return batch

    @staticmethod
    @transactional
    def update_batch(task_id: int, batch_id: int, data: dict) -> PackingBatch:
        """
        更新已有 PackingBatch
        - 仅当 PackingTask 处于 in_progress 时允许更新
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update a batch in a non-in_progress Packing Task", 16013)

        batch = PackingTaskService.get_batch(task_id, batch_id)

        if 'operation_time' in data:
            batch.operation_time = data['operation_time']
        if 'remark' in data:
            batch.remark = data['remark']

        # db.session.commit()
        return batch

    @staticmethod
    @transactional
    def delete_batch(task_id: int, batch_id: int):
        """
        删除指定的 PackingBatch
        - 仅当 PackingTask.status == 'in_progress' 时允许删除
        - 若已产生明细（PackingTaskDetail），可视业务决定是否禁止删除或自动删除
        """
        task = PackingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete a batch in a non-in_progress Packing Task", 16014)

        batch = PackingTaskService.get_batch(task_id, batch_id)
        db.session.delete(batch)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # 4. 状态流转 & 状态日志
    # -------------------------------------------------------------------------
    @staticmethod
    @transactional
    def _update_task_status(task: PackingTask, new_status: str, operator_id: int) -> PackingTask:
        """
        更新 PackingTask 的状态，并写入状态变更日志（PackingTaskStatusLog）。
        如有需要，设置 started_at 或 completed_at。
        """
        if new_status not in PackingTask.PACKING_TASK_STATUSES:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        old_status = task.status
        now = datetime.now()

        task.status = new_status
        if old_status == 'pending' and new_status == 'in_progress':
            task.started_at = now
        elif old_status == 'in_progress' and new_status == 'completed':
            task.completed_at = now

        # 写入状态变更日志
        status_log = PackingTaskStatusLog(
            task_id=task.id,
            old_status=old_status,
            new_status=new_status,
            operator_id=operator_id,
            changed_at=now
        )
        db.session.add(status_log)
        db.session.flush()

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def process_task(task_or_id: int | PackingTask, operator_id: int) -> PackingTask:
        """
        将 PackingTask 从 pending 切换为 in_progress
        """
        task = PackingTaskService._get_instance(task_or_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot process a non-pending Packing Task", 16007)

        return PackingTaskService._update_task_status(task, 'in_progress', operator_id)

    @staticmethod
    @transactional
    def complete_task(task_or_id: int | PackingTask, operator_id: int) -> PackingTask:
        """
        将 PackingTask 从 in_progress 切换为 completed，并更新库存
        """
        task = PackingTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in_progress Packing Task", 16008)
        
        task = PackingTaskService._update_task_status(task, 'completed', operator_id)

        # 更新DN状态
        DNService.packing_dn(task.dn)

        return task

    # -------------------------------------------------------------------------
    # 5. 其他辅助方法
    # -------------------------------------------------------------------------
    @staticmethod
    @transactional
    def create_packing_task_from_dn(dn_id: int, created_by_id: int) -> PackingTask:
        """
        根据 DN 自动生成一个 Packing Task。
        """
        dn = DNService.get_dn(dn_id)
        if not dn.details:
            raise BadRequestException("DN has no details to create a Packing Task.", 16016)

        packing_task = PackingTask(
            dn_id=dn_id,
            status='pending',
            created_by=created_by_id
        )
        db.session.add(packing_task)
        
        # db.session.commit()
        return packing_task
    
    @staticmethod
    def get_packing_monthly_stats(months=6, filters=None):
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
        query = PackingTask.query.with_entities(
            extract('year', PackingTask.created_at).label('year'),
            extract('month', PackingTask.created_at).label('month'),
            func.sum(
                case((PackingTask.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((PackingTask.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((PackingTask.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            PackingTask.created_at >= start_date,
            PackingTask.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, PackingTask.dn_id == DN.id)

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
                    db.extract('year', PackingTask.created_at) == target_year,
                    db.extract('month', PackingTask.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = PackingTask.query.with_entities(
            PackingTask.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            PackingTask.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('sorting_type'):
                query = query.filter(PackingTask.sorting_type == filters['sorting_type'])

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, PackingTask.dn_id == DN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(DN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(PackingTask.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in PackingTask.PACKING_TASK_STATUSES]