import secrets
from flask import current_app, g, request
from flask_jwt_extended import get_jwt_identity,verify_jwt_in_request
from extensions import db, jwt
from system.user.models import User
from .models import APIKey

def generate_api_key():
    """生成唯一的 API Key"""
    return secrets.token_hex(32)  # 返回 64 位十六进制字符串

def validate_api_key():
    """验证 API Key 并设置系统身份"""
    api_key = request.headers.get("X-API-KEY")
    print(f"API Key: {api_key}")
    if api_key:
        key_entry = APIKey.query.filter_by(key=api_key, is_active=True).first()
        if key_entry:
            # 设置系统身份
            g.current_system = {"api_key": key_entry}
        else:
            g.current_system = None
    else:
        g.current_system = None


def validate_jwt_and_api_key():
    """验证 JWT 或 API Key 并设置 g.current_user 和 g.current_system"""
    # 初始化 g.current_system 和 g.current_user
    g.current_system = None
    g.current_user = None

   

    # 尝试验证 JWT（可选）
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            # 如果 current_user 尚未设置，则通过 identity 加载用户
            if not hasattr(g, "current_user") or g.current_user is None:
                user = db.session.get(User, identity)
                if user and user.is_active:
                    g.current_user = user
            return  # 如果 JWT 验证通过，则结束函数
    except Exception:
        pass  # 如果没有有效的 JWT，不做处理

    # 如果没有 JWT，检查是否存在 API Key
    api_key = request.headers.get("X-API-KEY")
    if api_key:
        key_entry = APIKey.query.filter_by(key=api_key, is_active=True).first()
        if key_entry and key_entry.is_active:
            # 设置 g.current_system
            g.current_system = {"api_key": key_entry}

            # 如果 API Key 关联了 user_id，则设置 g.current_user
            if key_entry.user_id:
                if (
                    not hasattr(g, "current_user") or g.current_user is None 
                    or g.current_user.id != key_entry.user_id
                ):
                    user = db.session.get(User, key_entry.user_id)
                    if user and user.is_active:
                        g.current_user = user

