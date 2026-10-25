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
    'method': fields.String(description='Method (only write requests are logged)', enum=ActivityLog.LOGGED_METHODS),
    'request_data': fields.String(description='Request Data'),
    'response_data': fields.String(description='Response Data'),
    'status_code': fields.Integer(description='Status Code'),
    'ip_address': fields.String(description='IP Address'),
    'processing_time': fields.Integer(description='Processing time in milliseconds'),
    'created_at': fields.DateTime(readonly=True, description='Created At'),
})

# -----------------------------
# 定义请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('actor', type=str, location='args', help='Actor')
pagination_parser.add_argument('endpoint', type=str, location='args', help='Endpoint')
pagination_parser.add_argument('method', type=str, location='args',
                               help='Method (POST / PUT / DELETE; GET requests are not logged)',
                               choices=ActivityLog.LOGGED_METHODS)
pagination_parser.add_argument('status_code', type=int, location='args', help='Status Code')
pagination_parser.add_argument('ip_address', type=inputs.ipv4, help='IP address to filter by', location='args')
pagination_parser.add_argument('created_at', type=inputs.datetime_from_iso8601, help='Filter logs created on or after this date', location='args')


def _date_or_datetime(value):
    """结束时间：只给日期（YYYY-MM-DD）→ date（含那一整天，按默认时区）；否则 ISO 8601 时刻"""
    text = str(value).strip()
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        return inputs.date_from_iso8601(text)
    return inputs.datetime_from_iso8601(text)


_date_or_datetime.__schema__ = {'type': 'string', 'format': 'date-time'}

pagination_parser.add_argument('created_at_end', type=_date_or_datetime, location='args',
                               help='Filter logs created on or before this time (YYYY-MM-DD = through that whole day '
                                    'in the default time zone; a time without offset is in the default time zone)')
pagination_parser.add_argument('keyword', type=str, location='args', help='Keyword to search in actor, endpoint, request_data, response_data')

# -----------------------------
# 创建分页模型（基于输出模型）
# -----------------------------
pagination_model = create_pagination_model(api_ns, log_model)
