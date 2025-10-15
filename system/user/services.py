from datetime import datetime, timedelta
import re
from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token
from flask_restx import abort
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException, UnauthorizedException
from extensions.transaction import transactional
from .models import User, Role, Permission

EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'

class UserService:

    @staticmethod
    def login(account: str, password: str):
        """
        用户登录服务：检查用户名和密码，返回相应的 token 和用户信息
        """
        # 判断是否是email，如果是email则查找email，否则查找user_name
        if '@' in account and re.fullmatch(EMAIL_REGEX, account):
            user = User.query.filter_by(email=account).first()
        else:
            user = User.query.filter_by(user_name=account).first()

        if not user:
            raise NotFoundException("User not found", 13002)
        
        # 验证密码
        if not user.check_password(password):
            raise UnauthorizedException("Invalid username or password", 11001)

        # 检查用户是否激活
        if not user.is_active:
            raise UnauthorizedException("User is not active", 11006)  
        
        # 判断用户类型是否是staff，如果是查看是否过期，检查的是company的过期时间
        if user.type == 'staff':
        # 将user变成staff类型
            from warehouse.staff.models import Staff
            staff = Staff.query.filter_by(id=user.id).first()
            if staff.company.expired_at and  staff.company.expired_at < datetime.now():
                raise UnauthorizedException("Company is expired", 11007)

        # 生成 JWT token
        access_token = create_access_token(identity=user)        
        expires_in = current_app.config['JWT_ACCESS_TOKEN_EXPIRES']
        refresh_token = create_refresh_token(identity=user)
        refresh_expires_in = current_app.config['JWT_REFRESH_TOKEN_EXPIRES']

        # 更新用户模型的refresh_token及有效期（需持久化存储）
        user.refresh_token_expires = datetime.now() + refresh_expires_in
        db.session.add(user)
        db.session.commit()

        # 返回用户信息和 token
        user_dict = user.to_dict()
        user_dict["access_token"] = access_token        
        user_dict["expires_in"] = expires_in.total_seconds()
        user_dict["refresh_token"] = refresh_token
        user_dict["refresh_expires_in"] = refresh_expires_in.total_seconds()

        return user_dict
    
    @staticmethod
    def refresh_token(current_user):
        """
        刷新 token
        """

        access_token = create_access_token(identity=current_user)        
        expires_in = current_app.config['JWT_ACCESS_TOKEN_EXPIRES']

        # 返回用户信息和 token
        user_dict = current_user.to_dict()
        user_dict["access_token"] = access_token        
        user_dict["expires_in"] = expires_in.total_seconds()        

        # 检查当前refresh_token剩余有效期
        expires_at = current_user.refresh_token_expires_at  # 在User模型中记录

        if expires_at is None:
            # 初始化新 refresh_token 及其有效期
            new_refresh_token = create_refresh_token(identity=current_user)
            refresh_expires_in = current_app.config['JWT_REFRESH_TOKEN_EXPIRES']

            # 更新用户模型的refresh_token及有效期（需持久化存储）
            current_user.refresh_token_expires_at = datetime.now() + refresh_expires_in
            db.session.add(current_user)
            db.session.commit()

            user_dict["refresh_token"] = new_refresh_token
            user_dict["refresh_expires_in"] = refresh_expires_in.total_seconds()
            return user_dict

        # 若剩余时间不足24小时，生成新refresh_token
        remaining_time = expires_at - datetime.now()
        if remaining_time < timedelta(hours=24):
            new_refresh_token = create_refresh_token(identity=current_user)
            refresh_expires_in = current_app.config['JWT_REFRESH_TOKEN_EXPIRES']
            # 更新用户模型的refresh_token及有效期（需持久化存储）
            current_user.refresh_token_expires_at = datetime.now() + refresh_expires_in
            db.session.add(current_user)
            db.session.commit()

            user_dict["refresh_token"] = new_refresh_token            
            user_dict["refresh_expires_in"] = refresh_expires_in.total_seconds()

        return user_dict

    @staticmethod
    def _get_instance(user_or_id: int | User) -> User:
        """
        根据传入参数返回 User 实例。
        如果参数为 int，则调用 get_user 获取 User 实例；
        否则直接返回传入的 User 实例。
        """
        if isinstance(user_or_id, int):
            return UserService.get_user(user_or_id)
        return user_or_id

    @staticmethod
    def list_users(filters: dict):
        """
        根据过滤条件返回 User 查询对象
        """
        query = User.query.order_by(User.id.desc())

        if filters.get('user_name'):
            query = query.filter(User.username.ilike(f"%{filters['username']}%"))
        if filters.get('email'):
            query = query.filter(User.email.ilike(f"%{filters['email']}%"))

        return query

    @staticmethod
    def get_user(user_id: int) -> User:
        """
        根据 ID 获取单个 User，不存在时抛出 404
        """
        user = get_object_or_404(User, user_id)
        return user

    @staticmethod
    @transactional
    def create_user(data: dict) -> User:
        """
        创建新 User
        """
        new_user = User(
            user_name=data['user_name'],
            email=data['email'],
            avatar=data.get('avatar', ''),
        )

        new_user.set_password(data['password'])
        # Assign roles if provided
        if 'roles' in data:
            roles = Role.query.filter(Role.name.in_(data['roles'])).all()
            new_user.roles = roles

        db.session.add(new_user)
        # db.session.commit()
        return new_user

    @staticmethod
    @transactional
    def update_user(user_id: int, data: dict) -> User:
        """
        更新 User 信息
        """
        user = UserService.get_user(user_id)

        user.user_name = data.get('user_name', user.user_name)
        user.email = data.get('email', user.email)
        user.avatar = data.get('avatar', user.avatar)

        if 'roles' in data:
            roles = Role.query.filter(Role.name.in_(data['roles'])).all()
            user.roles = roles

        if 'password' in data:
            user.set_password(data.get('password'))

        # db.session.commit()
        return user

    @staticmethod
    @transactional
    def delete_user(user_id: int):
        """
        删除 User
        """
        user = UserService.get_user(user_id)
        db.session.delete(user)
        # db.session.commit()

    @staticmethod
    @transactional
    def change_password(user_id, old_password, new_password):
        user = User.query.get_or_404(user_id)
        
        # 验证旧密码
        if not user.check_password(old_password):
            raise BadRequestException("Old password is incorrect",10003)
        
        # 这里使用了 User 模型中的 set_password 方法来加密新密码
        user.set_password(new_password)
        # db.session.commit()


