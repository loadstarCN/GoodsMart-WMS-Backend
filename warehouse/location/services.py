# services.py
from sqlalchemy import or_, func
from extensions.db import *
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common.locks import lock_row
from .models import Location

class LocationService:

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
    def list_locations(filters: dict):
        """
        根据过滤条件返回位置查询对象
        """
        query = Location.query.order_by(Location.id.desc())

        if filters.get('location_type'):
            query = query.filter(Location.location_type == filters['location_type'])
        if filters.get('code'):
            query = query.filter(Location.code.ilike(f"%{filters['code']}%"))

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
        创建新库位
        """
        new_location = Location(
            warehouse_id=data['warehouse_id'],
            code=data['code'],
            description=data.get('description'),
            location_type=data['location_type'],
            width=data.get('width'),
            depth=data.get('depth'),
            height=data.get('height'),
            capacity=data.get('capacity'),
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

        deactivating = bool(location.is_active) and not data.get('is_active', location.is_active)
        changing_type = data.get('location_type', location.location_type) != location.location_type
        if deactivating:
            LocationService._assert_no_stock(location, 'deactivate it')
        if changing_type:
            LocationService._assert_no_stock(location, 'change its location_type')

        location.code = data.get('code', location.code)
        location.description = data.get('description', location.description)
        location.location_type = data.get('location_type', location.location_type)
        location.width = data.get('width', location.width)
        location.depth = data.get('depth', location.depth)
        location.height = data.get('height', location.height)
        location.capacity = data.get('capacity', location.capacity)
        location.is_active = data.get('is_active', location.is_active)

        # db.session.commit()
        return location

    @staticmethod
    @transactional
    def delete_location(location_id: int):
        """
        删除库位（硬删除）。库位上还有货时 409（16084）：
        SQLite 不强制外键时货会凭空消失，PostgreSQL 上则撞 goods_locations 的 RESTRICT 外键变成 500
        """
        location = lock_row(LocationService.get_location(location_id))
        LocationService._assert_no_stock(location, 'delete it')
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