from extensions.db import *
from extensions.transaction import transactional
from .models import Department

class DepartmentService:

    @staticmethod
    def _get_instance(department_or_id: int | Department) -> Department:
        """
        根据传入参数返回 Department 实例。
        如果参数为 int，则调用 get_department 获取 Department 实例；
        否则直接返回传入的 Department 实例。
        """
        if isinstance(department_or_id, int):
            return DepartmentService.get_department(department_or_id)
        return department_or_id

    @staticmethod
    def list_departments(filters: dict):
        """
        根据过滤条件返回 Department 查询对象
        """
        query = Department.query.order_by(Department.id.desc())        

        if filters.get('company_id'):
            query = query.filter(Department.company_id == filters['company_id'])
        if filters.get('is_active') is not None:
            query = query.filter(Department.is_active == filters['is_active'])
        if filters.get('name'):
            query = query.filter(Department.name.ilike(f"%{filters['name']}%"))

        return query

    @staticmethod
    def get_department(department_id: int) -> Department:
        """
        根据 ID 获取单个 Department，不存在时抛出 404
        """
        department = get_object_or_404(Department, department_id)
        return department

    @staticmethod
    @transactional
    def create_department(data: dict, created_by_id: int) -> Department:
        """
        创建新 Department
        """
        new_department = Department(
            name=data['name'],
            description=data.get('description'),
            company_id=data['company_id'],
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_department)
        # db.session.commit()
        return new_department

    @staticmethod
    @transactional
    def update_department(department_id: int, data: dict) -> Department:
        """
        更新 Department 信息
        """
        department = DepartmentService.get_department(department_id)

        department.name = data.get('name', department.name)
        department.description = data.get('description', department.description)
        department.is_active = data.get('is_active', department.is_active)
        department.company_id = data.get('company_id', department.company_id)

        # db.session.commit()
        return department

    @staticmethod
    @transactional
    def delete_department(department_id: int):
        """
        删除 Department
        """
        department = DepartmentService.get_department(department_id)
        db.session.delete(department)
        # db.session.commit()
