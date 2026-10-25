from extensions.db import *
from extensions.error import BadRequestException
from extensions.transaction import transactional
from warehouse.staff.models import Staff
from warehouse.staff.services import StaffService
from warehouse.common.clock import normalize_timezone_input
from warehouse.common.address_format import (
    WAREHOUSE_ADDRESS_LIMITS, format_single_line, is_structured, parse_columns, postal_code_of,
)
from warehouse.common.export_profile import WAREHOUSE_EXPORT_FIELDS, normalize_export_profile
from warehouse.common.references import ensure_not_referenced
from warehouse.common.spec_capture import warehouse_spec_settings
from .models import Warehouse


def _apply_address(warehouse: Warehouse, data: dict, structured: dict, has_postal: bool, postal_code):
    """结构化地址：只改请求里带了的；改了结构化地址而没给单行 address 时按国家格式重新生成 address；
    给了 latin 而仓库还没有英文单行地址（address_en）、请求里也没给时，由 latin 生成 address_en。"""
    if has_postal:
        warehouse.zip_code = postal_code
    for field, value in structured.items():
        setattr(warehouse, field, value)
    address = {
        'country': warehouse.country_code, 'postal_code': warehouse.zip_code, 'admin_area': warehouse.admin_area,
        'locality': warehouse.locality, 'address_line1': warehouse.address_line1,
        'address_line2': warehouse.address_line2,
    }
    changed = any(key in structured for key in WAREHOUSE_ADDRESS_LIMITS) or has_postal
    if 'address' not in data and changed and is_structured(address):
        warehouse.address = format_single_line(address, 'local', limit=255) or warehouse.address
    if structured.get('latin') and 'address_en' not in data and not (warehouse.address_en or '').strip():
        latin = {**address, 'latin': structured['latin']}
        warehouse.address_en = format_single_line(latin, 'latin', limit=500) or None


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
        export_profile = normalize_export_profile(data, WAREHOUSE_EXPORT_FIELDS)
        structured = parse_columns(data, WAREHOUSE_ADDRESS_LIMITS)
        has_postal, postal_code = postal_code_of(data)
        tz_name = normalize_timezone_input(data.get('timezone'))       # 不合法 400 14022
        spec_settings = warehouse_spec_settings(data)                  # 规格采集默认规则，不合法 400 17020
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
            zip_code=postal_code,
            default_currency=data.get('default_currency'),
            company_id=data['company_id'],
            manager_id=data.get('manager_id'),
            is_active=data.get('is_active', True),
            timezone=tz_name,
            created_by=created_by_id
        )
        # 出口资料只设请求体里给了的字段（country_code 不给时走列默认值）
        for field, value in export_profile.items():
            setattr(new_warehouse, field, value)
        if new_warehouse.country_code is None and 'country_code' not in export_profile:
            new_warehouse.country_code = 'JP'          # 列默认值（生成地址时就要用到国家）
        for field, value in spec_settings.items():
            setattr(new_warehouse, field, value)
        _apply_address(new_warehouse, data, structured, has_postal, postal_code)

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
        更新 Warehouse 信息（company_id 不可变；manager 归属按仓库自己的公司校验）
        """
        warehouse = WarehouseService.get_warehouse(warehouse_id)
        export_profile = normalize_export_profile(data, WAREHOUSE_EXPORT_FIELDS)
        structured = parse_columns(data, WAREHOUSE_ADDRESS_LIMITS)
        has_postal, postal_code = postal_code_of(data)
        if 'timezone' in data:                                        # 只改请求里带了的；null / 空串 = 用默认时区
            warehouse.timezone = normalize_timezone_input(data['timezone'])
        # 规格采集默认规则：只改请求里带了的（不合法 400 17020）；已建的收货通知不受影响（单据上是快照）
        for field, value in warehouse_spec_settings(data).items():
            setattr(warehouse, field, value)

        manager = None
        new_manager_id = data.get('manager_id')
        if new_manager_id:
            manager = StaffService.get_staff(new_manager_id)
            # manager 必须与仓库同公司（以仓库落库的 company_id 为准，而非客户端传的值）
            if manager.company_id != warehouse.company_id:
                raise BadRequestException("Manager does not belong to the same company", 16027)

        # 换管理员（含显式置空）时，把仓库从旧管理员的管辖列表里摘掉
        old_manager_id = warehouse.manager_id
        if 'manager_id' in data and old_manager_id and old_manager_id != new_manager_id:
            old_manager = db.session.get(Staff, old_manager_id)
            if old_manager is not None and warehouse in old_manager.warehouses:
                old_manager.warehouses.remove(warehouse)
                db.session.add(old_manager)

        warehouse.name = data.get('name', warehouse.name)
        warehouse.address = data.get('address', warehouse.address)
        warehouse.phone = data.get('phone', warehouse.phone)
        warehouse.default_currency = data.get('default_currency', warehouse.default_currency)
        if 'manager_id' in data:
            warehouse.manager_id = new_manager_id
        warehouse.is_active = data.get('is_active', warehouse.is_active)
        for field, value in export_profile.items():
            setattr(warehouse, field, value)
        _apply_address(warehouse, data, structured, has_postal, postal_code)

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
        删除 Warehouse：仍有库位 / 库存 / 单据等引用时 409 44004（改用停用）。
        员工的仓库分配（staff_warehouse）不算引用，删除时由 ORM 一并清掉。
        """
        warehouse = WarehouseService.get_warehouse(warehouse_id)
        ensure_not_referenced(Warehouse, warehouse.id, 'warehouse', exclude=('staff_warehouse',))
        db.session.delete(warehouse)
        # db.session.commit()
