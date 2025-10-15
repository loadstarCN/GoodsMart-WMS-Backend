from extensions.db import *
from extensions.transaction import transactional
from .models import Recipient

class RecipientService:

    @staticmethod
    def _get_instance(recipient_or_id: int | Recipient) -> Recipient:
        """
        根据传入参数返回 Recipient 实例。
        如果参数为 int，则调用 get_recipient 获取 Recipient 实例；
        否则直接返回传入的 Recipient 实例。
        """
        if isinstance(recipient_or_id, int):
            return RecipientService.get_recipient(recipient_or_id)
        return recipient_or_id

    @staticmethod
    def list_recipients(filters: dict):
        """
        根据过滤条件返回 Recipient 查询对象
        """
        query = Recipient.query.order_by(Recipient.id.desc())

        if filters.get('company_id'):
            query = query.filter(Recipient.company_id == filters['company_id'])
        if filters.get('name'):
            query = query.filter(Recipient.name.ilike(f"%{filters['name']}%"))
        if filters.get('address'):
            query = query.filter(Recipient.address.ilike(f"%{filters['address']}%"))
        if filters.get('zip_code'):
            query = query.filter(Recipient.zip_code.ilike(f"%{filters['zip_code']}%"))
        if filters.get('phone'):
            query = query.filter(Recipient.phone.ilike(f"%{filters['phone']}%"))
        if filters.get('email'):
            query = query.filter(Recipient.email.ilike(f"%{filters['email']}%"))
        if filters.get('country'):
            query = query.filter(Recipient.country.ilike(f"%{filters['country']}%"))
        
        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Recipient.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Recipient.is_active == filters['is_active'])
        
        return query

    @staticmethod
    def get_recipient(recipient_id: int) -> Recipient:
        """
        根据 ID 获取单个 Recipient，不存在时抛出 404
        """
        recipient = get_object_or_404(Recipient, recipient_id)
        return recipient

    @staticmethod
    @transactional
    def create_recipient(data: dict, created_by_id: int) -> Recipient:
        """
        创建新 Recipient
        """
        new_recipient = Recipient(
            name=data['name'],
            address=data.get('address'),
            zip_code=data.get('zip_code'),
            phone=data.get('phone'),
            email=data.get('email'),
            contact=data.get('contact'),
            country=data['country'],
            is_active=data.get('is_active', True),
            created_by=created_by_id,
            company_id=data['company_id']
        )
        db.session.add(new_recipient)
        # db.session.commit()
        return new_recipient

    @staticmethod
    @transactional
    def update_recipient(recipient_id: int, data: dict) -> Recipient:
        """
        更新 Recipient 信息
        """
        recipient = RecipientService.get_recipient(recipient_id)

        recipient.name = data.get('name', recipient.name)
        recipient.address = data.get('address', recipient.address)
        recipient.zip_code = data.get('zip_code', recipient.zip_code)
        recipient.phone = data.get('phone', recipient.phone)
        recipient.email = data.get('email', recipient.email)
        recipient.contact = data.get('contact', recipient.contact)
        recipient.country = data.get('country', recipient.country)
        recipient.is_active = data.get('is_active', recipient.is_active)
        recipient.company_id = data.get('company_id', recipient.company_id)

        # db.session.commit()
        return recipient

    @staticmethod
    @transactional
    def delete_recipient(recipient_id: int):
        """
        删除 Recipient
        """
        recipient = RecipientService.get_recipient(recipient_id)
        db.session.delete(recipient)
        # db.session.commit()
