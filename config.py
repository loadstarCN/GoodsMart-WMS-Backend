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
    # Redis 不可用时限流退回进程内存计数并吞掉存储错误，避免登录等接口因 Redis 故障 500
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True
    RATELIMIT_SWALLOW_ERRORS = True
    # 调度器（webhook 推送 + 库存快照）只应在 WSGI 服务进程里启动；CLI / 迁移脚本请设为 False
    SCHEDULER_ENABLED = os.getenv('SCHEDULER_ENABLED', 'True') == 'True'
    # 加密敏感系统设置（SMTP 密码等）的 Fernet 密钥；不设则退回数据库内自动生成的密钥（仅混淆）
    SETTINGS_ENCRYPTION_KEY = os.getenv('SETTINGS_ENCRYPTION_KEY')
    # 出口单证（商业发票 / 装箱单）：单证日期所用时区；可选 TTF 字体（不设用 Helvetica，非拉丁字符退回内置日文 CID 字体）
    DOCUMENT_TIMEZONE = os.getenv('DOCUMENT_TIMEZONE', 'Asia/Tokyo')
    CUSTOMS_PDF_FONT_PATH = os.getenv('CUSTOMS_PDF_FONT_PATH') or None

    # 承运商对接：FedEx 自动建运单（Ship API）。API Key / Secret Key / 账号缺任一项 = 功能关闭
    FEDEX_API_BASE = os.getenv('FEDEX_API_BASE') or 'https://apis-sandbox.fedex.com'
    FEDEX_API_KEY = os.getenv('FEDEX_API_KEY') or None
    FEDEX_SECRET_KEY = os.getenv('FEDEX_SECRET_KEY') or None
    FEDEX_ACCOUNT_NUMBER = os.getenv('FEDEX_ACCOUNT_NUMBER') or None
    # 允许用 FedEx 自动建运单的公司 ID（逗号分隔）。运费记在同一个 FedEx 账号上：不设 = 所有公司都不允许
    FEDEX_ALLOWED_COMPANY_IDS = os.getenv('FEDEX_ALLOWED_COMPANY_IDS', '')
    FEDEX_SERVICE_TYPE = os.getenv('FEDEX_SERVICE_TYPE', 'INTERNATIONAL_ECONOMY')
    FEDEX_PICKUP_TYPE = os.getenv('FEDEX_PICKUP_TYPE', 'USE_SCHEDULED_PICKUP')
    # 面单打印方式：A4（激光打印机，PDF + PAPER_85X11_TOP_HALF_LABEL）/ THERMAL（4 英寸面单机，PDF + STOCK_4X6）；
    # 建单时可指定，不指定用默认。各格式的 imageType（PDF / PNG / ZPLII / EPL2）与纸张可覆盖，不设用上面的默认
    FEDEX_DEFAULT_LABEL_FORMAT = os.getenv('FEDEX_DEFAULT_LABEL_FORMAT') or 'A4'
    FEDEX_LABEL_A4_IMAGE_TYPE = os.getenv('FEDEX_LABEL_A4_IMAGE_TYPE') or None
    FEDEX_LABEL_A4_STOCK_TYPE = os.getenv('FEDEX_LABEL_A4_STOCK_TYPE') or None
    FEDEX_LABEL_THERMAL_IMAGE_TYPE = os.getenv('FEDEX_LABEL_THERMAL_IMAGE_TYPE') or None
    FEDEX_LABEL_THERMAL_STOCK_TYPE = os.getenv('FEDEX_LABEL_THERMAL_STOCK_TYPE') or None
    # 电子贸易单证（ETD）：开启时先把当前 CI 上传给 FedEx，建单时引用；关闭时仓库打印 CI 随货
    FEDEX_ETD_ENABLED = os.getenv('FEDEX_ETD_ENABLED', 'False').lower() in ('1', 'true', 'yes')
    # 关税付款方：RECIPIENT（收件人）/ SENDER（发件人账号）
    FEDEX_DUTIES_PAYMENT_TYPE = os.getenv('FEDEX_DUTIES_PAYMENT_TYPE', 'RECIPIENT')
    # Trade Documents Upload API 的地址（与 Ship API 不同域）；不设则按 FEDEX_API_BASE 是否为测试环境自动选
    FEDEX_DOCUMENT_API_BASE = os.getenv('FEDEX_DOCUMENT_API_BASE') or None
    FEDEX_CONNECT_TIMEOUT_SECONDS = float(os.getenv('FEDEX_CONNECT_TIMEOUT_SECONDS', 5))
    FEDEX_TIMEOUT_SECONDS = float(os.getenv('FEDEX_TIMEOUT_SECONDS', 30))
    # 建单总时限（OAuth + ETD 上传 + 建单请求，各次请求的超时按剩余时间收缩）；要小于 worker / 反向代理超时
    FEDEX_CREATE_BUDGET_SECONDS = float(os.getenv('FEDEX_CREATE_BUDGET_SECONDS', 90))
    # 建单记录停在 pending 超过这么多分钟按「结果不明」处理（进程被杀等）；实际不短于建单总时限 + 75 秒
    FEDEX_PENDING_STALE_MINUTES = float(os.getenv('FEDEX_PENDING_STALE_MINUTES', 10))

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
    # 测试里 FedEx 一律关闭（用例需要时自己设置并 mock HTTP），本机 .env 里的凭证不影响测试
    FEDEX_API_KEY = None
    FEDEX_SECRET_KEY = None
    FEDEX_ACCOUNT_NUMBER = None
    FEDEX_ALLOWED_COMPANY_IDS = ''
    FEDEX_ETD_ENABLED = False
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

    