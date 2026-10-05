"""Webhook 工具：回调 URL 校验（防 SSRF）与建连时固定已校验地址的发送通道"""
import ipaddress
import socket
import sys
from socket import timeout as SocketTimeout
from urllib.parse import urlparse

import requests
from flask import current_app
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.util import connection as _u3_connection

from extensions.error import BadRequestException


def _is_public_ip(ip_str: str) -> bool:
    """只有全局可路由的单播地址算公网。

    is_global 排除 RFC 1918、环回、链路本地、100.64.0.0/10 共享地址（含阿里云元数据 100.100.100.200）、
    保留 / 文档 / 基准测试段等；组播另外排除（老版本 Python 的 is_global 不排除 224.0.0.0/4）。
    IPv6 里内嵌的 IPv4（::ffff:a.b.c.d、6to4 2002::/16）按内嵌地址再判一次（不依赖 Python 版本）。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None and not _is_public_ip(str(embedded)):
            return False
    if not ip.is_global:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_webhook_url(url, strict=None):
    """校验 webhook_url。返回规范化后的 URL；空值返回 None。

    strict（默认取配置 WEBHOOK_URL_STRICT，生产环境应为 True）：
    - 只允许 https
    - 不允许 URL 内嵌用户名密码
    - 主机解析出的所有地址必须是公网地址（拒绝内网 / 环回 / 链路本地 / 共享地址 / 元数据服务）
    这里的解析只是提前拒绝；真正发送时 strict_webhook_session() 在建连时还会再解析、校验并连到校验过的地址。
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


# ---------------------------------------------------------------------------
# 发送通道：建连时解析一次、校验全部地址、只连校验过的地址（防 DNS rebinding）
# ---------------------------------------------------------------------------

class _PinnedAddressMixin:
    """替换 urllib3 连接的 _new_conn：

    1. 解析主机名，得到的地址里只要有一个不是公网地址就拒绝（不挑一个能用的连）；
    2. 依次连这些已校验的 IP（不再按主机名解析，校验与建连之间没有第二次 DNS 查询）；
    3. 连上后再核对 socket 对端地址（纵深防御）。
    self.host 不变，HTTPS 的 SNI 与证书主机名校验仍按原域名做，证书校验不受影响。
    """

    def _new_conn(self):
        host = self._dns_host
        try:
            infos = socket.getaddrinfo(host, self.port, _u3_connection.allowed_gai_family(), socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise NameResolutionError(self.host, self, e) from e

        addresses = []
        for info in infos:
            ip = str(info[4][0])
            if not _is_public_ip(ip):
                raise NewConnectionError(
                    self, f"webhook host {self.host} resolves to a non-public address {ip}; refused")
            if ip not in addresses:
                addresses.append(ip)
        if not addresses:
            raise NewConnectionError(self, f"webhook host {self.host} did not resolve to any address")

        last_error = None
        for ip in addresses:
            try:
                sock = _u3_connection.create_connection(
                    (ip, self.port),
                    self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            except SocketTimeout as e:
                last_error = ConnectTimeoutError(
                    self, f"Connection to {self.host} timed out. (connect timeout={self.timeout})")
                last_error.__cause__ = e
                continue
            except OSError as e:
                last_error = NewConnectionError(self, f"Failed to establish a new connection: {e}")
                last_error.__cause__ = e
                continue

            try:
                peer = str(sock.getpeername()[0])
            except OSError as e:
                sock.close()
                last_error = NewConnectionError(self, f"Failed to establish a new connection: {e}")
                continue
            if not _is_public_ip(peer):
                sock.close()
                raise NewConnectionError(
                    self, f"webhook connection to {self.host} reached a non-public address {peer}; refused")

            sys.audit("http.client.connect", self, self.host, self.port)
            return sock

        raise last_error


class _PinnedHTTPConnection(_PinnedAddressMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedAddressMixin, HTTPSConnection):
    pass


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class _PinnedAddressAdapter(HTTPAdapter):
    """连接池换成上面的连接类；其余（证书校验、超时、不重试）与 requests 默认一致"""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            'http': _PinnedHTTPConnectionPool,
            'https': _PinnedHTTPSConnectionPool,
        }


def strict_webhook_session() -> requests.Session:
    """严格模式（WEBHOOK_URL_STRICT）下发送 webhook 用的会话（用完由调用方关闭）。

    trust_env=False：不走环境变量里的代理（经代理时建连的是代理，没法校验真实目标地址），
    也不读 .netrc（避免把本机凭证带给接收方）；证书校验用 requests 默认的 certifi 根证书。
    """
    session = requests.Session()
    session.trust_env = False
    adapter = _PinnedAddressAdapter()
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session
