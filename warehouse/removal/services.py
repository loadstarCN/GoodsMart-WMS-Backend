from sqlalchemy import or_
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.common import (
    require_positive_int, require_bulk_list, require_fields, lock_goods_location, lock_inventory_rows,
    lock_stock_for_bulk,
)
from warehouse.goods.models import Goods
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from warehouse.common import clock
from .models import RemovalRecord


class RemovalService:
    """
    A service class that encapsulates the operations
    related to RemovalRecord.
    """

    # 对外下架接口（/removal/、/removal/bulk）允许的 reason。
    # 覆盖 Web（damaged / returned / transfer / picked / expired / lost / other）与 APP（return / damage / transfer）
    # 实际传的值；这些下架的货一律进 sorted_stock 待上架。
    # 'picking' 不在其中：拣货下架只由拣货完成在内部以 from_picking=True 记账（货走 picked_stock），
    # 客户端传 'picking' 会让货既不进 sorted_stock 也不进 picked_stock，等于免审批报损。
    PUBLIC_REASONS = frozenset({
        'return', 'returned', 'damage', 'damaged', 'transfer', 'picked', 'expired', 'lost', 'other',
    })
    PICKING_REASON = 'picking'

    @staticmethod
    def list_removal_records(filters: dict):
        """
        根据过滤条件，返回 RemovalRecord 的查询对象，并按 id 降序排序。
        """
        query = RemovalRecord.query.order_by(RemovalRecord.id.desc())

        # 基础过滤条件
        if filters.get('goods_id'):
            query = query.filter(RemovalRecord.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(RemovalRecord.location_id == filters['location_id'])
        if filters.get('operator_id'):
            query = query.filter(RemovalRecord.operator_id == filters['operator_id'])
        # 时间范围：只给日期的按所筛仓库当地的那一天（end_time 含当天），不带时区的时刻按仓库时区
        query = clock.filter_time_range(query, RemovalRecord.removal_time, filters.get('start_time'), filters.get('end_time'),
                                          filters.get('warehouse_id'))

        # 针对 Goods 相关的过滤，先统一 join 一次
        if any(key in filters for key in ['goods_code', 'keyword']):
            query = query.join(Goods, RemovalRecord.goods_id == Goods.id)

        # 关联商品表处理商品编码过滤
        if filters.get('goods_code'):
            query = query.filter(Goods.code.ilike(f"%{filters['goods_code']}%"))

        # 统一关联库位表处理相关过滤
        if any(key in filters for key in ['location_code', 'warehouse_id','warehouse_ids','keyword']):
            query = query.join(Location, RemovalRecord.location_id == Location.id)

        # 库位编码过滤
        if filters.get('location_code'):
            query = query.filter(Location.code.ilike(f"%{filters['location_code']}%"))

        # 关键字联合搜索
        if filters.get('keyword'):
            keyword = filters['keyword']
            query = query.filter(
                or_(
                    Goods.code.ilike(f"%{keyword}%"),
                    Goods.name.ilike(f"%{keyword}%"),
                    Goods.manufacturer.ilike(f"%{keyword}%"),
                    Goods.category.ilike(f"%{keyword}%"),
                    Goods.tags.ilike(f"%{keyword}%"),
                    Goods.brand.ilike(f"%{keyword}%"),
                    Location.code.ilike(f"%{keyword}%"),
                    RemovalRecord.reason.ilike(f"%{keyword}%")  # 包含下架原因搜索
                )
            )

        # 仓库过滤条件
        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))

        return query

    @staticmethod
    @transactional
    def create_removal_record(data: dict, created_by_id: int, *, from_picking: bool = False) -> RemovalRecord:
        """
        创建一条新的下架记录

        :param data: dict，包含 goods_id, location_id, quantity, reason, remark 等字段
        :param from_picking: 仅拣货完成（PickingTaskService.complete_task）内部传 True：
            reason 固定记为 'picking'，库存走 picking_removed（货由 dn_picked 计入 picked_stock）。
            对外接口不传，reason 必须在 PUBLIC_REASONS 内，否则 400（16083）
        :return: 新创建的 RemovalRecord 实例
        """
        if from_picking:
            require_fields(data, 'goods_id', 'location_id', 'quantity')
            reason = RemovalService.PICKING_REASON
        else:
            require_fields(data, 'goods_id', 'location_id', 'quantity', 'reason')
            reason = data['reason']
            if not isinstance(reason, str) or reason not in RemovalService.PUBLIC_REASONS:
                raise BadRequestException(
                    f"Invalid removal reason: {reason!r}. Allowed: {', '.join(sorted(RemovalService.PUBLIC_REASONS))}",
                    16083, field='reason'
                )
        goods_id = data['goods_id']
        location_id = data['location_id']
        quantity = require_positive_int(data['quantity'], 'quantity')
        location = get_object_or_404(Location, location_id)

        # 加锁顺序（B-103）：库存行先于库位库存行
        lock_inventory_rows([goods_id], location.warehouse_id)
        # 持锁读取商品库位记录：找不到 404，库存不足 400，读改写期间其它事务不能插进来
        goods_location_record = lock_goods_location(goods_id, location_id)
        if goods_location_record is None:
            raise NotFoundException("GoodsLocation not found", 13004)
        if goods_location_record.quantity < quantity:
            raise BadRequestException("Stock is not enough", 15001)

        new_record = RemovalRecord(
            goods_id=goods_id,
            location_id=location_id,
            quantity=quantity,
            reason=reason,
            remark=data.get('remark', ''),
            operator_id=created_by_id
        )
        db.session.add(new_record)
        db.session.flush()

        goods_location_record.quantity -= quantity
        if goods_location_record.quantity == 0:
            db.session.delete(goods_location_record)
        else:
            db.session.add(goods_location_record)
        db.session.flush()

        #更新库存信息：拣货下架的货走 picked_stock（由拣货流程记账），其它原因的下架进入待上架区。
        # 从 damaged 库位下架、或因破损（damage / damaged）下架的是破损品：记进待上架·破损，之后只能上到 damaged 库位
        if from_picking:
            InventoryService.picking_removed(goods_id, location.warehouse_id, quantity)
        else:
            damaged = (location.location_type == 'damaged'
                       or reason in InventoryService.DAMAGED_REMOVAL_REASONS)
            InventoryService.removal_completed(goods_id, location.warehouse_id, quantity, damaged=damaged)

        # db.session.commit()
        return new_record

    @staticmethod
    def get_removal_record(record_id: int) -> RemovalRecord:
        """
        根据 record_id 获取单个 RemovalRecord，不存在时抛出 404
        """
        return get_object_or_404(RemovalRecord, record_id)


    @staticmethod
    @transactional
    def bulk_create_removal_records(data_list: list, created_by_id: int) -> list:
        """
        批量创建下架记录

        :param data_list: list，每个元素为 dict，包含 goods_id, location_id, quantity, reason, remark 等字段
        :param created_by_id: 创建者的用户ID
        :return: 创建成功的 RemovalRecord 实例列表
        """
        require_bulk_list(data_list, 'records')
        # 逐条处理前按统一顺序锁齐全部库存行与库位库存行（B-103）：两批商品 / 库位顺序相反时不再互相等待
        lock_stock_for_bulk(
            (data.get('goods_id'), data.get('location_id')) for data in data_list if isinstance(data, dict)
        )
        new_records = []
        for data in data_list:
            new_record = RemovalService.create_removal_record(data, created_by_id)
            new_records.append(new_record)
            db.session.flush()

        # transactional 装饰器会在函数退出后统一提交
        return new_records
