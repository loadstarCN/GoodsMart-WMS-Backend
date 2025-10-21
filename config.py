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

    DEBUG = os.getenv('DEBUG', True)  # 调试模式

    # Celery 配置
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    # 确保导入任务模块
    CELERY_TASKS_MODULES = ['tasks']  # 自动发现任务模块的路径

    CELERY_USER_OPTIONS = {
        'task_serializer': 'json',
        'result_expires': 3600,
        'timezone': 'UTC'
    }

    LOG_DIRECTORY = os.getenv('LOG_DIRECTORY', 'logs/large_requests')  # Default to 'logs/large_requests' if env var is not set
    MAX_LOG_SIZE = int(os.getenv('MAX_LOG_SIZE', 1 * 1024 * 1024))  # 1MB default size

    CHECK_WHITELIST = os.getenv('CHECK_WHITELIST', 'False') == 'True'  # 是否检查白名单
    CHECK_BLACKLIST = os.getenv('CHECK_BLACKLIST', 'False') == 'True'  # 是否检查黑名单

    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', 300))  # 默认缓存超时时间
    CACHE_TYPE = os.getenv('CACHE_TYPE', 'redis')  # 缓存类型

    MQTT_BROKER_URL = os.getenv('MQTT_BROKER_URL', 'localhost')  # MQTT Broker URL
    MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', 1883))  # MQTT Broker 端口
    MQTT_USERNAME = os.getenv('MQTT_USERNAME', 'user')  # MQTT 用户名
    MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', 'password')  # MQTT 密码   

class DevelopmentConfig(Config):
    DEBUG = True # 只在开发环境中启用调试
    SQLALCHEMY_ECHO=False # 打印SQL语句
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI_DEV', 'sqlite:///test.db')  # 数据库连接
    REDIS_URL = os.getenv('REDIS_URL_DEV', 'redis://localhost:6379/0') # REDIS配置
    OSS_ENDPOINT = os.getenv('OSS_ENDPOINT_DEV')  # 阿里云 OSS Endpoint 地址
    OSS_BUCKET_NAME = os.getenv('OSS_BUCKET_NAME_DEV')  # 阿里云 OSS Bucket 名称
    OSS_HOST = os.getenv('OSS_HOST_DEV')  # 阿里云 OSS Host 地址


class TestingConfig(Config):
    TESTING = True
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

    