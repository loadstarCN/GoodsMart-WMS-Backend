"""SMTP「启用 TLS」：没设 / 空 = 开（与设置页的显示口径一致），只有明确的 false / 0 / no / off 才关"""
import pytest

from tests.helpers import *  # noqa: F401,F403
from system.settings.services import SettingsService, smtp_use_tls


@pytest.mark.parametrize('value, expected', [
    (None, True), ('', True), ('  ', True), ('true', True), ('1', True), ('yes', True), ('TRUE', True),
    ('false', False), ('0', False), ('no', False), ('off', False), ('False', False),
])
def test_smtp_use_tls_defaults_to_on(value, expected):
    assert smtp_use_tls(value) is expected


def test_send_email_uses_tls_when_setting_is_empty(client, monkeypatch):
    calls = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(('connect', host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls.append(('starttls',))

        def login(self, user, password):
            calls.append(('login', user))

        def send_message(self, message):
            calls.append(('send', message['To']))

    import system.settings.services as settings_services
    monkeypatch.setattr(settings_services.smtplib, 'SMTP', _FakeSMTP)
    config = {'smtp_host': 'smtp.example.com', 'smtp_port': '', 'smtp_username': 'mailer', 'smtp_password': 'x',
              'smtp_sender': '', 'smtp_use_tls': ''}
    monkeypatch.setattr(SettingsService, 'get_smtp_config', staticmethod(lambda: dict(config)))
    with client.application.app_context():
        SettingsService.send_email('to@example.com', 'Subject', '<p>x</p>')
        assert ('starttls',) in calls
        calls.clear()
        config['smtp_use_tls'] = 'false'
        SettingsService.send_email('to@example.com', 'Subject', '<p>x</p>')
        assert ('starttls',) not in calls and ('send', 'to@example.com') in calls
