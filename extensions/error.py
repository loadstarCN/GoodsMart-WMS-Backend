from flask_restx import ValidationError
from werkzeug.exceptions import HTTPException

class APIException(Exception):
    """API异常基类"""
    def __init__(self, message, biz_code, status_code=400, field=None, details=None):
        """
        :param message: 错误消息
        :param biz_code: 业务错误码
        :param status_code: HTTP状态码 (默认400)
        :param field: 相关字段 (可选)
        :param details: 结构化的附加信息 (可选，dict/list，响应里原样输出为 details)
        """
        super().__init__(message)
        self.biz_code = biz_code
        self.status_code = status_code
        self.field = field
        self.details = details
        self.message = message

    def __str__(self):
        return f"{self.status_code} {self.__class__.__name__}: {self.message}"

# 400 Bad Request
class BadRequestException(APIException):
    """400 错误"""
    def __init__(self, message, biz_code=40000, field=None, details=None):
        super().__init__(message, biz_code, 400, field, details)

# 401 Unauthorized
class UnauthorizedException(APIException):
    """401 错误"""
    def __init__(self, message, biz_code=41000, field=None, details=None):
        super().__init__(message, biz_code, 401, field, details)

# 403 Forbidden
class ForbiddenException(APIException):
    """403 错误"""
    def __init__(self, message, biz_code=42000, field=None, details=None):
        super().__init__(message, biz_code, 403, field, details)

# 404 Not Found
class NotFoundException(APIException):
    """404 错误"""
    def __init__(self, message, biz_code=43000, field=None, details=None):
        super().__init__(message, biz_code, 404, field, details)

# 409 Conflict
class ConflictException(APIException):
    """409 错误（资源仍被引用 / 状态冲突）"""
    def __init__(self, message, biz_code=44000, field=None, details=None):
        super().__init__(message, biz_code, 409, field, details)

# 500 Internal Server Error
class InternalServerError(APIException):
    """500 错误"""
    def __init__(self, message, biz_code=50000, field=None, details=None):
        super().__init__(message, biz_code, 500, field, details)

# 502 Bad Gateway
class BadGatewayException(APIException):
    """502 错误（上游外部服务返回错误，如承运商接口）"""
    def __init__(self, message, biz_code=50200, field=None, details=None):
        super().__init__(message, biz_code, 502, field, details)

# 504 Gateway Timeout
class GatewayTimeoutException(APIException):
    """504 错误（上游外部服务超时）"""
    def __init__(self, message, biz_code=50400, field=None, details=None):
        super().__init__(message, biz_code, 504, field, details)

def register_error_handlers(app):
    app.config['PROPAGATE_EXCEPTIONS'] = True

    # 处理自定义API异常
    @app.errorhandler(APIException)
    def handle_api_exception(error):
        response = {
            "status": "error",
            "code": error.biz_code,
            "message": error.message,
        }
        
        if error.field:
            response["field"] = error.field

        if getattr(error, 'details', None) is not None:
            response["details"] = error.details
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "status_code": error.status_code
            }
             
        return response, error.status_code
    
    # 处理 HTTPException (Werkzeug 标准异常)
    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        # 将标准HTTP异常转换为自定义格式
        return {
            "status": "error",
            "code": error.code * 100,  # 转换为业务错误码
            "message": error.description
        }, error.code
    
    # 处理ValueError（生产环境不回显异常原文，避免泄露内部信息）
    @app.errorhandler(ValueError)
    def handle_value_error(error):
        response = {
            "status": "error",
            "code": 40000,
            "message": str(error) if app.config.get('FLASK_ENV') == 'development' else "Invalid value"
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "details": str(error)
            }
        
        return response, 400
    
    # 处理请求验证错误
    @app.errorhandler(ValidationError)
    def handle_validation_error(error):
        errors = []
        if hasattr(error, 'errors') and isinstance(error.errors, dict):
            for field, messages in error.errors.items():
                if isinstance(messages, dict):
                    for sub_field, sub_messages in messages.items():
                        errors.append({
                            "field": f"{field}.{sub_field}",
                            "message": ", ".join(sub_messages)
                        })
                else:
                    errors.append({
                        "field": field,
                        "message": ", ".join(messages)
                    })
        else:
            errors = [{"message": str(error)}]
        
        response = {
            "status": "error",
            "code": 40000,
            "message": "Invalid request parameters",
            "errors": errors
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "full_details": str(error)
            }
        
        return response, 400
    
    # 处理其他未捕获异常
    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled exception occurred")
        response = {
            "status": "error",
            "code": 50000,
            "message": "Internal server error"
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "details": str(error)
            }
        return response, 500