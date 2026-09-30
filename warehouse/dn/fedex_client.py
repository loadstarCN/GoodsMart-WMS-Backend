"""FedEx REST API 客户端：OAuth、Ship API（建单 / 取消）、Trade Documents Upload API（ETD 上传）。

- 凭证与选项全部来自配置（FEDEX_*），API Key / Secret Key / 账号缺任一项 = 未启用。
- OAuth：POST {FEDEX_API_BASE}/oauth/token（client_credentials），token 进程内缓存到过期前 60 秒；
  业务请求遇 401（token 被提前作废）换一次 token 重试。
- 所有失败抛 FedexError：HTTP 状态码、FedEx 回的 errors[]（code / message）、transactionId；
  超时 / 连接失败另有标记。异常与日志里不带任何凭证。
- 结果是否确定（maybe_processed）：业务请求读超时、连接建立后中断、5xx、200 但不是 JSON → 可能已被 FedEx 处理
  （例如运单已建），调用方要按「结果不明」处理；连接没建立（连接超时 / DNS / 拒绝连接 / 证书校验失败）、4xx、
  token 请求失败、时间预算不够没发出去 → 确定没处理。
- 时间预算：业务函数可带 deadline（clock() 的时刻）。每次请求的连接 / 读取超时按剩余时间收缩，
  剩余不足 MIN_REQUEST_SECONDS 就不再发请求（FedexError.budget_exhausted），避免整个流程超过 worker 超时被杀。
"""
import json
import logging
import ssl
import threading
import time

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# FedEx 接口路径（developer.fedex.com：Ship API v1 / Trade Documents Upload API v1）
OAUTH_PATH = '/oauth/token'
SHIP_PATH = '/ship/v1/shipments'
CANCEL_PATH = '/ship/v1/shipments/cancel'
ETD_UPLOAD_PATH = '/documents/v1/etds/upload'

# Trade Documents Upload API 与 Ship API 不在同一个域名；FEDEX_DOCUMENT_API_BASE 不设时按 FEDEX_API_BASE 选
SANDBOX_DOCUMENT_API_BASE = 'https://documentapitest.prod.fedex.com/sandbox'
PRODUCTION_DOCUMENT_API_BASE = 'https://documentapi.prod.fedex.com'

# FedexError.what
TOKEN_REQUEST = 'OAuth token request'
CREATE_SHIPMENT = 'create shipment'
CANCEL_SHIPMENT = 'cancel shipment'
ETD_UPLOAD = 'trade document upload'

DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_READ_TIMEOUT = 30
TOKEN_REFRESH_MARGIN_SECONDS = 60
# 时间预算剩余不足这么多秒就不再发下一个请求（发了多半读超时，结果反而不明）
MIN_REQUEST_SECONDS = 5


def _clock() -> float:
    """单调时钟（测试里替换它来模拟时间流逝）"""
    return time.monotonic()


def clock() -> float:
    """当前时刻（deadline 用这个时钟算）"""
    return _clock()


class FedexError(Exception):
    """FedEx 接口调用失败。

    Attributes:
        status_code: HTTP 状态码（超时 / 连接失败为 None）
        errors: FedEx 回的 errors[]，只保留 code / message
        transaction_id: FedEx 的 transactionId（有的话）
        timeout: 请求超时（连接或读取），或时间预算不够没发出去
        maybe_processed: 结果不明 —— 请求可能已送达并被 FedEx 处理（例如已经建了运单）：
            读超时、连接建立后中断、5xx、200 但响应不是 JSON
        budget_exhausted: 时间预算不够，请求没有发出去
        what: 出错的是哪个请求（'OAuth token request' / 'create shipment' / 'cancel shipment' / ……）
    """

    def __init__(self, message, *, status_code=None, errors=None, transaction_id=None,
                 timeout=False, maybe_processed=False, budget_exhausted=False, what=None):
        super().__init__(message)
        self.message = message
        self.what = what
        self.status_code = status_code
        self.errors = errors or []
        self.transaction_id = transaction_id
        self.timeout = timeout
        self.maybe_processed = maybe_processed
        self.budget_exhausted = budget_exhausted

    @property
    def permission_denied(self) -> bool:
        """401 / 403：凭证不对，或 FedEx 项目没开通这个 API（如 Ship API）"""
        return self.status_code in (401, 403)


def _cfg(key, default=None):
    value = current_app.config.get(key)
    return default if value is None else value


def fedex_configured() -> bool:
    return bool(_cfg('FEDEX_API_KEY') and _cfg('FEDEX_SECRET_KEY') and _cfg('FEDEX_ACCOUNT_NUMBER'))


def api_base() -> str:
    return str(_cfg('FEDEX_API_BASE') or 'https://apis-sandbox.fedex.com').rstrip('/')


