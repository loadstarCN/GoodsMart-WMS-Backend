from extensions.db import *
from extensions.transaction import transactional
from .models import Carrier


class CarrierService:

    @staticmethod
    def _get_instance(carrier_or_id: int | Carrier) -> Carrier:
        """
        根据传入参数返回 Carrier 实例。
        如果参数为 int，则调用 get_carrier 获取 Carrier 实例；
        否则直接返回传入的 Carrier 实例。
        """
        if isinstance(carrier_or_id, int):
            return CarrierService.get_carrier(carrier_or_id)
        return carrier_or_id

    @staticmethod
    def list_carriers(filters: dict):
        """
        根据过滤条件返回 Carrier 查询对象
        """
        query = Carrier.query.order_by(Carrier.id.desc())

        if filters.get('company_id'):
            query = query.filter(Carrier.company_id == filters['company_id'])
        if filters.get('name'):
            query = query.filter(Carrier.name.ilike(f"%{filters['name']}%"))

        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Carrier.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Carrier.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_carrier(carrier_id: int) -> Carrier:
        """
        根据 ID 获取单个 Carrier，不存在时抛出 404
        """
        carrier = get_object_or_404(Carrier, carrier_id)
        return carrier

    @staticmethod
    @transactional
    def create_carrier(data: dict, created_by_id: int) -> Carrier:
        """
        创建新 Carrier
        """
        new_carrier = Carrier(
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
        db.session.add(new_carrier)
        # db.session.commit()
        return new_carrier

    @staticmethod
    @transactional
    def update_carrier(carrier_id: int, data: dict) -> Carrier:
        """
        更新 Carrier 信息
        """
        carrier = CarrierService.get_carrier(carrier_id)

        carrier.name = data.get('name', carrier.name)
        carrier.address = data.get('address', carrier.address)
        carrier.phone = data.get('phone', carrier.phone)
        carrier.zip_code = data.get('zip_code', carrier.zip_code)
        carrier.email = data.get('email', carrier.email)
        carrier.contact = data.get('contact', carrier.contact)
        carrier.is_active = data.get('is_active', carrier.is_active)
        carrier.company_id = data.get('company_id', carrier.company_id)

        # db.session.commit()
        return carrier

    @staticmethod
    @transactional
    def delete_carrier(carrier_id: int):
        """
        删除 Carrier
        """
        carrier = CarrierService.get_carrier(carrier_id)
        db.session.delete(carrier)
        # db.session.commit()
