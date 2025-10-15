# services.py
from sqlalchemy import or_
from extensions.db import *
from extensions.transaction import transactional
from .models import Location

class LocationService:

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
        更新库位信息
        """
        location = LocationService.get_location(location_id)
        
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
        删除库位（硬删除）
        """
        location = LocationService.get_location(location_id)
        db.session.delete(location)
        # db.session.commit()

    @staticmethod
    @transactional
    def deactivate_location(location_id: int) -> Location:
        """
        停用库位（软删除）
        """
        location = LocationService.get_location(location_id)
        location.is_active = False
        # db.session.commit()
        return location