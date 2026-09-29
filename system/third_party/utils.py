import hashlib
import secrets
from flask import current_app, g, request
from flask_jwt_extended import get_jwt_identity,verify_jwt_in_request
from extensions import db, jwt
from system.user.models import User
from .models import APIKey

def generate_api_key():
    """生成唯一的 API Key 明文（64 位十六进制）"""
    return secrets.token_hex(32)


def hash_api_key(raw_key: str) -> str:
    """API Key 只以 SHA-256 哈希落库，请求时对明文做同样哈希后查找"""
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def api_key_prefix(raw_key: str) -> str:
    return raw_key[:8]


def find_active_api_key(raw_key):
    """按明文查找启用中的 API Key"""
    if not raw_key:
        return None
    return APIKey.query.filter_by(key=hash_api_key(raw_key.strip()), is_active=True).first()


def _apply_api_key(key_entry):
    """把 API Key 身份写入 g（系统身份、公司、绑定用户）"""
    g.current_system = {"api_key": key_entry}
    # 设置 API Key 关联的 company_id（数据隔离）
    if key_entry.company_id:
        g.api_key_company_id = key_entry.company_id

    # 如果 API Key 关联了 user_id，则设置 g.current_user
    if key_entry.user_id and (g.get("current_user") is None or g.current_user.id != key_entry.user_id):
        user = db.session.get(User, key_entry.user_id)
        if user and user.is_active:
            g.current_user = user


def validate_api_key():
    """验证 API Key 并设置系统身份"""
    key_entry = find_active_api_key(request.headers.get("X-API-KEY"))
    if key_entry:
        _apply_api_key(key_entry)
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
            if g.current_user is None:
                user = db.session.get(User, identity)
                if user and user.is_active:
                    g.current_user = user
            return  # 如果 JWT 验证通过，则结束函数
    except Exception:
        pass  # 如果没有有效的 JWT，不做处理

    # 如果没有 JWT，检查是否存在 API Key
    key_entry = find_active_api_key(request.headers.get("X-API-KEY"))
    if key_entry:
        _apply_api_key(key_entry)


def get_api_key_company_id():
    """获取当前 API Key 关联的 company_id

    优先级：请求参数 > API Key 绑定值
    用于 views 中替代硬编码 args.get('company_id')
    """
    return getattr(g, 'api_key_company_id', None)
