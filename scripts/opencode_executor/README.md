# OpenCode self-hosted executor

One bounded refactor phase per `workflow_dispatch`, executed by OpenCode on
the WSL self-hosted runner, tested, committed, and pushed for human review.

## Architecture

```text
ChatGPT / Human
→ Actions → OpenCode Executor → Run workflow (task_issue, phase, model, test_profile, target_branch)
→ GitHub routes to self-hosted runner (label opencode-executor)
→ runner _work checkout (isolated from developer working trees)
→ scripts/opencode_executor/run_task.sh
→ validate inputs → read Issue → branch → opencode run → test profile → commit/push
→ GitHub CI on the pushed branch
→ human reviewer checks acceptance criteria → next phase
```

GitHub (Issues + branches + PRs + state file) is the persistent memory.
Every round is a fresh OpenCode session; nothing depends on session memory.

## Files

| Path | Role |
| --- | --- |
| `.github/workflows/opencode-executor.yml` | Dispatch-only workflow |
| `scripts/opencode_executor/run_task.sh` | Executor entry point |
| `scripts/opencode_executor/validate_input.py` | Strict input allowlists |
| `scripts/opencode_executor/select_tests.py` | Profile → real test command |
| `scripts/opencode_executor/prepare_runner.sh` | Host preflight (no tokens) |
| `.github/refactor/` | State schema + example + program README |
| `.github/ISSUE_TEMPLATE/opencode-phase-task.md` | Task Issue template |
| `tests/test_opencode_executor.py` | Validator/mapping/workflow tests |

## One-time setup

Already done by this change: workflow, scripts, schema, templates, tests.

Manual (human only):

1. GitHub repo → Settings → Actions → Runners → New self-hosted runner
   (Linux / X64 on this host; `uname -m` = x86_64).
2. In WSL, run the exact `./config.sh` / `./run.sh` commands GitHub shows,
   adding the label `opencode-executor`. Keep the default `_work` folder.
3. Confirm `gh auth status` and OpenCode provider/model auth for the
   runner user. Or run `scripts/opencode_executor/prepare_runner.sh` to check.
4. Smoke test with `test_profile=none` before real phases.

The registration token is one-time, expires fast, and must never enter the repo.

## Daily usage

Actions → OpenCode Executor → Run workflow, e.g.:

```text
task_issue    = 123
phase         = P3
model         = opencode-go/muse-spark-1.3-contributor
test_profile  = full
target_branch = automation/jobdesk-v3-P3   (empty = automation/jobdesk-v3-<phase>)
```

Local equivalent (same validation, same script):

```bash
scripts/opencode_executor/run_task.sh \
  --task-issue 123 --phase P3 \
  --model opencode-go/muse-spark-1.3-contributor \
  --test-profile full --target-branch automation/jobdesk-v3-P3
```

## Test profiles (real mappings, `docs/TESTING.md` + CI parity)

| Profile | Command | Notes |
| --- | --- | --- |
| `none` | (skip) | Smoke/setup runs |
| `fast` | `./scripts/test.sh -q -m "not integration" --ignore=tests/test_install_release_wheel.py` | Excludes installer-pinned test like CI's non-3.12 matrix |
| `full` | `./scripts/test.sh -q` | Whole suite (2474 tests at time of writing) |
| `integration` | `./scripts/test.sh -q -m integration` | `@pytest.mark.integration` only |
| `gui` | (error) | No GUI suite exists → explicit failure, never silent skip |

## Safety contract

- `workflow_dispatch` only — PRs can never auto-run on the host.
- Inputs ride via `env:` and are re-validated (charset allowlists); the model
  is one argv entry to `opencode run` (`--model`, verified against
  `opencode run --help` v2.0.16); raw commands are never accepted.
- One run at a time: Actions `concurrency` + host `flock`.
- Runner `_work` checkout is enforced; developer trees are never touched.
- Dirty tree → abort. No `reset --hard`, no `clean -fd`, no rebase, no merge.
- Branches must match `automation/*`; `main`/`master` are refused twice
  (validator + executor). Push is plain `git push` — never `--force`.
- Logs live outside the repo and are secret-redacted before upload.
- Existing CI, coverage gate (`fail_under = 85`), lint, and type checks are
  untouched. `gui` fails loudly instead of weakening any gate.
