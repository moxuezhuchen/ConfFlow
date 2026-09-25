#!/usr/bin/env bash
# OpenCode self-hosted executor — one bounded phase per invocation.
#
# Only `opencode run` flags verified by this machine's `opencode run --help`
# (v2.0.16) are used: --model, --title (plus opt-in --auto). The prompt is
# passed as the positional <message...> argument; the model is always a
# separate argv entry, never interpolated into shell source.
#
# Safety properties:
#   * inputs validated by validate_input.py before any use;
#   * single-flight per host via flock; workflow adds Actions concurrency;
#   * never force-pushes, never merges, never touches main/master;
#   * never reset --hard / clean -fd; aborts on a dirty tree;
#   * logs live outside the repo so `git add -A` cannot scoop them up;
#   * secrets are never passed as CLI args and logs are redacted.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATE_PY="$SCRIPT_DIR/validate_input.py"
SELECT_PY="$SCRIPT_DIR/select_tests.py"

TASK_ISSUE="${TASK_ISSUE:-}"
PHASE="${PHASE:-}"
MODEL="${MODEL:-}"
TEST_PROFILE="${TEST_PROFILE:-}"
TARGET_BRANCH="${TARGET_BRANCH:-}"
AUTO_APPROVE=0
LOG_DIR="${OPENCODE_LOG_DIR:-${RUNNER_TEMP:-/tmp}/opencode-executor-logs}"
LOCK_FILE="${OPENCODE_EXECUTOR_LOCK:-/tmp/opencode-executor.lock}"
# Optional: seconds; only honoured when the `timeout` binary exists.
OPENCODE_TIMEOUT_SECS="${OPENCODE_TIMEOUT_SECS:-}"

usage() {
    cat <<'EOF'
Usage: run_task.sh --task-issue N --phase P3 --model provider/model \
    --test-profile fast|full|integration|none [--target-branch automation/...]
    [--auto-approve] [--log-dir DIR]

Each flag also falls back to its env var: TASK_ISSUE, PHASE, MODEL,
TEST_PROFILE, TARGET_BRANCH, OPENCODE_LOG_DIR.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task-issue) TASK_ISSUE="${2:-}"; shift 2 ;;
        --phase) PHASE="${2:-}"; shift 2 ;;
        --model) MODEL="${2:-}"; shift 2 ;;
        --test-profile) TEST_PROFILE="${2:-}"; shift 2 ;;
        --target-branch) TARGET_BRANCH="${2:-}"; shift 2 ;;
        --auto-approve) AUTO_APPROVE=1; shift ;;
        --log-dir) LOG_DIR="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -z "$TARGET_BRANCH" && -n "$PHASE" ]]; then
    # Deterministic default keeps branch naming uniform across rounds.
    TARGET_BRANCH="automation/jobdesk-v3-${PHASE}"
fi

fail() { echo "EXECUTOR-FAIL: $*" >&2; exit 1; }
step() { echo "===== $* ====="; }

# ---------------------------------------------------------------- lock ----
# Fail fast when another executor holds the host lock: two writers must
# never share one checkout.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    fail "another OpenCode executor is running (lock $LOCK_FILE held); refusing concurrent run"
fi
step "acquired host lock: $LOCK_FILE"

# ----------------------------------------------------------- validation ----
# NOTE: never chmod anything inside the repo here — the working tree must
# stay exactly as checked out until the dirty-tree guard below has passed.
# The helpers are always invoked via "$PYBIN" explicitly, so +x is irrelevant.
PYBIN="${PYTHON:-python3}"
if ! "$PYBIN" "$VALIDATE_PY" \
        --task-issue "$TASK_ISSUE" \
        --phase "$PHASE" \
        --model "$MODEL" \
        --test-profile "$TEST_PROFILE" \
        --target-branch "$TARGET_BRANCH"; then
    fail "input validation rejected the requested inputs (see above)"
fi
step "inputs valid: issue=$TASK_ISSUE phase=$PHASE model=$MODEL profile=$TEST_PROFILE branch=$TARGET_BRANCH"

case "$TARGET_BRANCH" in
    main|master|automation/main|automation/master)
        fail "refusing to operate on protected branch $TARGET_BRANCH" ;;
esac

# ------------------------------------------------------------ repo setup ---
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "not inside a git repository"
cd "$REPO_ROOT"
step "repo root: $REPO_ROOT"

