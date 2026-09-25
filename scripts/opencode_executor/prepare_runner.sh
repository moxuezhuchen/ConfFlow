#!/usr/bin/env bash
# One-time host preparation for the OpenCode self-hosted runner (WSL).
#
# This script checks prerequisites and prints the manual registration steps.
# It NEVER registers a runner by itself and NEVER handles tokens: GitHub
# shows a one-time token on the "New self-hosted runner" page and the human
# operator pastes the official commands. No credential is stored by this script.
set -euo pipefail

echo "=== OpenCode executor host preflight (WSL) ==="
echo "arch: $(uname -m) | kernel: $(uname -r)"
echo ""

ok=1
need() {
    if command -v "$1" >/dev/null 2>&1; then
        echo "OK   $1: $(command -v "$1")"
    else
        echo "MISS $1 (required)"; ok=0
    fi
}
need git
need python3
need gh
need opencode
need flock
command -v timeout >/dev/null 2>&1 && echo "OK   timeout (optional)" || echo "INFO timeout absent: OPENCODE_TIMEOUT_SECS will be ignored"
echo ""
python3 scripts/opencode_executor/validate_input.py \
    --task-issue 1 --phase P1 --model opencode-go/muse-spark-1.3-contributor \
    --test-profile fast --target-branch automation/smoke 2>/dev/null \
    && echo "OK   validator self-test" || { echo "FAIL validator self-test"; ok=0; }
python3 scripts/opencode_executor/select_tests.py fast >/dev/null \
    && echo "OK   profile self-test" || { echo "FAIL profile self-test"; ok=0; }
echo ""
echo "--- gh auth ---"
gh auth status || { echo "Run: gh auth login"; ok=0; }
echo ""
echo "--- opencode auth ---"
opencode auth list || echo "INFO: confirm provider/model auth in the opencode app"
echo ""
if [[ -d "$HOME/actions-runner" ]]; then
    echo "INFO: ~/actions-runner already exists; check its labels include opencode-executor"
else
    echo "INFO: no ~/actions-runner yet — registration is manual (see below)"
fi
echo ""
cat <<'EOF'
=== Manual registration (one time, human only) ===
1. GitHub repo page → Settings → Actions → Runners → "New self-hosted runner".
2. Select Image: Linux, Architecture: X64 (on this host: x86_64; if your host
   differs, change runs-on labels in .github/workflows/opencode-executor.yml).
3. On this WSL host, run EXACTLY the commands GitHub shows (./config.sh with
   the one-time token, then ./run.sh). Add the label:
       opencode-executor
   Keep the default work folder (<runner>/_work): the workflow refuses to run
   anywhere else, so developer checkouts such as /opt/ConfFlow are never
   touched by the executor.
4. Run the runner as a service if desired (sudo ./svc.sh install && sudo ./svc.sh start).
5. Confirm OpenCode provider/model auth for the runner user.
6. Dispatch a smoke run: Actions → OpenCode Executor → Run workflow
   (task_issue=<smoke issue>, phase=P1, test_profile=none).
The registration token shown by GitHub expires quickly and must never be
committed to the repo.
EOF
[[ "$ok" == "1" ]] && echo "PREFLIGHT-PASS" || { echo "PREFLIGHT-FAIL (see MISS lines)"; exit 1; }
