from flask_restx import Namespace, fields
from extensions import authorizations

api_ns = Namespace('settings', description='System settings', authorizations=authorizations)

smtp_config_model = api_ns.model('SmtpConfig', {
    'smtp_host': fields.String(description='SMTP server host'),
    'smtp_port': fields.String(description='SMTP server port'),
    'smtp_username': fields.String(description='SMTP username'),
    'smtp_password': fields.String(description='SMTP password (empty = unchanged)'),
    'smtp_sender': fields.String(description='Sender email address'),
    'smtp_use_tls': fields.String(description='Use TLS (true/false)'),
})

smtp_test_model = api_ns.model('SmtpTest', {
    'to': fields.String(required=True, description='Recipient email address'),
})
