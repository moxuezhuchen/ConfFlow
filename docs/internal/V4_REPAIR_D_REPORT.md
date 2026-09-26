# V4 Repair — D Report: persistence / resume / publication / application

Owner: repair subagent D (wave 1). Scope: `execution/batch.py`,
`persistence/*`, `domain/{publication,step_result,completion}.py`,
`application/v4_run.py` (including importer), NEW
`tests/v4/test_repair_persistence.py`. No commits/pushes. No files outside
ownership modified; no existing tests modified.

Status: owned implementation complete; 24/24 new scoped tests pass; ruff
check + format clean on all owned files; mypy clean on owned files (3
remaining errors are C-owned files, listed below). Existing suites show
ONLY intended fail-closed breakage (enumerated below for wave-H). No whole
architecture repaired is claimed: unit/scoped evidence only; integration
gate belongs to the lead.

## 1. Authority alignment (supervisor corrections applied)

- No sibling-API availability fallbacks anywhere in owned code: no
  `getattr`/`try-import`/`hasattr` shims, no direct
  `PROFILES`/`CHECKS`/`RECOVERIES` reads in application (all resolution via
  `registry.resolve_program/profile_implementation/check_implementation/
  recovery_implementation`), no application-owned analysis definition /
  provenance / aggregation runtime, no parallel `_run_analysis_aggregate`
  path. Analysis uses the same generic `BatchStepExecutor` + registered
  executor; until wave-2 F registers its item adapter it fails closed.
- Registry API migrated to A's atomic design: `resolve_executor`,
  `executor_implementation(capability)` (returns the CLASS; application
  instantiates directly: `registry.executor_implementation(...)()`), no
  dynamic fallback. Test registration uses atomic
  `register_executor(contract, class)`.
- C binding API used exactly:
  `resolve_execution_binding(*, program, planned, defaults: BindingRequestDefaults,
  adapter_default_executable)`.
- `jsonschema.validate` used directly (required dependency, 4.26.0); no
  minimal-validation fallback.
- Producer gating lives in B materialization (`StepOutputs.status` +
  assembly scoped errors); application only propagates errors scoped to
  the current step and refuses skipped (waiting) steps. No second
  application policy engine (previous `_gate_producer_state` deleted).
- B's `StepOutputs.status=None → COMPLETED` shim is NOT relied on: D
  always stamps the actual `StepStatus` (+ `producer_step_digest` +
  `item_statuses`). Initial workflow inputs stay in `RunInputs`, never in
  `StepOutputs`.
- B's `make_result_id` new signature honored (`producer_digest` =
  producing `WorkItem.semantic_digest`, required kwarg); D never invents
  ids, only gates and round-trips them.
- Lead-owned environment work respected: D created NO permanent
  application-only pure-environment authority. The interim
  `_pure_implementation_environment` was DELETED; pure steps fail closed
  with explicit `D-ENV-1` pending the lead helper
  (`/tmp/confflow-v4-environment-api.md` not yet published at write
  time). Calculation environments use C's `EnvironmentMeasurer` directly.
- Import durability per supervision notes: exclusive `os.link`
  arbitration (concurrent first imports converge on one winner, losers
  adopt positionally after geometry verification), file+directory fsync,
  strict map shape/count/type checks, never-overwrite winner IDs.

## 2. ROOT CAUSE / FIX / TEST / STATUS

### D-1 Importer positional identity (audit 12, freeze 1)
- ROOT CAUSE: `import_xyz` minted `xyz:<index>` ids; position was
  scientific identity, colliding across files and reorders.
- FIX (`application/v4_run.py`): fresh imports mint UUID hex; explicit
  `entity_ids` sequence binds caller entities with exact-count and
  uniqueness validation. `source_name` stays provenance-only.
- TEST: `TestOpaqueImporter` (6 tests: independence, distinct
  duplicates, explicit binding, count/dup failures, malformed).
- STATUS: implemented + passing. Existing `TestXyzImporter`
  (`xyz:0` assertions) intentionally fails → wave-H.

### D-2 Durable import maps + concurrent arbitration (audit 12/18, freeze 1)
- ROOT CAUSE: no persistence of import identity; resume re-minted ids or
  guessed by geometry.
- FIX (NEW `persistence/imports.py`, exported in `persistence/__init__`):
  per-input maps `<run_root>/imports/<name>.json` with source-content
  digest + ordered entity/geometry digests; exclusive-link publication
  (no overwrite of winner ids on same digest; changed digest fails
  closed as new generation); `resolve_imported_structures` adopts winner
  ids positionally with geometry verification; inputs without raw bytes
  validate against an entity snapshot in application.
- TEST: `TestImportMaps` (7 tests incl. real 4-process convergence via
  `ProcessPoolExecutor`, winner-stability, changed-bytes/corrupt
  failures).
- STATUS: implemented + passing.

### D-3 Item-backed resume, no published-file shortcut (audit 2, freeze 12)
- ROOT CAUSE: `load_published_step_result` early-`continue` skipped
  current item reuse checks; changed inputs/failed steps reused stale
  snapshots.
