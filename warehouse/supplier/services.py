from extensions.db import *
from extensions.transaction import transactional
from .models import Supplier

class SupplierService:

    @staticmethod
    def _get_instance(supplier_or_id: int | Supplier) -> Supplier:
        """
        根据传入参数返回 Supplier 实例。
        如果参数为 int，则调用 get_supplier 获取 Supplier 实例；
        否则直接返回传入的 Supplier 实例。
        """
        if isinstance(supplier_or_id, int):
            return SupplierService.get_supplier(supplier_or_id)
        return supplier_or_id

    @staticmethod
    def list_suppliers(filters: dict):
        """
        根据过滤条件返回 Supplier 查询对象
        """
        query = Supplier.query.order_by(Supplier.id.desc())

        if filters.get('company_id'):
            query = query.filter(Supplier.company_id == filters['company_id'])
            
        if filters.get('name'):
            query = query.filter(Supplier.name.ilike(f"%{filters['name']}%"))

        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Supplier.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Supplier.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_supplier(supplier_id: int) -> Supplier:
        """
        根据 ID 获取单个 Supplier，不存在时抛出 404
        """
        supplier = get_object_or_404(Supplier, supplier_id)
        return supplier

    @staticmethod
    @transactional
    def create_supplier(data: dict, created_by_id: int) -> Supplier:
        """
        创建新 Supplier
        """
        new_supplier = Supplier(
            name=data['name'],
            address=data.get('address'),
            phone=data.get('phone'),
            zip_code=data.get('zip_code'),
            email=data.get('email'),
            contact=data.get('contact'),
            company_id=data['company_id'],
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_supplier)
        # db.session.commit()
        return new_supplier

    @staticmethod
    @transactional
    def update_supplier(supplier_id: int, data: dict) -> Supplier:
        """
        更新 Supplier 信息
        """
        supplier = SupplierService.get_supplier(supplier_id)

        supplier.name = data.get('name', supplier.name)
        supplier.address = data.get('address', supplier.address)
        supplier.phone = data.get('phone', supplier.phone)
        supplier.zip_code = data.get('zip_code', supplier.zip_code)
        supplier.email = data.get('email', supplier.email)
        supplier.contact = data.get('contact', supplier.contact)
        supplier.is_active = data.get('is_active', supplier.is_active)
        supplier.company_id = data.get('company_id', supplier.company_id)

        # db.session.commit()
        return supplier

    @staticmethod
    @transactional
    def delete_supplier(supplier_id: int):
        """
        删除 Supplier
        """
        supplier = SupplierService.get_supplier(supplier_id)
        db.session.delete(supplier)
        # db.session.commit()
