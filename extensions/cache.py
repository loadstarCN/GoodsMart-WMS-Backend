# extensions/cache.py
from flask_caching import Cache

class CustomCache(Cache):
    def __init__(self, app=None):
        super().__init__(app)
        self._cache = None  # 初始化为 None

    def init_app(self, app):
        cache_config = {
            'CACHE_TYPE': app.config.get('CACHE_TYPE', 'redis'),
            'CACHE_REDIS_URL': app.config.get('REDIS_URL'),
            'CACHE_DEFAULT_TIMEOUT': app.config.get('CACHE_DEFAULT_TIMEOUT', 300)
        }
        super().init_app(app, config=cache_config)  # 调用父类的 init_app 方法
        self._cache = self.cache  # 将缓存对象存储在实例变量中

    def get_cache(self):
        return self._cache  # 返回缓存对象

cache = CustomCache()  # 使用自定义缓存类