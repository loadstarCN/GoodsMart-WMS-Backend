from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.inventory.services import InventoryService

from .models import DN, DNDetail

class DNService:
    """
    A service class that encapsulates various operations
    related to DN and DNDetail.
    """

    # ------------------------------------
    # DN Services 私有方法
    # ------------------------------------


    @staticmethod
    def _get_instance(dn_or_id: int | DN) -> DN:
        """
        根据传入参数返回 DN 实例。
        如果参数为 int，则调用 get_dn 获取 DN 实例；
        否则直接返回传入的 DN 实例。
        """
        if isinstance(dn_or_id, int):
            return DNService.get_dn(dn_or_id)
        return dn_or_id
    
    @staticmethod
    @transactional
    def _update_dn_status(dn: DN, new_status: str) -> DN:
        """
        Update the status of a specific DN.
        Possible statuses in DN_STATUSES: ('pending','in_progress', 'picked', 'packed', 'delivered', 'completed','closed')
        """
        if new_status not in DN.DN_STATUSES:
            raise BadRequestException(f"Invalid DN status: {new_status}", 14007)
        dn.status = new_status
        if new_status == 'in_progress':
            dn.started_at = datetime.now()
        elif new_status == 'picked':
            dn.picked_at = datetime.now()
        elif new_status == 'packed':
            dn.packed_at = datetime.now()
        elif new_status == 'delivered':
            dn.delivered_at = datetime.now()
        elif new_status == 'completed':
            dn.completed_at = datetime.now()
        elif new_status == 'closed':
            dn.closed_at = datetime.now()
        db.session.add(dn)
        db.session.flush()
        # db.session.commit()
        return dn
    
    @staticmethod
    @transactional
    def _update_and_calculate_quantity(dn_or_id: int | DN):
        """
        更新 DNDetail 的已拣选、已打包和已发货数量。
        参数可以是 DN 的 ID（int）或 DN 实例。
        如果找不到 DN，则抛出 NotFound 异常。
        """
        from warehouse.picking.models import PickingTask, PickingTaskDetail
        from warehouse.packing.models import PackingTask, PackingTaskDetail
        from warehouse.delivery.models import DeliveryTask

        dn = DNService._get_instance(dn_or_id)

        # 检查是否存在已完成的 DeliveryTask（状态为 completed 或 signed）
        has_completed_delivery = (
            db.session.query(DeliveryTask)
            .filter(
                DeliveryTask.dn_id == dn.id,
                DeliveryTask.status.in_(['completed', 'signed']),
                DeliveryTask.is_active == True
            )
            .first()
            is not None
        )

        for dn_detail in dn.details:
            goods_id = dn_detail.goods_id

            # 1. 计算已拣选数量（来自 PickingTaskDetail）
            picked_query = (
                db.session.query(PickingTaskDetail.picked_quantity)
                .join(PickingTask)
                .filter(
                    PickingTask.dn_id == dn.id,
                    PickingTaskDetail.goods_id == goods_id,
                    PickingTask.is_active == True,
                    PickingTask.status == 'completed'
                )
            )
            picked_quantity = sum(result[0] for result in picked_query.all())

            # 2. 计算已打包数量（来自 PackingTaskDetail）
            packed_query = (
                db.session.query(PackingTaskDetail.packed_quantity)
                .join(PackingTask)
                .filter(
                    PackingTask.dn_id == dn.id,
                    PackingTaskDetail.goods_id == goods_id,
                    PackingTask.is_active == True,
                    PackingTask.status == 'completed'
                )
            )
            packed_quantity = sum(result[0] for result in packed_query.all())

            # 3. 计算已发货数量（如果存在已完成的 DeliveryTask，则等于已打包数量）
            delivered_quantity = packed_quantity if has_completed_delivery else 0

            # 更新 DNDetail 的统计值
            dn_detail.picked_quantity = picked_quantity
            dn_detail.packed_quantity = packed_quantity
            dn_detail.delivered_quantity = delivered_quantity

        db.session.add_all(dn.details)
        db.session.flush()
        # db.session.commit()

        return dn
    
    # ------------------------------------
    # DN Services 共有方法
    # ------------------------------------

    @staticmethod
    def list_dns(filters: dict):
        """
        根据过滤条件，返回 DN 的查询对象。

        :param filters: dict 类型，包含可能的过滤字段
        :return: 一个 SQLAlchemy Query 对象或已经过滤后的结果
        """
        query = DN.query.order_by(DN.id.desc())

        # 假设在 DN 模型里定义了 DN_TYPES, DN_STATUSES, 并且有相应的字段
        # 这里只是示例，根据实际需求增加筛选条件

        if filters.get('dn_type'):
            query = query.filter(DN.dn_type == filters['dn_type'])
        if filters.get('order_number'):
            query = query.filter(DN.order_number.ilike(f"%{filters['order_number']}%"))
        if filters.get('status'):
            query = query.filter(DN.status == filters['status'])
        if filters.get('carrier_id'):
            query = query.filter(DN.carrier_id == filters['carrier_id'])
        if filters.get('recipient_id'):
            query = query.filter(DN.recipient_id == filters['recipient_id'])
        if filters.get('expected_shipping_date'):
            query = query.filter(DN.expected_shipping_date >= filters['expected_shipping_date'])
        if filters.get('created_by'):
            query = query.filter(DN.created_by == filters['created_by'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(DN.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(DN.is_active == filters['is_active'])

        if filters.get('warehouse_id'):
            query = query.filter(DN.warehouse_id == filters['warehouse_id'])

        # 搜索DN.id 或 DN.order_number,或者在detail中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                dn_id = int(keyword)
                conditions.append(DN.id == dn_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 DN.id 过滤条件
                pass

            conditions.append(DN.order_number.ilike(f"%{keyword}%"))
            # conditions.append(DN.remark.ilike(f"%{keyword}%"))
            conditions.append(DN.details.any(DNDetail.goods.has(code=keyword)))
            query = query.filter(db.or_(*conditions))

        if filters.get('warehouse_id'):
            query = query.filter(DN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query
    
    @staticmethod
    def get_dn(dn_id: int) -> DN:
        """
        根据 ID 获取单个 DN，如不存在则抛出 404
        """
        return get_object_or_404(DN, dn_id)
    
    @staticmethod
    @transactional
    def create_dn(data: dict, created_by_id: int) -> DN:
        """
        创建一个新的 DN 以及可选的明细。

        :param data: DN 数据（可包含 details）
        :param created_by_id: 当前用户 ID
        :return: 新创建的 DN 对象
        """

        # 假设 data['expected_shipping_date'] = "2025-01-01"
        if 'expected_shipping_date' in data and isinstance(data['expected_shipping_date'], str):
            data['expected_shipping_date'] = datetime.strptime(data['expected_shipping_date'], '%Y-%m-%d').date()
            
        new_dn = DN(
            recipient_id=data['recipient_id'],
            shipping_address=data['shipping_address'],
            expected_shipping_date=data['expected_shipping_date'],
            warehouse_id=data['warehouse_id'],
            carrier_id=data.get('carrier_id'),
            dn_type=data.get('dn_type', 'shipping'),  # 默认 shipping
            status=data.get('status', 'pending'),      # 默认 pending
            order_number=data.get('order_number'),
            transportation_mode=data.get('transportation_mode'),
            packaging_info=data.get('packaging_info'),
            special_handling=data.get('special_handling'),
            remark=data.get('remark'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_dn)
        db.session.flush()

        # 创建 DN 明细（如果有）
        for detail in data.get('details', []):
            new_detail = DNDetail(
                dn_id=new_dn.id,
                goods_id=detail['goods_id'],
                quantity=detail.get('quantity', 0),
                picked_quantity=detail.get('picked_quantity', 0),
                packed_quantity=detail.get('packed_quantity', 0),
                delivered_quantity=detail.get('delivered_quantity', 0),                
                remark=detail.get('remark', ''),
                created_by=created_by_id
            )
            db.session.add(new_detail)
            db.session.flush()

            InventoryService.update_and_calculate_dn_stock(new_detail.goods_id,new_dn.warehouse_id)

        # db.session.commit()

        

        return new_dn

    @staticmethod
    @transactional
    def update_dn(dn_or_id: int | DN, data: dict) -> DN:
        """
        更新指定的 DN 记录（仅当其状态为 pending 时允许更新，或按实际业务修改）。

        :param dn_id: 待更新的 DN ID
        :param data: 要更新的字段
        :return: 更新后的 DN 对象
        :raises: NotFound / ValueError
        """

        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot update a non-pending DN", 16001)
        
        # 假设 data['expected_shipping_date'] = "2025-01-01"
        if 'expected_shipping_date' in data and isinstance(data['expected_shipping_date'], str):
            data['expected_shipping_date'] = datetime.strptime(data['expected_shipping_date'], '%Y-%m-%d').date()
        
        dn.recipient_id = data.get('recipient_id', dn.recipient_id)
        dn.shipping_address = data.get('shipping_address', dn.shipping_address)
        dn.expected_shipping_date = data.get('expected_shipping_date', dn.expected_shipping_date)
        dn.carrier_id = data.get('carrier_id', dn.carrier_id)
        dn.dn_type = data.get('dn_type', dn.dn_type)
        dn.status = data.get('status', dn.status)
        dn.order_number = data.get('order_number', dn.order_number)
        dn.transportation_mode = data.get('transportation_mode', dn.transportation_mode)
        dn.packaging_info = data.get('packaging_info', dn.packaging_info)
        dn.special_handling = data.get('special_handling', dn.special_handling)
        dn.remark = data.get('remark', dn.remark)
        dn.is_active = data.get('is_active', dn.is_active)

        db.session.add(dn)
        db.session.flush()

        if 'details' in data:
            DNService.sync_dn_details(dn, data.get('details', []), dn.created_by)  # 更新明细

        # db.session.commit()
        return dn

    @staticmethod
    @transactional
    def delete_dn(dn_or_id: int | DN):
        """
        删除指定的 DN（仅当其状态为 pending 时允许删除，或按实际业务修改）。
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending DN", 16002)

        goods_ids = [detail.goods_id for detail in dn.details]
        warehouse_id = dn.warehouse_id

        db.session.delete(dn)
        db.session.flush()

        for goods_id in goods_ids:
            InventoryService.update_and_calculate_dn_stock(goods_id,warehouse_id)

        # db.session.commit()

    @staticmethod
    @transactional
    def deactive_dn(dn_or_id: int | DN):
        """
        将指定的 DN 标记为非活动状态（is_active=False）。
        """
        dn = DNService._get_instance(dn_or_id)
        dn.is_active = False
        db.session.add(dn)
        db.session.flush()

        for detail in dn.details:
            InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)

        # db.session.commit()
        return dn
   

    @staticmethod
    def list_dn_details(dn_id: int):
        """
        获取指定 DN 下的所有 DNDetail 列表
        """
        dn = DNService.get_dn(dn_id)  # 若不存在会 404
        return dn.details
    
    @staticmethod
    def get_dn_detail(dn_id: int, detail_id: int) -> DNDetail:
        """
        根据 detail_id 获取 DNDetail，并确保其 dn_id 匹配
        """
        detail = get_object_or_404(DNDetail, detail_id)
        if detail.dn_id != dn_id:
            raise NotFoundException(f"DNDetail (id={detail_id}) is not part of DN (id={dn_id}).", 13001)
        return detail

    @staticmethod
    @transactional
    def create_dn_detail(dn_id: int, data: dict, created_by_id: int) -> DNDetail:
        """
        在指定 DN 下创建一条 DNDetail（仅当 DN 状态为 pending 时）。
        """
        dn = DNService.get_dn(dn_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot add details to a non-pending DN", 16003)

        new_detail = DNDetail(
            dn_id=dn_id,
            goods_id=data['goods_id'],
            quantity=data.get('quantity', 0),
            picked_quantity=data.get('picked_quantity', 0),
            packed_quantity=data.get('packed_quantity', 0),
            delivered_quantity=data.get('delivered_quantity', 0),
            remark=data.get('remark', ''),
            created_by=created_by_id
        )
        db.session.add(new_detail)
        db.session.flush()

        InventoryService.update_and_calculate_dn_stock(new_detail.goods_id,dn.warehouse_id)

        # db.session.commit()
        return new_detail

    @staticmethod
    @transactional
    def update_dn_detail(dn_id: int, detail_id: int, update_data: dict) -> DNDetail:
        """
        更新指定 DNDetail（仅当所属的 DN 状态为 pending 时）。
        """
        dn = DNService.get_dn(dn_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot update details in a non-pending DN", 16004)

        detail = DNService.get_dn_detail(dn_id, detail_id)

        detail.goods_id = update_data.get('goods_id', detail.goods_id)
        detail.quantity = update_data.get('quantity', detail.quantity)
        detail.picked_quantity = update_data.get('picked_quantity', detail.picked_quantity)
        detail.packed_quantity = update_data.get('packed_quantity', detail.packed_quantity)
        detail.delivered_quantity = update_data.get('delivered_quantity', detail.delivered_quantity)
        detail.remark = update_data.get('remark', detail.remark)

        db.session.add(detail)
        db.session.flush()
        InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)

        # db.session.commit()
        return detail

   
    @staticmethod
    @transactional
    def delete_dn_detail(dn_id: int, detail_id: int):
        """
        删除指定的 DNDetail（仅当其所属 DN 状态为 pending 时）。
        """
        dn = DNService.get_dn(dn_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot delete details from a non-pending DN", 16005)

        detail = DNService.get_dn_detail(dn_id, detail_id)
        db.session.delete(detail)
        db.session.flush()

        InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)

        # db.session.commit()

    @staticmethod
    @transactional
    def sync_dn_details(dn_or_id: int | DN, details_data: list, created_by: int) -> list:
        """
        同步DN明细（全量新增/更新/删除操作）
        参数规则：
        1. 传入id存在则更新记录
        2. 没有id则创建新记录
        3. 原detail不在新数据中的自动删除
        """
        dn = DNService._get_instance(dn_or_id)
        
        if dn.status != 'pending':
            raise BadRequestException("Cannot sync details in non-pending DN", 16006)

        existing_details = {d.id: d for d in dn.details}
        new_detail_ids = set()
        processed_goods = set()  # 用于校验商品重复

        # 处理更新和新增
        for item in details_data:
            # 商品ID重复校验
            goods_id = item['goods_id']
            if goods_id in processed_goods:
                raise BadRequestException(f"Duplicate goods_id: {goods_id}", 16025)
            processed_goods.add(goods_id)

            # 处理明细ID
            if 'id' in item and item['id'] in existing_details:
                # 更新现有记录
                detail = existing_details[item['id']]
                detail.quantity = item['quantity']
                detail.picked_quantity = item.get('picked_quantity', 0)
                detail.packed_quantity = item.get('packed_quantity', 0)
                detail.remark = item.get('remark', '')
                db.session.add(detail)
                new_detail_ids.add(detail.id)
            else:
                # 创建新记录
                new_detail = DNDetail(
                    dn_id=dn.id,
                    goods_id=goods_id,
                    quantity=item['quantity'],
                    picked_quantity=item.get('picked_quantity', 0),
                    packed_quantity=item.get('packed_quantity', 0),
                    remark=item.get('remark', ''),
                    created_by=created_by
                )
                db.session.add(new_detail)
                new_detail_ids.add(new_detail.id)

        # 删除不存在于新数据中的记录
        deleted_goods_ids = set()
        for detail_id in existing_details:
            if detail_id not in new_detail_ids:
                detail = existing_details[detail_id]
                deleted_goods_ids.add(detail.goods_id)
                db.session.delete(detail)
        
        db.session.flush()
        db.session.expire(dn, ['details'])
        # 更新后的明细列表
        latest_details = dn.details

        for detail in latest_details:
            InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)
        
        # 更新被删除明细的库存状态
        for goods_id in deleted_goods_ids:
            InventoryService.update_and_calculate_dn_stock(goods_id, dn.warehouse_id)

        return dn.details

    @staticmethod
    @transactional
    def progress_dn(dn_or_id:int | DN) -> DN:
        """
        Mark a DN as 'in progress' (例如进行中)，仅当 DN 状态为 pending 时可变更为 in progress
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot mark a DN as 'in progress' that is not in 'pending' status.", 16000)
        
        dn = DNService._update_dn_status(dn, "in_progress")

        # 创建拣货
        from warehouse.picking.services import PickingTaskService
        PickingTaskService.create_picking_task_from_dn(dn.id,dn.created_by)

        return dn
    
    
    
    @staticmethod
    @transactional
    def picking_dn(dn_or_id:int | DN) -> DN:
        """
        Mark a DN as 'picked' (例如拣货操作)，仅当 DN 状态为 in_progress 时可变更为 picked
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'in_progress':
            raise BadRequestException("Cannot pick a DN that is not in 'in_progress' status.", 16000)
        
        dn = DNService._update_dn_status(dn, "picked")
        DNService._update_and_calculate_quantity(dn_or_id)

        for detail in dn.details:            
            # 更新库存信息
            InventoryService.dn_picked(detail.goods_id,dn.warehouse_id,detail.quantity,detail.picked_quantity)

        # 创建打包任务
        from warehouse.packing.services import PackingTaskService
        PackingTaskService.create_packing_task_from_dn(dn.id,dn.created_by)
        
        return dn

    
    
    @staticmethod
    @transactional
    def packing_dn(dn_or_id:int | DN) -> DN:
        """
        Mark a DN as 'packed' (例如打包操作)，仅当 DN 状态为 picked 时可变更为 packed
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'picked':
            raise BadRequestException("Cannot pack a DN that is not in 'picked' status.", 16000)
        
        dn = DNService._update_dn_status(dn, "packed")        
        DNService._update_and_calculate_quantity(dn_or_id)

        for detail in dn.details:

            InventoryService.dn_packed(detail.goods_id,dn.warehouse_id,detail.packed_quantity)

        # 创建发货任务
        from warehouse.delivery.services import DeliveryTaskService
        DeliveryTaskService.create_delivery_task_from_dn(dn.id,dn.created_by)

        return dn

    @staticmethod
    @transactional
    def delivery_dn(dn_or_id:int | DN) -> DN:
        """
        Mark a DN as 'delivered' (例如发货操作)，仅当 DN 状态为 packed 时可变更为 delivered
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'packed':
            raise BadRequestException("Cannot ship a DN that is not in 'packed' status.", 16000)
        
        dn = DNService._update_dn_status(dn, "delivered")
        DNService._update_and_calculate_quantity(dn_or_id)

        for detail in dn.details:
            InventoryService.dn_delivered(detail.goods_id,dn.warehouse_id,detail.delivered_quantity)
           
        return dn

    @staticmethod
    @transactional
    def complete_dn(dn_or_id:int | DN) -> DN:
        """
        Mark a DN as 'completed'.
        一般只有在 delivered 状态后才可变成 completed
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'delivered':
            raise BadRequestException("Cannot complete a DN that is not in 'delivered' status.", 16000)
      
        dn = DNService._update_dn_status(dn, "completed")
        DNService._update_and_calculate_quantity(dn_or_id)

        for detail in dn.details:
            InventoryService.dn_completed(detail.goods_id,dn.warehouse_id,detail.delivered_quantity)

        return dn
    
    @staticmethod
    @transactional
    def close_dn(dn_or_id: int | DN):
        """
        将 DN 标记为 'closed'（已关闭）。
        参数可以是 DN 的 id（int）或 DN 实例。
        如果找不到 DN，则抛出 NotFound 异常。
        """
        dn = DNService._get_instance(dn_or_id)

        # 判断是否为pending状态
        if dn.status != 'pending':
            raise BadRequestException("Cannot close a DN that is not in 'pending' status.", 16022)
        
        dn = DNService._update_dn_status(dn, "closed")

        for detail in dn.details:
            InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)
    
        return dn
    
    @staticmethod
    def get_dn_monthly_stats(months=6, filters=None):
        """获取最近N个月各状态ASN统计（支持仓库过滤）
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
        query = DN.query.with_entities(
            extract('year', DN.created_at).label('year'),
            extract('month', DN.created_at).label('month'),
            func.sum(
                case((DN.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((DN.status == 'in_progress', 1), else_=0)
            ).label('in_progress'),
            func.sum(
                case((DN.status == 'picked', 1), else_=0)
            ).label('picked'),
            func.sum(
                case((DN.status == 'packed', 1), else_=0)
            ).label('packed'),
            func.sum(
                case((DN.status == 'delivered', 1), else_=0)
            ).label('delivered'),
            func.sum(
                case((DN.status == 'completed', 1), else_=0)
            ).label('completed'),
            func.sum(
                case((DN.status == 'closed', 1), else_=0)
            ).label('closed')
        ).filter(
            DN.created_at >= start_date,
            DN.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('recipient_id'):
                query = query.filter(DN.recipient_id == filters['recipient_id'])
            if filters.get('carrier_id'):
                query = query.filter(DN.carrier_id == filters['carrier_id'])
            if filters.get('dn_type'):
                query = query.filter(DN.dn_type == filters['dn_type'])

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
                'picked': row.picked or 0,
                'packed': row.packed or 0,
                'delivered': row.delivered or 0,
                'completed': row.completed or 0,
                'closed': row.closed or 0
            } for row in query.all()
        }

        # 生成完整月份序列（处理空数据月份）
        date_series = []
        current = start_date.replace(day=1)
        while current <= end_date:
            date_series.append(current.strftime("%Y-%m"))
            current += relativedelta(months=1)

        # 按前端要求构建数据结构
        status_order = ['pending','in_progress', 'picked', 'packed', 'delivered', 'completed','closed']
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
                    db.extract('year', DN.created_at) == target_year,
                    db.extract('month', DN.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = DN.query.with_entities(
            DN.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            DN.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('recipient_id'):
                query = query.filter(DN.recipient_id == filters['recipient_id'])
            if filters.get('carrier_id'):
                query = query.filter(DN.carrier_id == filters['carrier_id'])
            if filters.get('dn_type'):
                query = query.filter(DN.dn_type == filters['dn_type'])

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(DN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(DN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(DN.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in DN.DN_STATUSES]  # 确保顺序与模型一致