- FIX (`application/v4_run.py::run`): early return deleted. Every step
  re-assembles current items and executes through
  `BatchStepExecutor.execute_step_resumable`; the store decides per-item
  reuse; completed items are never re-executed (verified: second run
  issues zero executor calls).
- TEST: lifecycle test asserts 4 calls first run, 0 new calls second
  run with freshly minted UUID inputs reloaded via `import_sources`.
- STATUS: implemented + passing.

### D-4 Assembly/status gating (audit 3, freeze 4)
- ROOT CAUSE: assembly errors ignored; empty item sets completed
  vacuously; producer failed/partial state consumed blindly.
- FIX: application raises `DomainError` on errors scoped to the current
  step (or unscoped) and on skipped (waiting) steps — never executes
  them. Failed/cancelled/partial producer gating is B's assembly via
  stamped `StepOutputs.status` (verified: undeclared-partial
  consumption raises `cardinality_error` from assembly). Legal-empty
  sets flow through the generic batch path and complete explicitly.
- TEST: existing assembly suites (unmodified) + lifecycle run; negative
  gate covered by `test_unregistered_executor_fails_closed` path and
  B-owned tests.
- STATUS: implemented; application-side propagation passing. Full
  partial-matrix proof belongs to integration.

### D-5 Strict publication, no gap exception (audit 10/18, freeze 12)
- ROOT CAUSE: `batch.py` published step results covering non-durable
  items with a diagnostic (`publication_durability_gap`).
- FIX: any non-durable item raises `PersistenceError` (no publish);
  `verify_for_publication` failure always raises (no gap smuggling).
- TEST: `test_invalidated_item_refuses_to_publish` (COMPLETED row with
  mismatched digest → invalidated → raise; no `step_result.json`).
- STATUS: implemented + passing. Existing gap-expecting test
  (`test_v43_coverage.py`) intentionally fails → wave-H.

### D-6 Generic executor routing, one lifecycle (audit 9, freeze 9/15)
- ROOT CAUSE: application branched analysis vs calculation with ORCA
  fallback; confgen/transform misrouted.
- FIX: single `_run_step` for all capabilities — registry descriptor
  (`resolve_executor`), registry implementation class
  (`executor_implementation(...)()`), `requires_adapter` decides
  native-backed (program/profile/checks/recovery/binding/measured env)
  vs pure (currently fail-closed D-ENV-1). Batch constructor takes the
  resolved executor; `_bind_recovery` kept ONLY for direct-batch
  callers until all callers resolve via registry (D-C-5).
- TEST: lifecycle tests run calculation through the generic path;
  `test_unregistered_executor_fails_closed` proves fail-closed dispatch.
- STATUS: implemented + passing for registered capabilities.

### D-7 Actual binding/environment, real attempt (audit 14/19, freeze 11)
- ROOT CAUSE: hardcoded `v4-run`/empty-env/`environment=None` binding;
  `sanitize` workdirs without attempt isolation; attempt never reached
  context.
