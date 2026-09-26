# V4 Repair — Stream C Report (executors / native boundary / binding)

Date: 2026-09-26. Worker: OpenCode MS 1.3 repair subagent C.
Authority: `docs/internal/V4_REPAIR_EXECUTION_FREEZE.md` (takes precedence
over the draft contract decisions) + all supervision corrections received
during the wave, including the shared-contract correction on
`producer_digest` and the native-evidence note on GOAT seed semantics.

Scope kept: only C-owned files were written (list in STATUS §C0). No
existing test was edited. No commit/push/merge/tag/release.

## C0. Files written (exclusive ownership)

- `confflow/execution/work_item_executor.py` — attempt-aware dirs, strict
  checkpoint staging, recovery binding-ownership, typed seed threading,
  `producer_digest` publication to profiles.
- `confflow/execution/native.py` — additive `ResolvedCalculationInputs.seed`
  (typed `int | None`); `StagedArtifact` consumption contract documented.
- `confflow/execution/profiles.py` — additive `ProfileContext.producer_digest`.
- `confflow/execution/profile_standard.py` — output-bound result subjects,
  direct `make_result_id`, measurement-vs-transformation geometry rule.
- `confflow/execution/recovery_standard.py` — binding-owned launch facts,
  adapter-owned syntax delegation.
- `confflow/execution/binding_resolution.py` (NEW) — explicit-argument
  binding resolution + `validate_step_seed`.
- `confflow/execution/confgen_executor.py` (NEW) — chain-mode torsion
  confgen on `confflow.science`.
- `confflow/execution/transform_executor.py` (NEW) — refine/deduplicate/
  filter on `confflow.science`.
- `confflow/science/{__init__,torsion,bonds,cluster}.py` (NEW, exclusive) —
  extracted pure science, no legacy orchestration transitives.
- `confflow/programs/gaussian/adapter.py`, `programs/gaussian/rendering.py`
  — adapter-owned rescue syntax; QST+checkpoint explicit rejection.
- `confflow/programs/orca/adapter.py`, `programs/orca/goat.py` — invented
  `Seed` removed; GOAT seed fail-closed gates.
- `tests/v4/test_repair_executors.py` (NEW) — 19 scoped tests, all passing.

## ROOT CAUSE / FIX / TEST / STATUS (per owned drift)

### 9 — ConfGen/Transform had no real route (all formal paths fell to calculation)

- ROOT CAUSE: no confgen/transform executor existed; application branched
  analysis-vs-rest with a default-ORCA fallback.
- FIX: `ConfgenExecutor` and `TransformExecutor` implement the uniform
  seam `execute(work_item, context, *, should_cancel=None) ->
  WorkItemResult` (same shape as `WorkItemExecutor.execute`). Pure: no
  native launch, no Calculation calls, no legacy imports. Confgen science
  is chain-mode torsion (chains/steps/angles/rotate_side/no_rotate/
  add_bond/del_bond/bond_scale/clash_threshold/max_conformers vocabulary,
  Rodrigues rotation, BFS rotating side, ring refusal, clash filter) from
  `confflow.science.torsion`; bond perception via `science.bonds`
  (`core.bonding` authority). Transform science: deduplicate collapses
  content-identical records only inside
  `(charge, multiplicity, group_key, role, elements)` groups with the
  lowest-id representative (reorder-invariant); refine is topology-grouped
  RMSD dedup (defaults `rmsd_threshold_angstrom=0.25` from
  `RefineOptions.threshold`, `bond_scale=1.2` from
  `topology.BOND_SCALE_FACTOR`, Kabsch from `science.cluster`, mapping-gated
  per `compare_frames`); filter is explicit atom-count/truncation
  selection. Legacy-only features fail closed explicitly (`optimize=true`,
  unknown keys, energy-window/imaginary filtering documented as
  inapplicable: transform ports carry structures only).
- TEST: `test_repair_executors.py` (torsion determinism, seed-missing,
  ring refusal, seeded cap, dedup groups/reorder, refine RMSD/topology,
  filter) — 19 passed.
- STATUS: implemented + scoped-tested. Integration (4-way dispatch in
  application/batch, registry implementation registration) belongs to
  A/D — dependencies C-A1, C-D1 below. No whole-architecture claim.

### 14 — ExecutionBinding was dropped/covered (hardcoded v4-run, empty env)

- ROOT CAUSE: `application/v4_run.py` hardcoded binding id/env and dropped
  planned target/walltime.
