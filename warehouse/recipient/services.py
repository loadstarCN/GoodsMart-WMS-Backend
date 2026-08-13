from extensions.db import db, get_object_or_404
from extensions.transaction import transactional
from .models import Recipient, RecipientExternalMapping

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

        if any(filters.get(key) for key in (
            'external_reference', 'name', 'email', 'keyword'
        )):
            query = query.outerjoin(RecipientExternalMapping)

        if filters.get('company_id'):
            query = query.filter(Recipient.company_id == filters['company_id'])
        if filters.get('external_reference'):
            query = query.filter(
                RecipientExternalMapping.external_reference
                == filters['external_reference']
            )
        if filters.get('name'):
            value = f"%{filters['name']}%"
            query = query.filter(db.or_(
                RecipientExternalMapping.display_name.ilike(value),
                Recipient.name.ilike(value),
            ))
        if filters.get('address'):
            query = query.filter(Recipient.address.ilike(f"%{filters['address']}%"))
        if filters.get('zip_code'):
            query = query.filter(Recipient.zip_code.ilike(f"%{filters['zip_code']}%"))
        if filters.get('phone'):
            query = query.filter(Recipient.phone.ilike(f"%{filters['phone']}%"))
        if filters.get('email'):
            value = f"%{filters['email']}%"
            query = query.filter(db.or_(
                RecipientExternalMapping.email.ilike(value),
                Recipient.email.ilike(value),
            ))
        if filters.get('country'):
            query = query.filter(Recipient.country.ilike(f"%{filters['country']}%"))
        if filters.get('keyword'):
            keyword = f"%{filters['keyword']}%"
            query = query.filter(db.or_(
                Recipient.name.ilike(keyword),
                RecipientExternalMapping.display_name.ilike(keyword),
                Recipient.address.ilike(keyword),
                Recipient.phone.ilike(keyword),
                Recipient.contact.ilike(keyword),
            ))
        
        if (('is_active' not in filters or filters['is_active'] is None)
                and not filters.get('external_reference')):
            query = query.filter(Recipient.is_active.is_(True))
        elif filters.get('is_active') is not None:
            # 否则按用户传入的值进行过滤
            query = query.filter(Recipient.is_active == filters['is_active'])
        
        return query

    @staticmethod
    def get_recipient(recipient_id: int, company_id: int | None = None) -> Recipient:
        """
        根据 ID 获取单个 Recipient，不存在时抛出 404
        """
        if company_id is None:
            recipient = get_object_or_404(Recipient, recipient_id)
        else:
            recipient = Recipient.query.filter_by(
                id=recipient_id, company_id=company_id
            ).first()
            if recipient is None:
                from extensions.error import NotFoundException
                raise NotFoundException(
                    f'Recipient with id {recipient_id} not found', 13001
                )
        return recipient

    @staticmethod
    @transactional
    def create_recipient(data: dict, created_by_id: int) -> Recipient:
        """
        创建新 Recipient
        """
        external_reference = data.get('external_reference')
        new_recipient = Recipient(
            name=(
                Recipient.integration_storage_name(
                    data['name'], external_reference
                ) if external_reference else data['name']
            ),
            address=data.get('address'),
            zip_code=data.get('zip_code'),
            phone=data.get('phone'),
            email=None if external_reference else data.get('email'),
            contact=data.get('contact'),
            country=data['country'],
            is_active=data.get('is_active', True),
            created_by=created_by_id,
            company_id=data['company_id']
        )
        db.session.add(new_recipient)
        db.session.flush()
        if external_reference:
            db.session.add(RecipientExternalMapping(
                company_id=data['company_id'],
                external_reference=str(external_reference),
                recipient_id=new_recipient.id,
                display_name=data['name'],
                email=data.get('email'),
            ))
        return new_recipient

    @staticmethod
    @transactional
    def update_recipient(recipient_id: int, data: dict,
                         company_id: int | None = None) -> Recipient:
        """
        更新 Recipient 信息
        """
        recipient = RecipientService.get_recipient(recipient_id, company_id)

        external_reference = data.get('external_reference')
        mapping = recipient.external_mapping
        if external_reference and mapping is None:
            mapping = RecipientExternalMapping(
                company_id=recipient.company_id,
                external_reference=str(external_reference),
                recipient_id=recipient.id,
                display_name=data.get('name', recipient.name),
                email=data.get('email'),
            )
            db.session.add(mapping)
            recipient.external_mapping = mapping
        elif mapping is not None:
            if external_reference:
                mapping.external_reference = str(external_reference)
            mapping.display_name = data.get('name', mapping.display_name)
            if 'email' in data:
                mapping.email = data['email']

        if mapping is None:
            recipient.name = data.get('name', recipient.name)
        recipient.address = data.get('address', recipient.address)
        recipient.zip_code = data.get('zip_code', recipient.zip_code)
        recipient.phone = data.get('phone', recipient.phone)
        if mapping is None:
            recipient.email = data.get('email', recipient.email)
        recipient.contact = data.get('contact', recipient.contact)
        recipient.country = data.get('country', recipient.country)
        recipient.is_active = data.get('is_active', recipient.is_active)
        recipient.company_id = data.get('company_id', recipient.company_id)

        # db.session.commit()
        return recipient

    @staticmethod
    @transactional
    def delete_recipient(recipient_id: int, company_id: int | None = None):
        """
        删除 Recipient
        """
        recipient = RecipientService.get_recipient(recipient_id, company_id)
        db.session.delete(recipient)
        # db.session.commit()
