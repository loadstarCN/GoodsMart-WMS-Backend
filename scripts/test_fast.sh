#!/usr/bin/env bash
# ============================================================
# 后端测试一键快速跑：pytest-xdist 多进程并行，最后打印耗时。Windows 在 Git Bash 里运行（bash scripts/test_fast.sh）。
#
#   scripts/test_fast.sh                          全量 tests/，并行进程数默认 = CPU 逻辑核数（最多 16）
#   scripts/test_fast.sh tests/test_asn.py        只跑指定的文件 / 目录 / 用例（默认单进程，省掉起进程的几秒）
#   scripts/test_fast.sh -k revoke -x             其余参数原样交给 pytest（可与路径一起用）
#   WORKERS=8 scripts/test_fast.sh                指定并行进程数；WORKERS=0 不并行
#   PYTHON=/path/to/python scripts/test_fast.sh   指定解释器（默认依次找 .venv、venv、python3、python）
#
# 测试用 SQLite 内存库与 fakeredis（tests/conftest.py、tests/helpers.py），不需要本机的 PostgreSQL / Redis；
# 每个进程只建一次模板库，每条测试复制一份，进程之间、测试之间互不影响。
# 依赖：pip install -r requirements-dev.txt（pytest、pytest-xdist、fakeredis）
# ============================================================
set -u

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in .venv/Scripts/python.exe .venv/bin/python venv/Scripts/python.exe venv/bin/python; do
        if [ -x "$candidate" ]; then PY="$candidate"; break; fi
    done
fi
if [ -z "$PY" ]; then
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

if ! "$PY" -c "import pytest, xdist, fakeredis" >/dev/null 2>&1; then
    echo "缺少测试依赖（pytest / pytest-xdist / fakeredis），先执行: $PY -m pip install -r requirements-dev.txt" >&2
    exit 1
fi

# 参数里有测试路径（文件 / 目录 / 节点 id）就只跑这些，否则跑整个 tests/
selected=false
for arg in "$@"; do
    case "$arg" in
        -*) ;;
        tests|tests/*|*.py|*.py::*|*/tests/*) selected=true ;;
    esac
done

if [ -n "${WORKERS:-}" ]; then
    workers="$WORKERS"
elif [ "$selected" = true ]; then
    workers=0
else
    workers=$("$PY" -c "import os; print(min(os.cpu_count() or 1, 16))")
fi

cmd=("$PY" -m pytest -q -rfE)
if [ "$workers" != "0" ]; then
    # worksteal：快的进程从慢的进程那里分走剩下的用例，最后不会剩一个进程独自跑长尾
    cmd+=(-n "$workers" --dist worksteal)
fi
if [ "$selected" = false ]; then
    cmd+=(tests)
fi
cmd+=("$@")

echo "命令: ${cmd[*]}"
start=$(date +%s)
"${cmd[@]}"
status=$?
elapsed=$(( $(date +%s) - start ))
echo "------------------------------------------------------------"
printf '耗时 %dm%02ds（%d 秒），退出码 %d\n' $((elapsed / 60)) $((elapsed % 60)) "$elapsed" "$status"
exit $status
