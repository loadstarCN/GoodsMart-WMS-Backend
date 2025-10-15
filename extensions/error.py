from flask_restx import ValidationError
from werkzeug.exceptions import HTTPException

class APIException(Exception):
    """API异常基类"""
    def __init__(self, message, biz_code, status_code=400, field=None):
        """
        :param message: 错误消息
        :param biz_code: 业务错误码
        :param status_code: HTTP状态码 (默认400)
        :param field: 相关字段 (可选)
        """
        super().__init__(message)
        self.biz_code = biz_code
        self.status_code = status_code
        self.field = field
        self.message = message

    def __str__(self):
        return f"{self.status_code} {self.__class__.__name__}: {self.message}"

# 400 Bad Request
class BadRequestException(APIException):
    """400 错误"""
    def __init__(self, message, biz_code=40000, field=None):
        super().__init__(message, biz_code, 400, field)

# 401 Unauthorized
class UnauthorizedException(APIException):
    """401 错误"""
    def __init__(self, message, biz_code=41000, field=None):
        super().__init__(message, biz_code, 401, field)

# 403 Forbidden
class ForbiddenException(APIException):
    """403 错误"""
    def __init__(self, message, biz_code=42000, field=None):
        super().__init__(message, biz_code, 403, field)

# 404 Not Found
class NotFoundException(APIException):
    """404 错误"""
    def __init__(self, message, biz_code=43000, field=None):
        super().__init__(message, biz_code, 404, field)

# 500 Internal Server Error
class InternalServerError(APIException):
    """500 错误"""
    def __init__(self, message, biz_code=50000, field=None):
        super().__init__(message, biz_code, 500, field)

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
    
    # 处理ValueError
    @app.errorhandler(ValueError)
    def handle_value_error(error):
        response = {
            "status": "error",
            "code": 40000,
            "message": str(error)
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