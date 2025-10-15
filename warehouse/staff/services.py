from extensions.db import *
from extensions.transaction import transactional
from .models import Staff
from warehouse.warehouse.models import Warehouse
from system.user.models import Role
from sqlalchemy import or_,func

class StaffService:

    @staticmethod
    def _get_instance(staff_or_id: int | Staff) -> Staff:
        """
        根据传入参数返回 Staff 实例。
        如果参数为 int，则调用 get_staff 获取 Staff 实例；
        否则直接返回传入的 Staff 实例。
        """
        if isinstance(staff_or_id, int):
            return StaffService.get_staff(staff_or_id)
        return staff_or_id

    @staticmethod
    def list_staff(filters: dict):
        """
        根据过滤条件返回 Staff 查询对象
        """
        query = Staff.query.order_by(Staff.id.desc())

        if filters.get('company_id'):
            query = query.filter(Staff.company_id == filters['company_id'])
        if filters.get('department_id'):
            query = query.filter(Staff.department_id == filters['department_id'])
        
        if filters.get('user_name'):
            query = query.filter(Staff.user_name.ilike(f"%{filters['user_name']}%"))
        if filters.get('email'):
            query = query.filter(Staff.email.ilike(f"%{filters['email']}%"))
        if filters.get('phone'):
            query = query.filter(Staff.phone.ilike(f"%{filters['phone']}%"))
        if filters.get('position'):
            query = query.filter(Staff.position.ilike(f"%{filters['position']}%"))
        if filters.get('employee_number'):
            query = query.filter(Staff.employee_number.ilike(f"%{filters['employee_number']}%"))
        if filters.get('hire_date'):
            query = query.filter(Staff.hire_date >= filters['hire_date'])

        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Staff.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Staff.is_active == filters['is_active'])        

        if filters.get('keyword'):
            keyword = filters['keyword']
            query = query.filter(
                or_(
                    Staff.user_name.ilike(f"%{keyword}%"),
                    Staff.email.ilike(f"%{keyword}%"),
                    Staff.phone.ilike(f"%{keyword}%"),
                    Staff.position.ilike(f"%{keyword}%"),
                    Staff.employee_number.ilike(f"%{keyword}%")
                )
            )

        return query

    @staticmethod
    def get_staff(staff_id: int) -> Staff:
        """
        根据 ID 获取单个 Staff，不存在时抛出 404
        """
        staff = get_object_or_404(Staff, staff_id)
        return staff

    @staticmethod
    @transactional
    def create_staff(data: dict, created_by_id: int) -> Staff:
        """
        创建新 Staff
        """
        new_staff = Staff(
            user_name=data['user_name'],
            avatar=data.get('avatar'),
            email=data['email'],
            phone=data.get('phone'),
            openid=data.get('openid'),
            company_id=data['company_id'],
            department_id=data.get('department_id'),
            position=data.get('position'),
            employee_number=data.get('employee_number'),
            hire_date=data.get('hire_date'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        if 'password' in data:
            if data.get('password'):
                new_staff.set_password(data.get('password'))

        # 分配角色
        if 'roles' in data:
            roles = Role.query.filter(Role.name.in_(data['roles'])).all()
            new_staff.roles = roles

        # 处理与 Warehouse 的多对多关联
        if 'warehouse_ids' in data:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(data['warehouse_ids'])).all()
            new_staff.warehouses = warehouses

        db.session.add(new_staff)
        # db.session.commit()
        return new_staff

    @staticmethod
    @transactional
    def update_staff(staff_id: int, data: dict) -> Staff:
        """
        更新 Staff 信息
        """
        staff = StaffService.get_staff(staff_id)

        staff.user_name = data.get('user_name', staff.user_name)
        staff.avatar = data.get('avatar', staff.avatar)
        staff.email = data.get('email', staff.email)
        staff.phone = data.get('phone', staff.phone)
        staff.openid = data.get('openid', staff.openid)
        staff.position = data.get('position', staff.position)
        staff.employee_number = data.get('employee_number', staff.employee_number)
        staff.hire_date = data.get('hire_date', staff.hire_date)
        staff.is_active = data.get('is_active', staff.is_active)

        if 'password' in data:
            staff.set_password(data.get('password'))

        if 'roles' in data:
            roles = Role.query.filter(Role.name.in_(data['roles'])).all()
            staff.roles = roles

        # 更新与 Warehouse 的多对多关联
        if 'warehouse_ids' in data:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(data['warehouse_ids'])).all()
            staff.warehouses = warehouses

        # db.session.commit()
        return staff

    @staticmethod
    @transactional
    def delete_staff(staff_id: int):
        """
        删除 Staff
        """
        staff = StaffService.get_staff(staff_id)
        db.session.delete(staff)
        # db.session.commit()
