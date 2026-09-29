"""JWT 扩展

除了 JWTManager 实例，这里还集中定义了三类回调（身份序列化、用户加载、吊销检查），
由 app.py 与 tests/helpers.py 共用，保证测试与生产行为一致。

吊销机制（Redis）：
- jwt_block:<jti>      单个 token 吊销（登出、refresh 轮换后作废旧 refresh token）
- jwt_cutoff:<user_id> 用户级吊销：iat 早于该时间戳的 token 全部失效（改密、重置密码、停用）
"""
import time
from datetime import datetime

from flask import current_app
from flask_jwt_extended import JWTManager

__all__ = ['jwt', 'authorizations', 'register_jwt_callbacks', 'revoke_token',
           'revoke_all_user_tokens', 'load_active_user']

jwt = JWTManager()         # JWT 扩展
authorizations = {
	"jsonWebToken": {
		"type": "apiKey",
		"in": "header",
		"name": "Authorization"
	}
}

BLOCKLIST_PREFIX = 'jwt_block:'
USER_CUTOFF_PREFIX = 'jwt_cutoff:'


def _redis():
    from extensions.redis import redis_client
    return redis_client


def _refresh_ttl_seconds():
    return int(current_app.config['JWT_REFRESH_TOKEN_EXPIRES'].total_seconds())


def revoke_token(jti, exp=None):
    """吊销单个 token。exp 为该 token 的过期时间戳，用来决定 Redis 记录保留多久。"""
    if not jti:
        return
    ttl = int(exp - time.time()) if exp else _refresh_ttl_seconds()
    if ttl <= 0:
        return
    _redis().setex(f'{BLOCKLIST_PREFIX}{jti}', ttl, '1')


def revoke_all_user_tokens(user_id):
    """吊销某用户当前已签发的全部 token（改密 / 重置密码 / 停用账号时调用）。"""
    _redis().setex(f'{USER_CUTOFF_PREFIX}{user_id}', _refresh_ttl_seconds(), str(int(time.time())))


def load_active_user(identity):
    """按 id 加载用户；账号被停用、或员工所属公司停用/过期时返回 None（→ 401）。"""
    from extensions.db import db
    from system.user.models import User

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        return None

    if user.type == 'staff':
        company = getattr(user, 'company', None)
        if company is not None:
            if not company.is_active:
                return None
            if company.expired_at and company.expired_at < datetime.now():
                return None
    return user


def register_jwt_callbacks(manager):
    """注册身份序列化 / 用户加载 / 吊销检查回调。"""

    @manager.user_identity_loader
    def user_identity_lookup(user):
        """定义如何将用户对象序列化到 JWT 中"""
        return str(user.id)  # 强制转换为字符串

    @manager.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        """根据 JWT 数据加载用户（被停用 / 公司过期返回 None）"""
        return load_active_user(jwt_data.get("sub"))

    @manager.user_lookup_error_loader
    def user_lookup_error_callback(_jwt_header, _jwt_data):
        return {"msg": "User is inactive or not found"}, 401

    @manager.token_in_blocklist_loader
    def token_revoked_callback(_jwt_header, jwt_data):
        r = _redis()
        jti = jwt_data.get('jti')
        if jti and r.exists(f'{BLOCKLIST_PREFIX}{jti}'):
            return True
        cutoff = r.get(f'{USER_CUTOFF_PREFIX}{jwt_data.get("sub")}')
        if cutoff:
            cutoff = int(cutoff.decode() if isinstance(cutoff, bytes) else cutoff)
            if int(jwt_data.get('iat', 0)) < cutoff:
                return True
        return False
