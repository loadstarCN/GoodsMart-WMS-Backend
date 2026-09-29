import json,os,uuid
from datetime import datetime
from flask import request,g,current_app
from flask_jwt_extended import verify_jwt_in_request, current_user,get_jwt_identity
from extensions import db
from .models import ActivityLog

# 键名包含这些片段即视为敏感（大小写不敏感，递归处理嵌套 dict / list）
_SENSITIVE_KEY_PARTS = ('password', 'token', 'secret', 'api_key', 'apikey', 'authorization', 'credential')
# 精确匹配的敏感键
_SENSITIVE_KEY_EXACT = {'key', 'code', 'otp'}

# 这些端点的请求 / 响应体整体不入库（登录凭证、密钥、SMTP 配置等）
_REDACT_BODY_ENDPOINTS = (
    '/user/login', '/user/refresh', '/user/logout',
    '/user/forgot-password', '/user/reset-password', '/user/change-password',
    '/settings/smtp', '/third-party/api-keys',
)
_REDACTED = '[redacted]'


def _is_sensitive_key(key) -> bool:
    k = str(key).lower()
    return k in _SENSITIVE_KEY_EXACT or any(part in k for part in _SENSITIVE_KEY_PARTS)


def _mask_obj(obj):
    if isinstance(obj, dict):
        return {k: ('***' if _is_sensitive_key(k) else _mask_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_obj(item) for item in obj]
    return obj


def _mask_sensitive(data_str):
    """将 JSON 字符串中的敏感字段值替换为 '***'（递归、模糊匹配键名）"""
    try:
        return json.dumps(_mask_obj(json.loads(data_str)), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return data_str


def _should_redact_body(path: str) -> bool:
    return any(marker in path for marker in _REDACT_BODY_ENDPOINTS)


def _capture_request_body() -> str:
    if request.is_json:
        payload = request.get_json(silent=True)
        if payload is not None:
            return json.dumps(_mask_obj(payload), ensure_ascii=False)
    # 非 JSON（表单 / 文件上传 / 畸形 JSON）：只记录文本形式，解码失败的字节用替换符
    try:
        return request.get_data(as_text=True)
    except Exception:
        return _REDACTED


def _capture_response_body(response) -> str:
    try:
        if response.is_json:
            payload = response.get_json(silent=True)
            if payload is not None:
                return json.dumps(_mask_obj(payload), ensure_ascii=False)
        if response.direct_passthrough:
            return '[stream]'
        return response.get_data(as_text=True)
    except Exception:
        return _REDACTED

def save_large_data(data):
    """如果数据超过指定大小，则保存到文件"""
    filename = str(uuid.uuid4()) + ".log"
    filepath = os.path.join(current_app.config['LOG_DIRECTORY'], filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(data)
    return filepath

def before_request_logging():
    """在每个请求前记录基础信息，仅限 POST、PUT、DELETE"""
    if request.method not in ['POST', 'PUT', 'DELETE']:
        return  # 不记录非目标方法的请求

    g.start_time = datetime.now()  # 记录请求开始时间
    g.actor = "Unknown"  # 默认操作人员

    # 用户角色验证
    try:
        # 用户认证
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        g.actor = f"User:{user_id}"  # 设置当前用户为操作人员
    except Exception:
        # 检查第三方系统上下文
        if hasattr(g, "current_system") and g.current_system:
            api_key = g.current_system.get("api_key")
            if api_key:
                g.actor = f"System:{api_key.system_name}"
        # 未认证
        else:
            g.actor = "Unknown"


def after_request_logging(response):

    """在每个请求后记录访问日志，仅限 POST、PUT、DELETE"""
    if request.method not in ['POST', 'PUT', 'DELETE']:
        return response  # 不记录非目标方法的请求

    start_time = g.get('start_time') or datetime.now()
    processing_time_ms = (datetime.now() - start_time).total_seconds() * 1000  # 保留小数部分

    # 请求 / 响应体：敏感端点整体不记录；其余递归脱敏。任何一步失败都不影响主请求，
    # 也不能让这条操作在审计里消失，所以退化为记录 [redacted]。
    if _should_redact_body(request.path):
        request_data = response_data = _REDACTED
    else:
        request_data = _capture_request_body()
        response_data = _capture_response_body(response)

    try:
        if len(request_data) > current_app.config['MAX_LOG_SIZE']:
            request_data = save_large_data(request_data)
        if len(response_data) > current_app.config['MAX_LOG_SIZE']:
            response_data = save_large_data(response_data)

        # 创建日志记录
        log = ActivityLog(
            actor=g.get('actor', 'Unknown'),
            endpoint=request.path,
            method=request.method,
            ip_address=request.remote_addr,
            request_data=request_data,
            response_data=response_data,
            status_code=response.status_code,
            request_content_type=request.content_type,
            response_content_type=response.content_type,
            processing_time=processing_time_ms,
        )

        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error logging activity: {str(e)}")

    return response

