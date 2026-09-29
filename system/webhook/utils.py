"""Webhook 工具：回调 URL 校验（防 SSRF）"""
import ipaddress
import socket
from urllib.parse import urlparse

from flask import current_app

from extensions.error import BadRequestException


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_webhook_url(url, strict=None):
    """校验 webhook_url。返回规范化后的 URL；空值返回 None。

    strict（默认取配置 WEBHOOK_URL_STRICT，生产环境应为 True）：
    - 只允许 https
    - 不允许 URL 内嵌用户名密码
    - 主机解析出的所有地址必须是公网地址（拒绝内网 / 环回 / 链路本地 / 元数据服务）
    """
    if url is None:
        return None
    url = str(url).strip()
    if not url:
        return None

    if strict is None:
        strict = bool(current_app.config.get('WEBHOOK_URL_STRICT', True))

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise BadRequestException("webhook_url must be a valid http(s) URL", 14011)
    if parsed.username or parsed.password:
        raise BadRequestException("webhook_url must not contain credentials", 14011)

    if not strict:
        return url

    if parsed.scheme != 'https':
        raise BadRequestException("webhook_url must use https", 14011)

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BadRequestException("webhook_url host cannot be resolved", 14012)

    for info in infos:
        if not _is_public_ip(str(info[4][0])):
            raise BadRequestException("webhook_url must not point to a private or local address", 14013)

    return url
