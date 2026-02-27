#!/usr/bin/env bash
# 运行完整测试并生成覆盖率报告
set -euo pipefail

echo "正在运行完整测试套件并计算覆盖率..."
python -m pytest --cov=confflow --cov-report=term-missing tests/

echo "测试与覆盖率分析完成。"
