#!/usr/bin/env bash
# ============================================================
# GoodsMart WMS 测试运行脚本
# 用法:
#   ./run_tests.sh           -- 运行全部测试
#   ./run_tests.sh -k user   -- 只运行包含 "user" 的测试
#   ./run_tests.sh --cov     -- 运行并生成覆盖率报告
#   ./run_tests.sh -f        -- 遇到第一个失败立即停止
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 激活虚拟环境
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "❌ 找不到虚拟环境，请先创建: python -m venv venv"
    exit 1
fi

# 解析参数
COVERAGE=false
FAILFAST=false
FILTER=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cov) COVERAGE=true; shift ;;
        -f|--failfast) FAILFAST=true; shift ;;
        -k) FILTER="$2"; shift 2 ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# 构建 pytest 命令
CMD="python -m pytest tests/ -v"

if [ "$FAILFAST" = true ]; then
    CMD="$CMD -x"
fi

if [ -n "$FILTER" ]; then
    CMD="$CMD -k '$FILTER'"
fi

if [ "$COVERAGE" = true ]; then
    CMD="$CMD --cov=. --cov-config=.coveragerc --cov-report=term-missing --cov-report=html:htmlcov"
fi

if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
    CMD="$CMD ${EXTRA_ARGS[*]}"
fi

echo "============================================================"
echo " GoodsMart WMS Backend Tests"
echo " 时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo " 命令: $CMD"
echo "============================================================"

eval $CMD
EXIT_CODE=$?

echo "============================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo " ✅ 全部测试通过"
else
    echo " ❌ 有测试失败，退出码: $EXIT_CODE"
fi

if [ "$COVERAGE" = true ] && [ -d "htmlcov" ]; then
    echo " 📊 HTML 覆盖率报告: htmlcov/index.html"
fi

echo "============================================================"
exit $EXIT_CODE
