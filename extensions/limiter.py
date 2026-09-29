# extensions/limiter.py
import ipaddress

from flask import request
from flask_limiter import Limiter
from config import Config
import os


storage_uri = None

# 根据 FLASK_ENV 环境变量动态加载配置,这里其实实现的并不好，没有统一到config中管理。但是如果不在这一步去初始化Limiter，则后面没有机会去初始化了了，否则装饰器会报错。
if Config.FLASK_ENV == 'production':
    storage_uri = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
elif Config.FLASK_ENV == 'testing':
    storage_uri = os.getenv('REDIS_URL_TEST', 'redis://localhost:6379/0')
else:
    storage_uri = os.getenv('REDIS_URL_DEV', 'redis://localhost:6379/0')


def _is_loopback(addr):
    try:
        return ipaddress.ip_address(addr).is_loopback
    except (TypeError, ValueError):
        return False


def get_client_address():
    """限流键：客户端 IP。

    配置了 TRUSTED_PROXY_COUNT 时 ProxyFix 已把 remote_addr 还原为真实客户端；
    未配置但 remote_addr 是本机回环（典型的 nginx → gunicorn 本地反代）时，退而取
    X-Forwarded-For 的最后一跳（nginx 追加的那一段，客户端无法伪造），否则所有用户
    会共用代理 IP 的一份配额，上班高峰登录会被误限。
    """
    addr = request.remote_addr or '127.0.0.1'
    if _is_loopback(addr):
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            last_hop = forwarded.split(',')[-1].strip()
            if last_hop:
                return last_hop
    return addr


# 创建 Limiter 实例，并初始化
limiter = Limiter(
    get_client_address,  # 获取客户端的 IP 地址
    app=None,  # 不在此处初始化，而是通过 init_app
    storage_uri=storage_uri,  # 使用应用配置中的 Redis URL
    storage_options={"socket_connect_timeout": 30},  # 可选配置
    strategy="fixed-window",  # 或 "moving-window"
)

def limit_init_app(app):
    """Initialize the limiter with app configuration."""
    # 初始化 Limiter 扩展
    limiter.init_app(app)