command -v opencode >/dev/null 2>&1 || fail "opencode CLI not found on PATH"
command -v gh >/dev/null 2>&1 || fail "gh CLI not found on PATH (needed for task_issue fetch)"
command -v git >/dev/null 2>&1 || fail "git not found on PATH"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${PHASE}-issue${TASK_ISSUE}"
RUN_LOG_DIR="$LOG_DIR/$RUN_ID"
mkdir -p "$RUN_LOG_DIR"
ISSUE_JSON="$RUN_LOG_DIR/issue.json"
PROMPT_FILE="$RUN_LOG_DIR/prompt.md"
OPENCODE_LOG="$RUN_LOG_DIR/opencode.log"
TEST_LOG="$RUN_LOG_DIR/tests.log"
SUMMARY_FILE="$RUN_LOG_DIR/executor-summary.md"

if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    fail "working tree is dirty; refusing to touch user changes (commit/stash first)"
fi
step "working tree clean"

# ---------------------------------------------------------- task source ----
# The GitHub Issue is the source of truth; the full task text is never
# passed through workflow inputs.
step "fetching task from GitHub Issue #$TASK_ISSUE"
if ! gh issue view "$TASK_ISSUE" --json number,title,body,state,url >"$ISSUE_JSON"; then
    fail "could not read Issue #$TASK_ISSUE (check number and gh auth)"
fi
ISSUE_TITLE="$("$PYBIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['title'])" "$ISSUE_JSON")"
ISSUE_STATE="$("$PYBIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['state'])" "$ISSUE_JSON")"
ISSUE_URL="$("$PYBIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['url'])" "$ISSUE_JSON")"
ISSUE_BODY="$("$PYBIN" -c "import json,sys; print(json.load(open(sys.argv[1]))['body'] or '')" "$ISSUE_JSON")"
echo "issue: $ISSUE_TITLE ($ISSUE_STATE) $ISSUE_URL"

# --------------------------------------------------------------- branch ----
step "preparing branch $TARGET_BRANCH"
git fetch origin --prune 2>/dev/null || echo "WARN: git fetch failed; continuing with local refs" >&2
if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH"; then
    echo "branch exists locally; continuing on it"
    git checkout "$TARGET_BRANCH"
    if git show-ref --verify --quiet "refs/remotes/origin/$TARGET_BRANCH"; then
        git pull --ff-only origin "$TARGET_BRANCH" \
            || fail "branch diverged from origin/$TARGET_BRANCH; manual reconcile required (no rebase/merge done)"
    fi
elif git show-ref --verify --quiet "refs/remotes/origin/$TARGET_BRANCH"; then
    echo "branch exists on origin; checking out tracking branch"
    git checkout -b "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
else
    echo "creating new branch from current HEAD ($(git rev-parse --short HEAD))"
    git checkout -b "$TARGET_BRANCH"
fi
CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "$TARGET_BRANCH" ]] || fail "branch checkout mismatch: $CURRENT_BRANCH"
if [[ -n "$(git status --porcelain)" ]]; then
    fail "working tree became dirty after checkout; aborting"
fi

# --------------------------------------------------------------- prompt ----
step "writing OpenCode prompt to $PROMPT_FILE"
TEST_CMD="$("$PYBIN" "$SELECT_PY" "$TEST_PROFILE" 2>"$RUN_LOG_DIR/select_tests.err" \
    || { echo "test profile '$TEST_PROFILE' unsupported here; OpenCode must not claim tests ran" >"$RUN_LOG_DIR/select_tests.err"; echo "PROFILE-UNAVAILABLE"; })"
if [[ "$TEST_CMD" == "PROFILE-UNAVAILABLE" ]]; then
    TEST_CMD="(no test command available for profile '$TEST_PROFILE' — report tests as NOT RUN)"
fi
cat >"$PROMPT_FILE" <<'__OPENCODE_PROMPT_EOF_7f3a2c1e_static__'
You are executing one bounded development phase.

PHASE-PLACEHOLDER
__OPENCODE_PROMPT_EOF_7f3a2c1e_static__
# The untrusted Issue body is appended with a quoted heredoc delimiter above
# plus a static template: even a hostile body line can never terminate the
# heredoc or execute as shell. Dynamic fields are injected below by
# python (argv, never shell) so no expansion pass ever sees them.
"$PYBIN" - "$PROMPT_FILE" "$REPO_ROOT" "$TARGET_BRANCH" "$PHASE" \
    "$TASK_ISSUE" "$ISSUE_TITLE" "$ISSUE_URL" "$ISSUE_STATE" \
    "$TEST_PROFILE" "$TEST_CMD" "$ISSUE_BODY" <<'PYEOF'
