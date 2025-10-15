# extensions/limiter.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
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

# 创建 Limiter 实例，并初始化
limiter = Limiter(
    get_remote_address,  # 获取客户端的 IP 地址
    app=None,  # 不在此处初始化，而是通过 init_app
    storage_uri=storage_uri,  # 使用应用配置中的 Redis URL
    storage_options={"socket_connect_timeout": 30},  # 可选配置
    strategy="fixed-window",  # 或 "moving-window"
)

def limit_init_app(app):
    """Initialize the limiter with app configuration."""   
    # 初始化 Limiter 扩展
    limiter.init_app(app)
