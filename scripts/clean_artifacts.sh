#!/usr/bin/env bash
set -euo pipefail

echo "[clean] removing test/tool caches and artifacts..."
rm -rf .artifacts .pytest_cache .mypy_cache .ruff_cache .pytest_basetemp
rm -rf htmlcov coverage.xml .coverage reports

find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

echo "[clean] done"
