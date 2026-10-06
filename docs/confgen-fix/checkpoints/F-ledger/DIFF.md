# F-ledger DIFF (proto preview — NOT submitted)

Base: `c9bbc9f8cc0ae94875aaaab180c8488f7ea81f25` (`fix/confgen-1d`).
Proto: `/tmp/fix1d-ledger-proto` (detached HEAD) only; formal line untouched.
Frozen patch: `/tmp/fix1d-ledger-output/F-ledger.patch` (`git diff --binary`, new files included).

## Production (whitelist only, no component impl)

- `confflow/science/confgen/accounting.py`: `attempt_ledger_counts(records, *, issued_history=None)`; `None`=legacy (suppressed always skipped). With history, suppressed whose `(parent_target_id, axis, ordinal)` was ever issued counts as issued `attempted_without_success` + `by_status[suppressed]` + conditional `suppressed_after_issue` (only when >0, else no new key). Doc clarifies retry multi-start stays in `start_statistics`, never ledger.
- `confflow/science/confgen/engine.py`: `_RunState._issued_history:set` (generic, no axis literals, no component import); input gate + retry solver non-None add `(parent_target_id, axis, ordinal)` before realize (exception still issued; policy/suppression gates return before, attempt 0); `finish()` passes history to ledger and recomputes `realization_attempts = attempted - suppressed_skipped(true skip)` (no-gap identical). Leaf/certificate/equations untouched.
- `kernel_records.py`: untouched. `coordination/*`, registry/wire/model/planner/tolerances/fixtures: untouched.

## Tests: `tests/v4/test_confgen_ledger_suppressed_after_issue.py` new, 5 nodes

- `test_suppressed_after_issue_counts_as_issued` (spy kernel直调: 3 issued→2 published+1 suppressed-after-issue; ledger 3/0/1, leaf 2+1, attempts 3, cert ok).
- `test_legacy_counts_without_history_preserves_skip` (unit: old None preserves 1/1, history flips to 2/0/1).
- `test_no_retry_report_has_no_new_key` (no-retry bytes guard).
- `test_stage_error_still_issued_and_not_retryable` (exception issued, never retried).
- `test_retry_cancellation_propagates_without_partial` (probe propagates).

## Verification (measured, no fabrication)

- New 5 passed (1.39s); old impl + new tests: 2 failed/3 passed (failure evidence `old_impl_new_tests_fail.log`).
- Old `retry_statistics` 10 passed (58.05s); old core subset (suppression/conditional) 10 passed (58.10s); real C2 positive-control 1 passed (53.23s).
- Real suppression diff: OLD `issued 18/skipped 12/attempts 18/by {failed 3, pub 15}/without 3` → NEW `issued 20/skipped 10/after_issue 2/by {failed 3, pub 15, suppressed 2}/without 5/attempts 20`; input `20/13/13`, sibling `14/2/2`, sigma `1/0/0`; leaf `15/3/12`, counts/cert/count_details/terminal all identical.
- No-retry bytes: `_ShiftStage` 3-state report sha256 `6f64f6a49…` old==new, ledger keys identical, no new key.
- ruff passed, black passed, mypy passed (accounting+engine).
