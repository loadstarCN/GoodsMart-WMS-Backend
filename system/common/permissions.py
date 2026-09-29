from functools import wraps
from flask import g
from flask_restx import abort
from flask_jwt_extended import jwt_required, current_user

from extensions.error import BadRequestException, ForbiddenException, NotFoundException, UnauthorizedException

# 只允许平台超管持有 / 授予的权限。非超管（公司管理员等）不得把这些权限
# 授予 API Key 或角色，否则可以借道提权为平台管理员。
PLATFORM_ONLY_PERMISSIONS = frozenset({
    'all_access',
    'user_read', 'user_edit', 'user_delete',
    'role_read', 'role_edit', 'role_delete',
    'permission_read', 'permission_edit', 'permission_delete',
    'logs_read', 'logs_create', 'logs_edit', 'logs_delete',
    'limiter_read', 'limiter_edit', 'limiter_delete',
    'tasks_execute',
    'company_delete',
})


def get_actor_context():
    """返回当前调用方的 (is_super, permissions, company_id)，兼容 JWT 用户与 API Key 两种路径。

    - JWT 用户：权限来自角色；company_id 仅 staff 有
    - API Key：权限来自 key.permissions；company_id 取 key 绑定值，未绑定时退回绑定用户的公司
    """
    if current_user:
        perms = {p.name for role in current_user.roles for p in role.permissions}
        return 'all_access' in perms, perms, getattr(current_user, 'company_id', None)

    system = getattr(g, 'current_system', None)
    if system and system.get('api_key') is not None:
        key = system['api_key']
        perms = set(key.permissions or [])
        company_id = key.company_id
        if company_id is None:
            company_id = getattr(getattr(g, 'current_user', None), 'company_id', None)
        return 'all_access' in perms, perms, company_id

    return False, set(), None


def get_actor_company_id():
    """当前调用方所属公司；平台管理员 / 超级密钥返回 None（不受公司限制）"""
    return get_actor_context()[2]


def assert_grantable_permissions(permission_names, actor=None):
    """校验调用方是否有资格授予这些权限（给 API Key 或角色）。

    规则：超管随意；其他人不得授予 PLATFORM_ONLY_PERMISSIONS，且只能授予自己拥有的权限
    （持有 company_all_access 视为拥有全部公司范围权限）。
    """
    is_super, perms, _ = actor or get_actor_context()
    if is_super:
        return
    for name in permission_names:
        if name in PLATFORM_ONLY_PERMISSIONS:
            raise ForbiddenException(f"Not allowed to grant permission '{name}'", 12006)
        if 'company_all_access' not in perms and name not in perms:
            raise ForbiddenException(f"Not allowed to grant permission '{name}' you do not have", 12006)


# 单独使用，在不适用使用用户和第三方统一的验证系统的情况下
def role_required(required_roles):
    def decorator(f):
        @wraps(f)
        @jwt_required()
        def wrapper(*args, **kwargs):
            if not current_user:
                raise NotFoundException("User not found", 13002)

            user_roles = [role.name for role in current_user.roles]
            if not any(role in user_roles for role in required_roles):
                raise ForbiddenException("Access denied: insufficient role", 12001)

            return f(*args, **kwargs)
        return wrapper
    return decorator


def permission_required(required_permissions):
    """
    装饰器，要求传入的 required_permissions 是一个列表，
    列表中每个元素为一个字符串，例如：["goods_read"]

    装饰器会检查当前用户（或 API Key）是否拥有所需权限。
    权限是从用户的角色中继承来的，数据库和模型层面仍然保留了角色的概念。
    """
    # 校验格式

    if isinstance(required_permissions, str):
        required_permissions = [required_permissions]
    elif not (isinstance(required_permissions, list) and all(isinstance(p, str) for p in required_permissions)):
        raise BadRequestException("Invalid required_permissions format. Must be a string or list of permission strings.", 14008)


    def decorator(func):
        @wraps(func)
        @jwt_required(optional=True)  # 支持 JWT 或 API Key 认证
        def wrapper(*args, **kwargs):

            # 如果当前有通过 JWT 认证的用户
            if current_user:
                # 从用户的所有角色中收集权限
                user_permissions = {perm.name for role in current_user.roles for perm in role.permissions}
                # 只要任意一个权限满足即可
                if not any(permission in user_permissions for permission in required_permissions):
                    raise ForbiddenException("Permission denied", 12001)
                return func(*args, **kwargs)

            # 如果没有当前用户，尝试使用 API Key 方式验证
            elif hasattr(g, "current_system") and g.current_system:
                api_key = g.current_system.get("api_key")
                if not api_key:
                    raise ForbiddenException("API Key not found.", 12004)
                # 同样只要任意一个权限满足即可
                if not any(api_key.has_permission(permission) for permission in required_permissions):
                    raise ForbiddenException("Permission denied", 12001)
                return func(*args, **kwargs)

            else:
                raise UnauthorizedException("Unauthorized", 11003)
        return wrapper
    return decorator
