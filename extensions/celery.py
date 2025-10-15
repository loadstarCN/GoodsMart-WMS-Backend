from celery import Celery

class CeleryExtension(object):
    def __init__(self, app=None):
        self.celery = None
        self.user_options = {}  # 添加 user_options 属性，用于存储配置信息
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """创建并配置 Celery 应用"""
        self.celery = Celery(
            app.import_name,
            broker=app.config['CELERY_BROKER_URL'],
            backend=app.config['CELERY_RESULT_BACKEND']
        )

        # 更新 Celery 配置
        self.celery.conf.update({
            'broker_url': app.config['CELERY_BROKER_URL'],
            'result_backend': app.config['CELERY_RESULT_BACKEND'],
        })

        # 可以添加额外的配置到 user_options
        self.user_options = app.config.get('CELERY_USER_OPTIONS', {})

        # 将 Flask 配置项和自定义选项合并
        self.celery.conf.update(self.user_options)

        # 自动发现任务
        self.celery.autodiscover_tasks(app.config['CELERY_TASKS_MODULES'])

        # 将 Flask 上下文绑定到 Celery
        class ContextTask(self.celery.Task):
            def __call__(self, *args, **kwargs):
                with app.app_context():
                    return self.run(*args, **kwargs)

        self.celery.Task = ContextTask

        # 将 celery 实例添加到 app.extensions
        if not hasattr(app, 'extensions'):
            app.extensions = {}
        app.extensions['celery'] = self
    
    def get_client(self):
        """返回 celery 客户端"""
        return self.celery

# 创建 Celery 扩展实例
celery_ext = CeleryExtension()