from datetime import datetime
from typing import List
from extensions.db import *
from extensions.transaction import transactional
from .models import ActivityLog


class LogService:

    @staticmethod
    def list_logs(filters: dict) -> List[ActivityLog]:
        """
        根据过滤条件返回 ActivityLog 查询对象
        """
        query = ActivityLog.query.order_by(ActivityLog.created_at.desc())

        if filters.get('actor'):
            query = query.filter(ActivityLog.actor.ilike(f"%{filters['actor']}%"))
        if filters.get('endpoint'):
            query = query.filter(ActivityLog.endpoint.ilike(f"%{filters['endpoint']}%"))
        if filters.get('method'):
            query = query.filter(ActivityLog.method.ilike(f"%{filters['method']}%"))
        if filters.get('status_code') is not None:
            query = query.filter(ActivityLog.status_code == filters['status_code'])
        if filters.get('ip_address'):
            query = query.filter(ActivityLog.ip_address == filters['ip_address'])
        if filters.get('created_at'):
            query = query.filter(ActivityLog.created_at >= filters['created_at'])
        if filters.get('keyword'):
            keyword = f"%{filters['keyword']}%"
            query = query.filter(
                (ActivityLog.actor.ilike(keyword)) |
                (ActivityLog.endpoint.ilike(keyword)) |
                (ActivityLog.request_data.ilike(keyword)) |
                (ActivityLog.response_data.ilike(keyword))
            )

        return query

    @staticmethod
    def get_log(log_id: int) -> ActivityLog:
        """
        根据日志 ID 获取单个日志，不存在时抛出 404
        """
        log = get_object_or_404(ActivityLog, log_id)
        return log

    @staticmethod
    @transactional
    def create_log(data: dict) -> ActivityLog:
        """
        创建新的日志记录
        """
        log = ActivityLog(**data)
        db.session.add(log)
        # db.session.commit()
        return log

    @staticmethod
    @transactional
    def update_log(log_id: int, data: dict) -> ActivityLog:
        """
        更新日志记录
        """
        log = LogService.get_log(log_id)

        # 更新字段
        log.actor = data.get('actor', log.actor)
        log.endpoint = data.get('endpoint', log.endpoint)
        log.method = data.get('method', log.method)
        log.request_data = data.get('request_data', log.request_data)
        log.response_data = data.get('response_data', log.response_data)
        log.status_code = data.get('status_code', log.status_code)
        log.ip_address = data.get('ip_address', log.ip_address)
        log.request_content_type = data.get('request_content_type', log.request_content_type)
        log.response_content_type = data.get('response_content_type', log.response_content_type)
        log.processing_time = data.get('processing_time', log.processing_time)

        # db.session.commit()
        return log

    @staticmethod
    @transactional
    def delete_log(log_id: int):
        """
        删除日志记录
        """
        log = LogService.get_log(log_id)
        db.session.delete(log)
        # db.session.commit()
