from datetime import datetime
from extensions.db import *
from warehouse.common.search import number_conditions
from extensions.error import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from extensions.transaction import transactional
from warehouse.common import require_fields, require_non_negative_int, require_company_goods
from warehouse.common.locks import (
    lock_row, lock_goods_location, lock_or_create_goods_location, lock_inventory_rows, lock_order_key,
)
from warehouse.common import clock
from warehouse.cyclecount.services import CycleCountTaskService
from warehouse.inventory.services import InventoryService
from .models import Adjustment, AdjustmentDetail
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from datetime import datetime, timedelta


def _resolve_detail_quantities(data: dict, current: AdjustmentDetail | None = None) -> tuple[int, int, int]:
    """
    解析并校验明细的三个数量：system_quantity / actual_quantity / adjustment_quantity。
    - 缺省值取现有明细（更新）或 0（新建 system_quantity）
    - system / actual 必须是非负整数（共享校验，拒绝 bool / 小数 / 非数字 → 400 而不是撞 CHECK 变 500）
    - adjustment_quantity 是派生值：缺省时自动计算；显式给出时必须等于 actual − system，否则 400
    """
    if current is not None:
        # 更新：没传（或传 null）的数量沿用现有明细
        provided = {k: v for k, v in data.items() if v is not None}
        data = {'system_quantity': current.system_quantity, 'actual_quantity': current.actual_quantity, **provided}
    require_fields(data, 'actual_quantity')
    system_quantity = require_non_negative_int(data.get('system_quantity'), 'system_quantity', default=0)
    actual_quantity = require_non_negative_int(data['actual_quantity'], 'actual_quantity')

    expected = actual_quantity - system_quantity
    given = data.get('adjustment_quantity')
    if given is not None and (isinstance(given, bool) or given != expected):
        raise BadRequestException(
            "adjustment_quantity must equal actual_quantity - system_quantity", 16038)
    return system_quantity, actual_quantity, expected


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
        # keyword：调整单号（纯数字时）/ 调整原因 / 明细里的商品编码、商品名、库位编码（部分匹配）
        if filters.get('keyword'):
            from warehouse.goods.models import Goods
            from warehouse.location.models import Location
            keyword = str(filters['keyword']).strip()
            pattern = f"%{keyword}%"
            conditions = [
                Adjustment.adjustment_reason.ilike(pattern),
                Adjustment.details.any(AdjustmentDetail.goods.has(
                    db.or_(Goods.code.ilike(pattern), Goods.name.ilike(pattern)))),
                Adjustment.details.any(AdjustmentDetail.location.has(Location.code.ilike(pattern))),
            ]
            # 单号：纯数字或 ADJ-4（调整单）、CC-2（来源盘点单）
            conditions += number_conditions(keyword, {'ADJ': [Adjustment.id], 'CC': [Adjustment.cyclecount_id]},
                                            [Adjustment.id])
            query = query.filter(db.or_(*conditions))
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
        # status / is_active / created_by 不接受客户端输入：新单固定 pending + 启用
        new_adjustment = Adjustment(
            warehouse_id=data['warehouse_id'],
            adjustment_reason=data.get('adjustment_reason'),
            status='pending',
            is_active=True,
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
        adjustment = AdjustmentService._assert_active(lock_row(AdjustmentService.get_adjustment(adjustment_id)))
        if adjustment.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Adjustment", 16001)

        # 白名单：只允许改调整原因；status / is_active / created_by 忽略（状态只走 approve / complete）
        adjustment.adjustment_reason = data.get('adjustment_reason', adjustment.adjustment_reason)

        # db.session.commit()
        return adjustment

    @staticmethod
    @transactional
    def delete_adjustment(adjustment_id: int):
        """
        删除指定 Adjustment（仅当其状态为 pending 时）
        """
        # 先锁调整单行并重读：与并发的 approve 串行
        adjustment = AdjustmentService._assert_active(lock_row(AdjustmentService.get_adjustment(adjustment_id)))
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
    def _assert_active(adjustment: Adjustment) -> Adjustment:
        """已取消（is_active=False）的调整单拒绝一切操作 → 409 16111。
        status 是数据库枚举（PG 上加枚举值的 ALTER TYPE 有事务限制），取消不新增状态，用 is_active=False 表示"""
        if adjustment is not None and not adjustment.is_active:
            raise ConflictException(
                f"Adjustment {adjustment.id} has been cancelled; it can no longer be operated.",
                16111, details={'adjustment_id': adjustment.id}
            )
        return adjustment

    @staticmethod
    def _assert_details_editable(adjustment: Adjustment):
        """已取消的调整单 409 16111；审批（approved）之后明细冻结：增删改一律 409"""
        AdjustmentService._assert_active(adjustment)
        if adjustment.status != 'pending':
            raise ConflictException(
                f"Adjustment details are frozen once the adjustment is {adjustment.status}", 16039)

    @staticmethod
    @transactional
    def create_adjustment_detail(adjustment_id: int, data: dict, created_by_id: int) -> AdjustmentDetail:
        """
        创建新的 AdjustmentDetail（仅 pending 的调整单；数量必须自洽）

        :param adjustment_id: 所属的 Adjustment ID
        :param data: 包含请求中的调整明细数据
        :param created_by_id: 创建者用户 ID
        :return: 新创建的 AdjustmentDetail 对象
        """
        adjustment = lock_row(AdjustmentService.get_adjustment(adjustment_id))
        AdjustmentService._assert_details_editable(adjustment)
        require_fields(data, 'goods_id')
        require_company_goods(data['goods_id'], adjustment.warehouse.company_id)
        system_quantity, actual_quantity, adjustment_quantity = _resolve_detail_quantities(data)

        new_detail = AdjustmentDetail(
            adjustment_id=adjustment_id,
            goods_id=data['goods_id'],
            location_id=data.get('location_id'),
            system_quantity=system_quantity,
            actual_quantity=actual_quantity,
            adjustment_quantity=adjustment_quantity,
            remark=data.get('remark')
        )
        db.session.add(new_detail)
        # 记录最后改明细的人：审批人不能是他（B-105）
        adjustment.details_modified_by = created_by_id
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
    def update_adjustment_detail(adjustment_id: int, detail_id: int, data: dict,
                                 operator_id: int | None = None) -> AdjustmentDetail:
        """
        更新指定 AdjustmentDetail

        :param adjustment_id: Adjustment 的 ID
        :param detail_id: AdjustmentDetail 的 ID
        :param data: 要更新的字段
        :param operator_id: 修改人；记到 adjustments.details_modified_by，之后他不能审批这张单（B-105）
        :return: 更新后的 AdjustmentDetail 对象
        """
        detail = AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)
        # 先锁调整单、再锁明细行（与审批 / 完成串行），再按最新状态检查是否可改
        lock_row(detail.adjustment)
        detail = lock_row(detail)
        AdjustmentService._assert_details_editable(detail.adjustment)
        system_quantity, actual_quantity, adjustment_quantity = _resolve_detail_quantities(data, detail)
        if data.get('goods_id') is not None:
            require_company_goods(data['goods_id'], detail.adjustment.warehouse.company_id)

        detail.goods_id = data.get('goods_id', detail.goods_id)
        detail.location_id = data.get('location_id', detail.location_id)
        detail.system_quantity = system_quantity
        detail.actual_quantity = actual_quantity
        detail.adjustment_quantity = adjustment_quantity
        detail.remark = data.get('remark', detail.remark)
        if operator_id is not None:
            detail.adjustment.details_modified_by = operator_id

        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def delete_adjustment_detail(adjustment_id: int, detail_id: int, operator_id: int | None = None):
        """
        删除指定的 AdjustmentDetail（仅 pending 的调整单）
        operator_id：删除人，记到 adjustments.details_modified_by（B-105）
        """
        detail = AdjustmentService.get_adjustment_detail(adjustment_id, detail_id)
        # 先锁调整单、再锁明细行（与审批 / 完成串行），再按最新状态检查是否可改
        lock_row(detail.adjustment)
        detail = lock_row(detail)
        AdjustmentService._assert_details_editable(detail.adjustment)
        if operator_id is not None:
            detail.adjustment.details_modified_by = operator_id
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
            adjustment.approved_at = clock.now()
            adjustment.approved_by = operator_id
        if adjustment.status == 'approved' and new_status == 'completed':
            adjustment.completed_at = clock.now()
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
        # 先锁调整单行并重读状态：与并发的重复审批 / 删除 / 取消串行
        adjustment = AdjustmentService._assert_active(lock_row(AdjustmentService._get_instance(task_or_id)))
        if adjustment.status != 'pending':
            raise BadRequestException("Cannot approve an Adjustment that is not pending", 16018)
        # 职责分离：创建人不能审批自己的调整单
        if adjustment.created_by == operator_id:
            raise ForbiddenException("The creator of an Adjustment cannot approve it", 16040)
        # 最后增 / 改 / 删明细的人也不能审批（否则可以先改别人建的单再自己审批，B-105）→ 409 16122
        if adjustment.details_modified_by is not None and adjustment.details_modified_by == operator_id:
            raise ConflictException(
                "You modified the details of this Adjustment; another user must approve it.", 16122,
                details={'adjustment_id': adjustment.id, 'details_modified_by': adjustment.details_modified_by}
            )

        return AdjustmentService._update_adjustment_status(adjustment, 'approved',operator_id)

    @staticmethod
    @transactional
    def complete_adjustment(task_or_id: int | Adjustment, operator_id: int) -> Adjustment:
        """
        更新 Adjustment 的状态为 completed，按明细的 adjustment_quantity（差值）调整库位库存
        - 正向调整且目标库位还没有该商品的库位记录（如「在新库位发现 3 件」）→ 在锁下新建一行；
          库位必须启用（与上架一致，停用 → 400 16085），该商品在仓库还没有库存记录时一并建
        - 负向调整但库位记录不存在（审批后库位被清空）/ 明细没有库位 → 409 16113：
          这张单完成不了，请取消（PUT /adjustment/<id>/cancel/）后按当前库存重新建单
        - 差值为 0 的明细不动库存
        """
        # 先锁调整单行并重读状态（再锁库位 / 库存行）：并发的重复完成在这里挡下，库位数量不会调两次
        adjustment = AdjustmentService._assert_active(lock_row(AdjustmentService._get_instance(task_or_id)))
        if adjustment.status != 'approved':
            raise BadRequestException("Cannot complete an Adjustment that is not approved", 16019)

        adjustment = AdjustmentService._update_adjustment_status(adjustment, 'completed',operator_id)

        # 加锁顺序（B-103，见 common/locks.py）：先按 goods_id 升序锁齐要动的库存行，
        # 再按 (goods_id, location_id) 升序逐行锁库位库存行（FOR UPDATE 读取，避免多 worker 并发下丢更新）
        changed = [d for d in adjustment.details if (d.adjustment_quantity or 0) != 0]
        lock_inventory_rows([d.goods_id for d in changed], adjustment.warehouse_id)
        touched_goods_ids = []
        for detail in sorted(adjustment.details, key=lambda d: lock_order_key(d.goods_id, d.location_id)):
            delta = detail.adjustment_quantity or 0
            if delta == 0:
                continue
            if detail.goods_id not in touched_goods_ids:
                touched_goods_ids.append(detail.goods_id)
            goods_location_record = AdjustmentService._lock_or_create_goods_location(adjustment, detail)
            goods_location_record.quantity += delta

            if goods_location_record.quantity < 0:
                raise BadRequestException(f"Insufficient stock for goods_id={detail.goods_id} in location_id={detail.location_id}", 15002)
            elif goods_location_record.quantity == 0:
                db.session.delete(goods_location_record)
            else:
                db.session.add(goods_location_record)
            db.session.flush()

        # 更新库存（只重算动过库位库存的商品；库存行上面已锁，这里只是重入）
        for goods_id in sorted(touched_goods_ids, key=lock_order_key):
            InventoryService.update_and_calculate_stock(goods_id, adjustment.warehouse_id)

        return adjustment

    @staticmethod
    def _lock_or_create_goods_location(adjustment: Adjustment, detail: AdjustmentDetail):
        """完成调整时取明细对应的库位库存行（FOR UPDATE）；正向调整且不存在时在锁下新建（quantity 先记 0）"""
        from warehouse.location.models import Location
        from warehouse.location.services import LocationService

        delta = detail.adjustment_quantity or 0
        if detail.location_id is None:
            raise ConflictException(
                f"Adjustment detail {detail.id} has no location; this adjustment cannot be completed. "
                f"Cancel it and create a new one with a location.",
                16113, details={'detail_id': detail.id, 'goods_id': detail.goods_id, 'location_id': None,
                                'adjustment_quantity': delta}
            )
        record = lock_goods_location(detail.goods_id, detail.location_id)
        if record is not None:
            return record
        if delta < 0:
            raise ConflictException(
                f"Goods {detail.goods_id} has no stock in location {detail.location_id} any more, so it cannot be "
                f"reduced by {-delta}; the stock changed after this adjustment was created. "
                f"Cancel this adjustment and create a new one that matches the current stock.",
                16113, details={'detail_id': detail.id, 'goods_id': detail.goods_id,
                                'location_id': detail.location_id, 'adjustment_quantity': delta}
            )
        # 正向：与上架相同，先给库位行加共享锁再判断启用状态（与并发的停用串行），再在锁下新建库位库存行
        LocationService.require_active_location(
            lock_row(get_object_or_404(Location, detail.location_id), shared=True))
        # 该商品在仓库还没有库存行时先建（savepoint 兜底并发首次创建，B-107），再在锁下取 / 建库位库存行
        InventoryService.ensure_inventory(detail.goods_id, adjustment.warehouse_id)
        record, _ = lock_or_create_goods_location(detail.goods_id, detail.location_id)
        return record

    @staticmethod
    @transactional
    def cancel_adjustment(task_or_id: int | Adjustment, operator_id: int) -> Adjustment:
        """
        取消调整单：pending / approved 都可取消，不动库存。
        - status 列是数据库枚举，不新增 cancelled 状态：取消 = is_active=False，status 保持原值；
          operator_id 记取消人、updated_at 即取消时间
        - 已取消的调整单拒绝一切操作（16111）；已完成的不能取消（409 16112）
        - 由盘点单生成的调整单取消后，同一盘点单可以重新生成（16086 只看启用中的调整单）
        """
        adjustment = AdjustmentService._assert_active(lock_row(AdjustmentService._get_instance(task_or_id)))
        if adjustment.status not in ('pending', 'approved'):
            raise ConflictException(
                f"Cannot cancel an Adjustment in '{adjustment.status}' status.", 16112,
                details={'adjustment_id': adjustment.id, 'status': adjustment.status}
            )
        adjustment.is_active = False
        adjustment.operator_id = operator_id
        db.session.flush()
        return adjustment

    @staticmethod
    @transactional
    def create_adjustment_from_cyclecount(cyclecount_id: int, created_by_id: int) -> Adjustment:
        """
        创建一个新的 Adjustment 任务，并将其与指定的 CycleCount 关联
        同一盘点单只能有一张启用中的调整单（409 16086）：complete_adjustment 按差值累加，
        重复生成会让库存被调两倍。调整单记录来源盘点单（cyclecount_id），存量调整单为 NULL 不参与判断
        """
        # 先锁盘点单行并重读：并发的重复生成在这里串行，后到的一个能看到前一个已提交的调整单
        cyclecount = lock_row(CycleCountTaskService.get_task(cyclecount_id))

        # 只有盘点完成后 system_quantity / difference 才是最终值，未完成不能据此生成调整单
        if cyclecount.status != 'completed':
            raise BadRequestException("CycleCountTask must be completed before creating an Adjustment", 16041)

        existing = (
            db.session.query(Adjustment.id)
            .filter(Adjustment.cyclecount_id == cyclecount.id, Adjustment.is_active.is_(True))
            .first()
        )
        if existing is not None:
            raise ConflictException(
                f"An Adjustment (id={existing.id}) has already been created from CycleCountTask {cyclecount.id}",
                16086, details={'adjustment_id': existing.id}
            )

        if len(cyclecount.task_details) == 0:
            raise BadRequestException("CycleCountTask has no details to create an Adjustment", 16016)
        
        # 遍历task_details，删选出difference不为零的记录
        task_details = [detail for detail in cyclecount.task_details if (detail.difference or 0) != 0]
        if len(task_details) == 0:
            raise BadRequestException("No differences found in CycleCountTask details", 16020)

        adjustment_details = []
        for detail in task_details:
            # 系统数 / 差异是录入实盘那一刻的值（盘点完成要求每行都已录入，见 CycleCountTaskService），
            # 完成调整时按差值累加到当时的库位库存，盘点期间正常的出入库不会被算成差异
            system_quantity = detail.system_quantity or 0
            adjustment_detail = AdjustmentDetail(
                goods_id=detail.goods_id,
                location_id=detail.location_id,
                system_quantity=system_quantity,
                actual_quantity=detail.actual_quantity,
                adjustment_quantity=detail.actual_quantity - system_quantity
            )
            adjustment_details.append(adjustment_detail)

        adjustment = Adjustment(
            warehouse_id=cyclecount.warehouse_id,
            adjustment_reason="Adjustment by CycleCountTask ID: {}".format(cyclecount_id),
            cyclecount_id=cyclecount.id,
            status='pending',
            is_active=True,
            details=adjustment_details,
            created_by=created_by_id
        )
        db.session.add(adjustment)
        db.session.flush()

        return adjustment
    
    @staticmethod
    def get_adjustment_monthly_stats(months=6, filters=None):
        """获取最近N个月各状态 Adjustment 统计（支持仓库过滤）
        Args:
            months (int): 统计月份数（默认6个月）
            filters (dict): 过滤条件字典，可包含：
                - warehouse_id: 单个仓库ID
                - warehouse_ids: 多个仓库ID列表
        Returns:
            list: 符合前端图表要求的数据序列
        """
        # 计算时间范围
        end_date = clock.stats_now(filters)
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
                'approved': row.approved or 0,
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
        """获取各状态 Adjustment 在当前月、前一个月和去年同月的统计"""
        now = clock.stats_now(filters)
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


