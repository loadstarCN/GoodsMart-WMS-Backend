from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model
from .models import ActivityLog

# -----------------------------
# 定义 Logs 命名空间
# -----------------------------
api_ns = Namespace('logs', description='Logs related operations', authorizations=authorizations)

# -----------------------------
# 定义日志输出模型
# -----------------------------
log_model = api_ns.model('Log', {
    'id': fields.Integer(readonly=True, description='ID'),
    'actor': fields.String(description='Actor'),
    'endpoint': fields.String(description='Endpoint'),
    'method': fields.String(description='Method', enum=ActivityLog.METHOD_TYPES),
    'request_data': fields.String(description='Request Data'),
    'response_data': fields.String(description='Response Data'),
    'status_code': fields.Integer(description='Status Code'),
    'ip_address': fields.String(description='IP Address'),
    'created_at': fields.DateTime(readonly=True, description='Created At'),
})

# -----------------------------
# 定义请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('actor', type=str, location='args', help='Actor')
pagination_parser.add_argument('endpoint', type=str, location='args', help='Endpoint')
pagination_parser.add_argument('method', type=str, location='args', help='Method', choices=ActivityLog.METHOD_TYPES)
pagination_parser.add_argument('status_code', type=int, location='args', help='Status Code')
pagination_parser.add_argument('ip_address', type=inputs.ipv4, help='IP address to filter by', location='args')
pagination_parser.add_argument('created_at', type=inputs.datetime_from_iso8601, help='Filter logs created on or after this date', location='args')
pagination_parser.add_argument('keyword', type=str, location='args', help='Keyword to search in actor, endpoint, request_data, response_data')

# -----------------------------
# 创建分页模型（基于输出模型）
# -----------------------------
pagination_model = create_pagination_model(api_ns, log_model)
