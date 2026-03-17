from flask_restx import Resource
from system.common import permission_required
from .schemas import api_ns, smtp_config_model, smtp_test_model
from .services import SettingsService


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
        except Exception as e:
            return {'message': f'Failed to send: {str(e)}'}, 400
