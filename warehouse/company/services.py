from datetime import datetime
from extensions.db import *
from extensions.transaction import transactional
from .models import Company

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
        # 假设 data['expired_at'] = "2025-01-01"
        if 'expired_at' in data and isinstance(data['expired_at'], str):
            data['expired_at'] = datetime.strptime(data['expired_at'], '%Y-%m-%d')
            
        new_company = Company(
            name=data['name'],
            email=data.get('email'),
            phone=data.get('phone'),
            address=data.get('address'),
            zip_code=data.get('zip_code'),
            logo=data.get('logo'),
            default_currency=data.get('default_currency'),
            is_active=data.get('is_active', True),
            expired_at=data.get('expired_at'),
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
        # 假设 data['expired_at'] = "2025-01-01"
        if 'expired_at' in data and isinstance(data['expired_at'], str):
            data['expired_at'] = datetime.strptime(data['expired_at'], '%Y-%m-%d')
            
        company = CompanyService.get_company(company_id)

        company.name = data.get('name', company.name)
        company.email = data.get('email', company.email)
        company.phone = data.get('phone', company.phone)
        company.address = data.get('address', company.address)
        company.zip_code = data.get('zip_code', company.zip_code)
        company.logo = data.get('logo', company.logo)
        company.default_currency = data.get('default_currency', company.default_currency)
        company.is_active = data.get('is_active', company.is_active)
        company.expired_at = data.get('expired_at', company.expired_at)

        # db.session.commit()
        return company

    @staticmethod
    @transactional
    def delete_company(company_id: int):
        """
        删除 Company
        """
        company = CompanyService.get_company(company_id)
        db.session.delete(company)
        # db.session.commit()
