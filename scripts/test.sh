#!/usr/bin/env bash
# ConfFlow test runner - runs pytest with all artifacts in system temp directory
set -e

# Create isolated temp directory
TEMP_BASE=$(mktemp -d -t confflow_pytest_XXXXXX)

# Cleanup on exit
trap "rm -rf '$TEMP_BASE'" EXIT INT TERM

if [[ -x ".venv/bin/python" ]]; then
  PYTEST_PYTHON=".venv/bin/python"
else
  PYTEST_PYTHON="${PYTHON:-python}"
fi

# Run pytest with all artifacts redirected to temp directory
COVERAGE_FILE="$TEMP_BASE/.coverage" \
"$PYTEST_PYTHON" -m pytest \
  -o cache_dir="$TEMP_BASE/cache" \
  --basetemp="$TEMP_BASE/basetemp" \
  "$@"
