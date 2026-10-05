#!/usr/bin/env bash
# Run the JobDesk-v2 pytest suite against a chosen ConfFlow worktree.
#
# JobDesk tests hard-code /opt/ConfFlow as the producer cwd and
# /opt/ConfFlow/.venv/bin/python as the producer interpreter (PLAN.md Q15).
# This script binds the requested ConfFlow worktree onto /opt/ConfFlow, and
# the real .venv back on top of it, inside a private mount namespace, so no
# other process sees the change and the /opt/ConfFlow checkout is never
# switched.  Execution agents call this script; they never call unshare or
# mount themselves (PLAN.md G10).
#
# Usage:
#   run_jd_tests.sh [--cf DIR] [--jd DIR] [--junit FILE] [-- PYTEST_ARGS...]
#
# Defaults: --cf /opt/cf-worktrees/exec-cf, --jd /opt/cf-worktrees/exec-jd.
# Without PYTEST_ARGS the whole suite runs.  Example (collection only):
#   run_jd_tests.sh -- --collect-only -q
set -euo pipefail

cf=/opt/cf-worktrees/exec-cf
jd=/opt/cf-worktrees/exec-jd
junit=""
while [ $# -gt 0 ]; do
    case "$1" in
        --cf) cf="$2"; shift 2 ;;
        --jd) jd="$2"; shift 2 ;;
        --junit) junit="$2"; shift 2 ;;
        --) shift; break ;;
        *) echo "run_jd_tests.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

cf="$(realpath "$cf")"
jd="$(realpath "$jd")"
real_venv=/opt/ConfFlow/.venv
venv_stash=/tmp/refactor-venv

for path in "$cf/confflow" "$jd/src/jobdesk_v2" "$real_venv/bin/python"; do
    if [ ! -e "$path" ]; then
        echo "run_jd_tests.sh: missing $path" >&2
        exit 2
    fi
done
mkdir -p "$cf/.venv" "$venv_stash"

pytest_args=(-q -o addopts= -p no:cacheprovider)
if [ -n "$junit" ]; then
    mkdir -p "$(dirname "$junit")"
    pytest_args+=("--junitxml=$junit")
fi
pytest_args+=("$@")

export CF_BIND="$cf" JD_DIR="$jd" REAL_VENV="$real_venv" VENV_STASH="$venv_stash"
exec unshare -m --propagation private bash -s -- "${pytest_args[@]}" <<'INNER'
set -euo pipefail
mount --bind "$REAL_VENV" "$VENV_STASH"
mount --bind "$CF_BIND" /opt/ConfFlow
mount --bind "$VENV_STASH" /opt/ConfFlow/.venv
cd "$JD_DIR"
export PYTHONPATH="$JD_DIR/src" QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1
exec python3 -m pytest "$@"
INNER