- FIX (C part): `execution/binding_resolution.py` with
  `resolve_execution_binding(*, program, planned, defaults,
  adapter_default_executable)` (planned wins, defaults fill gaps,
  env/target/walltime/executable preserved, no filesystem validation so
  remote targets never need local absolute paths) and
  `resolve_remote_target_binding(*, program, handoff_execution,
  target_default_executable, target_env)` (basename-only executable
  carry). Function-level `ItemExecutionContext` keeps
  `execution_binding`; recovery can no longer override it (see 21).
- TEST: planned-wins/remote-basename cases pass.
- STATUS: implemented + scoped-tested. Caller wiring (application/batch/
  remote) is D/E work — dependency C-D2. Concrete signature for D is
  above; no `V4RunRequest`/application import anywhere in the module.

### 17 — Staged checkpoints could pass unused (ORCA ignored, QST ignored)

- ROOT CAUSE: staging skipped pathless artifacts and never required
  checksum/subject/role; adapters were not required to consume.
- FIX: `_stage_checkpoints` fails closed (run-relative locator, role
  `checkpoint`, `sha256` checksum, subject in item inputs required);
  adapters consume or explicitly reject: ORCA raises
  `artifact_unsupported` on any checkpoint; Gaussian renders `%OldChk`
  for standard/IRC and raises `artifact_unsupported` for QST+checkpoint;
  executor pre-launch gate `_require_checkpoint_consumption` enforces the
  ORCA side. Contract documented on `StagedArtifact`.
- TEST: weak-artifact staging rejections, both adapter rejections pass.
- STATUS: implemented + scoped-tested.

### 21 — Recovery rewrote native syntax and overrode machine binding

- ROOT CAUSE: executor merged launch facts into recovery params with
  scientific params winning; route/ModRedundant rewriting lived in the
  recovery policy.
- FIX: executor strips user `executable/env/walltime_seconds/work_dir`
  (records stripped keys) and re-supplies driver-owned facts under
  reserved `_binding_*` keys; `TsRescueScanPolicy._request_kwargs`
  reads binding facts first. Syntax moved to adapter ownership:
  `GaussianProgramAdapter.{rescue_scan_keyword,rescue_freeze_directive,
  ensure_modredundant_keyword,scan_keyword_from_ts}` backed by
  `programs/gaussian/rendering.py`; policy calls adapter methods with
  local wrappers kept only for the unbound instance.
- TEST: syntax-equivalence + binding-override tests pass.
- STATUS: implemented + scoped-tested.

### 22 — Dual seed authority (+ GOAT native-evidence correction)

- ROOT CAUSE: step seed and native GOAT `Seed` were independent; the
  integer `%goat Seed` itself was invented dialect (ORCA 6.1 documents
  no integer Seed; older manuals document a `RANDOMSEED` boolean with
  unverified 6.1 semantics — evidence notes
  `/tmp/confflow-v4-native-evidence-notes.md`).
- FIX: `Seed` removed from `GOAT_BLOCK_KEYS` (now unknown-key error);
  step seed travels typed as `ResolvedCalculationInputs.seed`
  (`validate_step_seed` type gate in binding_resolution; adapter owns
  all native seed vocabulary). ORCA adapter fails closed on GOAT:
  missing typed seed → seed-required error; present seed →
  `native_seed_unresolved` error (no false determinism claim).
  Recovery `_modified_inputs` propagates `inputs.seed`.
- TEST: unknown-Seed rejection, both GOAT gates pass.
- STATUS: implemented + scoped-tested. Native seed rendering is
  UNRESOLVED pending wave-2 G verification against the installed
  binary — dependency C-G1. Existing tests asserting the invented
  dialect now fail (listed in STATUS §F for H).

### 6 (C part) — Standard profile subject chain + SP identity

- ROOT CAUSE: results bound the input id while the output was a new
  entity; parsed-but-unchanged SP geometry minted a produced entity.
- FIX: results bind the output entity in all cases (`subject_id =
  structure.id`); `make_result_id` stamped directly (see producer_digest
  coordination below); measurement rule from native facts:
  `PRODUCED` + content-identical (geometry-digest equality via B's
  domain digest) + single-parent → input record retained
  (`PASSTHROUGH` semantics); genuinely new content → produced entity;
  `NONE` → passthrough record (existing pinned behavior kept).
- TEST: output-subject binding, SP-identity retention, producer-digest
  stability/change tests pass.
- STATUS: implemented + scoped-tested. One existing test
  (`test_result_subjects_and_provenance`, asserting the old input-bound
  subject) now fails — it encodes audit #6's exact violation; left for
  H with evidence (STATUS §F).

