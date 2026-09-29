import hmac
import re
import secrets
import threading
from datetime import datetime, timedelta
from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token
from extensions.db import *
from extensions.error import BadRequestException, ConflictException, ForbiddenException, NotFoundException, UnauthorizedException
from extensions.jwt import revoke_all_user_tokens
from extensions.redis import redis_client
from extensions.transaction import transactional
from system.common.permissions import assert_grantable_permissions, get_actor_context
from .models import User, Role, Permission

EMAIL_REGEX = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
RESET_CODE_PREFIX = 'pwd_reset:'
RESET_ATTEMPTS_PREFIX = 'pwd_reset_attempts:'
RESET_CODE_TTL = 300  # 5 minutes
RESET_MAX_ATTEMPTS = 5


def _send_email_async(to: str, subject: str, html: str):
    """在后台线程发送邮件，避免 SMTP 往返阻塞请求线程（uwsgi 单线程时会拖垮整站）"""
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                from system.settings.services import SettingsService
                SettingsService.send_email(to, subject, html)
            except Exception as e:
                app.logger.error(f"Failed to send email to {to}: {e}")

    threading.Thread(target=_run, daemon=True).start()


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

        if not user or not user.check_password(password):
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
            if not staff.company.is_active:
                raise UnauthorizedException("Company is not active", 11007)

        # 生成 JWT token
        access_token = create_access_token(identity=user)
        expires_in = current_app.config['JWT_ACCESS_TOKEN_EXPIRES']
        refresh_token = create_refresh_token(identity=user)
        refresh_expires_in = current_app.config['JWT_REFRESH_TOKEN_EXPIRES']

        # 更新用户模型的refresh_token及有效期（需持久化存储）
        user.refresh_token_expires_at = datetime.now() + refresh_expires_in
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
        刷新 token。剩余有效期不足 24h 时轮换 refresh_token（调用方负责吊销旧的）。
        """

        access_token = create_access_token(identity=current_user)
        expires_in = current_app.config['JWT_ACCESS_TOKEN_EXPIRES']

        # 返回用户信息和 token
        user_dict = current_user.to_dict()
        user_dict["access_token"] = access_token
        user_dict["expires_in"] = expires_in.total_seconds()

        # 检查当前refresh_token剩余有效期
        expires_at = current_user.refresh_token_expires_at  # 在User模型中记录

        if expires_at is None or (expires_at - datetime.now()) < timedelta(hours=24):
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

        if filters.get('username'):
            query = query.filter(User.user_name.ilike(f"%{filters['username']}%"))
        if filters.get('email'):
            query = query.filter(User.email.ilike(f"%{filters['email']}%"))
        if filters.get('keyword'):
            keyword = f"%{filters['keyword']}%"
            query = query.filter(User.user_name.ilike(keyword) | User.email.ilike(keyword))
        if filters.get('is_active') is not None:
            query = query.filter(User.is_active == filters['is_active'])
        if filters.get('type'):
            query = query.filter(User.type == filters['type'])

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
        if not data.get('password'):
            raise BadRequestException("password is required", 10013)

        new_user = User(
            user_name=data['user_name'],
            email=data['email'],
            avatar=data.get('avatar', ''),
            is_active=bool(data.get('is_active', True)),
        )

        new_user.set_password(data['password'])
        # Assign roles if provided（只允许授予调用方有资格授予的角色）
        if 'roles' in data:
            new_user.roles = RoleService.resolve_assignable_roles(data['roles'])

        db.session.add(new_user)
        # db.session.commit()
        return new_user

    @staticmethod
    @transactional
    def update_user(user_id: int, data: dict, actor_id=None) -> User:
        """
        更新 User 信息（含 is_active；不允许停用自己）
        """
        user = UserService.get_user(user_id)

        user.user_name = data.get('user_name', user.user_name)
        user.email = data.get('email', user.email)
        user.avatar = data.get('avatar', user.avatar)

        revoke = False
        if 'is_active' in data:
            is_active = bool(data['is_active'])
            if not is_active and actor_id == user.id:
                raise BadRequestException("You cannot deactivate your own account", 10010)
            if user.is_active and not is_active:
                revoke = True
            user.is_active = is_active

        if 'roles' in data:
            user.roles = RoleService.resolve_assignable_roles(data['roles'])

        if data.get('password'):
            user.set_password(data.get('password'))
            revoke = True

        if revoke:
            revoke_all_user_tokens(user.id)

        # db.session.commit()
        return user

    @staticmethod
    @transactional
    def delete_user(user_id: int, actor_id=None):
        """
        删除 User（不允许删除自己；仍绑定启用中的 API Key 时拒绝，避免级联删除导致集成中断）
        """
        user = UserService.get_user(user_id)
        if actor_id == user.id:
            raise BadRequestException("You cannot delete your own account", 10011)

        from system.third_party.models import APIKey
        bound_keys = APIKey.query.filter_by(user_id=user.id, is_active=True).count()
        if bound_keys:
            raise ConflictException(
                f"User is bound to {bound_keys} active API key(s); reassign or deactivate them first", 44001
            )

        revoke_all_user_tokens(user.id)
        db.session.delete(user)
        # db.session.commit()

    @staticmethod
    @transactional
    def change_password(user_id, old_password, new_password):
        user = get_object_or_404(User, user_id)

        # 验证旧密码
        if not user.check_password(old_password):
            raise BadRequestException("Old password is incorrect",10003)
        if not new_password:
            raise BadRequestException("New password is required", 10013)

        # 这里使用了 User 模型中的 set_password 方法来加密新密码
        user.set_password(new_password)
        # 改密后既有 token 全部失效
        revoke_all_user_tokens(user.id)
        # db.session.commit()

    @staticmethod
    def forgot_password(email: str):
        """发送密码重置验证码到邮箱。

        无论邮箱是否存在都静默返回，避免枚举用户；邮件在后台线程发送。
        """
        user = User.query.filter_by(email=email).first()
        if not user or not user.is_active:
            return

        # 生成 6 位验证码（CSPRNG）
        code = f'{secrets.randbelow(1_000_000):06d}'

        # 存入 Redis，5 分钟过期；同时清零尝试次数
        redis_client.setex(f'{RESET_CODE_PREFIX}{email}', RESET_CODE_TTL, code)
        redis_client.delete(f'{RESET_ATTEMPTS_PREFIX}{email}')

        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 30px;">
            <h2 style="color: #4a6cf7; margin-bottom: 20px;">GoodsMart WMS</h2>
            <p>You are resetting your password. Use the verification code below:</p>
            <div style="background: #f5f7ff; border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #4a6cf7;">{code}</span>
            </div>
            <p style="color: #666;">This code expires in <strong>5 minutes</strong>.</p>
            <p style="color: #666;">If you did not request this, please ignore this email.</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
            <p style="color: #999; font-size: 12px;">GoodsMart Warehouse Management System</p>
        </div>
        """
        _send_email_async(email, 'Password Reset Code - GoodsMart WMS', html)

    @staticmethod
    @transactional
    def reset_password(email: str, code: str, new_password: str):
        """验证验证码并重置密码（错误 5 次即作废验证码）"""
        code_key = f'{RESET_CODE_PREFIX}{email}'
        attempts_key = f'{RESET_ATTEMPTS_PREFIX}{email}'

        stored_code = redis_client.get(code_key)
        if not stored_code:
            raise BadRequestException("Verification code expired or not found", 10007)

        attempts = redis_client.incr(attempts_key)
        redis_client.expire(attempts_key, RESET_CODE_TTL)
        if attempts > RESET_MAX_ATTEMPTS:
            redis_client.delete(code_key)
            raise BadRequestException("Too many attempts, please request a new code", 10009)

        stored = stored_code.decode('utf-8') if isinstance(stored_code, bytes) else str(stored_code)
        if not hmac.compare_digest(stored, str(code)):
            raise BadRequestException("Invalid verification code", 10008)

        user = User.query.filter_by(email=email).first()
        if not user or not user.is_active:
            raise BadRequestException("Verification code expired or not found", 10007)
        if not new_password:
            raise BadRequestException("New password is required", 10013)

        user.set_password(new_password)
        # 清除已使用的验证码，并吊销该用户既有 token
        redis_client.delete(code_key)
        redis_client.delete(attempts_key)
        revoke_all_user_tokens(user.id)


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

        name = filters.get('name') or filters.get('keyword')
        if name:
            query = query.filter(Role.name.ilike(f"%{name}%"))
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
    def resolve_assignable_roles(role_names, actor=None):
        """按名字解析角色，并校验调用方有资格授予这些角色。

        规则与 API Key 权限一致：超管随意；其他人不得授予含平台专属权限的角色，
        也不得授予自己没有的权限（持 company_all_access 视为拥有全部公司范围权限）。
        """
        if not role_names:
            return []
        names = list(dict.fromkeys(role_names))
        roles = Role.query.filter(Role.name.in_(names)).all()
        missing = set(names) - {r.name for r in roles}
        if missing:
            raise BadRequestException(f"Unknown roles: {', '.join(sorted(missing))}", 14014)

        actor = actor or get_actor_context()
        for role in roles:
            try:
                assert_grantable_permissions({p.name for p in role.permissions}, actor)
            except ForbiddenException:
                raise ForbiddenException(f"Not allowed to assign role '{role.name}'", 12007)
        return roles

    @staticmethod
    def _resolve_permissions(permission_ids, actor=None):
        if not permission_ids:
            return []
        ids = list(dict.fromkeys(permission_ids))
        permissions = Permission.query.filter(Permission.id.in_(ids)).all()
        if len(permissions) != len(ids):
            raise BadRequestException("One or more permission ids do not exist", 14014)
        assert_grantable_permissions({p.name for p in permissions}, actor or get_actor_context())
        return permissions

    @staticmethod
    @transactional
    def create_role(data: dict) -> Role:
        """
        创建新 Role
        """
        role = Role(
            name=data['name'],
            description=data.get('description', ''),
            is_active=bool(data.get('is_active', True)),
        )
        role.permissions = RoleService._resolve_permissions(data.get('permissions', []))

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
        if 'is_active' in data:
            role.is_active = bool(data['is_active'])
        if 'permissions' in data:
            role.permissions = RoleService._resolve_permissions(data.get('permissions', []))

        # db.session.commit()
        return role

    @staticmethod
    @transactional
    def delete_role(role_id: int):
        """
        删除 Role（仍有用户使用时拒绝）
        """
        role = RoleService.get_role(role_id)
        in_use = role.users.count()
        if in_use:
            raise ConflictException(f"Role is assigned to {in_use} user(s) and cannot be deleted", 44002)
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

        name = filters.get('name') or filters.get('keyword')
        if name:
            query = query.filter(Permission.name.ilike(f"%{name}%") | Permission.description.ilike(f"%{name}%"))

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
