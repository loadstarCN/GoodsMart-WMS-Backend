from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.asn.models import ASN
from .models import SortingTask, SortingTaskDetail, SortingTaskStatusLog, SortingBatch
from datetime import datetime, timedelta
from warehouse.asn.services import ASNService
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract

class SortingTaskService:

    # ------------------------------------------------------------------------------------
    # SortingTaskService 私有方法
    # ------------------------------------------------------------------------------------

    @staticmethod
    def _get_instance(task_or_id: int | SortingTask) -> SortingTask:
        """
        根据传入参数返回 SortingTask 实例。
        如果参数为 int，则调用 get_task 获取 SortingTask 实例；
        否则直接返回传入的 SortingTask 实例。
        """
        if isinstance(task_or_id, int):
            return SortingTaskService.get_task(task_or_id)
        return task_or_id
    
    # 状态更新 & 状态日志
    @staticmethod
    @transactional
    def _update_task_status(task: SortingTask, new_status: str, operator_id: int) -> SortingTask:
        """
        更新 SortingTask 的状态，并写入状态变更日志（SortingTaskStatusLog）。
        如有需要，设置 started_at 或 completed_at。
        """
        if new_status not in SortingTask.SORTING_TASK_STATUSES:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        old_status = task.status

        # 状态切换逻辑
        task.status = new_status
        now = datetime.now()
        if old_status == 'pending' and new_status == 'in_progress':
            task.started_at = now
        elif old_status == 'in_progress' and new_status == 'completed':
            task.completed_at = now

        # 写入状态变更日志
        status_log = SortingTaskStatusLog(
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
    
    # ------------------------------------------------------------------------------------
    # SortingTaskService 公共方法
    # ------------------------------------------------------------------------------------


    @staticmethod
    def list_tasks(filters: dict):
        """
        根据过滤条件，返回 SortingTask 的查询对象。
        """
        query = SortingTask.query.order_by(SortingTask.id.desc())

        if filters.get('asn_id'):
            query = query.filter(SortingTask.asn_id == filters['asn_id'])
        if filters.get('status'):
            query = query.filter(SortingTask.status == filters['status'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(SortingTask.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(SortingTask.is_active == filters['is_active'])

        # 搜索ASN.id 或 ASN.tracking_number，或者在detail中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                task_id = int(keyword)
                conditions.append(SortingTask.id == task_id)
                conditions.append(SortingTask.asn_id == task_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 ASN.id 过滤条件
                pass

            conditions.append(SortingTask.task_details.any(SortingTaskDetail.goods.has(code=keyword)))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id') or filters.get('warehouse_ids'):
            query = query.join(ASN, SortingTask.asn_id == ASN.id)

        if filters.get('warehouse_id'):
            query = query.filter(ASN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(ASN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query
    
    @staticmethod
    def get_task(task_id: int) -> SortingTask:
        """
        根据 task_id 获取单个 SortingTask，不存在时抛出 404
        """
        return get_object_or_404(SortingTask, task_id)

    @staticmethod
    @transactional
    def create_task(data: dict, created_by_id: int) -> SortingTask:
        """
        创建新的 Sorting Task
        """
        new_task = SortingTask(
            asn_id=data['asn_id'],
            status=data.get('status', 'pending'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_task)
        # db.session.commit()
        return new_task

    
    @staticmethod
    @transactional
    def update_task(task_id: int, data: dict) -> SortingTask:
        """
        更新指定 SortingTask（仅当其 status 为 pending 时）
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Sorting Task", 16001)

        task.asn_id = data.get('asn_id', task.asn_id)
        task.status = data.get('status', task.status)
        task.is_active = data.get('is_active', task.is_active)

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def delete_task(task_id: int):
        """
        删除指定 SortingTask（仅当其 status 为 pending 时）
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Sorting Task", 16002)

        db.session.delete(task)
        # db.session.commit()

    # ------------------------------------------------------------------------------------
    # TaskDetail 相关
    # ------------------------------------------------------------------------------------
    @staticmethod
    def list_task_details(task_id: int):
        """
        获取指定 SortingTask 下所有的 SortingTaskDetail
        """
        task = SortingTaskService.get_task(task_id)
        return task.task_details

    @staticmethod
    def get_task_detail(task_id: int, detail_id: int) -> SortingTaskDetail:
        """
        根据 detail_id 获取单个 SortingTaskDetail，并校验其 sorting_task_id 是否匹配
        """
        detail = get_object_or_404(SortingTaskDetail, detail_id)
        if detail.sorting_task_id != task_id:
            raise NotFoundException(f"Sorting Task Detail (id={detail_id}) not found in Sorting Task (id={task_id}).", 13001)
        return detail

    @staticmethod
    @transactional
    def create_task_detail(task_id: int, data: dict, created_by_id: int) -> SortingTaskDetail:
        """
        创建新的 SortingTaskDetail（仅当所属的 SortingTask 为 in_progress）。
        
        注意：此时需要在 data 中包含 batch_id 才能正确关联到 SortingBatch
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot create Sorting Task Detail in a non-in-progress Sorting Task", 16009)

        new_detail = SortingTaskDetail(
            sorting_task_id=task_id,
            batch_id=data['batch_id'],               # 必填字段，关联分拣批次
            goods_id=data['goods_id'],
            sorted_quantity=data.get('sorted_quantity', 0),
            damage_quantity=data.get('damage_quantity', 0),
            operator_id=created_by_id
        )
        db.session.add(new_detail)
        # db.session.commit()
        return new_detail

    
    @staticmethod
    @transactional
    def update_task_detail(task_id: int, detail_id: int, data: dict) -> SortingTaskDetail:
        """
        更新指定 SortingTaskDetail（仅当所属的 SortingTask 为 in_progress）。
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update Sorting Task Detail in a non-in-progress Sorting Task", 16010)

        detail = SortingTaskService.get_task_detail(task_id, detail_id)

        # 如果要更新 batch_id，请确保传入
        if 'batch_id' in data:
            detail.batch_id = data['batch_id']
        detail.goods_id = data.get('goods_id', detail.goods_id)
        detail.sorted_quantity = data.get('sorted_quantity', detail.sorted_quantity)
        detail.damage_quantity = data.get('damage_quantity', detail.damage_quantity)

        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_task_detail(task_id: int, detail_id: int):
        """
        删除指定的 SortingTaskDetail（仅当所属的 SortingTask 状态为 in_progress）。
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete Sorting Task Detail in a non-in-progress Sorting Task",16011)

        detail = SortingTaskService.get_task_detail(task_id, detail_id)
        db.session.delete(detail)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # Sorting Batch 相关 (增删改查)
    # -------------------------------------------------------------------------
    @staticmethod
    def list_batches(task_id: int):
        """
        获取指定 SortingTask 下所有的 SortingBatch
        因为 sorting_task.batches 是 lazy='dynamic', 需要 .all() 来获取列表
        """
        task = SortingTaskService.get_task(task_id)
        return task.batches  # 返回列表
    
    @staticmethod
    def get_batch(task_id: int, batch_id: int) -> SortingBatch:
        """
        根据 batch_id 获取单个 SortingBatch，并验证其 sorting_task_id 是否匹配
        """
        batch = get_object_or_404(SortingBatch, batch_id)
        if batch.sorting_task_id != task_id:
            raise NotFoundException(f"Batch (id={batch_id}) not found in Task (id={task_id}).", 13001)
        return batch
    
    @staticmethod
    @transactional
    def create_batch(task_or_id: int | SortingTask, data: dict, operator_id: int):
        """
        同时适用于：
        1) 只创建批次
        2) 创建批次并批量添加 SortingTaskDetail

        统一规则： 仅当 SortingTask.status == 'in_progress' 才允许操作。
        
        data 示例:
            {
                "operation_time": "2025-02-01T08:00:00",  # 可选
                "remark": "备注信息",                     # 可选
                "details": [
                    {
                        "goods_id": 1,
                        "sorted_quantity": 10,
                        "damage_quantity": 1,
                        ...
                    },
                    ...
                ]
            }
        如果 data 内部没有 "details" 或其为空数组，表示仅创建批次。
        返回: (batch, [list_of_details]) 
        """

        print(data)

        task = SortingTaskService._get_instance(task_or_id)
        # 统一只允许在 in_progress 状态创建（批次 + 明细）
        if task.status != 'in_progress':
            raise BadRequestException("Cannot create a batch or details in a non-in-progress Sorting Task", 16012)

        op_time = data.get('operation_time')
        if isinstance(op_time, str):
            # 如果确认是 ISO8601
            op_time = datetime.fromisoformat(op_time)
            # 否则使用 datetime.strptime(op_time, '%Y-%m-%dT%H:%M:%S') 等

        # 1) 创建批次
        new_batch = SortingBatch(
            sorting_task_id=task.id,
            operator_id=operator_id,
            operation_time=op_time or datetime.now(),
            remark=data.get('remark', '')
        )
        db.session.add(new_batch)
        db.session.flush()  # 为了获取新批次的 ID

        # 2) 如果有 details，就批量创建 detail
        details_data = data.get("details", [])
        # 2. 强制校验类型（若存在且非列表则报错）
        if details_data is not None and not isinstance(details_data, list):
            raise BadRequestException("'details' must be a list (empty is allowed)", 16015)

        for item in details_data:
            detail_obj = SortingTaskDetail(
                sorting_task_id=task.id,
                batch_id=new_batch.id,  
                goods_id=item['goods_id'],
                sorted_quantity=item.get('sorted_quantity', 0),
                damage_quantity=item.get('damage_quantity', 0),
                operator_id=operator_id
            )
            db.session.add(detail_obj)

        # 3) 一次性提交
        # db.session.commit()

        # 返回批次对象和所创建的明细列表
        return new_batch


    

    @staticmethod
    @transactional
    def update_batch(task_id: int, batch_id: int, data: dict) -> SortingBatch:
        """
        更新已有 SortingBatch
        - 仅当 SortingTask 处于 in_progress 时允许更新
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update a batch in a non-in-progress Sorting Task", 16013)

        batch = SortingTaskService.get_batch(task_id, batch_id)

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
        删除指定的 SortingBatch
        - 仅当 SortingTask 处于 in_progress 时允许删除
        - 若已产生明细（SortingTaskDetail），可视业务决定是否禁止删除或自动删除
        """
        task = SortingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete a batch in a non-in-progress Sorting Task", 16014)

        batch = SortingTaskService.get_batch(task_id, batch_id)
        db.session.delete(batch)
        # db.session.commit()

   
    @staticmethod
    @transactional
    def process_task(task_or_id: int | SortingTask, operator_id: int) -> SortingTask:
        """
        更新 SortingTask 的状态为 in_progress
        """
        task = SortingTaskService._get_instance(task_or_id)

        if task.status != 'pending':
            raise BadRequestException("Cannot process a non-pending Sorting Task", 16007)
        
        task = SortingTaskService._update_task_status(task, 'in_progress', operator_id)
        return task


    @staticmethod
    @transactional
    def complete_task(task_or_id: int | SortingTask, operator_id: int) -> SortingTask:
        """
        更新 SortingTask 的状态为 completed
        """
        task = SortingTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in_progress Sorting Task", 16008)

        task = SortingTaskService._update_task_status(task, 'completed', operator_id)

        # 更新 ASN Detail 的数量字段
        ASNService.complete_asn(task.asn)
                    
        return task

    @staticmethod
    @transactional
    def create_sorting_task_from_asn(asn_id: int) -> SortingTask:
        """
        根据 ASN 及其明细自动生成一个 Sorting Task。
        （本示例只生成主 Task，不自动生成 SortingTaskDetail。）
        """
        asn = ASNService.get_asn(asn_id)
        if not asn.details:
            raise BadRequestException("ASN has no details to create a Sorting Task.", 16016)

        sorting_task = SortingTask(
            asn_id=asn_id,
            status='pending',
            created_by=asn.created_by
        )
        db.session.add(sorting_task)
        # db.session.commit()
        return sorting_task
    
    @staticmethod
    def get_sorting_monthly_stats(months=6, filters=None):
        """获取最近N个月各状态Sorting统计（支持仓库过滤）
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
        query = SortingTask.query.with_entities(
            extract('year', SortingTask.created_at).label('year'),
            extract('month', SortingTask.created_at).label('month'),
            func.sum(
                case((SortingTask.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((SortingTask.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((SortingTask.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            SortingTask.created_at >= start_date,
            SortingTask.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:

            if filters.get('sorting_type'):
                query = query.filter(SortingTask.sorting_type == filters['sorting_type'])

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(ASN, SortingTask.asn_id == ASN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(ASN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(ASN.warehouse_id.in_(filters['warehouse_ids'])) 

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
                    db.extract('year', SortingTask.created_at) == target_year,
                    db.extract('month', SortingTask.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = SortingTask.query.with_entities(
            SortingTask.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            SortingTask.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('sorting_type'):
                query = query.filter(SortingTask.sorting_type == filters['sorting_type'])

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(ASN, SortingTask.asn_id == ASN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(ASN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(ASN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(SortingTask.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in SortingTask.SORTING_TASK_STATUSES]