def is_sandbox() -> bool:
    return 'sandbox' in api_base().lower()


def document_api_base() -> str:
    base = _cfg('FEDEX_DOCUMENT_API_BASE')
    if base:
        return str(base).rstrip('/')
    return SANDBOX_DOCUMENT_API_BASE if is_sandbox() else PRODUCTION_DOCUMENT_API_BASE


def _timeout(deadline=None, what='request'):
    """(连接超时, 读取超时)。带 deadline 时按剩余时间收缩（两者之和不超过剩余时间）；
    剩余不足 MIN_REQUEST_SECONDS → FedexError(budget_exhausted)，请求不发。"""
    connect = float(_cfg('FEDEX_CONNECT_TIMEOUT_SECONDS', DEFAULT_CONNECT_TIMEOUT) or DEFAULT_CONNECT_TIMEOUT)
    read = float(_cfg('FEDEX_TIMEOUT_SECONDS', DEFAULT_READ_TIMEOUT) or DEFAULT_READ_TIMEOUT)
    if deadline is None:
        return (connect, read)
    remaining = deadline - _clock()
    if remaining < MIN_REQUEST_SECONDS:
        raise FedexError(f"FedEx {what} not sent: time budget exhausted ({max(remaining, 0):.0f}s left)",
                         timeout=True, budget_exhausted=True, what=what)
    connect = min(connect, remaining / 2)
    return (connect, min(read, remaining - connect))


def _connection_not_established(exc) -> bool:
    """连接没建立起来（DNS 失败 / 拒绝连接 / 证书校验失败）→ 请求肯定没发出去。
    其它 ConnectionError（连接中途被断开等）请求可能已送达，按结果不明处理。"""
    from urllib3.exceptions import NewConnectionError   # NameResolutionError 是它的子类
    seen, stack = set(), [exc]
    while stack:
        item = stack.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, (NewConnectionError, ssl.SSLCertVerificationError)):
            return True
        stack.extend([getattr(item, 'reason', None), item.__cause__, item.__context__])
        stack.extend(arg for arg in getattr(item, 'args', ()) if isinstance(arg, BaseException))
    return False


def _send(method, url, **kwargs):
    """发 HTTP 请求（测试里整体替换这个函数，不联网）"""
    return requests.request(method, url, **kwargs)


def _json_or_none(resp):
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_from_response(resp, what) -> FedexError:
    body = _json_or_none(resp) or {}
    errors = []
    for item in body.get('errors') or []:
        if isinstance(item, dict):
            errors.append({'code': item.get('code'), 'message': item.get('message')})
    transaction_id = body.get('transactionId')
    if errors:
        text = '; '.join(f"{e['code']}: {e['message']}" for e in errors)
    else:
        text = (getattr(resp, 'text', '') or '')[:300] or 'no error details'
    return FedexError(f"FedEx {what} failed (HTTP {resp.status_code}): {text}",
                      status_code=resp.status_code, errors=errors, transaction_id=transaction_id, what=what)


# ------------------------------------------------------------------
# OAuth token 缓存（按 API 地址 + API Key 分开缓存，换配置不会串用）
# ------------------------------------------------------------------
_token_cache = {}
_token_lock = threading.Lock()


def clear_token_cache():
    with _token_lock:
        _token_cache.clear()


def _cache_key():
    return (api_base(), _cfg('FEDEX_API_KEY'))


def _cached_token():
    with _token_lock:
        entry = _token_cache.get(_cache_key())
        if entry and time.time() < entry[1] - TOKEN_REFRESH_MARGIN_SECONDS:
            return entry[0]
    return None


def _invalidate_token(token):
    with _token_lock:
        key = _cache_key()
        entry = _token_cache.get(key)
        if entry and entry[0] == token:
            _token_cache.pop(key, None)


