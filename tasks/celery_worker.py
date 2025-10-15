import sys
# import os

# # 将项目根目录添加到 sys.path 中，确保可以找到 extensions 目录
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from celery import Celery
from config import Config  # 从 config 中导入配置
# from tasks.example import example_task  # 确保导入任务模块

# 创建 Celery 实例并手动加载配置
def create_celery():
    # 创建 Celery 实例
    celery = Celery(
        'tasks',  # 给 Celery 一个名字（通常是模块名）
        broker=Config.CELERY_BROKER_URL,  # 配置消息代理（Broker）
        backend=Config.CELERY_RESULT_BACKEND,  # 配置结果后端（Backend）
    )

    # 手动加载配置
    celery.conf.update({
        'accept_content': ['json'],  # 指定接受的内容类型
        'task_serializer': 'json',  # 指定任务序列化格式
        'timezone': 'UTC',  # 设置时区
        'worker_concurrency': 1,  # 设置并发为 1，避免使用多进程
        'task_acks_late': True,  # 确保任务被确认
    })

    # 单进程模式，避免使用多进程
    if sys.platform == 'win32':
        celery.conf.update({
            'pool': 'solo',  # Windows 系统使用 solo 模式
        })

    # 确保任务被注册
    celery.autodiscover_tasks(['tasks'])

    return celery

# 调用 create_celery 配置并返回 celery 实例
celery = create_celery()

# 如果此脚本被直接执行，则输出 Celery 配置信息
if __name__ == "__main__":
    print("Celery worker is ready to be run through command line.")
