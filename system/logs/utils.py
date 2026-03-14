import json,os,uuid
from datetime import datetime
from flask import request,g,current_app
from flask_jwt_extended import verify_jwt_in_request, current_user,get_jwt_identity
from extensions import db
from .models import ActivityLog

_SENSITIVE_FIELDS = {'password', 'old_password', 'new_password', 'token', 'secret', 'key'}

def _mask_sensitive(data_str):
    """将请求体 JSON 中的敏感字段值替换为 '***'"""
    try:
        obj = json.loads(data_str)
        if isinstance(obj, dict):
            for field in _SENSITIVE_FIELDS:
                if field in obj:
                    obj[field] = '***'
            return json.dumps(obj)
    except (json.JSONDecodeError, TypeError):
        pass
    return data_str

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
    
    try:
        end_time = datetime.now()
        processing_time_ms = (end_time - g.start_time).total_seconds() * 1000  # 保留小数部分

        # 获取请求数据
        request_content_type = request.content_type
        if request_content_type == 'application/json':
            request_data = _mask_sensitive(json.dumps(request.get_json()))
        else:
            request_data = request.data.decode('utf-8')  # 存储原始请求体

        # 获取响应数据
        response_content_type = response.content_type
        if response_content_type == 'application/json':
            response_data = json.dumps(response.get_json())  # 将 JSON 转换为字符串存储
        else:
            response_data = response.data.decode('utf-8')  # 存储原始响应体

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
            request_content_type=request_content_type,
            response_content_type=response_content_type,
            processing_time=processing_time_ms,
        )

        db.session.add(log)
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error logging activity: {str(e)}")

    return response

