import logging

from flask_restx import Resource
from system.common import permission_required
from .schemas import api_ns, smtp_config_model, smtp_test_model
from .services import SettingsService

logger = logging.getLogger(__name__)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/smtp')
class SmtpConfigResource(Resource):

    @permission_required(['all_access'])
    @api_ns.marshal_with(smtp_config_model)
    def get(self):
        """获取 SMTP 配置"""
        config = SettingsService.get_smtp_config()
        # 密码不返回明文
        if config.get('smtp_password'):
            config['smtp_password'] = '********'
        return config

    @permission_required(['all_access'])
    @api_ns.expect(smtp_config_model)
    def put(self):
        """保存 SMTP 配置"""
        data = api_ns.payload
        SettingsService.save_smtp_config(data)
        return {'message': 'SMTP configuration saved'}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/smtp/test')
class SmtpTestResource(Resource):

    @permission_required(['all_access'])
    @api_ns.expect(smtp_test_model)
    def post(self):
        """发送测试邮件"""
        to = api_ns.payload.get('to')
        if not to:
            return {'message': 'Recipient email is required'}, 400
        try:
            SettingsService.send_test_email(to)
            return {'message': 'Test email sent successfully'}, 200
        except Exception:
            # 异常原文（SMTP 服务器回应、内网主机名 / 端口、认证细节等）只写日志，不回给调用方
            logger.exception('SMTP test email failed')
            return {'message': 'Failed to send the test email; check the SMTP settings and the server log'}, 400
