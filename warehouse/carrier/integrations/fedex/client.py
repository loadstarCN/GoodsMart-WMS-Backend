"""FedEx REST API 客户端：OAuth、Ship API（建单 / 取消）、Trade Documents Upload API（ETD 上传）、
Pickup Request API（查可约时间 / 预约揽收 / 取消揽收）。

- 凭证由调用方传入（FedexClient(api_key=…, …)，来自承运商记录的对接设置），不读全局配置；异常与日志里不带任何凭证。
- OAuth：POST {api_base}/oauth/token（client_credentials），token 进程内按「API 地址 + API Key」缓存到过期前 60 秒；
  业务请求遇 401（token 被提前作废）换一次 token 重试。
- 所有失败抛 CarrierError：HTTP 状态码、FedEx 回的 errors[]（code / message）、transactionId；超时 / 连接失败另有标记。
- 结果是否确定（maybe_processed）：业务请求读超时、连接建立后中断、5xx、200 但不是 JSON → 可能已被 FedEx 处理
  （例如运单已建），调用方要按「结果不明」处理；连接没建立（连接超时 / DNS / 拒绝连接 / 证书校验失败）、4xx、
  token 请求失败、时间预算不够没发出去 → 确定没处理。
- 时间预算：业务方法可带 deadline（base.clock() 的时刻）。每次请求的连接 / 读取超时按剩余时间收缩，
  剩余不足 base.MIN_REQUEST_SECONDS 就不再发请求（CarrierError.budget_exhausted），避免整个流程超过 worker 超时被杀。
"""
import json
import logging
import ssl
import threading
import time

import requests

from ..base import (
    STEP_AUTH, STEP_CANCEL, STEP_CREATE, STEP_DOCUMENTS, STEP_PICKUP, STEP_PICKUP_AVAILABILITY, STEP_PICKUP_CANCEL,
    CarrierError, request_timeout,
)

logger = logging.getLogger(__name__)

# FedEx 接口路径（developer.fedex.com：Ship API v1 / Trade Documents Upload API v1 / Pickup Request API v1）
OAUTH_PATH = '/oauth/token'
SHIP_PATH = '/ship/v1/shipments'
CANCEL_PATH = '/ship/v1/shipments/cancel'
ETD_UPLOAD_PATH = '/documents/v1/etds/upload'
PICKUP_PATH = '/pickup/v1/pickups'
PICKUP_CANCEL_PATH = '/pickup/v1/pickups/cancel'
PICKUP_AVAILABILITY_PATH = '/pickup/v1/pickups/availabilities'

# 请求名（写进错误信息）→ CarrierError.step
TOKEN_REQUEST = 'OAuth token request'
CREATE_SHIPMENT = 'create shipment'
CANCEL_SHIPMENT = 'cancel shipment'
ETD_UPLOAD = 'trade document upload'
CREATE_PICKUP = 'create pickup'
CANCEL_PICKUP = 'cancel pickup'
PICKUP_AVAILABILITY = 'pickup availability'
_STEPS = {TOKEN_REQUEST: STEP_AUTH, CREATE_SHIPMENT: STEP_CREATE, CANCEL_SHIPMENT: STEP_CANCEL,
          ETD_UPLOAD: STEP_DOCUMENTS, CREATE_PICKUP: STEP_PICKUP, CANCEL_PICKUP: STEP_PICKUP_CANCEL,
          PICKUP_AVAILABILITY: STEP_PICKUP_AVAILABILITY}

TOKEN_REFRESH_MARGIN_SECONDS = 60


def _error(message, what, **kwargs) -> CarrierError:
    return CarrierError(message, step=_STEPS.get(what), **kwargs)


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


def _error_from_response(resp, what) -> CarrierError:
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
    return _error(f"FedEx {what} failed (HTTP {resp.status_code}): {text}", what,
                  status_code=resp.status_code, errors=errors, transaction_id=transaction_id)


# ------------------------------------------------------------------
# OAuth token 缓存（按 API 地址 + API Key 分开缓存，多个账号 / 换配置不会串用）
# ------------------------------------------------------------------
_token_cache = {}
_token_lock = threading.Lock()


def clear_token_cache():
    with _token_lock:
        _token_cache.clear()


