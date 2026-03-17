"""Webhook 事件 — API 模型与请求解析器"""
from flask_restx import Namespace, fields
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model

# -----------------------------
# 定义 Webhook 命名空间
# -----------------------------
api_ns = Namespace(
    'webhook',
    description='Webhook 事件推送日志',
    authorizations=authorizations,
)

# -----------------------------
# 事件类型枚举
# -----------------------------
EVENT_TYPES = [
    'asn.received',
    'asn.completed',
    'dn.in_progress',
    'dn.delivered',
    'dn.completed',
]

STATUS_TYPES = ['pending', 'sent', 'failed']

# -----------------------------
# 输出模型
# -----------------------------
webhook_event_model = api_ns.model('WebhookEvent', {
    'id': fields.Integer(readonly=True, description='ID'),
    'api_key_id': fields.Integer(description='API Key ID'),
    'event_type': fields.String(description='事件类型', enum=EVENT_TYPES),
    'payload': fields.Raw(description='推送数据'),
    'status': fields.String(description='状态', enum=STATUS_TYPES),
    'attempts': fields.Integer(description='已尝试次数'),
    'next_retry_at': fields.DateTime(description='下次重试时间'),
    'created_at': fields.DateTime(description='创建时间'),
    'sent_at': fields.DateTime(description='发送成功时间'),
    'last_error': fields.String(description='最后一次错误信息'),
})

# -----------------------------
# 请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument(
    'event_type', type=str, location='args',
    help='事件类型筛选', choices=EVENT_TYPES,
)
pagination_parser.add_argument(
    'status', type=str, location='args',
    help='状态筛选', choices=STATUS_TYPES,
)
pagination_parser.add_argument(
    'api_key_id', type=int, location='args',
    help='按 API Key ID 筛选',
)
pagination_parser.add_argument(
    'keyword', type=str, location='args',
    help='关键词搜索（事件类型、错误信息）',
)

# -----------------------------
# 分页模型
# -----------------------------
pagination_model = create_pagination_model(api_ns, webhook_event_model)