def _request_token(deadline=None):
    timeout = _timeout(deadline, TOKEN_REQUEST)
    try:
        resp = _send(
            'POST', f"{api_base()}{OAUTH_PATH}",
            data={
                'grant_type': 'client_credentials',
                'client_id': _cfg('FEDEX_API_KEY'),
                'client_secret': _cfg('FEDEX_SECRET_KEY'),
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise FedexError("FedEx OAuth token request timed out", timeout=True, what=TOKEN_REQUEST) from exc
    except requests.RequestException as exc:
        raise FedexError(f"FedEx OAuth token request failed: {type(exc).__name__}", what=TOKEN_REQUEST) from exc
    if resp.status_code != 200:
        raise _error_from_response(resp, TOKEN_REQUEST)
    body = _json_or_none(resp) or {}
    token = body.get('access_token')
    if not token or not isinstance(token, str):
        raise FedexError("FedEx OAuth token response has no access_token", status_code=resp.status_code,
                         what=TOKEN_REQUEST)
    try:
        expires_in = int(body.get('expires_in') or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    with _token_lock:
        _token_cache[_cache_key()] = (token, time.time() + expires_in)
    return token


def access_token(force_refresh=False, deadline=None):
    if not force_refresh:
        token = _cached_token()
        if token:
            return token
    return _request_token(deadline)


# ------------------------------------------------------------------
# 业务请求
# ------------------------------------------------------------------

def _call(method, url, what, *, json_body=None, files=None, data=None, ok_statuses=(200,), deadline=None):
    """带 Bearer token 的请求；401 换一次 token 重试。返回响应 JSON（dict）。
    结果不明（maybe_processed）的情形见模块说明；token 请求本身的失败一律是确定失败。"""
    if not fedex_configured():
        raise FedexError("FedEx is not configured", what=what)
    resp = None
    for attempt in range(2):
        token = access_token(force_refresh=attempt > 0, deadline=deadline)
        headers = {'Authorization': f'Bearer {token}', 'X-locale': 'en_US'}
        if json_body is not None:
            headers['Content-Type'] = 'application/json'
        timeout = _timeout(deadline, what)
        try:
            resp = _send(method, url, json=json_body, files=files, data=data, headers=headers, timeout=timeout)
        except requests.ConnectTimeout as exc:
            raise FedexError(f"FedEx {what} timed out (connect)", timeout=True, what=what) from exc
        except requests.Timeout as exc:
            raise FedexError(f"FedEx {what} timed out", timeout=True, maybe_processed=True, what=what) from exc
        except requests.ConnectionError as exc:
            if _connection_not_established(exc):
                raise FedexError(f"FedEx {what} could not connect: {type(exc).__name__}", what=what) from exc
            raise FedexError(f"FedEx {what} connection lost: {type(exc).__name__}", maybe_processed=True,
                             what=what) from exc
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError) as exc:
            # 响应读到一半出错：请求已经被处理了
            raise FedexError(f"FedEx {what} response broken: {type(exc).__name__}", maybe_processed=True,
                             what=what) from exc
        except requests.RequestException as exc:
            raise FedexError(f"FedEx {what} request failed: {type(exc).__name__}", what=what) from exc
        if resp.status_code != 401:
            break
        _invalidate_token(token)
    if resp.status_code not in ok_statuses:
        error = _error_from_response(resp, what)
        # 5xx：FedEx 那边出错，请求可能已经处理了（例如运单已建）；4xx 是明确拒绝
        error.maybe_processed = resp.status_code >= 500
        raise error
    body = _json_or_none(resp)
    if body is None:
        raise FedexError(f"FedEx {what} returned a non-JSON response (HTTP {resp.status_code})",
                         status_code=resp.status_code, maybe_processed=True, what=what)
    return body


def create_shipment(payload: dict, deadline=None) -> dict:
    """POST /ship/v1/shipments"""
    return _call('POST', f"{api_base()}{SHIP_PATH}", CREATE_SHIPMENT, json_body=payload, deadline=deadline)


def cancel_shipment(tracking_number: str, sender_country_code: str, deadline=None) -> dict:
    """PUT /ship/v1/shipments/cancel（整票取消）"""
    payload = {
        'accountNumber': {'value': _cfg('FEDEX_ACCOUNT_NUMBER')},
        'senderCountryCode': sender_country_code,
        'deletionControl': 'DELETE_ALL_PACKAGES',
        'trackingNumber': tracking_number,
    }
    return _call('PUT', f"{api_base()}{CANCEL_PATH}", CANCEL_SHIPMENT, json_body=payload, deadline=deadline)


def upload_etd_document(content: bytes, file_name: str, origin_country: str, destination_country: str,
                        doc_type: str = 'COMMERCIAL_INVOICE', deadline=None) -> dict:
    """Trade Documents Upload API（建单前上传，workflowName = ETDPreshipment）。

    multipart/form-data：document（JSON 字符串）+ attachment（文件）。成功 201，
    output.meta.docId 在建单时通过 etdDetail.attachedDocuments[].documentId 引用。
    """
    document = {
        'workflowName': 'ETDPreshipment',
        'carrierCode': 'FDXE',
        'name': file_name,
        'contentType': 'application/pdf',
        'meta': {
            'shipDocumentType': doc_type,
            'originCountryCode': origin_country,
            'destinationCountryCode': destination_country,
        },
    }
    return _call(
        'POST', f"{document_api_base()}{ETD_UPLOAD_PATH}", ETD_UPLOAD,
        data={'document': json.dumps(document)},
        files={'attachment': (file_name, content, 'application/pdf')},
        ok_statuses=(200, 201),
        deadline=deadline,
    )
