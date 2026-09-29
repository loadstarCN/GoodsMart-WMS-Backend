from datetime import datetime
from extensions.db import *
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from .models import Company


def parse_expired_at(value):
    """expired_at 接受 'YYYY-MM-DD'、ISO 字符串、datetime 或空值（空值表示不过期）"""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).strip().replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        raise BadRequestException("expired_at must be a date (YYYY-MM-DD) or ISO datetime", 14017)


class CompanyService:

    @staticmethod
    def _get_instance(company_or_id: int | Company) -> Company:
        """
        根据传入参数返回 Company 实例。
        如果参数为 int，则调用 get_company 获取 Company 实例；
        否则直接返回传入的 Company 实例。
        """
        if isinstance(company_or_id, int):
            return CompanyService.get_company(company_or_id)
        return company_or_id

    @staticmethod
    def list_companies(filters: dict):
        """
        根据过滤条件返回 Company 查询对象
        """
        query = Company.query.order_by(Company.id.desc())

        if filters.get('company_id'):
            query = query.filter(Company.id == filters['company_id'])
        if filters.get('is_active') is not None:
            query = query.filter(Company.is_active == filters['is_active'])
        if filters.get('name'):
            query = query.filter(Company.name.ilike(f"%{filters['name']}%"))
        if filters.get('expired_at_start'):
            query = query.filter(Company.expired_at >= filters['expired_at_start'])
        if filters.get('expired_at_end'):
            query = query.filter(Company.expired_at <= filters['expired_at_end'])


        return query

    @staticmethod
    def get_company(company_id: int) -> Company:
        """
        根据 ID 获取单个 Company，不存在时抛出 404
        """
        company = get_object_or_404(Company, company_id)
        return company

    @staticmethod
    @transactional
    def create_company(data: dict, created_by_id: int) -> Company:
        """
        创建新 Company
        """
        new_company = Company(
            name=data['name'],
            email=data.get('email'),
            phone=data.get('phone'),
            address=data.get('address'),
            zip_code=data.get('zip_code'),
            logo=data.get('logo'),
            default_currency=data.get('default_currency'),
            is_active=data.get('is_active', True),
            expired_at=parse_expired_at(data.get('expired_at')),
            created_by=created_by_id
        )
        db.session.add(new_company)
        # db.session.commit()
        return new_company

    @staticmethod
    @transactional
    def update_company(company_id: int, data: dict) -> Company:
        """
        更新 Company 信息
        """
        company = CompanyService.get_company(company_id)

        company.name = data.get('name', company.name)
        company.email = data.get('email', company.email)
        company.phone = data.get('phone', company.phone)
        company.address = data.get('address', company.address)
        company.zip_code = data.get('zip_code', company.zip_code)
        company.logo = data.get('logo', company.logo)
        company.default_currency = data.get('default_currency', company.default_currency)
        if 'is_active' in data:
            company.is_active = bool(data['is_active'])
        if 'expired_at' in data:
            company.expired_at = parse_expired_at(data['expired_at'])

        # db.session.commit()
        return company

    @staticmethod
    def _related_counts(company_id: int) -> dict:
        """统计仍引用该公司的记录（这些外键都是 RESTRICT，直接删会撞数据库约束变成 500）"""
        from warehouse.carrier.models import Carrier
        from warehouse.department.models import Department
        from warehouse.goods.models import Goods
        from warehouse.recipient.models import Recipient
        from warehouse.staff.models import Staff
        from warehouse.supplier.models import Supplier
        from warehouse.warehouse.models import Warehouse

        counts = {}
        for label, model in (
            ('staff', Staff), ('warehouses', Warehouse), ('departments', Department), ('goods', Goods),
            ('suppliers', Supplier), ('carriers', Carrier), ('recipients', Recipient),
        ):
            count = model.query.filter_by(company_id=company_id).count()
            if count:
                counts[label] = count
        return counts

    @staticmethod
    @transactional
    def delete_company(company_id: int):
        """
        删除 Company：仍有员工 / 仓库 / 商品等关联数据时返回 409；删除同时停用其 API Key
        """
        company = CompanyService.get_company(company_id)

        related = CompanyService._related_counts(company_id)
        if related:
            detail = ', '.join(f"{k}: {v}" for k, v in related.items())
            raise ConflictException(f"Company still has related records ({detail}); remove them first", 44003)

        from system.third_party.services import APIKeyService
        APIKeyService.deactivate_company_keys(company_id)

        db.session.delete(company)
        # db.session.commit()
