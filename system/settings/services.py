"""系统设置服务"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from cryptography.fernet import Fernet

from extensions import db
from .models import SystemSetting

# 用于加密敏感配置（如 SMTP 密码）的密钥
# 首次部署时自动生成并存入 system_settings 表
_fernet = None

SMTP_KEYS = ['smtp_host', 'smtp_port', 'smtp_username', 'smtp_password', 'smtp_sender', 'smtp_use_tls']


def _get_fernet():
    """获取或初始化加密器"""
    global _fernet
    if _fernet:
        return _fernet

    setting = SystemSetting.query.get('_encryption_key')
    if not setting:
        key = Fernet.generate_key().decode()
        setting = SystemSetting(key='_encryption_key', value=key, is_secret=True)
        db.session.add(setting)
        db.session.commit()

    _fernet = Fernet(setting.value.encode())
    return _fernet


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        return ''


class SettingsService:

    @staticmethod
    def get(key: str, default: str = '') -> str:
        setting = SystemSetting.query.get(key)
        if not setting:
            return default
        if setting.is_secret:
            return _decrypt(setting.value)
        return setting.value

    @staticmethod
    def set(key: str, value: str, is_secret: bool = False):
        stored_value = _encrypt(value) if is_secret else value
        setting = SystemSetting.query.get(key)
        if setting:
            setting.value = stored_value
            setting.is_secret = is_secret
        else:
            setting = SystemSetting(key=key, value=stored_value, is_secret=is_secret)
            db.session.add(setting)
        db.session.commit()

    @staticmethod
    def get_smtp_config() -> dict:
        """获取 SMTP 配置"""
        config = {}
        for key in SMTP_KEYS:
            config[key] = SettingsService.get(key, '')
        return config

    @staticmethod
    def save_smtp_config(data: dict):
        """保存 SMTP 配置"""
        for key in SMTP_KEYS:
            if key not in data:
                continue
            is_secret = key == 'smtp_password'
            value = data[key]
            # 密码为空字符串时不覆盖（前端未修改）
            if is_secret and value == '':
                continue
            SettingsService.set(key, str(value), is_secret=is_secret)

    @staticmethod
    def send_email(to: str, subject: str, html_body: str):
        """使用数据库中的 SMTP 配置发送邮件"""
        config = SettingsService.get_smtp_config()

        if not config.get('smtp_host') or not config.get('smtp_username'):
            raise ValueError('SMTP is not configured')

        msg = MIMEMultipart('alternative')
        msg['From'] = config.get('smtp_sender') or config['smtp_username']
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        port = int(config.get('smtp_port') or 587)
        use_tls = str(config.get('smtp_use_tls', 'true')).lower() in ('true', '1', 'yes')

        with smtplib.SMTP(config['smtp_host'], port, timeout=10) as server:
            if use_tls:
                server.starttls()
            server.login(config['smtp_username'], config['smtp_password'])
            server.send_message(msg)

    @staticmethod
    def send_test_email(to: str) -> bool:
        """发送测试邮件"""
        html = """
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #4a6cf7;">GoodsMart WMS</h2>
            <p>This is a test email from GoodsMart WMS.</p>
            <p>If you received this email, your SMTP configuration is working correctly.</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
            <p style="color: #999; font-size: 12px;">GoodsMart Warehouse Management System</p>
        </div>
        """
        SettingsService.send_email(to, 'GoodsMart WMS - SMTP Test', html)
        return True