- FIX: C's `resolve_execution_binding` with planned-wins + executables
  defaults + adapter default; real `EnvironmentMeasurer` environments
  for native steps; batch passes the real durable `current_attempt`
  into `ItemExecutionContext` (`replace(context, attempt=...)`) at both
  claim sites (C's `attempt` field + `attempt_dir` now consume it).
- TEST: `EchoExecutor.attempts == [1,1,1,1]`; native-step reuse keys on
  measured digests.
- STATUS: implemented + passing (D-side; C owns dir layout semantics).

### D-8 RunState lifecycle (audit 18, freeze 12)
- ROOT CAUSE: application never touched `RunState` (lifecycle truth
  missing; crash-E repair undiscoverable).
- FIX: load-or-init with definition-digest fail-closed on generation
  change; `ensure_step` for all plan steps; RUNNING before each step;
  terminal transition with `detect_published` digest after each
  publication; `save_run_state` at every transition. No second lock
  runtime invented (per-item claims + RunState are the mechanism).
- TEST: lifecycle test asserts per-step COMPLETED + digests in
  `run_state.json`.
- STATUS: implemented + passing.

### D-9 Schema-true durable manifest (audit 8)
- ROOT CAUSE: hand-built manifest (`step_result_digest` string,
  illegal counts, wrong analysis shape, empty artifacts, memory-only).
- FIX: steps carry real `detect_published` digests, exact
  `{completed,failed,cancelled}` counts, full diagnostics; analyses are
  `{capability, step_id}`; artifacts only with `sha256:` checksums +
  run-relative locators; validated with `jsonschema` against
  `run_result_json_schema()`; atomically written to
  `run_root/run_result.json` (dir fsynced); report mirrors durable bytes.
  Run status preserves partial/cancelled (no longer collapsed to failed).
- TEST: lifecycle test validates the written file against the actual
  schema.
- STATUS: implemented + passing. Final manifest authority stays with
  lead integration.

### D-10 Runtime ResultRef gates (coordination)
- ROOT CAUSE: `result_id` optional field with no runtime enforcement;
  store/publication rebuilds dropped it (`_build_scientific_result`,
  `_scientific_result` ignored `result_id`), so resume silently lost B
  identity.
- FIX: strict `result_id` round-trip in both rebuilds (corrupted →
  `CorruptStateError`, never healed); gates rejecting missing ids and
  duplicates at: `domain.publication.verify_step_publication`
  (→`PublicationError`), `persistence.publish_step_result`
  (→`PersistenceError`), `load_published_step_result`
  (→`CorruptStateError`, import gate), store `complete`/`fail`/
  `_record_cancelled` (→`PersistenceError`, commit gates),
  `_result_from_dict` (→`CorruptStateError`, store-read gate). Empty
  sets pass vacuously.
- TEST: `TestResultIdentityGates` (5 tests) + close/reopen identity
  test over real store files.
- STATUS: implemented + passing. Forces C/F emitter stamping (pending,
  D-C/F-1).

### D-11 environment=None prohibited (coordination, freeze 7)
- ROOT CAUSE: `None` digests compared equal → unknown stood in for
  verified equivalence.
- FIX: `execute_step_resumable` raises `PersistenceError` when
  `request.environment is None`, before validation, on every path.
- TEST: `test_missing_environment_is_prohibited`.
- STATUS: implemented + passing. Existing env-less batch tests (3 in
  resume/reuse suites) intentionally fail → wave-H.

## 3. Concrete integration dependencies (for lead)

- D-A-1 (RESOLVED): atomic registry with `resolve_executor`,
  `executor_implementation` (CLASS), `resolve_program`,
  `profile/check/recovery_implementation` — all consumed directly.
- D-C-1 (RESOLVED): `resolve_execution_binding` + `BindingRequestDefaults`
  consumed with exact signature; planned-wins preserved.
- D-C-4 (RESOLVED): `ItemExecutionContext.attempt` + `attempt_dir` —
  D passes the real durable attempt at both claim sites.
- D-B-1 (RESOLVED): `StepOutputs.status`/`producer_step_digest`/
  `item_statuses` consumed; no `None`-status reliance.
- D-B-2 (ALIGNED): `make_result_id(..., producer_digest=...)` honored in
  D tests; D never mints production ids.
- D-ENV-1 (PENDING, lead-owned): pure-implementation environment helper
  (`execution/contracts.py`, `execution/environment.py`,
  `/tmp/confflow-v4-environment-api.md` not yet published). Pure steps
  fail closed explicitly; interim helper DELETED, not duplicated.
- D-C/F-1 (PENDING): emitters must stamp `result_id` (C profiles, F
  analysis) or production commits/publications fail closed at D gates.
- D-F-1 (PENDING, wave 2): analysis execute-item adapter with the
  `execute(work_item, context, *, should_cancel)` seam. Note:
  `AnalysisExecutor.execute` takes `(AnalysisInputs, definition)`, NOT
  the work-item seam — F must bridge it; D will not drive it per-item.
  Default registry currently maps analysis → core class, which the
  generic batch path cannot yet consume (correctly fail-closed today
  via D-ENV-1 + seam mismatch).
- D-C-5 (PENDING, cleanup): remove `BatchStepExecutor._bind_recovery`
  once remote worker (E) resolves recoveries via
  `registry.recovery_implementation(..., adapter=...)`.
- D-LEAD-1: CLI (`v4cli.py`, lead-owned) should pass raw XYZ bytes as
  `V4RunRequest.import_sources` so same-byte resumes reload persisted
  IDs; finalize `run_result.json` filename; own entry cutover.
- D-H-1 (wave-H test updates, all intended, none edited by D):
  `TestXyzImporter` (`xyz:N` gone), gap-expecting coverage test,
  unstamped-result fixtures in `test_v43_store`/`test_v43_publication`
  (fail with `identity-less`), env-less batch tests (fail with
  measured-environment prohibition), analysis/application E2E doubles.

## 4. Observed drift outside ownership (no patches made)

- `remote/transport.py:144` builds `result_id=f"{kind}:{subject}"`
  (value/subject-derived identity) — E-owned, flagged for wave-2.
- mypy on owned files surfaces 3 errors inside C-owned
  `confgen_executor.py`/`transform_executor.py` (`no-any-return`) —
  not edited, flagged for C.
- `StepOutputs.effective_status` `None→COMPLETED` shim still present
  in B's assembly at write time (B reopened to remove); D does not
  depend on it.

## 5. Verification evidence

- NEW `tests/v4/test_repair_persistence.py`: 24 passed.
- `ruff check` + `ruff format`: clean on all 8 owned files.
- `mypy` on owned files: no owned errors.
- Existing suites sampled WITHOUT modification:
  `test_v46_application` + `test_v43_publication` + `test_v43_store`:
  56 passed / 26 failed — every sampled failure is one of the intended
  forcing functions (identity-less fixtures, `xyz:N` assertions).
  `test_v43_resume` + `test_v43_reuse`: 42 passed / 3 failed — all 3 are
  exactly the environment-None prohibition.
- Real-process evidence: 4-way `ProcessPoolExecutor` import race
  converges on one ID sequence; store close/reopen round-trips
  `result_id`; second application run issues zero executor calls.