import sys

prompt_file, repo, branch, phase, issue_no, title, url, state, profile, cmd, body = sys.argv[1:12]
with open(prompt_file, encoding="utf-8") as fh:
    template = fh.read()
filled = template.replace(
    "PHASE-PLACEHOLDER",
    "Repository: "
    + repo
    + " (current checkout, branch "
    + branch
    + ")\nPhase: "
    + phase
    + "\nTask source: GitHub Issue #"
    + issue_no
    + " — "
    + title
    + " ("
    + url
    + ", state "
    + state
    + ")\nTarget branch: "
    + branch
    + "\nRequested test profile: "
    + profile
    + "\nResolved test command: "
    + cmd
    + "\n\nIssue body:\n----\n"
    + body
    + "\n----\n",
)
with open(prompt_file, "w", encoding="utf-8") as fh:
    fh.write(filled)
PYEOF
cat >>"$PROMPT_FILE" <<'__OPENCODE_PROMPT_EOF_7f3a2c1e_rules__'
Rules:
- Work only on the requested phase. Do not expand scope.
- Before modifying code, read: CONTRIBUTING.md, docs/ARCHITECTURE.md,
  docs/DEVELOPMENT.md, docs/TESTING.md, and any AGENTS.md present.
- Preserve existing public behavior unless the task explicitly changes it.
- Do not rewrite unrelated code.
- Do not suppress tests merely to get green CI.
- Do not weaken coverage thresholds.
- Do not delete failing tests unless the issue explicitly requires test
  replacement and the reason is justified.
- Do not force push. Do not merge. Do not rebase other branches.
- Do not modify secrets, credentials, or GitHub runner registration.
- Run the requested test profile using the repository's own runner.
  If the profile is unavailable, report tests as NOT RUN.
- If blocked, stop and report the blocker clearly.
- Commit nothing yourself; the executor harness commits and pushes.
  Leave the working tree with only intended changes.
- At completion, output:
  1. changed files
  2. tests run
  3. test results
  4. unresolved risks
  5. whether acceptance criteria appear satisfied (your call is advisory;
     a human reviewer makes the final done/review decision)
__OPENCODE_PROMPT_EOF_7f3a2c1e_rules__
PROMPT_BYTES="$(wc -c <"$PROMPT_FILE")"
echo "prompt bytes: $PROMPT_BYTES"
if [[ "$PROMPT_BYTES" -gt 204800 ]]; then
    fail "prompt exceeds 200 KiB; refusing to invoke OpenCode (shrink the Issue body)"
fi

# -------------------------------------------------------------- opencode ---
# Flags used (--model, --title) are exactly those listed by
# `opencode run --help` on this host. Model travels as one argv entry.
step "invoking OpenCode (model=$MODEL)"
PROMPT_TEXT="$(cat "$PROMPT_FILE")"
OPENCODE_ARGS=(run --model "$MODEL" --title "executor-${PHASE}-issue-${TASK_ISSUE}" "$PROMPT_TEXT")
if [[ "$AUTO_APPROVE" == "1" ]]; then
    OPENCODE_ARGS+=(--auto)
    echo "WARN: --auto permission approval enabled for this run" >&2
fi
set +e
if [[ "$OPENCODE_TIMEOUT_SECS" =~ ^[0-9]+$ ]] && command -v timeout >/dev/null 2>&1; then
    timeout "$OPENCODE_TIMEOUT_SECS" opencode "${OPENCODE_ARGS[@]}" >"$OPENCODE_LOG" 2>&1
else
    if [[ -n "$OPENCODE_TIMEOUT_SECS" ]]; then
        echo "WARN: ignoring malformed OPENCODE_TIMEOUT_SECS='$OPENCODE_TIMEOUT_SECS' (need seconds)" >&2
    fi
    opencode "${OPENCODE_ARGS[@]}" >"$OPENCODE_LOG" 2>&1
fi
OPENCODE_RC=$?
set -e
echo "opencode exit code: $OPENCODE_RC (log: $OPENCODE_LOG)"
if [[ "$OPENCODE_RC" -ne 0 ]]; then
    tail -40 "$OPENCODE_LOG" >&2 || true
    fail "opencode run failed with exit $OPENCODE_RC; see $OPENCODE_LOG"
fi

