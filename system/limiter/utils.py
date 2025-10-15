from flask import request,current_app
from flask_restx import abort
from extensions import redis_client
from extensions.error import ForbiddenException
from .models import IPBlacklist, IPWhitelist

def initialize_ip_lists():   
    # 从数据库加载白名单和黑名单到 Redis
    load_whitelist_from_db()
    load_blacklist_from_db()

def load_whitelist_from_db():
    # 先清空 Redis 中的 whitelist 集合
    # 判断是否有该集合
    if redis_client.exists('whitelist'):
        redis_client.delete('whitelist')
    whitelisted_ips = IPWhitelist.query.all()
    for ip in whitelisted_ips:
        redis_client.sadd('whitelist', ip.ip_address)

def load_blacklist_from_db():
    # 先清空 Redis 中的 blacklist 集合
    if redis_client.exists('blacklist'):
        redis_client.delete('blacklist')
    blacklisted_ips = IPBlacklist.query.all()
    for ip in blacklisted_ips:
        redis_client.sadd('blacklist', ip.ip_address)

def check_ip():
    ip = request.remote_addr
    
    # 检查黑名单
    if current_app.config['CHECK_BLACKLIST']:
        if redis_client.sismember('blacklist', ip):
            raise ForbiddenException("Your IP is blacklisted.", 12002)

    # 检查白名单
    if current_app.config['CHECK_WHITELIST']:
        if not redis_client.sismember('whitelist', ip):
            raise ForbiddenException("Your IP is not whitelisted.", 12003)
