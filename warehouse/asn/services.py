from datetime import datetime, timedelta
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.inventory.services import InventoryService
from .models import ASN, ASNDetail
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
class ASNService:
    """
    A service class that encapsulates various operations
    related to ASN and ASNDetail.
    """

    # --------------------------------------
    # ASNService私有方法
    # --------------------------------------

    @staticmethod
    def _get_instance(asn_or_id: int | ASN) -> ASN:
        """
        根据传入参数返回 ASN 实例。
        如果参数为 int，则调用 get_asn 获取 ASN 实例；
        否则直接返回传入的 ASN 实例。
        """
        if isinstance(asn_or_id, int):
            return ASNService.get_asn(asn_or_id)
        return asn_or_id
    
    @staticmethod
    @transactional
    def _update_asn_status(asn: ASN, new_status: str) -> ASN:
        """
        Update the status of a specific ASN. Possible statuses:
        - 'pending'
        - 'received'
        - 'completed'
        etc.
        """
        if new_status not in ASN.ASN_STATUSES:
            raise BadRequestException(f"Invalid status: {new_status}", 14007)
        asn.status = new_status
        
        if new_status == 'received':
            asn.received_at = datetime.now()
        elif new_status == 'completed':
            asn.completed_at = datetime.now()
        elif new_status == 'closed':
            asn.closed_at = datetime.now()

        asn.updated_at = datetime.now()
        db.session.add(asn)
        db.session.flush()
        # db.session.commit()
        return asn
    
    @staticmethod
    @transactional
    def _update_and_calculate_quantity(asn_or_id: int | ASN):
        """
        更新 ASNDetail 的实际数量并计算已分拣数量。
        参数可以是 ASN 的 ID（int）或 ASN 实例。
        如果找不到 ASN，则抛出 NotFound 异常。
        """
        
        from warehouse.sorting.models import SortingTask, SortingTaskDetail
        asn = ASNService._get_instance(asn_or_id)

        for asn_detail in asn.details:
            # 查询与该 ASN 关联、且属于当前商品（goods_id）的已完成的 SortingTaskDetail
            st_details = (
                db.session.query(SortingTaskDetail)
                .join(SortingTask)
                .filter(
                    SortingTask.asn_id == asn.id,
                    SortingTaskDetail.goods_id == asn_detail.goods_id,
                    SortingTask.is_active == True,
                    SortingTask.status == 'completed'
                )
                .all()
            )

            # 计算已分拣数量和损坏数量
            sorted_quantity = sum(detail.sorted_quantity for detail in st_details)
            damage_quantity = sum(detail.damage_quantity for detail in st_details)

            # 更新 ASNDetail 的统计值
            asn_detail.sorted_quantity = sorted_quantity
            asn_detail.damage_quantity = damage_quantity
            asn_detail.actual_quantity = sorted_quantity + damage_quantity

        db.session.add_all(asn.details)
        db.session.flush()
        # db.session.commit()

        return asn

    # --------------------------------------
    # ASNService 公共方法
    # --------------------------------------
   
    @staticmethod
    def get_asn(asn_id: int) -> ASN:
        """
        Retrieve a single ASN by its ID, or raise a 404 NotFound if not found.
        """
        return get_object_or_404(ASN,asn_id)  # Raises NotFound if not found

    @staticmethod
    def list_asns(filters: dict):
        """
        根据过滤条件，返回 ASN 的查询对象。

        :param filters: dict 类型，包含可能的过滤字段
        :return: 一个 SQLAlchemy Query 对象或已经过滤后的结果
        """
        query = ASN.query.order_by(ASN.id.desc())

        if filters.get('asn_type'):
            query = query.filter(ASN.asn_type == filters['asn_type'])
        if filters.get('status'):
            query = query.filter(ASN.status == filters['status'])
        if filters.get('tracking_number'):
            query = query.filter(ASN.tracking_number.ilike(f"%{filters['tracking_number']}%"))
        if filters.get('supplier_id'):
            query = query.filter(ASN.supplier_id == filters['supplier_id'])
        if filters.get('carrier_id'):
            query = query.filter(ASN.carrier_id == filters['carrier_id'])
        if filters.get('expected_arrival_date'):
            query = query.filter(ASN.expected_arrival_date >= filters['expected_arrival_date'])
        if filters.get('created_by'):
            query = query.filter(ASN.created_by == filters['created_by'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(ASN.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(ASN.is_active == filters['is_active'])

        # 搜索ASN.id 或 ASN.tracking_number，或者在detail中的goods.code
        if filters.get('keyword'):
            keyword = filters['keyword']
            conditions = []
            try:
                # 尝试将 keyword 转换为整数
                asn_id = int(keyword)
                conditions.append(ASN.id == asn_id)
            except ValueError:
                # 如果转换失败，则说明 keyword 不全是数字，不加入 ASN.id 过滤条件
                pass

            conditions.append(ASN.tracking_number.ilike(f"%{keyword}%"))
            # conditions.append(ASN.remark.ilike(f"%{keyword}%"))
            conditions.append(ASN.details.any(ASNDetail.goods.has(code=keyword)))
            query = query.filter(db.or_(*conditions))


        if filters.get('warehouse_id'):
            query = query.filter(ASN.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(ASN.warehouse_id.in_(filters['warehouse_ids'])) 

        return query
    
    @staticmethod
    @transactional
    def create_asn(data: dict, created_by_id: int) -> ASN:
        """
        创建一个新的 ASN 以及可选的明细。

        :param data: ASN 数据（包含 details）
        :param created_by_id: 当前用户 ID
        :return: 新创建的 ASN 对象
        """
        

        # 假设 data['expected_arrival_date'] = "2025-01-01"
        if 'expected_arrival_date' in data and isinstance(data['expected_arrival_date'], str):
            data['expected_arrival_date'] = datetime.strptime(data['expected_arrival_date'], '%Y-%m-%d').date()

        new_asn = ASN(
            supplier_id=data['supplier_id'],
            tracking_number=data.get('tracking_number'),
            warehouse_id=data['warehouse_id'],
            carrier_id=data.get('carrier_id'),
            asn_type=data.get('asn_type', 'inbound'), # 默认为 inbound
            status=data.get('status', 'pending'), # 默认为 pending
            expected_arrival_date=data.get('expected_arrival_date'),
            remark=data.get('remark'),
            created_by=created_by_id
        )
        db.session.add(new_asn)
        db.session.flush()

        
        # 创建 ASN 明细（如果有）
        for detail in data.get('details', []):
            new_detail = ASNDetail(
                asn_id=new_asn.id,
                goods_id=detail['goods_id'],
                quantity=detail['quantity'],
                actual_quantity=detail.get('actual_quantity', 0),
                sorted_quantity=detail.get('sorted_quantity', 0),
                damage_quantity=detail.get('damage_quantity', 0),
                weight=detail.get('weight', 0.0),
                volume=detail.get('volume', 0.0),
                remark=detail.get('remark'),
                created_by=created_by_id
            )
            db.session.add(new_detail)
            db.session.flush()

            try:
                InventoryService.get_inventory(new_detail.goods_id, new_asn.warehouse_id)
            except  NotFoundException:
                # 如果库存记录不存在，则创建新的库存记录 
                InventoryService.create_inventory({"goods_id": new_detail.goods_id, "warehouse_id": new_asn.warehouse_id})
                db.session.flush()
            
            # 重新计算库存
            InventoryService.update_and_calculate_asn_stock(new_detail.goods_id, new_asn.warehouse_id)

        # db.session.commit()
        return new_asn
    
    @staticmethod
    @transactional
    def update_asn(asn_or_id: int | ASN, data: dict) -> ASN:
        """
        更新指定的 ASN 记录（仅当其状态为 pending 时允许更新）。

        :param asn_id: 待更新的 ASN ID
        :param data: 要更新的字段
        :return: 更新后的 ASN 对象
        :raises: NotFound 如果该 ASN 不存在
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot update a non-pending ASN", 16001)
        
        # asn.warehouse_id = data.get('warehouse_id', asn.warehouse_id)
        asn.supplier_id = data.get('supplier_id', asn.supplier_id)
        asn.tracking_number = data.get('tracking_number', asn.tracking_number)
        asn.carrier_id = data.get('carrier_id', asn.carrier_id)
        asn.asn_type = data.get('asn_type', asn.asn_type)
        asn.status = data.get('status', asn.status)
        asn.expected_arrival_date = data.get('expected_arrival_date', asn.expected_arrival_date)
        asn.remark = data.get('remark', asn.remark)
        asn.is_active = data.get('is_active', asn.is_active)

        db.session.add(asn)
        db.session.flush()

        if 'details' in data:
            ASNService.sync_asn_details(asn, data.get('details', []), asn.created_by)  # 更新明细

        # db.session.commit()
        return asn

    @staticmethod
    @transactional
    def delete_asn(asn_or_id: int | ASN):
        """
        删除指定的 ASN（仅当其状态为 pending 时允许删除）。
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending ASN", 16002)
        
        goods_ids = [detail.goods_id for detail in asn.details]
        warehouse_id = asn.warehouse_id
        db.session.delete(asn)
        db.session.flush()
        for goods_id in goods_ids:
            InventoryService.update_and_calculate_asn_stock(goods_id, warehouse_id)

        # db.session.commit()

    @staticmethod
    @transactional
    def deactive_asn(asn_or_id: int | ASN):
        """
        将指定 ASN 标记为非活动状态。
        """
        asn = ASNService._get_instance(asn_or_id)
        asn.is_active = False
        db.session.add(asn)
        db.session.flush()

        for detail in asn.details:
            InventoryService.update_and_calculate_asn_stock(detail.goods_id, asn.warehouse_id)
            
        # db.session.commit()
        return asn


    @staticmethod
    def list_asn_details(asn_id: int):
        """
        获取指定 ASN 下的所有 ASNDetail 列表
        """
        asn = ASNService.get_asn(asn_id)  # 如果不存在会 404
        return asn.details
    
    @staticmethod
    def get_asn_detail(asn_id: int, detail_id: int) -> ASNDetail:
        """
        Retrieve a single ASNDetail by its detail_id, ensuring it belongs to asn_id.
        Raises NotFound if not found or if detail.asn_id != asn_id.
        """
        detail = get_object_or_404(ASNDetail, detail_id)
        if detail.asn_id != asn_id:
            raise NotFoundException(f"ASNDetail (id={detail_id}) is not part of ASN (id={asn_id}).", 13001)
        return detail

    @staticmethod
    @transactional
    def create_asn_detail(asn_id: int, data: dict, created_by_id: int) -> ASNDetail:
        """
        在指定 ASN 下创建一条 ASNDetail（仅当 ASN 状态为 pending 时）
        """
        asn = ASNService.get_asn(asn_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot add details to a non-pending ASN", 16003)

        new_detail = ASNDetail(
            asn_id=asn_id,
            goods_id=data['goods_id'],
            quantity=data['quantity'],
            actual_quantity=data.get('actual_quantity', 0),
            sorted_quantity=data.get('sorted_quantity', 0),
            damage_quantity=data.get('damage_quantity', 0),
            weight=data.get('weight', 0.0),
            volume=data.get('volume', 0.0),
            remark=data.get('remark'),
            created_by=created_by_id
        )
        db.session.add(new_detail)
        db.session.flush()

        # 更新库存信息
        try:
            InventoryService.get_inventory(new_detail.goods_id, asn.warehouse_id)
        except NotFoundException:
            # 如果库存记录不存在，则创建新的库存记录
            InventoryService.create_inventory({"goods_id": new_detail.goods_id, "warehouse_id": asn.warehouse_id})
            db.session.flush()

        InventoryService.update_and_calculate_asn_stock(new_detail.goods_id, asn.warehouse_id)
        # db.session.commit()
        return new_detail

    @staticmethod
    @transactional
    def update_asn_detail(asn_id: int, detail_id: int, update_data: dict) -> ASNDetail:
        """
        Update a single ASNDetail record for the specified ASN.
        """
        asn = ASNService.get_asn(asn_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot update details in a non-pending ASN", 16004)

        detail = ASNService.get_asn_detail(asn_id, detail_id)

        detail.goods_id = update_data.get('goods_id', detail.goods_id)
        detail.quantity = update_data.get('quantity', detail.quantity)
        detail.actual_quantity = update_data.get('actual_quantity', detail.actual_quantity)
        detail.sorted_quantity = update_data.get('sorted_quantity', detail.sorted_quantity)
        detail.damage_quantity = update_data.get('damage_quantity', detail.damage_quantity)
        detail.weight = update_data.get('weight', detail.weight)
        detail.volume = update_data.get('volume', detail.volume)
        detail.remark = update_data.get('remark', detail.remark)

        db.session.add(detail)
        db.session.flush()
        InventoryService.update_and_calculate_asn_stock(detail.goods_id, asn.warehouse_id)

        # db.session.commit()
        return detail

    @staticmethod
    @transactional
    def sync_asn_details(asn_or_id: int | ASN, details_data: list, created_by: int) -> list:
        """
        同步ASN明细数据（包含新增/更新/删除操作）
        参数规则：
        1. 传入的detail_id存在则更新
        2. 没有detail_id则创建新记录
        3. 原有detail不在新数据中的则删除
        """
        asn = ASNService._get_instance(asn_or_id)
        
        if asn.status != 'pending':
            raise BadRequestException("Cannot sync details in non-pending ASN", 16006)

        existing_details = {d.id: d for d in asn.details}
        new_detail_ids = set()

        # 处理更新和新增
        for item in details_data:
            detail_id = item.get('id')
            
            if detail_id and detail_id in existing_details:
                # 更新现有记录
                detail = existing_details[detail_id]
                detail.quantity = item.get('quantity', detail.quantity)
                detail.actual_quantity = item.get('actual_quantity', detail.actual_quantity)
                detail.sorted_quantity = item.get('sorted_quantity', detail.sorted_quantity)
                detail.damage_quantity = item.get('damage_quantity', detail.damage_quantity)
                detail.weight = item.get('weight', detail.weight)
                detail.volume = item.get('volume', detail.volume)
                detail.remark = item.get('remark', detail.remark)
                db.session.add(detail)
                new_detail_ids.add(detail_id)
            else:
                # 创建新记录
                new_detail = ASNDetail(
                    asn_id=asn.id,
                    goods_id=item['goods_id'],
                    quantity=item['quantity'],
                    actual_quantity=item.get('actual_quantity', 0),
                    sorted_quantity=item.get('sorted_quantity', 0),
                    damage_quantity=item.get('damage_quantity', 0),
                    weight=item.get('weight', 0.0),
                    volume=item.get('volume', 0.0),
                    remark=item.get('remark'),
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
        db.session.expire(asn, ['details'])
        latest_details = asn.details # 重新加载后的明细列表
        
        for detail in latest_details:
            try:
                InventoryService.get_inventory(detail.goods_id, asn.warehouse_id)
            except NotFoundException:
                InventoryService.create_inventory({"goods_id": detail.goods_id, "warehouse_id": asn.warehouse_id})
                db.session.flush()

            InventoryService.update_and_calculate_asn_stock(detail.goods_id, asn.warehouse_id)
        
        # 更新被删除明细的库存状态
        for goods_id in deleted_goods_ids:
            try:
                InventoryService.get_inventory(goods_id, asn.warehouse_id)
            except NotFoundException:
                InventoryService.create_inventory({"goods_id": goods_id, "warehouse_id": asn.warehouse_id})
                db.session.flush()

            InventoryService.update_and_calculate_dn_stock(goods_id, asn.warehouse_id)

        return asn.details
    

    @staticmethod
    @transactional
    def delete_asn_detail(asn_id: int, detail_id: int):
        """
        删除指定的 ASNDetail（仅当其所属 ASN 状态为 pending 时）
        """
        asn = ASNService.get_asn(asn_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot delete details from a non-pending ASN", 16005)

        detail = ASNService.get_asn_detail(asn_id, detail_id)
        goods_id = detail.goods_id

        db.session.delete(detail)
        db.session.flush()

        InventoryService.update_and_calculate_asn_stock(goods_id, asn.warehouse_id)

        # db.session.commit()

    

    @staticmethod
    @transactional
    def receive_asn(asn_or_id: int | ASN):
        """
        将 ASN 标记为 'received'（已接收）。
        参数可以是 ASN 的 id（int）或 ASN 实例。
        如果找不到 ASN，则抛出 NotFound 异常。
        """
        asn = ASNService._get_instance(asn_or_id)

        if asn.status != 'pending':
            raise BadRequestException("Cannot receive a non-pending ASN", 16021)

        asn = ASNService._update_asn_status(asn, "received")

        # 更新库存信息
        for detail in asn.details:
            InventoryService.asn_received(detail.goods_id, asn.warehouse_id, detail.quantity)        

        # 自动创建分拣任务
        from warehouse.sorting.services import SortingTaskService
        SortingTaskService.create_sorting_task_from_asn(asn.id)

        return asn

    @staticmethod
    @transactional
    def complete_asn(asn_or_id: int | ASN):
        """
        将 ASN 标记为 'completed'（已完成）。
        参数可以是 ASN 的 id（int）或 ASN 实例。
        如果找不到 ASN，则抛出 NotFound 异常。
        """
        asn = ASNService._get_instance(asn_or_id)

        # 判断是否是received状态        
        if asn.status != 'received':
            raise BadRequestException("Cannot close a non-pending ASN", 16022)
        
                        
        asn = ASNService._update_asn_status(asn, "completed")
        ASNService._update_and_calculate_quantity(asn_or_id)

        # 更新库存信息
        for detail in asn.details:
            InventoryService.asn_completed(detail.goods_id, asn.warehouse_id, detail.quantity,detail.actual_quantity)
    
        return asn
    
    @staticmethod
    @transactional
    def close_asn(asn_or_id: int | ASN):
        """
        将 ASN 标记为 'closed'（已关闭）。
        参数可以是 ASN 的 id（int）或 ASN 实例。
        如果找不到 ASN，则抛出 NotFound 异常。
        """
        asn = ASNService._get_instance(asn_or_id)

        # 判断是否为pending状态
        if asn.status != 'pending':
            raise BadRequestException("Cannot close a non-pending ASN", 16022)
                
        asn = ASNService._update_asn_status(asn, "closed")

        # 更新库存信息
        for detail in asn.details:
            InventoryService.update_and_calculate_asn_stock(detail.goods_id, asn.warehouse_id)
    
        return asn
    
    @staticmethod
    def get_asn_monthly_stats(months=6, filters=None):
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
        query = ASN.query.with_entities(
            extract('year', ASN.created_at).label('year'),
            extract('month', ASN.created_at).label('month'),
            func.sum(
                case((ASN.status == 'pending', 1), else_=0)
            ).label('pending'),
            func.sum(
                case((ASN.status == 'received', 1), else_=0)
            ).label('received'),
            func.sum(
                case((ASN.status == 'completed', 1), else_=0)
            ).label('completed'),
            func.sum(
                case((ASN.status == 'closed', 1), else_=0)
            ).label('closed')
        ).filter(
            ASN.created_at >= start_date,
            ASN.is_active == True  # 过滤有效单据
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('supplier_id'):
                query = query.filter(ASN.supplier_id == filters['supplier_id'])
            if filters.get('carrier_id'):
                query = query.filter(ASN.carrier_id == filters['carrier_id'])
            if filters.get('asn_type'):
                query = query.filter(ASN.asn_type == filters['asn_type'])

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
                'received': row.received or 0,
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
        status_order = ['pending', 'received', 'completed', 'closed']
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
                    db.extract('year', ASN.created_at) == target_year,
                    db.extract('month', ASN.created_at) == target_month
                ), 1),
                else_=0
            )

        # 重构查询语句
        query = ASN.query.with_entities(
            ASN.status,
            func.sum(build_case(current_year, current_month)).label('current_month'),
            func.sum(build_case(prev_month_year, prev_month_month)).label('previous_month'),
            func.sum(build_case(current_year-1, current_month)).label('last_year')
        ).filter(
            ASN.is_active == True
        )

        # 动态添加仓库过滤条件
        if filters:
            if filters.get('supplier_id'):
                query = query.filter(ASN.supplier_id == filters['supplier_id'])
            if filters.get('carrier_id'):
                query = query.filter(ASN.carrier_id == filters['carrier_id'])
            if filters.get('asn_type'):
                query = query.filter(ASN.asn_type == filters['asn_type'])

            # 处理单个仓库ID和多个仓库ID的情况
            if filters.get('warehouse_id'):
                query = query.filter(ASN.warehouse_id == filters['warehouse_id'])
            if filters.get('warehouse_ids'):
                query = query.filter(ASN.warehouse_id.in_(filters['warehouse_ids'])) 

        # 执行查询（建议添加缓存机制）
        raw_data = {
            row.status: row for row in query.group_by(ASN.status).all()
        }

        # 结果集构建（确保状态顺序）
        return [{
            "name": status,
            "current_month": getattr(raw_data.get(status), 'current_month', 0),
            "previous_month": getattr(raw_data.get(status), 'previous_month', 0),
            "last_year": getattr(raw_data.get(status), 'last_year', 0)
        } for status in ASN.ASN_STATUSES]