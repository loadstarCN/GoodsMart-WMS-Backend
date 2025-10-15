from flask import Flask
from flask_cors import CORS
from config import Config,DevelopmentConfig, TestingConfig, ProductionConfig
from extensions import db,get_object_or_404, jwt, migrate,celery_ext,redis_client,error,oss,cache,limit_init_app,mqtt_client
from system.user.models import User
from system import blueprint as system_api
from tasks import blueprint as task_api
from warehouse import blueprint as warehouse_api
from system.third_party.utils import validate_jwt_and_api_key
from system.logs.utils import before_request_logging, after_request_logging
from system.limiter.utils import initialize_ip_lists,check_ip

import os

def create_app():
    app = Flask(__name__)
    CORS(app)  # 允许所有来源的请求

    # 根据 FLASK_ENV 环境变量动态加载配置
    if Config.FLASK_ENV == 'production':
        app.config.from_object(ProductionConfig)
    elif Config.FLASK_ENV == 'testing':
        app.config.from_object(TestingConfig)
    else:
        app.config.from_object(DevelopmentConfig)

    print(f"Running in {Config.FLASK_ENV} mode")

    # 初始化扩展
    db.init_app(app)    
    migrate.init_app(app, db) 
    redis_client.init_app(app)
    celery_ext.init_app(app)    
    limit_init_app(app)
    jwt.init_app(app)
    oss.init_app(app)
    cache.init_app(app)  # 初始化缓存
    # mqtt_client.init_app(app)  # 初始化 MQTT 客户端

    # 注册 JWT 回调
    @jwt.user_identity_loader
    def user_identity_lookup(user):
        """定义如何将用户对象序列化到 JWT 中"""
        return str(user.id)  # 强制转换为字符串

    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        """根据 JWT 数据加载用户"""
        identity = jwt_data.get("sub")
        if identity:
            return get_object_or_404(User, identity)
    
    # 注册 JWT and API Key 验证逻辑
    app.before_request(validate_jwt_and_api_key)


    # 确保日志目录存在  
    log_directory = app.config['LOG_DIRECTORY']
    os.makedirs(log_directory, exist_ok=True)
    # 注册Logs中间件
    app.before_request(before_request_logging)
    app.after_request(after_request_logging)

    # app.before_request(check_ip)

    # 注册命名空间或蓝图    
    app.register_blueprint(system_api, url_prefix='/system')
    app.register_blueprint(task_api, url_prefix='/tasks')
    app.register_blueprint(warehouse_api, url_prefix='/warehouse')

    # 初始化 IP 黑白名单    
    # with app.app_context():  # 推送应用上下文
    #     initialize_ip_lists()  # 调用初始化函数

    # 注册错误处理器
    error.register_error_handlers(app)    

    return app,celery_ext.get_client()

app,celery = create_app()

if __name__ == "__main__":    
    app.run(debug=True)
