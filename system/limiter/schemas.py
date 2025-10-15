from flask_restx import Namespace, fields, inputs
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model

# -----------------------------
# 定义 Limiter 命名空间
# -----------------------------
api_ns = Namespace('limiter', description='Limiter related operations', authorizations=authorizations)

# -----------------------------
# 定义输入模型
# -----------------------------
whitelist_input_model = api_ns.model('WhitelistInput', {
    'ip_address': fields.String(required=True, description='IP Address'),
    'reason': fields.String(description='Reason for whitelisting'),
})
blacklist_input_model = api_ns.model('BlacklistInput', {
    'ip_address': fields.String(required=True, description='IP Address'),
    'reason': fields.String(description='Reason for blacklisting'),
})

# -----------------------------
# 定义输出模型
# -----------------------------
whitelist_model = api_ns.model('Whitelist', {
    'id': fields.Integer(readonly=True, description='ID'),
    'ip_address': fields.String(description='IP Address'),
    'reason': fields.String(description='Reason for whitelisting'),
    'timestamp': fields.DateTime(readonly=True, description='Timestamp'),
})
blacklist_model = api_ns.model('Blacklist', {
    'id': fields.Integer(readonly=True, description='ID'),
    'ip_address': fields.String(description='IP Address'),
    'reason': fields.String(description='Reason for blacklisting'),
    'timestamp': fields.DateTime(readonly=True, description='Timestamp'),
})

# -----------------------------
# 定义请求参数解析器
# -----------------------------
pagination_parser = pagination_parser.copy()
pagination_parser.add_argument('ip_address', type=inputs.ipv4, location='args', help='IP Address')

# -----------------------------
# 创建分页模型
# -----------------------------
blacklist_pagination_model = create_pagination_model(api_ns, blacklist_model)
whitelist_pagination_model = create_pagination_model(api_ns, whitelist_model)
