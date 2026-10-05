"""健康检查：GET /health

部署流水线（.github/workflows/deploy.yml）在 pm2 重启后探测这个接口，确认新进程已经能处理请求、
跑的是刚部署的提交。不需要鉴权、不查数据库，只返回 {"status": "ok", "commit": "<git 短哈希>"}，
不暴露版本号、配置、依赖等其它信息。
"""
import os
import re
import subprocess

from flask import Blueprint, jsonify

blueprint = Blueprint('health', __name__)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SHORT_SHA = re.compile(r'^[0-9a-f]{7,40}$')


def read_git_commit(cwd=_APP_DIR):
    """代码目录当前提交的 git 短哈希（7 位，与 `git rev-parse --short=7 HEAD` 一致）；
    取不到（没装 git、不是仓库、超时、输出异常）时返回 'unknown'"""
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            cwd=cwd, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return out if _SHORT_SHA.match(out) else 'unknown'


# 进程启动（导入）时取一次：反映的是这个进程加载的代码。之后磁盘上的代码被 git reset 也不会变，
# 这样部署脚本能区分「pm2 已经换成新代码」和「还是旧进程 / 跑的是别的目录」
GIT_COMMIT = read_git_commit()


@blueprint.route('/health', methods=['GET'])
def health():
    return jsonify(status='ok', commit=GIT_COMMIT)
