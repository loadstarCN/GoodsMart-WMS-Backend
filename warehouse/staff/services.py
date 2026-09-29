from extensions.db import *
from extensions.error import BadRequestException, ForbiddenException
from extensions.jwt import revoke_all_user_tokens
from extensions.transaction import transactional
from .models import Staff
from warehouse.warehouse.models import Warehouse
from system.user.models import Role
from system.user.services import RoleService
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
    def accessible_warehouses(staff: Staff):
        """员工可访问的启用中仓库：company_admin 为本公司全部仓库，其余为分配给自己的仓库"""
        if staff.has_role('company_admin'):
            warehouses = staff.company.warehouses if staff.company else []
        else:
            warehouses = staff.warehouses
        return [w for w in warehouses if w.is_active]

    # ------------------------------------------------------------------
    # 归属校验：仓库 / 部门必须属于员工所在公司
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_warehouses(warehouse_ids, company_id):
        if not warehouse_ids:
            return []
        ids = list(dict.fromkeys(warehouse_ids))
        warehouses = Warehouse.query.filter(Warehouse.id.in_(ids)).all()
        if len(warehouses) != len(ids):
            raise BadRequestException("Invalid warehouse ID", 14009)
        for warehouse in warehouses:
            if warehouse.company_id != company_id:
                raise ForbiddenException("Warehouse does not belong to the staff's company", 12001)
        return warehouses

    @staticmethod
    def _validate_department(department_id, company_id):
        if department_id is None:
            return None
        from warehouse.department.models import Department
        department = db.session.get(Department, department_id)
        if not department or department.company_id != company_id:
            raise BadRequestException("Invalid department for this company", 14016)
        return department_id

    @staticmethod
    @transactional
    def create_staff(data: dict, created_by_id: int) -> Staff:
        """
        创建新 Staff
        """
        company_id = data['company_id']
        new_staff = Staff(
            user_name=data['user_name'],
            avatar=data.get('avatar'),
            email=data['email'],
            phone=data.get('phone'),
            openid=data.get('openid'),
            company_id=company_id,
            department_id=StaffService._validate_department(data.get('department_id'), company_id),
            position=data.get('position'),
            employee_number=data.get('employee_number'),
            hire_date=data.get('hire_date'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        if data.get('password'):
            new_staff.set_password(data.get('password'))

        # 分配角色（只允许授予调用方有资格授予的角色）
        if 'roles' in data:
            new_staff.roles = RoleService.resolve_assignable_roles(data['roles'])

        # 处理与 Warehouse 的多对多关联（仓库必须属于本公司）
        if 'warehouse_ids' in data:
            new_staff.warehouses = StaffService._resolve_warehouses(data['warehouse_ids'], company_id)

        db.session.add(new_staff)
        # db.session.commit()
        return new_staff

    @staticmethod
    @transactional
    def update_staff(staff_id: int, data: dict, actor_id=None) -> Staff:
        """
        更新 Staff 信息（company_id 不在此修改）
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
        if 'department_id' in data:
            staff.department_id = StaffService._validate_department(data.get('department_id'), staff.company_id)

        revoke = False
        if 'is_active' in data:
            is_active = bool(data['is_active'])
            if not is_active and actor_id == staff.id:
                raise BadRequestException("You cannot deactivate your own account", 10010)
            if staff.is_active and not is_active:
                revoke = True
            staff.is_active = is_active

        if data.get('password'):
            staff.set_password(data.get('password'))
            revoke = True

        if 'roles' in data:
            staff.roles = RoleService.resolve_assignable_roles(data['roles'])

        # 更新与 Warehouse 的多对多关联
        if 'warehouse_ids' in data:
            staff.warehouses = StaffService._resolve_warehouses(data['warehouse_ids'], staff.company_id)

        if revoke:
            revoke_all_user_tokens(staff.id)

        # db.session.commit()
        return staff

    @staticmethod
    @transactional
    def delete_staff(staff_id: int):
        """
        删除 Staff
        """
        staff = StaffService.get_staff(staff_id)
        revoke_all_user_tokens(staff.id)
        db.session.delete(staff)
        # db.session.commit()
