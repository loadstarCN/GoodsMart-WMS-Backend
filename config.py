import os
from dotenv import load_dotenv
from datetime import timedelta

# 加载 .env 文件
load_dotenv()

class Config:
    # 基础配置
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')  # 环境
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False  # 关闭跟踪修改（推荐关闭）

    OSS_ACCESS_KEY_ID = os.getenv('OSS_ACCESS_KEY_ID')  # 阿里云 OSS Access Key ID
    OSS_ACCESS_KEY_SECRET = os.getenv('OSS_ACCESS_KEY_SECRET')  # 阿里云 OSS Access Key Secret
    
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'super-secret-jwt')  # JWT 秘钥
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES",24)))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(hours=int(os.getenv("JWT_REFRESH_TOKEN_EXPIRES",168)))

    DEBUG = os.getenv('DEBUG', 'False') == 'True'  # 调试模式
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*')  # 允许的跨域来源，生产环境应设置为具体域名

    # 请求体上限（图片 / CSV 上传），超过返回 413，避免无限制上传拖垮单进程
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 20 * 1024 * 1024))

    LOG_DIRECTORY = os.getenv('LOG_DIRECTORY', 'logs/large_requests')  # Default to 'logs/large_requests' if env var is not set
    MAX_LOG_SIZE = int(os.getenv('MAX_LOG_SIZE', 1 * 1024 * 1024))  # 1MB default size

    CHECK_WHITELIST = os.getenv('CHECK_WHITELIST', 'False') == 'True'  # 是否检查白名单
    CHECK_BLACKLIST = os.getenv('CHECK_BLACKLIST', 'False') == 'True'  # 是否检查黑名单

    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', 300))  # 默认缓存超时时间
    CACHE_TYPE = os.getenv('CACHE_TYPE', 'redis')  # 缓存类型

    # Webhook 回调 URL 严格校验：只允许 https、拒绝内网地址（防 SSRF）。开发/测试环境默认关闭
    WEBHOOK_URL_STRICT = os.getenv('WEBHOOK_URL_STRICT', 'True') == 'True'
    # 前置反向代理层数（nginx 等）。>0 时信任 X-Forwarded-For/Proto，否则限流、日志、黑白名单拿到的都是代理 IP
    TRUSTED_PROXY_COUNT = int(os.getenv('TRUSTED_PROXY_COUNT', 0))
    # Flask-Limiter 开关（登录 / 找回密码等端点的限流）
    RATELIMIT_ENABLED = os.getenv('RATELIMIT_ENABLED', 'True') == 'True'
    RATELIMIT_HEADERS_ENABLED = True
    # 调度器（webhook 推送 + 库存快照）只应在 WSGI 服务进程里启动；CLI / 迁移脚本请设为 False
    SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'True') == 'True'
    # 加密敏感系统设置（SMTP 密码等）的 Fernet 密钥；不设则退回数据库内自动生成的密钥（仅混淆）
    SETTINGS_ENCRYPTION_KEY = os.getenv('SETTINGS_ENCRYPTION_KEY')

class DevelopmentConfig(Config):
    DEBUG = True # 只在开发环境中启用调试
    WEBHOOK_URL_STRICT = os.getenv('WEBHOOK_URL_STRICT', 'False') == 'True'
    SQLALCHEMY_ECHO=False # 打印SQL语句
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI_DEV', 'sqlite:///test.db')  # 数据库连接
    REDIS_URL = os.getenv('REDIS_URL_DEV', 'redis://localhost:6379/0') # REDIS配置
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT_DEV')  # 阿里云 OSS Endpoint 地址
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME_DEV')  # 阿里云 OSS Bucket 名称
    OSS_HOST = os.getenv('OSS_HOST_DEV')  # 阿里云 OSS Host 地址


class TestingConfig(Config):
    TESTING = True
    WEBHOOK_URL_STRICT = False
    RATELIMIT_ENABLED = False
    SCHEDULER_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI_TEST', 'sqlite:///test.db')  # 数据库连接
    REDIS_URL = os.getenv('REDIS_URL_TEST', 'redis://localhost:6379/0') # REDIS配置
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT_TEST')  # 阿里云 OSS Endpoint
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME_TEST')  # 阿里云 OSS Bucket 名称
    OSS_HOST = os.getenv('OSS_HOST_TEST')  # 阿里云 OSS Host 地址

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///test.db')  # 数据库连接    
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0') # REDIS配置
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT')  # 阿里云 OSS Endpoint
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME')  # 阿里云 OSS Bucket 名称
    OSS_HOST = os.getenv('OSS_HOST')  # 阿里云 OSS Host 地址

    