from extensions.db import *
from extensions.error import BadRequestException
from extensions.transaction import transactional
from warehouse.staff.services import StaffService
from .models import Warehouse

class WarehouseService:

    @staticmethod
    def _get_instance(warehouse_or_id: int | Warehouse) -> Warehouse:
        """
        根据传入参数返回 Warehouse 实例。
        如果参数为 int，则调用 get_warehouse 获取 Warehouse 实例；
        否则直接返回传入的 Warehouse 实例。
        """
        if isinstance(warehouse_or_id, int):
            return WarehouseService.get_warehouse(warehouse_or_id)
        return warehouse_or_id

    @staticmethod
    def list_warehouses(filters: dict):
        """
        根据过滤条件返回 Warehouse 查询对象
        """
        query = Warehouse.query.order_by(Warehouse.id.desc())

        if filters.get('company_id'):
            query = query.filter(Warehouse.company_id == filters['company_id'])
        if filters.get('name'):
            query = query.filter(Warehouse.name.ilike(f"%{filters['name']}%"))
        
        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(Warehouse.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Warehouse.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_warehouse(warehouse_id: int) -> Warehouse:
        """
        根据 ID 获取单个 Warehouse，不存在时抛出 404
        """
        warehouse = get_object_or_404(Warehouse, warehouse_id)
        return warehouse

    @staticmethod
    @transactional
    def create_warehouse(data: dict, created_by_id: int) -> Warehouse:
        """
        创建新 Warehouse
        """
        manager_id = data.get('manager_id')
        manager = None  # 显式初始化
        if manager_id:
            manager = StaffService.get_staff(manager_id)
            # 检查manager是否属于同一公司
            if manager.company_id != data['company_id']:
                raise BadRequestException("Manager does not belong to the same company", 16027)
        
        new_warehouse = Warehouse(
            name=data['name'],
            address=data.get('address'),
            phone=data.get('phone'),
            zip_code=data.get('zip_code'),
            default_currency=data.get('default_currency'),
            company_id=data['company_id'],
            manager_id=data.get('manager_id'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )

        db.session.add(new_warehouse)
        db.session.flush()  # 确保新对象的 ID 被分配
        # 如果有 manager，添加到仓库的管理员列表中
        if manager and not new_warehouse in manager.warehouses:
            manager.warehouses.append(new_warehouse)
            db.session.add(manager)

        # db.session.commit()
        return new_warehouse

    @staticmethod
    @transactional
    def update_warehouse(warehouse_id: int, data: dict) -> Warehouse:
        """
        更新 Warehouse 信息
        """
        manager_id = data.get('manager_id')
        manager = None
        if manager_id:
            manager = StaffService.get_staff(manager_id)
            # 检查manager是否属于同一公司
            if manager.company_id != data['company_id']:
                raise BadRequestException("Manager does not belong to the same company", 16027)

        old_manager = None
        warehouse = WarehouseService.get_warehouse(warehouse_id)
        if warehouse.manager:
            old_manager = warehouse.manager
        if manager_id and old_manager and old_manager.id != manager_id:
            # 如果旧的管理员存在且与新的管理员不同，则从旧的管理员中删除该仓库
            if manager.type == 'Staff':
                if warehouse in old_manager.warehouses:
                    # 从旧的管理员中删除该仓库
                    old_manager.warehouses.remove(warehouse)
                    db.session.add(old_manager)
                    db.session.flush()  # 确保新对象的 ID 被分配
            
        warehouse.name = data.get('name', warehouse.name)
        warehouse.address = data.get('address', warehouse.address)
        warehouse.phone = data.get('phone', warehouse.phone)
        warehouse.zip_code = data.get('zip_code', warehouse.zip_code)
        warehouse.default_currency = data.get('default_currency', warehouse.default_currency)
        warehouse.manager_id = data.get('manager_id', warehouse.manager_id)
        warehouse.is_active = data.get('is_active', warehouse.is_active)

        db.session.add(warehouse)
        db.session.flush()  # 确保新对象的 ID 被分配

        if manager and not warehouse in manager.warehouses:
            manager.warehouses.append(warehouse)
            db.session.add(manager)

        # db.session.commit()
        return warehouse

    @staticmethod
    @transactional
    def delete_warehouse(warehouse_id: int):
        """
        删除 Warehouse
        """
        warehouse = WarehouseService.get_warehouse(warehouse_id)
        db.session.delete(warehouse)
        # db.session.commit()
