from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from sqlalchemy.orm import selectinload
from extensions.db import *
from extensions.error import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from extensions.transaction import transactional
from warehouse.common import require_positive_int, require_fields
from warehouse.carrier.models import Carrier
from warehouse.goods.models import Goods
from warehouse.goods.services import GoodsService
from warehouse.inventory.services import InventoryService
from warehouse.supplier.models import Supplier
from warehouse.warehouse.models import Warehouse
from system.webhook.services import emit as webhook_emit
from .models import ASN, ASNDetail


class ASNService:
    """
    A service class that encapsulates various operations
    related to ASN and ASNDetail.
    """

    # 单据头允许由客户端写入的字段。status / is_active / created_by / *_at 等只能经
    # receive / complete / close / cancel 动作端点变更，客户端传了也一律忽略。
    ASN_WRITABLE_FIELDS = (
        'supplier_id', 'tracking_number', 'carrier_id', 'asn_type',
        'expected_arrival_date', 'order_number', 'remark',
    )
    # 明细允许写入的字段。actual / sorted / damage_quantity 是分拣完成时聚合得出的过程量。
    DETAIL_WRITABLE_FIELDS = ('quantity', 'weight', 'volume', 'remark')

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
    def _warehouse_company_id(warehouse_id) -> int:
        """单据仓库所属公司：goods_code / carrier_code / supplier 归属校验都以它为准"""
        warehouse = db.session.get(Warehouse, warehouse_id) if warehouse_id else None
        if not warehouse:
            raise BadRequestException("Invalid warehouse ID", 14009)
        return warehouse.company_id

    @staticmethod
    def _assert_company_consistent(data: dict, company_id: int):
        """body 显式给出的 company_id（API Key 路径由 view 注入）必须与单据仓库所属公司一致"""
        given = data.get('company_id')
        if given is not None and given != company_id:
            raise ForbiddenException("Permission denied: company_id does not match the warehouse's company", 12001)

    @staticmethod
    def _assert_partner_company(company_id: int, supplier_id=None, carrier_id=None):
        """供应商 / 承运商必须与单据仓库同属一家公司"""
        if supplier_id is not None:
            supplier = db.session.get(Supplier, supplier_id)
            if not supplier:
                raise NotFoundException(f"Supplier with id {supplier_id} not found", 13001)
            if supplier.company_id != company_id:
                raise ForbiddenException("Permission denied: supplier belongs to another company", 12001)
        if carrier_id is not None:
            carrier = db.session.get(Carrier, carrier_id)
            if not carrier:
                raise NotFoundException(f"Carrier with id {carrier_id} not found", 13001)
            if carrier.company_id != company_id:
                raise ForbiddenException("Permission denied: carrier belongs to another company", 12001)

    @staticmethod
    def _resolve_goods_id(item: dict, company_id: int) -> int:
        """按 goods_id / goods_code 解析商品，并校验商品属于单据仓库所在公司"""
        goods_id = item.get('goods_id')
        if goods_id:
            goods = db.session.get(Goods, goods_id)
            if not goods:
                raise NotFoundException(f"Goods with id {goods_id} not found", 13001)
        elif item.get('goods_code'):
            goods = GoodsService.get_goods_by_code(item['goods_code'], company_id)
            if not goods:
                raise BadRequestException(f"Goods not found for code: {item['goods_code']}", 16030)
        else:
            raise BadRequestException("goods_id or goods_code is required for detail", 16031)
        if goods.company_id != company_id:
            raise ForbiddenException("Permission denied: goods belongs to another company", 12001)
        return goods.id

    @staticmethod
    def _parse_date(value):
        """expected_arrival_date：接受 None / date / 'YYYY-MM-DD'"""
        if value is None or isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                pass
        raise BadRequestException("expected_arrival_date must be in YYYY-MM-DD format", 14021)

    @staticmethod
    def _detail_payload(item: dict, company_id: int, require_quantity: bool = True) -> dict:
        """从客户端明细中提取允许写入的字段（白名单），并解析 / 校验商品与数量"""
        payload = {}
        if require_quantity or 'goods_id' in item or 'goods_code' in item:
            payload['goods_id'] = ASNService._resolve_goods_id(item, company_id)
        if require_quantity or 'quantity' in item:
            payload['quantity'] = require_positive_int(item.get('quantity'), 'quantity')
        for field in ('weight', 'volume', 'remark'):
            if field in item:
                payload[field] = item[field]
        return payload

    @staticmethod
    def _ensure_inventory(goods_id: int, warehouse_id: int):
        """明细涉及的商品在该仓库必须有库存记录，没有则创建"""
        try:
            InventoryService.get_inventory(goods_id, warehouse_id)
        except NotFoundException:
            InventoryService.create_inventory({"goods_id": goods_id, "warehouse_id": warehouse_id})
            db.session.flush()

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

        # 一次按商品聚合该 ASN 所有已完成分拣任务的合格 / 损坏数量，避免逐明细查询
        rows = (
            db.session.query(
                SortingTaskDetail.goods_id,
                func.coalesce(func.sum(SortingTaskDetail.sorted_quantity), 0),
                func.coalesce(func.sum(SortingTaskDetail.damage_quantity), 0),
            )
            .join(SortingTask, SortingTaskDetail.sorting_task_id == SortingTask.id)
            .filter(
                SortingTask.asn_id == asn.id,
                SortingTask.is_active == True,
                SortingTask.status == 'completed'
            )
            .group_by(SortingTaskDetail.goods_id)
            .all()
        )
        totals = {goods_id: (sorted_qty, damage_qty) for goods_id, sorted_qty, damage_qty in rows}

        for asn_detail in asn.details:
            sorted_quantity, damage_quantity = totals.get(asn_detail.goods_id, (0, 0))
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
        # 列表 schema 逐行汇总 details（detail_count / total_* 等），预加载避免 N+1
        query = ASN.query.options(selectinload(ASN.details)).order_by(ASN.id.desc())

        if filters.get('asn_type'):
            query = query.filter(ASN.asn_type == filters['asn_type'])
        if filters.get('status'):
            query = query.filter(ASN.status == filters['status'])
        if filters.get('tracking_number'):
            query = query.filter(ASN.tracking_number.ilike(f"%{filters['tracking_number']}%"))
        if filters.get('order_number'):
            query = query.filter(ASN.order_number.ilike(f"%{filters['order_number']}%"))
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

        只接受 ASN_WRITABLE_FIELDS / DETAIL_WRITABLE_FIELDS 里的字段；
        status 固定为 pending，is_active 固定为 True。

        :param data: ASN 数据（包含 details）
        :param created_by_id: 当前用户 ID
        :return: 新创建的 ASN 对象
        """
        require_fields(data, 'warehouse_id', 'supplier_id')
        warehouse_id = data['warehouse_id']
        company_id = ASNService._warehouse_company_id(warehouse_id)
        ASNService._assert_company_consistent(data, company_id)

        supplier_id = data['supplier_id']

        # 支持通过 carrier_code 解析 carrier_id（跨系统匹配），只在单据仓库所属公司内查找
        carrier_id = data.get('carrier_id')
        if not carrier_id and data.get('carrier_code'):
            carrier = Carrier.query.filter_by(code=data['carrier_code'], company_id=company_id).first()
            if carrier:
                carrier_id = carrier.id
        ASNService._assert_partner_company(company_id, supplier_id=supplier_id, carrier_id=carrier_id)

        asn_type = data.get('asn_type') or 'inbound'  # 默认为 inbound
        if asn_type not in ASN.ASN_TYPES:
            raise BadRequestException(f"Invalid asn_type: {asn_type}", 14007)

        new_asn = ASN(
            supplier_id=supplier_id,
            tracking_number=data.get('tracking_number'),
            warehouse_id=warehouse_id,
            carrier_id=carrier_id,
            asn_type=asn_type,
            status='pending',
            expected_arrival_date=ASNService._parse_date(data.get('expected_arrival_date')),
            order_number=data.get('order_number'),
            remark=data.get('remark'),
            created_by=created_by_id,
            api_key_id=data.get('api_key_id'),
        )
        db.session.add(new_asn)
        db.session.flush()

        details_data = data.get('details') or []
        if not isinstance(details_data, list):
            raise BadRequestException("'details' must be a list", 16015)

        # 创建 ASN 明细（如果有）
        for detail in details_data:
            payload = ASNService._detail_payload(detail, company_id)
            new_detail = ASNDetail(asn_id=new_asn.id, created_by=created_by_id, **payload)
            db.session.add(new_detail)
            db.session.flush()

            ASNService._ensure_inventory(new_detail.goods_id, new_asn.warehouse_id)
            # 重新计算库存
            InventoryService.update_and_calculate_asn_stock(new_detail.goods_id, new_asn.warehouse_id)

        # db.session.commit()
        return new_asn

    @staticmethod
    @transactional
    def update_asn(asn_or_id: int | ASN, data: dict) -> ASN:
        """
        更新指定的 ASN 记录（仅当其状态为 pending 时允许更新）。
        只接受 ASN_WRITABLE_FIELDS 里的字段，status / is_active 等一律忽略。

        :param asn_id: 待更新的 ASN ID
        :param data: 要更新的字段
        :return: 更新后的 ASN 对象
        :raises: NotFound 如果该 ASN 不存在
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot update a non-pending ASN", 16001)

        company_id = asn.warehouse.company_id
        ASNService._assert_company_consistent(data, company_id)
        ASNService._assert_partner_company(
            company_id, supplier_id=data.get('supplier_id'), carrier_id=data.get('carrier_id')
        )
        if data.get('asn_type') and data['asn_type'] not in ASN.ASN_TYPES:
            raise BadRequestException(f"Invalid asn_type: {data['asn_type']}", 14007)

        for field in ASNService.ASN_WRITABLE_FIELDS:
            if field not in data:
                continue
            value = data[field]
            if field == 'expected_arrival_date':
                value = ASNService._parse_date(value)
            setattr(asn, field, value)

        db.session.add(asn)
        db.session.flush()

        if 'details' in data:
            ASNService.sync_asn_details(asn, data.get('details') or [], asn.created_by)  # 更新明细

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
    def list_asn_details(asn_or_id: int | ASN):
        """
        获取指定 ASN 下的所有 ASNDetail 列表
        """
        asn = ASNService._get_instance(asn_or_id)  # 如果不存在会 404
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
    def create_asn_detail(asn_or_id: int | ASN, data: dict, created_by_id: int) -> ASNDetail:
        """
        在指定 ASN 下创建一条 ASNDetail（仅当 ASN 状态为 pending 时）
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot add details to a non-pending ASN", 16003)

        payload = ASNService._detail_payload(data, asn.warehouse.company_id)
        new_detail = ASNDetail(asn_id=asn.id, created_by=created_by_id, **payload)
        db.session.add(new_detail)
        db.session.flush()

        # 更新库存信息
        ASNService._ensure_inventory(new_detail.goods_id, asn.warehouse_id)
        InventoryService.update_and_calculate_asn_stock(new_detail.goods_id, asn.warehouse_id)
        # db.session.commit()
        return new_detail

    @staticmethod
    @transactional
    def update_asn_detail(asn_or_id: int | ASN, detail_id: int, update_data: dict) -> ASNDetail:
        """
        Update a single ASNDetail record for the specified ASN.
        只接受 DETAIL_WRITABLE_FIELDS（以及换商品用的 goods_id / goods_code）。
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot update details in a non-pending ASN", 16004)

        detail = ASNService.get_asn_detail(asn.id, detail_id)
        old_goods_id = detail.goods_id

        payload = ASNService._detail_payload(update_data, asn.warehouse.company_id, require_quantity=False)
        for field, value in payload.items():
            setattr(detail, field, value)

        db.session.add(detail)
        db.session.flush()

        # 换了商品时，新旧商品的 ASN 库存都要重算
        if detail.goods_id != old_goods_id:
            ASNService._ensure_inventory(detail.goods_id, asn.warehouse_id)
            InventoryService.update_and_calculate_asn_stock(old_goods_id, asn.warehouse_id)
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
        if not isinstance(details_data, list):
            raise BadRequestException("'details' must be a list", 16015)

        company_id = asn.warehouse.company_id
        existing_details = {d.id: d for d in asn.details}
        new_detail_ids = set()

        # 处理更新和新增
        for item in details_data:
            detail_id = item.get('id')

            if detail_id and detail_id in existing_details:
                # 更新现有记录（只更新白名单字段，不换商品）
                detail = existing_details[detail_id]
                if 'quantity' in item:
                    detail.quantity = require_positive_int(item.get('quantity'), 'quantity')
                for field in ('weight', 'volume', 'remark'):
                    if field in item:
                        setattr(detail, field, item[field])
                db.session.add(detail)
                new_detail_ids.add(detail_id)
            else:
                # 创建新记录
                payload = ASNService._detail_payload(item, company_id)
                new_detail = ASNDetail(asn_id=asn.id, created_by=created_by, **payload)
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

        # 现存明细与被删明细涉及的商品都要重算 ASN 库存
        for goods_id in {d.goods_id for d in latest_details} | deleted_goods_ids:
            ASNService._ensure_inventory(goods_id, asn.warehouse_id)
            InventoryService.update_and_calculate_asn_stock(goods_id, asn.warehouse_id)

        return asn.details


    @staticmethod
    @transactional
    def delete_asn_detail(asn_or_id: int | ASN, detail_id: int):
        """
        删除指定的 ASNDetail（仅当其所属 ASN 状态为 pending 时）
        """
        asn = ASNService._get_instance(asn_or_id)
        if asn.status != 'pending':
            raise BadRequestException("Cannot delete details from a non-pending ASN", 16005)

        detail = ASNService.get_asn_detail(asn.id, detail_id)
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

        webhook_emit('asn.received', {
            'asn_id': asn.id, 'status': 'received', 'order_number': getattr(asn, 'order_number', None),
            'details': [{'goods_code': d.goods.code if d.goods else None,
                         'quantity': d.quantity} for d in asn.details],
        }, api_key_id=asn.api_key_id)

        return asn

    @staticmethod
    def _goods_spec_payload(goods) -> dict:
        """asn.completed 明细附带的商品主数据规格（单件）

        分拣站（Station）在完成分拣任务之前把称重/测量结果写进商品主数据，
        所以这里读到的就是本次入库刚测出的值。单位写进字段名，避免订阅方误读：
        重量 kg（Numeric → float，否则 JSON 序列化失败），尺寸 mm。
        未测量的为 None。
        """
        if goods is None:
            return {}
        return {
            'goods_weight_kg': float(goods.weight) if goods.weight is not None else None,
            'goods_length_mm': goods.length,
            'goods_width_mm': goods.width,
            'goods_height_mm': goods.height,
        }

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
            raise BadRequestException("Cannot complete a non-received ASN", 16022)


        asn = ASNService._update_asn_status(asn, "completed")
        ASNService._update_and_calculate_quantity(asn)

        # 更新库存信息
        for detail in asn.details:
            InventoryService.asn_completed(detail.goods_id, asn.warehouse_id, detail.quantity,detail.actual_quantity)

        webhook_emit('asn.completed', {
            'asn_id': asn.id, 'status': 'completed', 'order_number': asn.order_number,
            'details': [{'goods_code': d.goods.code if d.goods else None,
                         'actual_quantity': d.actual_quantity,
                         'quantity': d.quantity,
                         'sorted_quantity': d.sorted_quantity,
                         'damage_quantity': d.damage_quantity,
                         # weight/volume 为该明细行的合计值（kg / m³），
                         # 入库分拣时在 WMS 称量录入，是重量数据的唯一来源，
                         # 由订阅方（Wholesale）换算回填其商品主数据
                         'weight': d.weight,
                         'volume': d.volume,
                         **ASNService._goods_spec_payload(d.goods)} for d in asn.details],
        }, api_key_id=asn.api_key_id)

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
    @transactional
    def cancel_asn(asn_or_id: int | ASN):
        """
        取消 ASN：
        - pending：等同 close
        - received 且分拣任务尚无任何批次 / 明细：回滚 asn_received 的签收库存，
          停用分拣任务并关闭单据
        - 其它状态（completed / closed，或分拣已有进度）：409
        """
        from warehouse.sorting.models import SortingTask

        asn = ASNService._get_instance(asn_or_id)
        if asn.status == 'pending':
            return ASNService.close_asn(asn)
        if asn.status != 'received':
            raise ConflictException(f"Cannot cancel an ASN in '{asn.status}' status", 16059)

        tasks = SortingTask.query.filter_by(asn_id=asn.id, is_active=True).all()
        for task in tasks:
            if task.status == 'completed' or task.batches or task.task_details:
                raise ConflictException("Cannot cancel an ASN whose sorting task already has progress", 16060)

        for detail in asn.details:
            InventoryService.asn_cancelled(detail.goods_id, asn.warehouse_id, detail.quantity)
        for task in tasks:
            task.is_active = False
            db.session.add(task)

        asn = ASNService._update_asn_status(asn, "closed")
        for detail in asn.details:
            InventoryService.update_and_calculate_asn_stock(detail.goods_id, asn.warehouse_id)

        webhook_emit('asn.cancelled', {
            'asn_id': asn.id, 'status': 'closed', 'order_number': asn.order_number,
            'details': [{'goods_code': d.goods.code if d.goods else None,
                         'quantity': d.quantity} for d in asn.details],
        }, api_key_id=asn.api_key_id)

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
