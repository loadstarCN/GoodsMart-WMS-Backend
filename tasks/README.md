## 使用说明

### 异步任务执行
示例异步任务代码见 example.py 文件，任务函数应使用 @shared_task 装饰器进行标注。

系统将自动扫描并导入 tasks 目录下所有被 @task 装饰器修饰的函数。具体路径配置可参考 /config.py 文件。

任务管理接口定义在 views.py 文件中。

celery_worker.py 文件为独立运行的 Celery 示例脚本。

## Celery Worker启动说明
### 启动方式
#### 使用 Flask 上下文启动（推荐）
在项目根目录下执行以下命令启动 Celery Worker，确保使用 Flask 上下文：

#### Linux 系统
```
celery -A app.celery worker --loglevel=info
```

#### Windows 系统
由于 Windows 系统默认不支持 Gevent 或 Eventlet，启动时需要安装 Eventlet：
```
pip install eventlet
celery -A app.celery worker --loglevel=info  -P eventlet
```

### 独立启动（不使用 Flask 上下文）
在无需 Flask 上下文的情况下，可以独立运行 Celery Worker。请参考 Celery_worker.py 文件中的示例代码。

#### Linux 系统
```
celery -A tasks.celery_worker.celery worker --loglevel=info
```

#### Windows 系统
同样，Windows 系统下也需要安装 Eventlet：
```
pip install eventlet
celery -A tasks.celery_worker.celery worker --loglevel=info  -P eventlet
```

### Flower监控
启动命令
```
celery --broker=redis://r-uf6sv22urfi9l856kt:k5RRs35VrLx9wucLfx4J@r-uf6sv22urfi9l856ktpd.redis.rds.aliyuncs.com:6379/10 flower --basic-auth=loop:Loop@2024 --port=5555
```