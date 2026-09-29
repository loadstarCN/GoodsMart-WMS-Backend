from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, case, extract
from extensions.db import *
from extensions.error import (
    BadRequestException, ConflictException, ForbiddenException, NotFoundException,
)
from extensions.transaction import transactional
from warehouse.common import require_positive_int, require_bulk_list, require_fields
from warehouse.inventory.services import InventoryService

from warehouse.goods.services import GoodsService
from system.webhook.services import emit as webhook_emit
from .models import DN, DNDetail


# ------------------------------------
# 出库线共用的请求体处理（picking / packing / delivery 也从这里引用）。
# 数量 / 列表 / 必填校验用 warehouse.common 的共享实现；这里只放白名单裁剪与日期解析。
# ------------------------------------

def pick_fields(data: dict, allowed) -> dict:
    """白名单裁剪：status / is_active / created_by / *_quantity / *_at 等只能由流程端点改。"""
    if not isinstance(data, dict):
        raise BadRequestException("Request body must be a JSON object", 16015)
    return {k: data[k] for k in allowed if k in data}


def parse_date_value(value, field: str):
    """'YYYY-MM-DD' 字符串或 date；格式不对 → 400"""
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), '%Y-%m-%d').date()
        except ValueError:
            pass
    raise BadRequestException(f"{field} must be a date in YYYY-MM-DD format", 16050)


