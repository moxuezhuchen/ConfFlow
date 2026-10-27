# Review fixes 2026-10-27 (GPT review of c89a9bdd)

Branch `refactor/review-fixes-20261027`, worktree `/opt/cf-worktrees/review-20261027`, based on
main f9fc5be1. All five findings still reproduce on f9fc5be1 with the review probe
(`/tmp/claude-0/-opt-ConfFlow/74034c76-7f66-4941-a148-5afd6c743456/scratchpad/probes.py`):

```
SCRIPT_STALE failed completed [('He',)]
REFINE_PERMUTATION without_topology 1 with_topology 2
OPTIONAL_RELEASE published 2 overall_release done per_state ['done', 'deferred']
DEFINITION_RACE completed completed different_definitions True
QUOTA_ORPHAN child_alive True second_full_capacity_lease True
```

The review's AP-002 / AP-107 gate failures are already fixed on main (clean-tree scan is empty).

## Rules for every task

- Work only inside the worktree; edit only the files the task owns. No commits, no pushes.
- Python: `/opt/ConfFlow/.venv/bin/python`. No pytest-xdist.
- Each task adds one new regression test file (named below) that fails on f9fc5be1 and
  passes after the fix. Base the test on the matching probe function.
- No new cross-layer imports (AP-002); execution modules must not import `confflow.core`
  (RT-065); persistence must not import application.
- Keep the change small. Do not touch `tools/loc_budget.json`; the integrator updates it.
- Acceptance for every task, run from the worktree:
  ```
  venv=/opt/ConfFlow/.venv/bin
  $venv/python -m pytest -q -p no:cacheprovider <new test> <listed existing tests>
  $venv/ruff check <owned files>; $venv/black --check <owned files>; $venv/mypy confflow
  ```
- Kill any background shells you started before finishing. Report: files changed, test
  output tail, and any decision you had to make.

## Task A [P1] optional-donor release status after merge

Owned: `confflow/execution/confgen_search_worker.py`, `confflow/execution/confgen_search_run.py`,
new `tests/v4/test_confgen_optional_release_merge.py`.

Problem: `_merge_donor_runs` copies string stats from the first sub-run only, so
`release` is `done` when another sub-run was `deferred`; publication then stamps every
conformer `search_release=done` and skips the `confgen_release_incomplete` warning.

Fix:
1. Each published structure carries its owning sub-run's release status (owner index from
   `deduped.owner`). Put it into the per-record summary data that `confgen_search_run`
   reads (key `release`), and set member metadata `search_release` from
   `rec.get("release", release)`.
2. Merged `stats["release"]` (values: off, not_needed, done, failed, deferred, partial):
   all sub-runs equal → that value; mixed → `deferred` if any deferred, else `partial` if
   any partial or failed, else `done` if any done, else `not_needed`/`off`.
   No new enum values.
3. The incomplete/failed warnings in `confgen_search_run` (and the stderr warning in the
   worker) must fire whenever any published member is not fully released. Add
   per-state release to the `confgen_release_incomplete` details.

Existing tests: `tests/v4/test_confgen_optional_donor_step.py` (probe imports its helpers
`_toy_graph, _rec, _geom, _kcal, _run_of, _job`).

## Task B [P1] script retry publishes stale outputs

Owned: `confflow/execution/script_executor.py`, new `tests/test_script_retry_outputs.py`.

Problem: the item dir is keyed by inputs, so a retry reuses it; `_collect_structures` /
`_collect_summary` only check that the declared file exists. A failed attempt's
`final.xyz` is published when the next attempt exits 0 without writing it.

Fix: before each launch, delete the declared output channel files (structures, summary,
and any declared artifact files) inside `item_dir`, using `_channel_path` so paths stay
confined to the item dir. Leave other files (script checkpoints) in place: resume
state survives, final products must come from this attempt. Check docs on script steps
(`grep -rn "outputs" docs | grep -i script`) and update the doc sentence if it describes
resume behaviour.

Existing tests: `tests/test_script_steps.py`.

## Task C [P1] quota reclaims a lease while group members still run

Owned: `confflow/execution/quota.py`, new `tests/test_server_quota_orphan_group.py`.

Problem: `_group_alive` returns False as soon as the leader is gone or mismatched, so a
lease whose child processes still run is reclaimed and a second full-capacity lease is
granted.

Fix: a lease is dead only when its whole process group is gone. When the leader is gone,
on POSIX with a recorded `pgid > 1`, check for any live non-zombie process in that group
(psutil scan with `os.getpgid`, fall back to `os.killpg(pgid, 0)`); only when none is
left return False. Non-POSIX keeps the leader-only rule. Also check whether the normal
release path (launcher/supervisor after the leader exits) can release while group members
still run; report it, fix only if it lives in `quota.py`.

Existing tests: `tests/test_server_quota.py`.

## Task D [P1] concurrent runs with different definitions on one run root

Owned: `confflow/application/v4_run.py`, `confflow/persistence/arbitration.py`,
new `tests/v4/test_run_definition_race.py`.

Problem: `_load_or_init_run_state` checks the definition digest before
`arbitration.begin_generation` takes the generation lock; `begin_generation` does not
re-check, so two different definitions both finish `completed` in one root.

Fix:
1. `begin_generation` gains `expected_definition_digest` check under `_generation_lock`:
   if the ledger already records a different non-empty `definition_digest`, raise a
   persistence error (new `DefinitionMismatchError(CorruptStateError)` or similar, exported
   in `__all__`) without writing anything.
2. In `_run_generation`, after `begin_generation` succeeds, reload the run state and run
   the same digest check again (state from before the lock may be stale), then save.
   Map the persistence error to the existing `DomainError` message
   ("run root already holds a different workflow definition ...").
3. Same-definition reruns and legacy roots without a digest must still work.

Existing tests: `tests/v4/test_terminal_arbitration.py`,
`tests/v4/test_terminal_arbitration_races.py`, `tests/test_v4_artifacts_rejections.py`.
The race test uses the probe's `DelayedApplication` pattern; expect exactly one run
completed and the other rejected with the definition error.

## Task E [P2] refine grouping splits renumbered topologies

Owned: `confflow/execution/transform_executor.py`,
new `tests/v4/test_transform_refine_topology_permutation.py`.

Problem: `_topology_identity` puts the raw `working_topology` adjacency in the grouping
key, so the same molecule with renumbered atoms lands in different groups before graph
isomorphism runs.

Fix: replace the raw adjacency in the key with a renumbering-invariant fingerprint, e.g.
the sorted multiset of `(element, sorted neighbour elements)` per atom. Equal fingerprints
do not prove isomorphism; first confirm the later comparison in `_refine` uses the
working topology (and `topology_bonds`), so different topologies with equal fingerprints
are still kept apart. Keep `topology_patch` in the key unless the patch is also shown
to be compared later.

Existing tests: `tests/v4/test_transform_refine_identity.py`,
`tests/v4/test_transform_refine_topology_bonds.py`,
`tests/v4/test_transform_refine_symmetry.py`, `tests/v4/test_refine_connectivity_report.py`.

## Integration (main agent)

1. Review each diff; rerun the probe from the worktree (expect: `failed failed` or no
   stale He; `1 1`; release not `done`; one rejected; `correctly_blocked`).
2. Update `tools/loc_budget.json` to measured values if AP-107 trips.
3. Full suite with `JOBDESK_V2_SRC` at the pinned JD sha, sharded manually; `mypy confflow`;
   CI black version for formatting.
4. One commit per task, push `refactor/review-fixes-20261027`, open one PR.