# ---------------------------------------------------------------- tests ----
step "running test profile: $TEST_PROFILE"
# NUL-separated argv from select_tests.py: elements with spaces (e.g. the
# "not integration" marker expression) survive intact; no shell is involved.
mapfile -d '' -t TEST_ARGV < <("$PYBIN" "$SELECT_PY" --null "$TEST_PROFILE" 2>/dev/null || true)
TEST_RESULT="NOT RUN"
if [[ "${#TEST_ARGV[@]}" -eq 0 ]]; then
    if [[ "$TEST_PROFILE" == "none" ]]; then
        echo "profile 'none': tests intentionally skipped" | tee "$TEST_LOG"
        TEST_RESULT="SKIPPED (profile none)"
    else
        echo "profile '$TEST_PROFILE' has no executable command here" | tee "$TEST_LOG"
        TEST_RESULT="NOT RUN (profile unavailable)"
    fi
else
    echo "command: ${TEST_ARGV[*]}" | tee "$TEST_LOG"
    set +e
    "${TEST_ARGV[@]}" >>"$TEST_LOG" 2>&1
    TEST_RC=$?
    set -e
    echo "test exit code: $TEST_RC" | tee -a "$TEST_LOG"
    if [[ "$TEST_RC" -eq 0 ]]; then
        TEST_RESULT="PASS"
    else
        TEST_RESULT="FAIL (exit $TEST_RC)"
    fi
fi
echo "test result: $TEST_RESULT"

# --------------------------------------------------------------- commit ----
step "committing and pushing (if changed)"
CHANGED_FILES="$(git status --porcelain)"
COMMIT_SHA="NO-COMMIT"
if [[ -z "$CHANGED_FILES" ]]; then
    echo "no changes produced; nothing to commit"
else
    echo "$CHANGED_FILES"
    if [[ "$TEST_RESULT" == FAIL* ]]; then
        fail "tests FAILED; refusing to commit/push. Fix and re-run (next round)."
    fi
    git add -A
    COMMIT_MSG_FILE="$RUN_LOG_DIR/commit-msg.txt"
    {
        echo "chore(automation): ${PHASE} round for issue #${TASK_ISSUE}"
        echo ""
        echo "Phase: $PHASE"
        echo "Task: #$TASK_ISSUE $ISSUE_TITLE"
        echo "Model: $MODEL"
        echo "Test profile: $TEST_PROFILE ($TEST_RESULT)"
        echo "Executor log dir: $RUN_ID"
    } >"$COMMIT_MSG_FILE"
    git commit -F "$COMMIT_MSG_FILE"
    COMMIT_SHA="$(git rev-parse HEAD)"
    echo "committed: $COMMIT_SHA"
    # Plain push only. --force/--delete/--mirror are never used here.
    git push origin "$TARGET_BRANCH"
    echo "pushed to origin/$TARGET_BRANCH"
fi

# -------------------------------------------------------------- summary ----
redact() {
    sed -E -e 's/(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)/***REDACTED***/g' \
           -e 's/(sk-[A-Za-z0-9_\-]+)/***REDACTED***/g' "$1"
}
{
    echo "# Executor summary — $PHASE (Issue #$TASK_ISSUE)"
    echo ""
    echo "- repo: $REPO_ROOT"
    echo "- branch: $TARGET_BRANCH"
    echo "- model: $MODEL"
    echo "- test profile: $TEST_PROFILE -> $TEST_RESULT"
    echo "- commit: $COMMIT_SHA"
    echo "- opencode exit: $OPENCODE_RC"
    echo ""
    echo "## Changed files"
    echo '```'
    if [[ -z "$CHANGED_FILES" ]]; then echo "(none)"; else echo "$CHANGED_FILES"; fi
    echo '```'
    echo ""
    echo "## Reviewer gate (all required before phase is done)"
    echo "- [ ] code pushed"
    echo "- [ ] requested tests pass"
    echo "- [ ] GitHub CI green"
    echo "- [ ] acceptance criteria satisfied"
    echo "- [ ] no blocking review"
    echo "Executor verdict cannot exceed 'review'. Only a human reviewer may mark 'done'."
} >"$SUMMARY_FILE"
redact "$OPENCODE_LOG" >"$RUN_LOG_DIR/opencode.redacted.log" || true

cat "$SUMMARY_FILE"
echo "logs: $RUN_LOG_DIR"
echo "EXECUTOR-OK phase=$PHASE issue=$TASK_ISSUE branch=$TARGET_BRANCH commit=$COMMIT_SHA tests=$TEST_RESULT"
