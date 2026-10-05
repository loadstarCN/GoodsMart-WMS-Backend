"""GET /health：部署流水线（.github/workflows/deploy.yml）重启后的健康探测"""
import os
import re
import shutil
import subprocess

import pytest
from flask import Flask

import health
from .helpers import setup_app

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bare_app():
    """只注册 health 蓝图、不初始化数据库 / Redis / JWT 的应用：能正常返回即说明接口不依赖这些"""
    app = Flask(__name__)
    app.register_blueprint(health.blueprint)
    return app


def test_health_returns_only_status_and_commit():
    resp = _bare_app().test_client().get('/health')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {'status', 'commit'}
    assert data['status'] == 'ok'
    assert data['commit'] == health.GIT_COMMIT
    assert data['commit'] == 'unknown' or re.fullmatch(r'[0-9a-f]{7,40}', data['commit'])


def test_health_without_auth_under_request_hooks():
    """挂上与生产相同的 JWT / API Key 前置钩子：不带凭证、带无效 token 都能访问"""
    app = setup_app()
    app.register_blueprint(health.blueprint)
    client = app.test_client()

    assert client.get('/health').status_code == 200
    resp = client.get('/health', headers={'Authorization': 'Bearer not-a-valid-token'})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'


def test_health_rejects_other_methods():
    assert _bare_app().test_client().post('/health').status_code == 405


@pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')
def test_read_git_commit_matches_git_short_hash():
    expected = subprocess.run(
        ['git', 'rev-parse', '--short=7', 'HEAD'], cwd=BACKEND_DIR,
        capture_output=True, text=True,
    )
    if expected.returncode != 0:
        pytest.skip('Backend directory is not a git checkout')
    assert health.read_git_commit(BACKEND_DIR) == expected.stdout.strip()


def test_read_git_commit_unknown_outside_repo(tmp_path):
    assert health.read_git_commit(str(tmp_path)) == 'unknown'


@pytest.mark.parametrize('error', [
    FileNotFoundError('git'),
    subprocess.TimeoutExpired(cmd='git', timeout=5),
    subprocess.CalledProcessError(returncode=128, cmd='git'),
])
def test_read_git_commit_unknown_when_git_fails(monkeypatch, error):
    def fake_run(*args, **kwargs):
        raise error

    monkeypatch.setattr(health.subprocess, 'run', fake_run)
    assert health.read_git_commit() == 'unknown'


def test_read_git_commit_rejects_unexpected_output(monkeypatch):
    """输出不是十六进制哈希（例如 git 打印了警告）时不原样返回"""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout='warning: something\n', stderr='')

    monkeypatch.setattr(health.subprocess, 'run', fake_run)
    assert health.read_git_commit() == 'unknown'
