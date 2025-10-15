from celery import shared_task
from time import sleep

@shared_task
def example_task(duration):
    """示例异步任务：模拟耗时操作"""
    sleep(duration)
    return f"Task completed after {duration} seconds"