class FedexClient:

    def __init__(self, *, api_base, document_api_base, api_key, secret_key, account_number):
        self.api_base = str(api_base).rstrip('/')
        self.document_api_base = str(document_api_base).rstrip('/')
        self.api_key = api_key
        self.secret_key = secret_key
        self.account_number = account_number

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key and self.account_number)

    def _timeout(self, deadline, what):
        return request_timeout(deadline, carrier='FedEx', what=what, step=_STEPS.get(what))

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _cache_key(self):
        return (self.api_base, self.api_key)

    def _cached_token(self):
        with _token_lock:
            entry = _token_cache.get(self._cache_key())
            if entry and time.time() < entry[1] - TOKEN_REFRESH_MARGIN_SECONDS:
                return entry[0]
        return None

    def _invalidate_token(self, token):
        with _token_lock:
            key = self._cache_key()
            entry = _token_cache.get(key)
            if entry and entry[0] == token:
                _token_cache.pop(key, None)

    def _request_token(self, deadline=None):
        timeout = self._timeout(deadline, TOKEN_REQUEST)
        try:
            resp = _send(
                'POST', f"{self.api_base}{OAUTH_PATH}",
                data={
                    'grant_type': 'client_credentials',
                    'client_id': self.api_key,
                    'client_secret': self.secret_key,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise _error("FedEx OAuth token request timed out", TOKEN_REQUEST, timeout=True) from exc
        except requests.RequestException as exc:
            raise _error(f"FedEx OAuth token request failed: {type(exc).__name__}", TOKEN_REQUEST) from exc
        if resp.status_code != 200:
            raise _error_from_response(resp, TOKEN_REQUEST)
        body = _json_or_none(resp) or {}
        token = body.get('access_token')
        if not token or not isinstance(token, str):
            raise _error("FedEx OAuth token response has no access_token", TOKEN_REQUEST,
                         status_code=resp.status_code)
        try:
            expires_in = int(body.get('expires_in') or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        with _token_lock:
            _token_cache[self._cache_key()] = (token, time.time() + expires_in)
        return token

    def access_token(self, force_refresh=False, deadline=None):
        if not self.configured:
            raise _error("FedEx is not configured (account number, API key and secret key are required)",
                         TOKEN_REQUEST)
        if not force_refresh:
            token = self._cached_token()
            if token:
                return token
        return self._request_token(deadline)

    # ------------------------------------------------------------------
    # 业务请求
    # ------------------------------------------------------------------

    def _call(self, method, url, what, *, json_body=None, files=None, data=None, ok_statuses=(200,),
              deadline=None):
        """带 Bearer token 的请求；401 换一次 token 重试。返回响应 JSON（dict）。
        结果不明（maybe_processed）的情形见模块说明；token 请求本身的失败一律是确定失败。"""
        resp = None
        for attempt in range(2):
            token = self.access_token(force_refresh=attempt > 0, deadline=deadline)
            headers = {'Authorization': f'Bearer {token}', 'X-locale': 'en_US'}
            if json_body is not None:
                headers['Content-Type'] = 'application/json'
            timeout = self._timeout(deadline, what)
            try:
                resp = _send(method, url, json=json_body, files=files, data=data, headers=headers, timeout=timeout)
            except requests.ConnectTimeout as exc:
                raise _error(f"FedEx {what} timed out (connect)", what, timeout=True) from exc
            except requests.Timeout as exc:
                raise _error(f"FedEx {what} timed out", what, timeout=True, maybe_processed=True) from exc
            except requests.ConnectionError as exc:
                if _connection_not_established(exc):
                    raise _error(f"FedEx {what} could not connect: {type(exc).__name__}", what) from exc
                raise _error(f"FedEx {what} connection lost: {type(exc).__name__}", what,
                             maybe_processed=True) from exc
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError) as exc:
                # 响应读到一半出错：请求已经被处理了
                raise _error(f"FedEx {what} response broken: {type(exc).__name__}", what,
                             maybe_processed=True) from exc
            except requests.RequestException as exc:
                raise _error(f"FedEx {what} request failed: {type(exc).__name__}", what) from exc
            if resp.status_code != 401:
                break
            self._invalidate_token(token)
        if resp.status_code not in ok_statuses:
            error = _error_from_response(resp, what)
            # 5xx：FedEx 那边出错，请求可能已经处理了（例如运单已建）；4xx 是明确拒绝
            error.maybe_processed = resp.status_code >= 500
            raise error
        body = _json_or_none(resp)
        if body is None:
            raise _error(f"FedEx {what} returned a non-JSON response (HTTP {resp.status_code})", what,
                         status_code=resp.status_code, maybe_processed=True)
        return body

    def create_shipment(self, payload: dict, deadline=None) -> dict:
        """POST /ship/v1/shipments"""
        return self._call('POST', f"{self.api_base}{SHIP_PATH}", CREATE_SHIPMENT, json_body=payload,
                          deadline=deadline)

    def cancel_shipment(self, tracking_number: str, sender_country_code: str, deadline=None) -> dict:
        """PUT /ship/v1/shipments/cancel（整票取消）"""
        payload = {
            'accountNumber': {'value': self.account_number},
            'senderCountryCode': sender_country_code,
            'deletionControl': 'DELETE_ALL_PACKAGES',
            'trackingNumber': tracking_number,
        }
        return self._call('PUT', f"{self.api_base}{CANCEL_PATH}", CANCEL_SHIPMENT, json_body=payload,
                          deadline=deadline)

    def upload_etd_document(self, content: bytes, file_name: str, origin_country: str, destination_country: str,
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
        return self._call(
            'POST', f"{self.document_api_base}{ETD_UPLOAD_PATH}", ETD_UPLOAD,
            data={'document': json.dumps(document)},
            files={'attachment': (file_name, content, 'application/pdf')},
            ok_statuses=(200, 201),
            deadline=deadline,
        )

    def pickup_availability(self, payload: dict, deadline=None) -> dict:
        """POST /pickup/v1/pickups/availabilities（只查询，不预约）"""
        return self._call('POST', f"{self.api_base}{PICKUP_AVAILABILITY_PATH}", PICKUP_AVAILABILITY,
                          json_body=payload, deadline=deadline)

    def create_pickup(self, payload: dict, deadline=None) -> dict:
        """POST /pickup/v1/pickups（预约快递员上门揽收）"""
        return self._call('POST', f"{self.api_base}{PICKUP_PATH}", CREATE_PICKUP, json_body=payload,
                          deadline=deadline)

    def cancel_pickup(self, payload: dict, deadline=None) -> dict:
        """PUT /pickup/v1/pickups/cancel"""
        return self._call('PUT', f"{self.api_base}{PICKUP_CANCEL_PATH}", CANCEL_PICKUP, json_body=payload,
                          deadline=deadline)