class RoleService:

    @staticmethod
    def _get_instance(role_or_id: int | Role) -> Role:
        """
        根据传入参数返回 Role 实例。
        如果参数为 int，则调用 get_role 获取 Role 实例；
        否则直接返回传入的 Role 实例。
        """
        if isinstance(role_or_id, int):
            return RoleService.get_role(role_or_id)
        return role_or_id

    @staticmethod
    def list_roles(filters: dict):
        """
        根据过滤条件返回 Role 查询对象
        """
        query = Role.query.order_by(Role.id.desc())

        if filters.get('name'):
            query = query.filter(Role.name.ilike(f"%{filters['name']}%"))
        if filters.get('is_active') is not None:
            query = query.filter(Role.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_role(role_id: int) -> Role:
        """
        根据 ID 获取单个 Role，不存在时抛出 404
        """
        role = get_object_or_404(Role, role_id)
        return role

    @staticmethod
    @transactional
    def create_role(data: dict) -> Role:
        """
        创建新 Role
        """
        role = Role(
            name=data['name'],
            description=data.get('description', '')
        )
        role.permissions = [db.session.get(Permission,permission_id) for permission_id in data.get('permissions', [])]

        db.session.add(role)
        # db.session.commit()
        return role

    @staticmethod
    @transactional
    def update_role(role_id: int, data: dict) -> Role:
        """
        更新 Role 信息
        """
        role = RoleService.get_role(role_id)

        role.name = data.get('name', role.name)
        role.description = data.get('description', role.description)
        role.permissions = [db.session.get(Permission,permission_id) for permission_id in data.get('permissions', [])]

        # db.session.commit()
        return role

    @staticmethod
    @transactional
    def delete_role(role_id: int):
        """
        删除 Role
        """
        role = RoleService.get_role(role_id)
        db.session.delete(role)
        # db.session.commit()


class PermissionService:

    @staticmethod
    def _get_instance(permission_or_id: int | Permission) -> Permission:
        """
        根据传入参数返回 Permission 实例。
        如果参数为 int，则调用 get_permission 获取 Permission 实例；
        否则直接返回传入的 Permission 实例。
        """
        if isinstance(permission_or_id, int):
            return PermissionService.get_permission(permission_or_id)
        return permission_or_id

    @staticmethod
    def list_permissions(filters: dict):
        """
        根据过滤条件返回 Permission 查询对象
        """
        query = Permission.query.order_by(Permission.id.desc())

        if filters.get('name'):
            query = query.filter(Permission.name.ilike(f"%{filters['name']}%"))
        if filters.get('is_active') is not None:
            query = query.filter(Permission.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_permission(permission_id: int) -> Permission:
        """
        根据 ID 获取单个 Permission，不存在时抛出 404
        """
        permission = get_object_or_404(Permission, permission_id)
        return permission

    @staticmethod
    @transactional
    def create_permission(data: dict) -> Permission:
        """
        创建新 Permission
        """
        permission = Permission(
            name=data['name'],
            description=data.get('description', ''),
            is_active=data.get('is_active', True)
        )
        db.session.add(permission)
        # db.session.commit()
        return permission

    @staticmethod
    @transactional
    def update_permission(permission_id: int, data: dict) -> Permission:
        """
        更新 Permission 信息
        """
        permission = PermissionService.get_permission(permission_id)

        permission.name = data.get('name', permission.name)
        permission.description = data.get('description', permission.description)
        permission.is_active = data.get('is_active', permission.is_active)

        # db.session.commit()
        return permission

    @staticmethod
    @transactional
    def delete_permission(permission_id: int):
        """
        删除 Permission
        """
        permission = PermissionService.get_permission(permission_id)
        db.session.delete(permission)
        # db.session.commit()
