from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config,DevelopmentConfig, TestingConfig, ProductionConfig
from extensions import db, jwt, migrate,redis_client,error,oss,cache,limit_init_app
from extensions.jwt import register_jwt_callbacks
from system import blueprint as system_api
from tasks import blueprint as task_api
from warehouse import blueprint as warehouse_api
from system.third_party.utils import validate_jwt_and_api_key
from system.logs.utils import before_request_logging, after_request_logging
from system.limiter.utils import initialize_ip_lists,check_ip

import os

_INSECURE_JWT_SECRETS = {'super-secret-jwt', 'your_secure_random_jwt_secret_key_here', ''}


def _validate_production_config(app):
    """生产环境启动前检查：密钥 / 数据库漏配直接拒绝启动，而不是带着默认值静默运行"""
    if Config.FLASK_ENV != 'production':
        return
    problems = []
    secret = app.config.get('JWT_SECRET_KEY') or ''
    if secret in _INSECURE_JWT_SECRETS or len(secret) < 32:
        problems.append('JWT_SECRET_KEY must be a random string of at least 32 characters')
    if str(app.config.get('SQLALCHEMY_DATABASE_URI', '')).startswith('sqlite'):
        problems.append('SQLALCHEMY_DATABASE_URI must point to the production database (sqlite is not allowed)')
    if problems:
        raise RuntimeError('Refusing to start in production: ' + '; '.join(problems))
    if app.config.get('CORS_ORIGINS') == '*':
        app.logger.warning('CORS_ORIGINS is "*" in production; set it to the admin/app domains')


def create_app():
    app = Flask(__name__)

    # 根据 FLASK_ENV 环境变量动态加载配置
    if Config.FLASK_ENV == 'production':
        app.config.from_object(ProductionConfig)
    elif Config.FLASK_ENV == 'testing':
        app.config.from_object(TestingConfig)
    else:
        app.config.from_object(DevelopmentConfig)

    _validate_production_config(app)

    # 反向代理后面时还原真实客户端 IP / 协议（限流、审计日志、IP 黑白名单都依赖 remote_addr）
    proxies = int(app.config.get('TRUSTED_PROXY_COUNT', 0))
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    CORS(app, origins=app.config['CORS_ORIGINS'])
    app.logger.info(f"Running in {Config.FLASK_ENV} mode")

    # 初始化扩展
    db.init_app(app)
    migrate.init_app(app, db)
    redis_client.init_app(app)
    limit_init_app(app)
    jwt.init_app(app)
    oss.init_app(app)
    cache.init_app(app)  # 初始化缓存

    # 注册 JWT 回调（身份序列化 / 用户加载含停用与公司过期检查 / token 吊销）
    register_jwt_callbacks(jwt)

    # 注册 JWT and API Key 验证逻辑
    app.before_request(validate_jwt_and_api_key)


    # 确保日志目录存在
    log_directory = app.config['LOG_DIRECTORY']
    os.makedirs(log_directory, exist_ok=True)
    # 注册Logs中间件
    app.before_request(before_request_logging)
    app.after_request(after_request_logging)

    # IP 黑白名单（由 CHECK_BLACKLIST / CHECK_WHITELIST 控制是否生效）
    app.before_request(check_ip)

    # 注册命名空间或蓝图
    app.register_blueprint(system_api, url_prefix='/system')
    app.register_blueprint(task_api, url_prefix='/tasks')
    app.register_blueprint(warehouse_api, url_prefix='/warehouse')

    # 注册 CLI 命令
    from system.webhook.commands import webhook_cli
    app.cli.add_command(webhook_cli)

    from tasks.commands import snapshot_cli
    app.cli.add_command(snapshot_cli)

    # 初始化 IP 黑白名单到 Redis（开关打开时才加载，避免无 Redis 的本地环境启动失败）
    if app.config.get('CHECK_BLACKLIST') or app.config.get('CHECK_WHITELIST'):
        with app.app_context():
            try:
                initialize_ip_lists()
            except Exception as e:
                app.logger.error(f"Failed to load IP lists into Redis: {e}")

    # 注册错误处理器
    error.register_error_handlers(app)

    # 启动定时任务调度器（webhook 推送 + 库存快照）；CLI / 迁移脚本可用 SCHEDULER_ENABLED=False 关闭
    if app.config.get('SCHEDULER_ENABLED', True):
        from scheduler import init_scheduler
        init_scheduler(app)

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5002, debug=app.config.get('DEBUG', False))
