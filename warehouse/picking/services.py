from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from warehouse.common import require_positive_int, require_bulk_list, lock_goods_location
from warehouse.dn.models import DN
from warehouse.dn.services import DNService, pick_fields, parse_datetime_value
from warehouse.location.models import Location
from warehouse.removal.services import RemovalService
from .models import PickingTask, PickingTaskDetail, PickingTaskStatusLog,PickingBatch
from extensions.transaction import transactional
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from sqlalchemy.orm import selectinload, joinedload
from datetime import datetime, timedelta

class PickingTaskService:

    @staticmethod
    def _get_instance(task_or_id: int | PickingTask) -> PickingTask:
        """
        根据传入参数返回 PickingTask 实例。
        如果参数为 int，则调用 get_task 获取 PickingTask 实例；
        否则直接返回传入的 PickingTask 实例。
        """
        if isinstance(task_or_id, int):
            return PickingTaskService.get_task(task_or_id)
        return task_or_id

    @staticmethod
    def list_tasks(filters: dict):
        """
        根据过滤条件，返回 PickingTask 的查询对象。
        """
        # 列表 schema 会逐行汇总 task_details / dn.details，这里一次性预加载，避免 N+1
        query = (
            PickingTask.query
            .options(
                selectinload(PickingTask.task_details),
                joinedload(PickingTask.dn).selectinload(DN.details),
            )
            .order_by(PickingTask.id.desc())
        )

        if filters.get('dn_id'):
            query = query.filter(PickingTask.dn_id == filters['dn_id'])
        if filters.get('status'):
            query = query.filter(PickingTask.status == filters['status'])
        
        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(PickingTask.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(PickingTask.is_active == filters['is_active'])

        # 搜索id,DN.id ，或者在detail中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                task_id = int(keyword)
                conditions.append(PickingTask.id == task_id)
                conditions.append(PickingTask.dn_id == task_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 DN.id 过滤条件
                pass

            conditions.append(PickingTask.task_details.any(PickingTaskDetail.goods.has(code=keyword)))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id') or filters.get('warehouse_ids'):
            query = query.join(DN, PickingTask.dn_id == DN.id)

        if filters.get('warehouse_id'):
            query = query.filter(DN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query

    @staticmethod
    @transactional
    def create_task(data: dict, created_by_id: int) -> PickingTask:
        """
        创建新的 Picking Task。只接受 dn_id；status 固定 pending、is_active 固定 True，
        状态只能经 process / complete 流转。
        """
        payload = pick_fields(data, ('dn_id',))
        dn_id = require_positive_int(payload.get('dn_id'), 'dn_id', 16044)
        get_object_or_404(DN, dn_id)

        new_task = PickingTask(
            dn_id=dn_id,
            status='pending',
            is_active=True,
            created_by=created_by_id
        )
        db.session.add(new_task)
        # db.session.commit()
        return new_task

    @staticmethod
    def get_task(task_id: int) -> PickingTask:
        """
        根据 task_id 获取单个 PickingTask，不存在时抛出 404
        """
        return get_object_or_404(PickingTask, task_id)

    @staticmethod
    @transactional
    def update_task(task_id: int, data: dict) -> PickingTask:
        """
        更新指定 PickingTask（仅当其 status 为 pending 时）
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Picking Task", 16001)

        # PickingTask 没有可由客户端编辑的业务字段：dn_id 不可改，
        # status 只经 process / complete 流转，is_active / created_by 忽略。
        pick_fields(data, ())

        # db.session.commit()
        return task

    @staticmethod
    @transactional
    def delete_task(task_id: int):
        """
        删除指定 PickingTask（仅当其 status 为 pending 时）
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Picking Task", 16002)

        db.session.delete(task)
        # db.session.commit()

    # ---------------------------------------------------------------------------------
    # Picking Task Detail 相关
    # ---------------------------------------------------------------------------------
    @staticmethod
    def list_task_details(task_id: int):
        """
        获取指定 PickingTask 下所有的 PickingTaskDetail
        """
        task = PickingTaskService.get_task(task_id)
        return task.task_details

    @staticmethod
    def get_task_detail(task_id: int, detail_id: int) -> PickingTaskDetail:
        """
        根据 detail_id 获取单个 PickingTaskDetail，并校验其 picking_task_id 是否匹配
        """
        detail = get_object_or_404(PickingTaskDetail, detail_id)
        if detail.picking_task_id != task_id:
            raise NotFoundException(f"Picking Task Detail (id={detail_id}) not found in Picking Task (id={task_id}).", 13001)
        return detail

    @staticmethod
    @transactional
    def update_task_detail(task_id: int, detail_id: int, data: dict) -> PickingTaskDetail:
        """
        更新指定 PickingTaskDetail（仅当所属的 PickingTask 为 in_progress）。
        只允许改 picked_quantity：location_id / goods_id / batch_id 一经批次创建即固定，
        传入不同的值直接 400（否则可以把明细挪到别的仓库的库位，complete 时跨仓扣库存）。
        增加的数量要同时通过计划量上限与库位库存校验。
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update Picking Task Detail in a non-in-progress Picking Task", 16010)

        detail = PickingTaskService.get_task_detail(task_id, detail_id)
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 16015)

        for field in ('location_id', 'goods_id', 'batch_id'):
            if field in data and data[field] != getattr(detail, field):
                raise BadRequestException(
                    f"{field} of a picking detail cannot be changed; delete it and pick again.", 16049
                )

        if 'picked_quantity' not in data:
            return detail

        new_quantity = require_positive_int(data.get('picked_quantity'), 'picked_quantity')
        PickingTaskService._assert_goods_in_dn(task, [detail.goods_id])
        delta = new_quantity - (detail.picked_quantity or 0)
        if delta > 0:
            PickingTaskService._assert_within_planned(task, {detail.goods_id: delta})
            PickingTaskService._assert_location_stock(task, [{
                'goods_id': detail.goods_id,
                'location_id': detail.location_id,
                'picked_quantity': delta,
            }])
        detail.picked_quantity = new_quantity
        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_task_detail(task_id: int, detail_id: int):
        """
        删除指定的 PickingTaskDetail（仅当所属的 PickingTask 状态为 in_progress
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete Picking Task Detail from a non-in_progress Picking Task", 16011)

        detail = PickingTaskService.get_task_detail(task_id, detail_id)
        db.session.delete(detail)
        # db.session.commit()

    # -------------------------------------------------------------------------
    # Picking Batch 相关 (增删改查)
    # -------------------------------------------------------------------------
    @staticmethod
    def list_batches(task_id: int):
        """
        获取指定 PickingTask 下所有的 PickingBatch
        """
        task = PickingTaskService.get_task(task_id)  # 复用 get_task
        return task.batches  # 返回 relationship 列表

    @staticmethod
    def _assert_within_planned(task: PickingTask, extra_by_goods: dict | None = None):
        """
        按商品维度校验拣货任务的累计已拣量不超过其 DN 的计划出库量。

        累计量 = task_details 现有已拣量 + extra_by_goods 本次拟新增量。

        create_batch 是纯追加操作，前端断网重试、token 过期重登、连点等都会重复
        append picked_quantity，撑大 total_picked_quantity，并最终在 dn_picked 处
        误报 15008（DN库存不足）、污染数据且使单据永久无法完成。此校验作为服务端
        权威防线，与 DNDetail 上 `picked_quantity <= quantity` 的数据库约束对齐。
        """
        extra_by_goods = extra_by_goods or {}

        planned = {}
        for d in task.dn.details:
            planned[d.goods_id] = planned.get(d.goods_id, 0) + (d.quantity or 0)

        picked = {}
        for td in task.task_details:
            picked[td.goods_id] = picked.get(td.goods_id, 0) + (td.picked_quantity or 0)

        for gid in set(picked) | set(extra_by_goods):
            # 计划外商品由 _assert_goods_in_dn 单独拦截（16042），这里只管计划内商品的上限。
            if gid not in planned:
                continue
            total = picked.get(gid, 0) + extra_by_goods.get(gid, 0)
            if total > planned[gid]:
                raise BadRequestException(
                    f"Picked quantity exceeds planned quantity for goods {gid}: "
                    f"total {total} > planned {planned[gid]}.",
                    16029
                )

    @staticmethod
    def _assert_goods_in_dn(task: PickingTask, goods_ids):
        """
        拣货明细的商品必须在 DN 明细内。

        拣货下架走 picking_removed（不再计入 sorted_stock），计划外商品被下架后既不进
        picked_stock 也不进待上架区，会从 total_stock 里凭空消失；与 sorting / packing
        的「明细必须属于单据」保持一致。批次创建 / 明细改量校验本次涉及的商品，
        complete 前对全部明细再验一次（拦历史脏数据）。
        """
        planned = {d.goods_id for d in task.dn.details}
        for gid in goods_ids:
            if gid not in planned:
                raise BadRequestException(f"Goods {gid} is not in the DN.", 16042)

    @staticmethod
    def _assert_locations_in_warehouse(task: PickingTask, location_ids):
        """明细库位必须属于 DN 所在仓库，否则 complete 时会跨仓扣库存。"""
        location_ids = {lid for lid in location_ids if lid is not None}
        if not location_ids:
            return
        warehouse_by_location = dict(
            db.session.query(Location.id, Location.warehouse_id)
            .filter(Location.id.in_(location_ids))
            .all()
        )
        for location_id in location_ids:
            if warehouse_by_location.get(location_id) != task.dn.warehouse_id:
                raise BadRequestException(
                    f"Location {location_id} does not belong to the DN warehouse.", 16054
                )

    @staticmethod
    def _assert_location_stock(task: PickingTask, details_data: list, exclude_task_id: int | None = None):
        """Validate that every picked unit exists in the selected warehouse bin.

        先校验库位属于 DN 仓库，再用共享的 lock_goods_location（FOR UPDATE，无 outer join）
        锁住库位库存行做余量比对。
        exclude_task_id：complete 前复核本任务全部明细时，把本任务自身已写入的
        明细从「其它任务预占」里排除，否则会把自己算两遍。
        """
        requested = {}
        for item in details_data:
            goods_id = item.get('goods_id')
            location_id = item.get('location_id')
            quantity = item.get('picked_quantity', 0) or 0
            if quantity <= 0:
                continue
            if goods_id is None or location_id is None:
                raise BadRequestException(
                    "goods_id and location_id are required for picked items", 16034
                )
            key = (goods_id, location_id)
            requested[key] = requested.get(key, 0) + quantity

        PickingTaskService._assert_locations_in_warehouse(task, [lid for _, lid in requested])

        for (goods_id, location_id), quantity in requested.items():
            stock = lock_goods_location(goods_id, location_id)
            if stock is None:
                raise BadRequestException(
                    f"Goods {goods_id} has no stock in location {location_id}.", 16035
                )

            reserved_query = (
                db.session.query(func.coalesce(func.sum(PickingTaskDetail.picked_quantity), 0))
                .join(PickingTask)
                .filter(
                    PickingTaskDetail.goods_id == goods_id,
                    PickingTaskDetail.location_id == location_id,
                    PickingTask.status == 'in_progress',
                    PickingTask.is_active.is_(True),
                )
            )
            if exclude_task_id is not None:
                reserved_query = reserved_query.filter(PickingTask.id != exclude_task_id)
            already_reserved = reserved_query.scalar()
            available = max((stock.quantity or 0) - (already_reserved or 0), 0)
            if quantity > available:
                raise BadRequestException(
                    f"Insufficient stock in location {location_id} for goods {goods_id}: "
                    f"requested {quantity}, available {available}.",
                    16036,
                )

    @staticmethod
    @transactional
    def create_batch(task_or_id: int | PickingTask, data: dict, operator_id: int) -> PickingBatch:
        """
        创建新的 PickingBatch，并可选地批量创建 PickingTaskDetail。
        仅当 PickingTask.status == 'in_progress' 时允许创建。
        data 示例:
            {
                "picking_task_id":<int:task_id>,
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
        task = PickingTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot create batch in a non-in_progress Picking Task", 16012)

        if not isinstance(data, dict):
            raise BadRequestException("Request body must be a JSON object", 16015)
        op_time = parse_datetime_value(data.get('operation_time'), 'operation_time')

        # 1) 明细字段逐条校验：goods_id / location_id 必填、picked_quantity 正整数（缺字段 400 而非 500）
        details_data = []
        for raw in require_bulk_list(data.get('details') or [], 'details', allow_empty=True):
            details_data.append({
                'goods_id': require_positive_int(raw.get('goods_id'), 'goods_id', 16034),
                'location_id': require_positive_int(raw.get('location_id'), 'location_id', 16034),
                'picked_quantity': require_positive_int(raw.get('picked_quantity'), 'picked_quantity'),
            })

        new_batch = PickingBatch(
            picking_task_id=task.id,
            operator_id=operator_id,
            operation_time=op_time or datetime.now(),
            remark=data.get('remark', '')
        )
        db.session.add(new_batch)
        db.session.flush()  # 以获取 new_batch.id

        # 2) 权威校验：累计已拣量（现有 + 本次）不得超过 DN 计划量，
        #    阻止重复 / 超量提交污染数据（见 _assert_within_planned）；
        #    库位必须在 DN 仓库内且有足够库存。
        incoming_by_goods = {}
        for item in details_data:
            gid = item['goods_id']
            incoming_by_goods[gid] = incoming_by_goods.get(gid, 0) + item['picked_quantity']
        PickingTaskService._assert_goods_in_dn(task, incoming_by_goods)
        PickingTaskService._assert_within_planned(task, incoming_by_goods)
        PickingTaskService._assert_location_stock(task, details_data)

        for item in details_data:
            detail_obj = PickingTaskDetail(
                picking_task_id=task.id,
                batch_id=new_batch.id,
                location_id=item['location_id'],
                goods_id=item['goods_id'],
                picked_quantity=item['picked_quantity'],
                operator_id=operator_id
            )
            db.session.add(detail_obj)

        

        # db.session.commit()
        return new_batch

    @staticmethod
    def get_batch(task_id: int, batch_id: int) -> PickingBatch:
        """
        根据 batch_id 获取单个 PickingBatch，并验证其 picking_task_id 是否匹配
        """
        batch = get_object_or_404(PickingBatch, batch_id)
        if batch.picking_task_id != task_id:
            raise NotFoundException(f"Batch (id={batch_id}) not found in Task (id={task_id}).", 13001)
        return batch

    @staticmethod
    @transactional
    def update_batch(task_id: int, batch_id: int, data: dict) -> PickingBatch:
        """
        更新已有 PickingBatch
        - 仅当 PickingTask 处于 in_progress 时允许更新
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot update a batch in a non-in_progress Picking Task", 16013)

        batch = PickingTaskService.get_batch(task_id, batch_id)
        data = pick_fields(data, ('operation_time', 'remark'))

        if data.get('operation_time') is not None:
            batch.operation_time = parse_datetime_value(data['operation_time'], 'operation_time')
        if 'remark' in data:
            batch.remark = data['remark']

        # db.session.commit()
        return batch

    @staticmethod
    @transactional
    def delete_batch(task_id: int, batch_id: int):
        """
        删除指定的 PickingBatch
        - 仅当 PickingTask.status == 'in_progress' 时允许删除
        - 若下方已有明细 (PickingTaskDetail)，视业务决定是否禁止删除或自动删除
        """
        task = PickingTaskService.get_task(task_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot delete a batch in a non-in_progress Picking Task", 16014)

        batch = PickingTaskService.get_batch(task_id, batch_id)
        db.session.delete(batch)
        # db.session.commit()

    # ---------------------------------------------------------------------------------
    # 状态流转 & 日志
    # ---------------------------------------------------------------------------------
    @staticmethod
    @transactional
    def _update_task_status(task: PickingTask, new_status: str, operator_id: int) -> PickingTask:
        """
        内部方法：更新PickingTask的状态，并记录状态日志。
        如需更新 started_at 或 completed_at，也在此处理。
        """
        if new_status not in PickingTask.PICKING_TASK_STATUSES:
            raise BadRequestException(f"Invalid status value: {new_status}", 14007)

        old_status = task.status
        now = datetime.now()

        # 设置新状态
        task.status = new_status

        # 若是 pending -> in_progress，则记录 started_at
        if old_status == 'pending' and new_status == 'in_progress':
            task.started_at = now
        # 若是 in_progress -> completed，则记录 completed_at
        elif old_status == 'in_progress' and new_status == 'completed':
            task.completed_at = now

        # 写入状态日志
        status_log = PickingTaskStatusLog(
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
    def process_task(task_or_id: int | PickingTask, operator_id: int) -> PickingTask:
        """
        将PickingTask从 pending 切换到 in_progress
        """
        task = PickingTaskService._get_instance(task_or_id)
        if task.status != 'pending':
            raise BadRequestException("Cannot process a non-pending Picking Task", 16007)

        return PickingTaskService._update_task_status(task, 'in_progress', operator_id)

    @staticmethod
    @transactional
    def complete_task(task_or_id: int | PickingTask, operator_id: int) -> PickingTask:
        """
        将PickingTask从 in_progress 切换到 completed，并更新库存
        """
        task = PickingTaskService._get_instance(task_or_id)
        if task.status != 'in_progress':
            raise BadRequestException("Cannot complete a non-in_progress Picking Task", 16008)

        # 防御性校验：累计已拣量不得超过 DN 计划量。对已被重复提交污染的历史单据
        # 给出明确报错（16029），而非在 dn_picked 处误报 15008 或撞数据库约束。
        PickingTaskService._assert_within_planned(task)
        # 扣库存前对全部明细复核：商品必须在 DN 明细内、库位必须在 DN 仓库内、
        # 库位库存仍然足够（明细可能在批次创建后被改量 / 库存被其它流程动过）。
        PickingTaskService._assert_goods_in_dn(task, {d.goods_id for d in task.task_details})
        details = [
            {
                'goods_id': d.goods_id,
                'location_id': d.location_id,
                'picked_quantity': d.picked_quantity or 0,
            }
            for d in task.task_details
        ]
        PickingTaskService._assert_location_stock(task, details, exclude_task_id=task.id)

        task = PickingTaskService._update_task_status(task, 'completed', operator_id)

        for detail in task.task_details:
            # 创建下架记录
            RemovalService.create_removal_record({
                'goods_id': detail.goods_id,
                'location_id': detail.location_id,
                'quantity': detail.picked_quantity,
                'reason': 'picking',
                'remark': f'DN Picking: {task.dn.order_number}',
            }, operator_id)

        # 更新DN状态
        DNService.picking_dn(task.dn)
        
        return task

    # ---------------------------------------------------------------------------------
    # 其他辅助方法
    # ---------------------------------------------------------------------------------
    @staticmethod
    @transactional
    def create_picking_task_from_dn(dn_id: int, created_by_id: int) -> PickingTask:
        """
        根据DN自动生成一个PickingTask
        """
        dn = DNService.get_dn(dn_id)
        if not dn.details:
            raise BadRequestException("DN has no details to create a Picking Task.", 16016)

        picking_task = PickingTask(
            dn_id=dn_id,
            status='pending',
            created_by=created_by_id
        )
        db.session.add(picking_task)
        # db.session.commit()
        return picking_task

    @staticmethod
    def get_picking_monthly_stats(months=6, filters=None):
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
        query = PickingTask.query.with_entities(
            extract('year', PickingTask.created_at).label('year'),
            extract('month', PickingTask.created_at).label('month'),
            func.sum(
                case((PickingTask.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((PickingTask.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((PickingTask.status == 'completed', 1), else_=0)
            ).label('completed')
        ).filter(
            PickingTask.created_at >= start_date,
            PickingTask.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, PickingTask.dn_id == DN.id)

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
                    db.extract('year', PickingTask.created_at) == target_year,
                    db.extract('month', PickingTask.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = PickingTask.query.with_entities(
            PickingTask.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            PickingTask.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('sorting_type'):
                query = query.filter(PickingTask.sorting_type == filters['sorting_type'])

            if filters.get('warehouse_id') or filters.get('warehouse_ids'):
                query = query.join(DN, PickingTask.dn_id == DN.id)

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(DN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(PickingTask.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in PickingTask.PICKING_TASK_STATUSES]
