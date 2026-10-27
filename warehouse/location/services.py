# services.py
from sqlalchemy import or_, func
from extensions.db import *
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common.locks import lock_row
from warehouse.common.validation import optional_number
from .models import Location

# 库位尺寸 / 容量（Float 列）：空串 → null；必须 > 0（chk_dimension_positive 约束 width / depth / height）
LOCATION_NUMBER_FIELDS = ('width', 'depth', 'height', 'capacity')


class LocationService:

    @staticmethod
    def _clean_numbers(data: dict) -> dict:
        """只处理请求里出现的尺寸字段：空串转 null，其余必须是 > 0 的数字，否则 400（16115）"""
        return {
            field: optional_number(data[field], field, positive=True)
            for field in LOCATION_NUMBER_FIELDS if field in data
        }

    @staticmethod
    def _history_counts(location_id: int) -> dict:
        """统计引用该库位的历史记录（这些外键都是 RESTRICT：PG 上直接删会撞外键变成 500）。
        数量为 0 的库位库存行不算历史，删库位时一并删掉。"""
        from warehouse.adjustment.models import AdjustmentDetail
        from warehouse.cyclecount.models import CycleCountTaskDetail
        from warehouse.inventory_snapshot.models import InventorySnapshot
        from warehouse.picking.models import PickingTaskDetail
        from warehouse.putaway.models import PutawayRecord
        from warehouse.removal.models import RemovalRecord
        from warehouse.stock.models import StockDailyBalance, StockLedger
        from warehouse.transfer.models import TransferRecord

        counts = {}
        for label, query in (
            ('putaway_records', PutawayRecord.query.filter(PutawayRecord.location_id == location_id)),
            ('removal_records', RemovalRecord.query.filter(RemovalRecord.location_id == location_id)),
            ('transfer_records', TransferRecord.query.filter(or_(
                TransferRecord.from_location_id == location_id, TransferRecord.to_location_id == location_id))),
            ('picking_details', PickingTaskDetail.query.filter(PickingTaskDetail.location_id == location_id)),
            ('cycle_count_details', CycleCountTaskDetail.query.filter(CycleCountTaskDetail.location_id == location_id)),
            ('adjustment_details', AdjustmentDetail.query.filter(AdjustmentDetail.location_id == location_id)),
            ('inventory_snapshots', InventorySnapshot.query.filter(InventorySnapshot.location_id == location_id)),
            ('stock_ledger', StockLedger.query.filter(StockLedger.location_id == location_id)),
            ('stock_daily_balances', StockDailyBalance.query.filter(StockDailyBalance.location_id == location_id)),
        ):
            count = query.order_by(None).count()
            if count:
                counts[label] = count
        return counts

    @staticmethod
    def _stock_quantity(location_id: int) -> int:
        """库位上现存货物总数（GoodsLocation.quantity 之和）"""
        from warehouse.goods.models import GoodsLocation
        return int(
            db.session.query(func.coalesce(func.sum(GoodsLocation.quantity), 0))
            .filter(GoodsLocation.location_id == location_id)
            .scalar() or 0
        )

    @staticmethod
    def _assert_no_stock(location: Location, action: str):
        """
        库位上还有货时禁止停用 / 改类型 / 删除（409 16084）。
        onhand / damage / return 是按「启用库位 × 库位类型」从 GoodsLocation 汇总的，
        有货时停用或改类型，下一次重算就会把这些货从 Inventory 里算丢或算错类型。
        """
        quantity = LocationService._stock_quantity(location.id)
        if quantity > 0:
            raise ConflictException(
                f"Location {location.code} still holds {quantity} item(s); cannot {action}. "
                f"Move or remove the stock first.",
                16084, details={'location_id': location.id, 'quantity': quantity}
            )

    @staticmethod
    def require_active_location(location: Location, field: str = 'location_id') -> Location:
        """
        上架 / 移库的目标库位必须启用中，否则 400（16085）。
        调用方需先 lock_row(location, shared=True)：与并发的停用（FOR UPDATE）串行，停用一方再按库位存量判断；
        上架 / 移库之间只持共享锁，互不阻塞。
        """
        if not location.is_active:
            raise BadRequestException(
                f"Location {location.code} is inactive and cannot receive stock.", 16085, field=field
            )
        return location

    @staticmethod
    def _get_instance(location_or_id: int | Location) -> Location:
        """
        根据传入参数返回 PackingTask 实例。
        如果参数为 int，则调用 get_task 获取 PackingTask 实例；
        否则直接返回传入的 PackingTask 实例。
        """
        if isinstance(location_or_id, int):
            return LocationService.get_location(location_or_id)
        return location_or_id


    @staticmethod
    def zone_expression():
        """First hyphen-delimited code segment, identical for list and zone counts."""
        from sqlalchemy import case, func
        position = (func.instr(Location.code, '-') if db.engine.dialect.name == 'sqlite'
                    else func.strpos(Location.code, '-'))
        return case((position > 1, func.substr(Location.code, 1, position - 1)), else_=Location.code)

    @staticmethod
    def list_locations(filters: dict):
        """
        根据过滤条件返回位置查询对象
        """
        query = Location.query.order_by(Location.id.desc())

        if filters.get('location_type'):
            query = query.filter(Location.location_type == filters['location_type'])
        if filters.get('code'):
            query = query.filter(Location.code.ilike(f"%{filters['code']}%"))
        if filters.get('zone') is not None:
            query = query.filter(LocationService.zone_expression() == filters['zone'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(Location.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Location.is_active == filters['is_active'])

        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))

        return query

    @staticmethod
    def get_location(location_id: int) -> Location:
        """
        根据ID获取单个库位，不存在时抛出404
        """
        location = get_object_or_404(Location, location_id)
        return location

    @staticmethod
    @transactional
    def create_location(data: dict, created_by_id: int) -> Location:
        """
        创建新库位（尺寸 / 容量：空串 → null，必须 > 0，否则 400 16115）
        """
        numbers = LocationService._clean_numbers(data)
        new_location = Location(
            warehouse_id=data['warehouse_id'],
            code=data['code'],
            description=data.get('description'),
            location_type=data['location_type'],
            width=numbers.get('width'),
            depth=numbers.get('depth'),
            height=numbers.get('height'),
            capacity=numbers.get('capacity'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_location)
        # db.session.commit()
        return new_location

    @staticmethod
    @transactional
    def update_location(location_id: int, data: dict) -> Location:
        """
        更新库位信息。
        库位上还有货时不能停用、不能改 location_type（409 16084）；值没变（Web 编辑页整表提交）不算改动。
        """
        # 先锁库位行并重读：与并发的上架 / 移库入（同样先锁目标库位）串行，存量判断看到的是已提交的数量
        location = lock_row(LocationService.get_location(location_id))
        numbers = LocationService._clean_numbers(data)

        deactivating = bool(location.is_active) and not data.get('is_active', location.is_active)
        changing_type = data.get('location_type', location.location_type) != location.location_type
        if deactivating:
            LocationService._assert_no_stock(location, 'deactivate it')
        if changing_type:
            LocationService._assert_no_stock(location, 'change its location_type')

        location.code = data.get('code', location.code)
        location.description = data.get('description', location.description)
        location.location_type = data.get('location_type', location.location_type)
        for field, value in numbers.items():
            setattr(location, field, value)
        location.is_active = data.get('is_active', location.is_active)

        # db.session.commit()
        return location

    @staticmethod
    @transactional
    def delete_location(location_id: int):
        """
        删除库位（硬删除）。
        - 库位上还有货时 409（16084）：SQLite 不强制外键时货会凭空消失，PostgreSQL 上则撞 goods_locations 的 RESTRICT 外键变成 500
        - 有上架 / 下架 / 移库 / 拣货 / 盘点 / 调整 / 库存快照等历史记录引用时 409（44009，
          details {entity: 'location', id, references: {记录类型: 行数}}）：
          这些外键都是 RESTRICT，PG 上直接删会变成 500；有历史的库位请改用停用
        - 数量为 0 的库位库存行不是历史，随库位一并删除
        """
        from warehouse.goods.models import GoodsLocation
        location = lock_row(LocationService.get_location(location_id))
        LocationService._assert_no_stock(location, 'delete it')
        related = LocationService._history_counts(location.id)
        if related:
            detail = ', '.join(f"{k}: {v}" for k, v in related.items())
            raise ConflictException(
                f"Location {location.code} is referenced by history records ({detail}); deactivate it instead.",
                44009, details={'entity': 'location', 'id': location.id, 'references': related}
            )
        # 数量为 0 的行逐行删（不用批量 DELETE：库存写入守卫按行核对数量为 0，warehouse/stock/guard.py）
        for row in GoodsLocation.query.filter(GoodsLocation.location_id == location.id).all():
            db.session.delete(row)
        db.session.flush()
        db.session.delete(location)
        # db.session.commit()

    @staticmethod
    @transactional
    def deactivate_location(location_id: int) -> Location:
        """
        停用库位（软删除）。库位上还有货时 409（16084）
        """
        location = lock_row(LocationService.get_location(location_id))
        if location.is_active:
            LocationService._assert_no_stock(location, 'deactivate it')
        location.is_active = False
        # db.session.commit()
        return location
