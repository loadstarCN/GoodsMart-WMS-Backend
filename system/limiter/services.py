from extensions.db import *
from extensions.transaction import transactional
from .models import IPBlacklist, IPWhitelist
from datetime import datetime

class LimiterService:

    @staticmethod
    def list_blacklist(filters: dict):
        """
        根据过滤条件返回 Blacklist 查询对象
        """
        query = IPBlacklist.query.order_by(IPBlacklist.id.desc())

        if filters.get('ip_address'):
            query = query.filter(IPBlacklist.ip_address.ilike(f"%{filters['ip_address']}%"))

        return query

    @staticmethod
    def get_blacklist_item(blacklist_id: int) -> IPBlacklist:
        """
        根据 ID 获取单个黑名单记录，不存在时抛出 404
        """
        blacklist_item = get_object_or_404(IPBlacklist, blacklist_id)
        return blacklist_item

    @staticmethod
    @transactional
    def create_blacklist(data: dict) -> IPBlacklist:
        """
        创建一个新的黑名单条目
        """
        ip_address = data['ip_address']
        reason = data.get('reason')

        new_blacklist_item = IPBlacklist(ip_address=ip_address, reason=reason, timestamp=datetime.now())
        db.session.add(new_blacklist_item)
        # db.session.commit()

        return new_blacklist_item

    @staticmethod
    @transactional
    def delete_blacklist_item(blacklist_id: int):
        """
        删除黑名单条目
        """
        blacklist_item = LimiterService.get_blacklist_item(blacklist_id)
        db.session.delete(blacklist_item)
        # db.session.commit()

    @staticmethod
    def list_whitelist(filters: dict):
        """
        根据过滤条件返回 Whitelist 查询对象
        """
        query = IPWhitelist.query.order_by(IPWhitelist.id.desc())

        if filters.get('ip_address'):
            query = query.filter(IPWhitelist.ip_address.ilike(f"%{filters['ip_address']}%"))

        return query

    @staticmethod
    def get_whitelist_item(whitelist_id: int) -> IPWhitelist:
        """
        根据 ID 获取单个白名单记录，不存在时抛出 404
        """
        whitelist_item = get_object_or_404(IPWhitelist, whitelist_id)
        return whitelist_item

    @staticmethod
    @transactional
    def create_whitelist(data: dict) -> IPWhitelist:
        """
        创建一个新的白名单条目
        """
        ip_address = data['ip_address']
        reason = data.get('reason')

        new_whitelist_item = IPWhitelist(ip_address=ip_address, reason=reason, timestamp=datetime.now())
        db.session.add(new_whitelist_item)
        # db.session.commit()

        return new_whitelist_item

    @staticmethod
    @transactional
    def delete_whitelist_item(whitelist_id: int):
        """
        删除白名单条目
        """
        whitelist_item = LimiterService.get_whitelist_item(whitelist_id)
        db.session.delete(whitelist_item)
        # db.session.commit()