### 19 (C part) — Non-injective, attempt-shared work dirs

- ROOT CAUSE: `sanitize_job_name` collides (`s:A:B` vs `s:A_B`), no
  attempt isolation.
- FIX: `hashed_item_slug` (blake2b-120 hex, bounded) +
  `ItemExecutionContext.attempt` + `durable_item_dir`/`attempt_dir`/
  `run_relative_prefix`; sanitize kept for native basenames only
  (presentation). Calculation, recovery driver, confgen report, and
  artifact prefixes use attempt dirs.
- TEST: collision/isolation/bounded-length tests pass.
- STATUS: implemented + scoped-tested. Durable layout cutover
  (`steps/<id>/<hash>_<attempt>` canonicalization, migration of the
  legacy `items/` layout) is D-owned — dependency C-D3.

### Shared-contract coordination: `producer_digest` keyword (with B)

- Coordinated exact keyword: **`producer_digest`** on B's
  `make_result_id(*, step_id, work_item_id, kind,
  subject_structure_id, discriminator, program, method, basis,
  producer_digest)` (B landed; required, validated as the producing
  item's digest).
- C passes the real `WorkItem.semantic_digest` verbatim: executor sets
  `ProfileContext.producer_digest = work_item.semantic_digest`
  (new additive field on C-owned `ProfileContext`); the standard
  profile calls `make_result_id(...,
  producer_digest=context.producer_digest)` directly — no introspection,
  no value_digest/path/ordinal substitute. Retries/reordering of the
  same semantic item retain refs; changed science moves the digest and
  mints new refs. Contexts without a digest (pre-contract unit
  fixtures) emit without `result_id`, honoring B's `Optional` field.
- Ensemble/path profiles do not yet stamp `result_id` (files outside
  C ownership) — dependency C-L1 for the lead.

## Integration dependencies (explicit, for the lead)

- C-A1: registry must map all four capabilities to implementations
  (`resolve_executor(capability)` etc.); C exposes
  `ConfgenExecutor`/`TransformExecutor` + existing calculation seam.
  A's `TestGoatSeedValidation` (invented `Seed`) conflicts with the
  evidence correction — A must drop/adjust it.
- C-B1: DONE both sides for standard results (`producer_digest`
  keyword coordinated and landed). Remaining: ensemble/path profile
  stamping (unowned files — needs lead assignment).
- C-D1: 4-way dispatch on `planned.executor` in application/batch to
  the uniform seam (D owns the call sites).
- C-D2: `resolve_execution_binding` / `resolve_remote_target_binding`
  signatures above are the binding call points for D/E.
- C-D3: attempt-dir canonicalization + legacy layout migration (D).
- C-D4: remote seed threading uses typed `inputs.seed`; envelope
  versioning is E-owned.
- C-G1: GOAT native seed semantics unresolved (RANDOMSEED boolean vs
  no 6.1 Seed) — G must verify against `/opt/orca611/orca` and the
  official manual URLs in the evidence notes before any seed rendering
  is claimed. Gaussian real IRC fixtures exist (`/opt/g16/tests/`)
  for G's parser work; C implemented no parser rewrites.
- C-L1 (lead): assign `profile_ensemble.py` /
  `profile_path_endpoints.py` result_id stamping.

## Verification evidence

- NEW scoped: `tests/v4/test_repair_executors.py` — 19 passed.
- Lint: ruff clean on `confflow/science`, `confflow/execution`,
  both adapters, `gaussian/rendering.py`, `orca/goat.py`, scoped tests.
- Types: mypy clean on all new/changed C files (11 files).
- Existing suites: `test_v42_profiles_checks + test_v42_adapters +
  test_v42_recovery + test_v42_coverage_recovery` — 183 passed, 1
  failed (the audit-#6-violating assertion, documented above);
  `test_v45_goat + test_v42_coverage_contracts` — 119 passed, 3 failed
  (invented-`Seed` assertions, documented above);
  `test_v42_executors` — 31 passed, 1 failed
  (`test_target_moves_environment_digest`: sibling's freeze-#7
  target-exclusion change in `contracts.py`, not a C file).
- Transitive-dependency probe (scoped test): importing
  `confflow.science` + both new executors pulls no
  `confflow.blocks/*`, `workflow`, `application`, `remote`, or
  `analysis` modules — CLEAN.
- No whole-architecture repair claimed; no E2E fabricated; no tests
  edited; no commits.
