from functools import wraps
from flask import g
from flask_restx import abort
from flask_jwt_extended import jwt_required, current_user

from extensions.error import BadRequestException, ForbiddenException, NotFoundException, UnauthorizedException


# 单独使用，在不适用使用用户和第三方统一的验证系统的情况下
def role_required(required_roles):
    def decorator(f):
        @wraps(f)
        @jwt_required()
        def wrapper(*args, **kwargs):
            if not current_user:
                raise NotFoundException("User not found", 13002)

            user_roles = [role.name for role in current_user.roles]
            print(user_roles)
            print(required_roles)
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
                # print("User roles:",current_user)
                # print("User permissions:",user_permissions)
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