def parse_datetime_value(value, field: str):
    """ISO 8601 / 'YYYY-MM-DD HH:MM:SS' 字符串或 datetime；格式不对 → 400"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            pass
    raise BadRequestException(f"{field} must be an ISO 8601 datetime", 16050)


def resolve_company_id(data: dict, warehouse) -> int:
    """按 goods_code / carrier_code 查主数据时使用的公司 = 单据仓库所属公司。

    仓库归属已由视图层 require_warehouse_scope 保证（员工 / 绑定公司的 API Key 都只能
    选自家仓库），所以不必再看 Staff.company_id 或 g.api_key_company_id；
    请求体显式给了 company_id 且与仓库公司不一致 → 403。绝不回落到 1。
    """
    company_id = warehouse.company_id
    if data.get('company_id') is not None and data['company_id'] != company_id:
        raise ForbiddenException("Permission denied: company_id does not match the warehouse", 12001)
    return company_id


# 请求体白名单：状态 / 审计 / 统计数量字段一律不接受客户端赋值
DN_CREATE_FIELDS = (
    'recipient_id', 'shipping_address', 'expected_shipping_date', 'warehouse_id',
    'carrier_id', 'carrier_code', 'dn_type', 'order_number', 'transportation_mode',
    'packaging_info', 'special_handling', 'remark',
)
DN_UPDATE_FIELDS = tuple(f for f in DN_CREATE_FIELDS if f != 'warehouse_id')
DN_DETAIL_FIELDS = ('goods_id', 'goods_code', 'quantity', 'remark')


class DNService:
    """
    A service class that encapsulates various operations
    related to DN and DNDetail.
    """

    # ------------------------------------
    # DN Services 私有方法
    # ------------------------------------

    @staticmethod
    def _own_reserved_by_goods(dn: DN | None) -> dict:
        """本 DN 当前已计入 Inventory.dn_stock 的预占量（按商品聚合）。
        新单 / 已关闭 / 已停用的 DN 没有预占，返回空。"""
        if dn is None or not dn.is_active or dn.status not in ('pending', 'in_progress'):
            return {}
        reserved = {}
        for detail in dn.details:
            reserved[detail.goods_id] = reserved.get(detail.goods_id, 0) + (detail.quantity or 0)
        return reserved

    @staticmethod
    def _assert_dn_stock_available(warehouse_id: int, requested_by_goods: dict, dn: DN | None = None):
        """校验各商品「本 DN 变更后的计划总量」不超过可用量。

        可用量 = onhand - locked - dn_stock + 本 DN 自身已预占量：dn_stock 已经包含了
        本 DN 现有明细，改量 / 加行 / 同步时必须把自己排除，否则会把自己算成别人的预占。
        create / create_detail / update_detail / sync 四处复用。
        """
        own = DNService._own_reserved_by_goods(dn)
        for goods_id, quantity in requested_by_goods.items():
            inventory = InventoryService._get_for_update(goods_id, warehouse_id)
            available = (
                inventory.onhand_stock
                - inventory.locked_stock
                - inventory.dn_stock
                + own.get(goods_id, 0)
            )
            if quantity > available:
                raise BadRequestException(
                    f"Insufficient available stock for goods {goods_id}: "
                    f"requested {quantity}, available {max(available, 0)}.",
                    16032,
                )

    @staticmethod
    def assert_company_master_data(company_id: int, recipient_id=None, carrier_id=None, goods_ids=()):
        """收货人 / 承运商 / 商品必须属于单据所在公司；不存在 400，跨公司 403。"""
        from warehouse.recipient.models import Recipient
        from warehouse.carrier.models import Carrier
        from warehouse.goods.models import Goods

        if recipient_id is not None:
            recipient = db.session.get(Recipient, recipient_id)
            if not recipient:
                raise BadRequestException(f"Recipient {recipient_id} not found", 16045)
            if recipient.company_id != company_id:
                raise ForbiddenException("Permission denied: recipient belongs to another company", 12001)

        if carrier_id is not None:
            carrier = db.session.get(Carrier, carrier_id)
            if not carrier:
                raise BadRequestException(f"Carrier {carrier_id} not found", 16045)
            if carrier.company_id != company_id:
                raise ForbiddenException("Permission denied: carrier belongs to another company", 12001)

        goods_ids = set(goods_ids)
        if goods_ids:
            rows = db.session.query(Goods.id, Goods.company_id).filter(Goods.id.in_(goods_ids)).all()
            found = {gid: cid for gid, cid in rows}
            missing = goods_ids - set(found)
            if missing:
                raise BadRequestException(f"Goods not found: {sorted(missing)}", 16045)
            foreign = [gid for gid, cid in found.items() if cid != company_id]
            if foreign:
                raise ForbiddenException("Permission denied: goods belongs to another company", 12001)

    @staticmethod
    def _resolve_carrier_by_code(company_id: int, carrier_code: str):
        """跨系统可直接传承运商 code，避免 Wholesale 依赖 WMS 内部自增 ID。"""
        from warehouse.carrier.models import Carrier
        carrier_code = carrier_code.strip().lower()
        carrier_aliases = {
            'yamato': ('yamato', 'ヤマト'),
            'sagawa': ('sagawa', '佐川'),
            'sf': ('sf', 'sf-express', '順豊'),
            'ems': ('ems',),
            'dhl': ('dhl',),
        }
        return Carrier.query.filter(
            Carrier.company_id == company_id,
            Carrier.is_active.is_(True),
            db.or_(
                func.lower(Carrier.code) == carrier_code,
                *[
                    Carrier.name.ilike(f'%{alias}%')
                    for alias in carrier_aliases.get(carrier_code, (carrier_code,))
                ],
            ),
        ).order_by(Carrier.id.asc()).first()

    @staticmethod
    def _resolve_goods_id(item: dict, company_id: int) -> int:
        """明细里 goods_id / goods_code 二选一；按公司查 code。"""
        goods_id = item.get('goods_id')
        if goods_id is None and item.get('goods_code'):
            goods = GoodsService.get_goods_by_code(item['goods_code'], company_id)
            if not goods:
                raise BadRequestException(f"Goods not found for code: {item['goods_code']}", 16030)
            return goods.id
        if goods_id is None:
            raise BadRequestException("goods_id or goods_code is required for detail", 16031)
        return require_positive_int(goods_id, 'goods_id', 16031)

    @staticmethod
    def _validate_dn_enums(payload: dict):
        if payload.get('dn_type') is not None and payload['dn_type'] not in DN.DN_TYPES:
            raise BadRequestException(f"Invalid dn_type: {payload['dn_type']}", 16047)
        mode = payload.get('transportation_mode')
        if mode is not None and mode not in DN.DN_TRANSPORTATION_MODES:
            raise BadRequestException(f"Invalid transportation_mode: {mode}", 16048)

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
        已拣 / 已打包量各用一次按商品聚合的查询算出，不再逐明细查两次。
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

        # 1. 已拣选数量（来自已完成的 PickingTask，按商品聚合）
        picked_by_goods = dict(
            db.session.query(
                PickingTaskDetail.goods_id,
                func.coalesce(func.sum(PickingTaskDetail.picked_quantity), 0),
            )
            .join(PickingTask, PickingTaskDetail.picking_task_id == PickingTask.id)
            .filter(
                PickingTask.dn_id == dn.id,
                PickingTask.is_active == True,
                PickingTask.status == 'completed',
            )
            .group_by(PickingTaskDetail.goods_id)
            .all()
        )

        # 2. 已打包数量（来自已完成的 PackingTask，按商品聚合）
        packed_by_goods = dict(
            db.session.query(
                PackingTaskDetail.goods_id,
                func.coalesce(func.sum(PackingTaskDetail.packed_quantity), 0),
            )
            .join(PackingTask, PackingTaskDetail.packing_task_id == PackingTask.id)
            .filter(
                PackingTask.dn_id == dn.id,
                PackingTask.is_active == True,
                PackingTask.status == 'completed',
            )
            .group_by(PackingTaskDetail.goods_id)
            .all()
        )

        for dn_detail in dn.details:
            picked_quantity = int(picked_by_goods.get(dn_detail.goods_id, 0) or 0)
            packed_quantity = int(packed_by_goods.get(dn_detail.goods_id, 0) or 0)
            # 3. 已发货数量（如果存在已完成的 DeliveryTask，则等于已打包数量）
            delivered_quantity = packed_quantity if has_completed_delivery else 0

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
        创建一个新的 DN 及其明细（details 必填且非空）。

        只接受白名单字段；status 固定 pending、is_active 固定 True，
        picked/packed/delivered_quantity 由流程计算，客户端传入一律忽略。
        收货人 / 承运商 / 商品必须与仓库属于同一公司。

        :param data: DN 数据（可包含 details）
        :param created_by_id: 当前用户 ID
        :return: 新创建的 DN 对象
        """
        from warehouse.warehouse.models import Warehouse

        payload = pick_fields(data, DN_CREATE_FIELDS)
        require_fields(payload, 'recipient_id', 'shipping_address', 'expected_shipping_date', 'warehouse_id')
        details_data = require_bulk_list(data.get('details'), 'details')

        warehouse_id = require_positive_int(payload['warehouse_id'], 'warehouse_id', 16044)
        warehouse = db.session.get(Warehouse, warehouse_id)
        if not warehouse:
            raise BadRequestException(f"Warehouse {warehouse_id} not found", 16045)

        company_id = resolve_company_id(data, warehouse)

        payload['expected_shipping_date'] = parse_date_value(payload['expected_shipping_date'], 'expected_shipping_date')
        DNService._validate_dn_enums(payload)

        if not payload.get('carrier_id') and payload.get('carrier_code'):
            carrier = DNService._resolve_carrier_by_code(company_id, payload['carrier_code'])
            if carrier:
                payload['carrier_id'] = carrier.id

        resolved_details = []
        requested_by_goods = {}
        for item in details_data:
            item = pick_fields(item, DN_DETAIL_FIELDS)
            goods_id = DNService._resolve_goods_id(item, company_id)
            if goods_id in requested_by_goods:
                raise BadRequestException(f"Duplicate goods_id: {goods_id}", 16025)
            quantity = require_positive_int(item.get('quantity'), 'quantity')
            requested_by_goods[goods_id] = quantity
            resolved_details.append({'goods_id': goods_id, 'quantity': quantity, 'remark': item.get('remark', '')})

        DNService.assert_company_master_data(
            company_id,
            recipient_id=payload['recipient_id'],
            carrier_id=payload.get('carrier_id'),
            goods_ids=requested_by_goods.keys(),
        )

        # 海外件：顶层 customs 为报关快照（国内件不带）。结构错误在建单前拒绝（16063 / 16064），
        # 内容不全照收，由 GET /dn/<id>/customs 的 problems 列出。
        customs_payload = None
        if data.get('customs') is not None:
            from .customs_services import CustomsService
            customs_payload = CustomsService.parse_for_new_dn(data['customs'], requested_by_goods.keys())

        # 新单一律 pending 并预占库存：这里拒绝超额预占，
        # 让不可能完成的集成报文根本进不了拣货。
        DNService._assert_dn_stock_available(warehouse_id, requested_by_goods)

        new_dn = DN(
            recipient_id=payload['recipient_id'],
            shipping_address=payload['shipping_address'],
            expected_shipping_date=payload['expected_shipping_date'],
            warehouse_id=warehouse_id,
            carrier_id=payload.get('carrier_id'),
            dn_type=payload.get('dn_type') or 'shipping',  # 默认 shipping
            status='pending',
            order_number=payload.get('order_number'),
            transportation_mode=payload.get('transportation_mode'),
            packaging_info=payload.get('packaging_info'),
            special_handling=payload.get('special_handling'),
            remark=payload.get('remark'),
            is_active=True,
            created_by=created_by_id,
            api_key_id=data.get('api_key_id'),
        )
        db.session.add(new_dn)
        db.session.flush()

        for detail in resolved_details:
            new_detail = DNDetail(
                dn_id=new_dn.id,
                goods_id=detail['goods_id'],
                quantity=detail['quantity'],
                remark=detail['remark'],
                created_by=created_by_id
            )
            db.session.add(new_detail)
            db.session.flush()

            InventoryService.update_and_calculate_dn_stock(new_detail.goods_id,new_dn.warehouse_id)

        if customs_payload is not None:
            from .customs_services import CustomsService
            CustomsService.store_snapshot(new_dn, customs_payload, created_by_id)

        # db.session.commit()

        return new_dn

    @staticmethod
    @transactional
    def update_dn(dn_or_id: int | DN, data: dict) -> DN:
        """
        更新指定的 DN 记录（仅当其状态为 pending 时允许更新）。
        只接受白名单字段：status / is_active / created_by / *_at 由流程端点变更，客户端传入忽略。

        :param dn_id: 待更新的 DN ID
        :param data: 要更新的字段
        :return: 更新后的 DN 对象
        :raises: NotFound / ValueError
        """

        dn = DNService._get_instance(dn_or_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot update a non-pending DN", 16001)

        payload = pick_fields(data, DN_UPDATE_FIELDS)
        company_id = dn.warehouse.company_id

        if 'expected_shipping_date' in payload:
            payload['expected_shipping_date'] = parse_date_value(payload['expected_shipping_date'], 'expected_shipping_date')
        DNService._validate_dn_enums(payload)

        if not payload.get('carrier_id') and payload.get('carrier_code'):
            carrier = DNService._resolve_carrier_by_code(company_id, payload['carrier_code'])
            if carrier:
                payload['carrier_id'] = carrier.id

        DNService.assert_company_master_data(
            company_id,
            recipient_id=payload.get('recipient_id'),
            carrier_id=payload.get('carrier_id'),
        )

        # NOT NULL 字段：传 None 视为不改
        for field in ('recipient_id', 'shipping_address', 'expected_shipping_date', 'dn_type'):
            if payload.get(field) is not None:
                setattr(dn, field, payload[field])
        # 可空字段：显式传 None 允许清空
        for field in ('carrier_id', 'order_number', 'transportation_mode',
                      'packaging_info', 'special_handling', 'remark'):
            if field in payload:
                setattr(dn, field, payload[field])

        db.session.add(dn)
        db.session.flush()

        if 'details' in data:
            details_data = require_bulk_list(data.get('details'), 'details')
            DNService.sync_dn_details(dn, details_data, dn.created_by)  # 更新明细

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
        同一商品在一张 DN 上只能有一行；数量不得超过排除本单预占后的可用量。
        """
        dn = DNService.get_dn(dn_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot add details to a non-pending DN", 16003)

        item = pick_fields(data, DN_DETAIL_FIELDS)
        company_id = dn.warehouse.company_id
        goods_id = DNService._resolve_goods_id(item, company_id)
        quantity = require_positive_int(item.get('quantity'), 'quantity')

        if any(d.goods_id == goods_id for d in dn.details):
            raise BadRequestException(f"Duplicate goods_id: {goods_id}", 16025)

        DNService.assert_company_master_data(company_id, goods_ids=[goods_id])
        DNService._assert_dn_stock_available(dn.warehouse_id, {goods_id: quantity}, dn)

        new_detail = DNDetail(
            dn_id=dn_id,
            goods_id=goods_id,
            quantity=quantity,
            remark=item.get('remark', ''),
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
        只接受 goods_id / goods_code / quantity / remark；picked/packed/delivered_quantity 忽略。
        """
        dn = DNService.get_dn(dn_id)
        if dn.status != 'pending':
            raise BadRequestException("Cannot update details in a non-pending DN", 16004)

        detail = DNService.get_dn_detail(dn_id, detail_id)
        item = pick_fields(update_data, DN_DETAIL_FIELDS)
        company_id = dn.warehouse.company_id

        goods_id = detail.goods_id
        if item.get('goods_id') is not None or item.get('goods_code'):
            goods_id = DNService._resolve_goods_id(item, company_id)
        quantity = detail.quantity
        if 'quantity' in item:
            quantity = require_positive_int(item.get('quantity'), 'quantity')

        if goods_id != detail.goods_id:
            if any(d.goods_id == goods_id for d in dn.details if d.id != detail.id):
                raise BadRequestException(f"Duplicate goods_id: {goods_id}", 16025)
            DNService.assert_company_master_data(company_id, goods_ids=[goods_id])

        # 本 DN 变更后该商品的计划总量（同商品其它行 + 本行新量）
        other_total = sum(
            (d.quantity or 0) for d in dn.details
            if d.id != detail.id and d.goods_id == goods_id
        )
        DNService._assert_dn_stock_available(dn.warehouse_id, {goods_id: other_total + quantity}, dn)

        old_goods_id = detail.goods_id
        detail.goods_id = goods_id
        detail.quantity = quantity
        if 'remark' in item:
            detail.remark = item['remark']

        db.session.add(detail)
        db.session.flush()
        InventoryService.update_and_calculate_dn_stock(detail.goods_id,dn.warehouse_id)
        if old_goods_id != detail.goods_id:
            InventoryService.update_and_calculate_dn_stock(old_goods_id, dn.warehouse_id)

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
        每个商品变更后的计划量都要通过「排除本单预占后的可用量」校验。
        """
        dn = DNService._get_instance(dn_or_id)

        if dn.status != 'pending':
            raise BadRequestException("Cannot sync details in non-pending DN", 16006)

        details_data = require_bulk_list(details_data, 'details', allow_empty=True)
        company_id = dn.warehouse.company_id
        existing_details = {d.id: d for d in dn.details}
        touched_goods_ids = {d.goods_id for d in dn.details}

        # 先解析并校验全部明细，再落库
        resolved = []
        requested_by_goods = {}
        for raw in details_data:
            item = pick_fields(raw, DN_DETAIL_FIELDS + ('id',))
            goods_id = DNService._resolve_goods_id(item, company_id)
            # 商品ID重复校验
            if goods_id in requested_by_goods:
                raise BadRequestException(f"Duplicate goods_id: {goods_id}", 16025)
            quantity = require_positive_int(item.get('quantity'), 'quantity')
            requested_by_goods[goods_id] = quantity
            resolved.append((item.get('id'), goods_id, quantity, item.get('remark', '')))

        DNService.assert_company_master_data(company_id, goods_ids=requested_by_goods.keys())
        DNService._assert_dn_stock_available(dn.warehouse_id, requested_by_goods, dn)

        new_detail_ids = set()
        for detail_id, goods_id, quantity, remark in resolved:
            touched_goods_ids.add(goods_id)
            if detail_id in existing_details:
                # 更新现有记录
                detail = existing_details[detail_id]
                detail.goods_id = goods_id
                detail.quantity = quantity
                detail.remark = remark
                db.session.add(detail)
                new_detail_ids.add(detail.id)
            else:
                # 创建新记录
                new_detail = DNDetail(
                    dn_id=dn.id,
                    goods_id=goods_id,
                    quantity=quantity,
                    remark=remark,
                    created_by=created_by
                )
                db.session.add(new_detail)
                db.session.flush()
                new_detail_ids.add(new_detail.id)

        # 删除不存在于新数据中的记录
        for detail_id, detail in existing_details.items():
            if detail_id not in new_detail_ids:
                db.session.delete(detail)

        db.session.flush()
        db.session.expire(dn, ['details'])

        # 涉及到的商品（新增 / 更新 / 删除 / 换货前后）统一重算预占
        for goods_id in touched_goods_ids:
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

        # Revalidate just before work starts. Stock may have changed since the
        # DN was created, especially for old integrations that over-reserved.
        for detail in dn.details:
            inventory = InventoryService._get_for_update(
                detail.goods_id, dn.warehouse_id
            )
            physical_available = inventory.onhand_stock - inventory.locked_stock
            if detail.quantity > physical_available:
                raise BadRequestException(
                    f"Insufficient physical stock for goods {detail.goods_id}: "
                    f"requested {detail.quantity}, available {max(physical_available, 0)}.",
                    16037,
                )

        dn = DNService._update_dn_status(dn, "in_progress")

        # 创建拣货
        from warehouse.picking.services import PickingTaskService
        PickingTaskService.create_picking_task_from_dn(dn.id,dn.created_by)

        webhook_emit('dn.in_progress', {
            'dn_id': dn.id, 'status': 'in_progress', 'order_number': dn.order_number,
        }, api_key_id=dn.api_key_id)

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

        # 获取 tracking_number
        from warehouse.delivery.models import DeliveryTask
        delivery_task = DeliveryTask.query.filter(
            DeliveryTask.dn_id == dn.id,
            DeliveryTask.is_active == True,
        ).first()
        tracking_number = delivery_task.tracking_number if delivery_task else None

        payload = {
            'dn_id': dn.id, 'status': 'delivered', 'order_number': dn.order_number,
            'tracking_number': tracking_number,
            'details': [
                {
                    'goods_code': detail.goods.code,
                    'planned_quantity': detail.quantity,
                    'picked_quantity': detail.picked_quantity,
                    'delivered_quantity': detail.delivered_quantity,
                }
                for detail in dn.details
            ],
        }
        # 海外件追加：当前单证（CI / PL）、箱子、发票合计；国内件 payload 不变
        from .customs_services import CustomsService
        payload.update(CustomsService.delivered_webhook_fields(dn))
        webhook_emit('dn.delivered', payload, api_key_id=dn.api_key_id)

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

        webhook_emit('dn.completed', {
            'dn_id': dn.id, 'status': 'completed', 'order_number': dn.order_number,
            'details': [
                {
                    'goods_code': detail.goods.code,
                    'planned_quantity': detail.quantity,
                    'picked_quantity': detail.picked_quantity,
                    'delivered_quantity': detail.delivered_quantity,
                }
                for detail in dn.details
            ],
        }, api_key_id=dn.api_key_id)

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
    @transactional
    def cancel_dn(dn_or_id: int | DN) -> DN:
        """
        取消 DN 并释放 dn_stock 预占：
        - pending：等同 close
        - in_progress：拣货任务尚无任何批次 / 明细时允许取消，拣货任务一并停用
        - 其它状态：拣货已经发生，409
        """
        dn = DNService._get_instance(dn_or_id)
        if dn.status == 'pending':
            return DNService.close_dn(dn)
        if dn.status != 'in_progress':
            raise ConflictException(f"Cannot cancel a DN in '{dn.status}' status.", 16052)

        from warehouse.picking.models import PickingTask
        tasks = PickingTask.query.filter(
            PickingTask.dn_id == dn.id,
            PickingTask.is_active.is_(True),
        ).all()
        for task in tasks:
            if task.status == 'completed' or task.batches or task.task_details:
                raise ConflictException(
                    "Cannot cancel a DN whose picking has already started.", 16053
                )

        for task in tasks:
            task.is_active = False
            db.session.add(task)

        dn = DNService._update_dn_status(dn, "closed")
        for detail in dn.details:
            InventoryService.update_and_calculate_dn_stock(detail.goods_id, dn.warehouse_id)

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
