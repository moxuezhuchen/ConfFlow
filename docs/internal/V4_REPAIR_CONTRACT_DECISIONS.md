# ConfFlow V4 Repair — Shared Contract Decisions (Freeze, reconciled)

Date: 2026-09-26. Baseline: `bfdae1c`, branch `feat/workflow-v4-greenfield`.
Authoritative sources (in precedence order):
1. `docs/internal/V4_REPAIR_EXECUTION_FREEZE.md` — supervising main-agent freeze.
   **Where this file and the freeze differ, the freeze is authoritative.**
2. `docs/internal/V4_ARCHITECTURE_REPAIR_PLAN_2026-09-26.md` — COMPLETE audit/plan
   (22 drifts, §1–§15).
3. This file — lead-reconciled implementable contracts for concurrently
   implementing workers A/B/C/D (wave 1) and E/F/G/H (wave 2).

Scope: **documentation only**. No production/test code is changed by this file.
No agents spawned. No commit/push. No credentials. No V5, new runtime/manager,
architecture allowlist, merge/tag/release, or independently authored
intermediate E2E data.

Reconciled changes vs the first draft (stale content removed):
`{geometry_digest: entity_id}` import map, `start_index` identity, `id_prefix`
mechanism, and tuple-returning `import_xyz` are **forbidden**; the kept
signature is `import_xyz(text, *, source_name="<input>",
entity_ids: tuple[str, ...] | None = None) -> StructureSet`. The duplicate
`ProducerStatus` authority is removed (`StepOutputs` carries `StepStatus`).
The `per_group`/`single_global` analysis aggregation setting is removed
(default is ONE whole-set aggregation WorkItem over existing SINGLE bindings).
`resolve_execution_binding(planned, request: V4RunRequest)` in
`execution/contracts.py` is removed; binding resolution lives in a NEW
C-owned execution module taking domain/execution types only. The B-owned
`durable_item_dir` with b32 names is removed; C creates bounded hashed item
dirs + attempt children from D-passed attempt numbers. `SEED_NOT_PERMITTED`
is removed (stochastic gating follows actual scientific capability, incl.
GOAT). The opaque-restriction rule is replaced (opaque must actually execute
if published). A no longer edits C-owned profile/recovery/program files.
`execution/contracts.py` is A-owned exclusively in wave 1 (C proposes).
The remote V2 no-edit freeze is replaced (envelope MUST version on semantic
change; E owns all remote files in wave 2). Global "zero-or-many results
fail" is replaced by the per-ID cardinality rule. Target locator is excluded
from environment reuse identity under a versioned rule. The "JobDesk
unavailable" issue is replaced by the actual in-scope consumer paths.

---

## 0. Frozen layering (applies to all waves)

```text
WorkflowDocument V4 → Compiler → ExecutionPlan → typed Binding materialization
→ WorkItem → Calculation / ConfGen / Transform / Analysis Executor
→ WorkItemResult → durable item commit → StepResult assembly + verified publication
→ downstream Binding materialization → … → RunResultManifest durable publication
→ JobDesk
```

1. Scientific Definition / Execution Binding / Scheduler Policy are three
   disjoint axes. Science never sets `executable/env/target/workdir`; scheduler
   never sets scientific identity; environment reuse identity is §6 below.
2. Structure / Result / Artifact / Binding / WorkItem / StepResult are typed
   semantic objects. Paths, filenames, list indices are never identity.
3. `WorkItem` is the minimum execute / schedule / resume unit
   (`confflow/domain/work_item.py:174`). Batch, IRC fan-out, GOAT ensemble are
   expressed as WorkItems/StructureSets, never by dynamic DAG expansion.
4. Gaussian/ORCA native syntax is owned exclusively by `ProgramAdapter`
   (`confflow/execution/native.py` + `confflow/programs/{gaussian,orca}/*`).
   `ResultProfile`, `Check`, `Recovery` are independent authorities (C-owned
   implementations; A-owned descriptors).
5. Durable order is frozen per `confflow/domain/publication.py:8-20` (8 stages):
   item executed → artifacts written → checksums verified → **item result
   durable** → step assembled → step published → run-state updated → downstream
   eligible. Item commit always precedes step publication. No exceptions for
   analysis, remote, or partial steps. No gap exception. Status-only blocked
   diagnostics are lifecycle/report data, never counterfeit publication.
6. Cross-step artifacts travel only as `ArtifactRef + checksum + subject
   identity + Binding`. `chk_from_step`, filename/dir pairing,
   `result.xyz/output_path` are never production truth.
7. local/remote share one executor family + `ProgramAdapter`; remote is
   transport/staging only. The local/shared-fs transport is a testable delivery
   implementation, not proof of cross-host execution.
8. Analysis is a pure executor (`confflow/analysis/executor.py:222`), never a
   step-level bypass, never launches Gaussian/ORCA.
9. Producer Contract is the only scientific interface to JobDesk
   (`confflow/producer/contract.py`); JobDesk edits/submits/views only.
10. All formal CLI/application/control paths enter V4; V2/V3 are
    migration/read-only (cutover owned by final lead, wave 3).

---

## 1. Stream A — Capability registry / compiler (drifts 9, 20; supports 1, 5, 14, 21, 22)

### 1.1 Single authority (FROZEN)

- Authority: `confflow/execution/registry.py:42` (`ExecutionRegistry`) built by
  `build_default_registry()`. `default_registry()` is read-only.
- Exposed resolvers (A implements; exact names fixed unless A documents
  equivalents before integration):
  `resolve_executor(capability)`, `resolve_profile(name)`,
  `resolve_check(name)`, `resolve_recovery(name, *, adapter=None)`,
  `resolve_program(name)`.
- Descriptor registration and implementation registration are ONE authority.
  Existing `PROFILES`/`CHECKS`/`RECOVERIES` dicts and program adapters may
  supply implementations, but application/batch/remote MUST resolve through
  the registry; independent consultation of those dicts is a drift.
- **A MUST NOT edit C-owned profile/recovery/program files** to accomplish
  this (`execution/{native,profiles,profile_standard,recovery,
  recovery_standard}.py`, `programs/gaussian/adapter.py`,
  `programs/orca/adapter.py` are C-owned in wave 1).
- Consumers that MUST read the registry and nothing else:
  `workflow/v4/validation.py` (`validate_definition`),
  `workflow/v4/plan.py` (`build_execution_plan`),
  `producer/contract.py` (capability sections),
  `application/v4_run.py` and `remote/worker.py` (via D/E dispatch, not direct
  dict imports).
- No `spec/instance` dual table, no default-ORCA fallback, no
  "non-analysis ⇒ calculation" branch, no `role`/`TaskName` dispatch anywhere.

### 1.2 Four executors on one WorkItem contract (FROZEN)

- Closed vocabulary stays `ExecutorCapability`
  (`calculation | confgen | analysis | structure_transform`).
- `ExecutorContract` keeps its shape; `calculation.requires_adapter=True`;
  other three declare built-in `input_ports`.
- Generic executor seam for ALL FOUR implementations (FROZEN):

```python
def execute(work_item: WorkItem, context: ItemExecutionContext, *, should_cancel=None) -> WorkItemResult: ...
```

- Batch selects the registry-resolved executor and shares
  claim→execute→commit→publish; no bespoke scientific branches in batch or
  application dispatch. C owns non-analysis implementations + context; D owns
  batch/persistence dispatch + application; F owns the analysis item adapter
  (which lives in the analysis package, not in application).

### 1.3 Compile / preflight errors (FROZEN)

- Unknown `executor | adapter | profile | check | recovery | program` ⇒
  `CAPABILITY_ERROR` at `validate_definition` (existing
  `UNKNOWN_EXECUTOR_CAPABILITY, UNKNOWN_EXECUTION_ADAPTER,
  UNKNOWN_RESULT_PROFILE, UNKNOWN_SCIENTIFIC_CHECK, UNKNOWN_RECOVERY` family;
  add `UNKNOWN_PROGRAM`). `compile_workflow` returns `ok=False`.
- `check not in profile.supported_checks` ⇒ `CHECK_NOT_SUPPORTED`;
  recovery/capability mismatch ⇒ `RECOVERY_UNSUPPORTED`;
  adapter/capability mismatch ⇒ `ADAPTER_CAPABILITY_MISMATCH`.
- Stochastic seed requirement follows the ACTUAL scientific capability
  (including GOAT), not the broad `calculation` enum alone: steps whose
  scientific capability is stochastic require an explicit integer seed
  (`SEED_REQUIRED`); do NOT indiscriminately reject seeds on calculation
  steps whose capability is deterministic.
- `opaque` profile: opaque MUST actually execute if published. Opaque steps
  compile; downstream `structure`/`result` bindings from an opaque producer
  are compile errors (only `artifact` ports with explicit role selectors may
  bind); the executor path runs the native program and publishes artifacts.
- Unsupported-but-declared combinations are rejected with structured
  diagnostics, never silently downgraded or hidden behind a permanent
  allowlist.

### 1.4 Versioning (FROZEN)

- Every descriptor keeps `contract_version: str`. Semantic descriptor change ⇒
  BUMP that descriptor's version only. `step_semantic_digest` already folds
  executor + adapter + profile + check + recovery versions; no new axis here.
- `SEMANTICS_VERSION` / `COMPILER_VERSION` BUMP only when a digest payload
  shape itself changes (B owns those version changes, §5).

### 1.5 Ownership — stream A (EXCLUSIVE, wave 1)

`execution/registry.py`, `execution/contracts.py`,
`workflow/v4/{validation,plan,compiler,schema,diagnostics}.py`,
capability-only sections of `producer/contract.py`;
NEW `tests/v4/test_repair_capabilities.py`; report
`docs/internal/V4_REPAIR_A_REPORT.md`.
DO NOT edit program/profile/check/recovery implementations, fingerprint,
assembly, application, batch, domain, or existing tests.

---

## 2. Stream B — Identity, ResultRef, Binding, mapping (drifts 3, 4, 6, 12, 13, 16, 19; supports 7, 15, 17)

### 2.1 Entity identity vs value digest (FROZEN)

- `StructureRecord.id` is entity identity; `geometry_digest` is content
  identity. Two records may share a geometry digest with different ids; one id
  never silently points at changed geometry.
- `ScientificResult.value_digest` covers `{kind, value, unit}` only — value
  equality, never entity identity. Provenance is excluded from the value
  digest by design.
- NEW (B implements): typed `ResultRef` + stable producer-scoped `result_id`
  on `ScientificResult`. Result identity MUST include producer context and a
  semantic output discriminator: different methods/producers/subjects are
  distinguishable. Production emitted/imported results REQUIRE an ID;
  deterministic construction from complete producer/subject/kind semantic data
  is acceptable; two source records are NEVER collapsed solely because their
  values match. Read-only legacy records without IDs must not silently become
  valid production refs.
- `ResultSet` gains `by_id(result_id)`; `PortSelector IDS` selects on entity
  ids (structures → `StructureRecord.id`, artifacts → `ArtifactRef.id`,
  results → `ResultRef.result_id`; current gap: result-port IDS returns
  unfiltered — B fixes). Cardinality is declared by the port: **IDS may select
  multiple distinct IDs on MANY ports; only duplicate/ambiguous candidates for
  the SAME requested ID fail.** No global exactly-one rule.
- `source_result_ids` in analysis become `ResultRef.result_id` strings (BUMP
  analysis digest kind with F; old manifests read-only).
- First-match helpers (`ResultSet.first`, analysis `select_result`) are NOT
  production selectors; production selection is explicit per-ID unique
  selection (ambiguity behavior co-owned with F).

### 2.2 Structure identity, lineage, measurement vs transformation (FROZEN)

- Import identity (fixes drift 12). FORBIDDEN: `{geometry_digest: entity_id}`
  maps (they merge equal-geometry distinct entities); `start_index` as
  scientific identity. KEPT signature (D owns the importer; B owns domain
  identity + digest support):

```python
# confflow/application/v4_run.py — signature FROZEN (D implements)
def import_xyz(
    text: str,
    *,
    source_name: str = "<input>",
    entity_ids: tuple[str, ...] | None = None,
) -> StructureSet: ...
```

  - Fresh imports mint independent opaque entity IDs (UUID at the import
    boundary is acceptable). An explicit ID sequence binds user-supplied
    entities with exact count validation. Path is provenance only.
  - CLI/run import persistence (D) records each named input's exact source
    content digest plus ordered entity records/IDs before execution. Resume of
    identical raw bytes reloads those IDs; reordered raw XYZ without explicit
    IDs MUST fail identity validation or require a new import — never guess by
    geometry. Reordering typed records WITH explicit IDs preserves identity.
  - The same entity may legitimately appear on multiple ports when its payload
    is identical; conflicting payloads under one ID fail.
- Measurement vs transformation (fixes drift 6): transformations bind output
  results to OUTPUT geometry entities; genuine measurements/passthrough retain
  INPUT identity (no new record). Intent is carried by explicit adapter/parser
  geometry intent/facts + `execution/output_identity.py` helpers — never
  `TaskName`/role runtime dispatch. Reaction lineage relations are explicit
  and validated; no blind role copying, no first-ancestor selection.

### 2.3 Digest axes (FROZEN — B owns version changes)

- Structure inputs fold: entity id, geometry digest, effective
  charge/multiplicity AND freeze, group_key, role, parent_ids/lineage_root_id.
  (Current gap: only geometry/charge/multiplicity/freeze folded.)
- Result inputs fold: `ResultRef` identity (incl. `result_id`) AND provenance
  as scientifically relevant. Value equality never substitutes for identity.
- Excluded: locators/paths, GUI/presentation, scheduler width.
- Old generations fail closed (`INVALIDATE_*`, history preserved), never
  silently reused. B owns `STEP_DIGEST_KIND` / `SEMANTICS_VERSION` /
  work-item-payload version changes.

### 2.4 Binding materialization with producer-status gating (FROZEN — fixes 3, 4)

- `StepOutputs` carries the actual `StepStatus` plus enough completion
  provenance for gating. **No separate `ProducerStatus` authority.**
- Assembly retains healthy-step items with scoped errors (no plan-wide wipe),
  checks actual partial/failed/cancelled producer state at materialization
  (compile checks never substitute for run checks).
- `Binding.partial_consumption`: producer `allow_partial` requires an explicit
  downstream declaration; producer `require_all` defaults `REQUIRE_COMPLETE`.
  COMPLETED ⇒ full outputs; PARTIAL + `ACCEPT_SUBSET` ⇒ accepted completed
  subset only; PARTIAL + `REQUIRE_COMPLETE` ⇒ downstream blocked (diagnostic);
  FAILED/CANCELLED ⇒ wait/fail per policy, never silent subset consumption.
- The CALLER (D) must distinguish waiting / missing / error / legal-empty and
  MUST NEVER execute an errored current step. Valid MANY-empty sets complete
  only explicitly (with an explicit diagnostic); missing/failed paths never
  reach vacuous completion.
- Final pairing check happens AFTER atom mapping (§2.5).

### 2.5 Atom mapping (FROZEN — fixes 16)

- Existing `kind`/`reference_slot` concepts retained.
  NEW: per-slot `permutations: Mapping[str, tuple[int, ...]]` expressing
  independent product/guess mappings. A single existing explicit permutation
  is a clearly defined UNIFORM SHORTHAND, not a second authority; normalize
  once to per-slot form.
- Assembly AND executor use the SAME mapping validation helper BEFORE element
  compatibility (`named_structures` / `execution_adapters` checks run after).
  No inference: disagreement under `identity` ⇒ `atom_mapping_required`;
  malformed/non-preserving ⇒ `atom_mapping_invalid`.

### 2.6 Ownership — stream B (EXCLUSIVE, wave 1)

`domain/{structure,result,binding,work_item}.py`,
`workflow/v4/{assembly,fingerprint}.py`,
`execution/{output_identity,named_structures,atom_mapping,execution_adapters}.py`;
NEW `tests/v4/test_repair_identity.py`; report `V4_REPAIR_B_REPORT.md`.
DO NOT edit application/importer, batch, profile, registry, contracts, or
existing tests.

---

## 3. Stream C — Executors, native boundary, ExecutionBinding (drifts 5, 9, 14, 17, 21, 22; locks §2.2 output rule)

### 3.1 Executors (FROZEN)

- All four implementations expose
  `execute(work_item, context, *, should_cancel=None) -> WorkItemResult`
  (NEW `execution/{confgen_executor,transform_executor}.py`; calculation stays
  `WorkItemExecutor`; analysis item adapter lives in the analysis package, F).
- ConfGen/Transform never call legacy code, Calculation, or Gaussian/ORCA.
- C owns non-analysis implementations + `ItemExecutionContext`; D owns
  batch/persistence dispatch + application. No full IRC/GOAT/NEB parser
  rewrites until wave-2 stream G. C owns profile/recovery implementation
  exclusively.

### 3.2 Native boundary (FROZEN)

- `ProgramAdapter` owns ALL native syntax; `ResultProfile` owns typed-output
  extraction; `ScientificCheck` owns acceptance; `RecoveryPolicy` owns rescue
  intent. Recovery never rewrites routes/ModRedundant, never sets
  executable/env/workdir/walltime; scientific recovery params never override
  binding environment.
- Checkpoint consumption (fixes 17): bound checkpoints require
  `{checksum, subject_structure_id, role, run-relative locator}`; missing or
  unverified ⇒ `ARTIFACT_ERROR`; subject must be an item input; adapters must
  consume staged checkpoints or raise `artifact_unsupported` (staged ≠ used).

### 3.3 ExecutionBinding resolution (FROZEN)

- Implemented in a NEW C-owned execution module
  (`execution/binding_resolution.py`): takes domain/execution types plus
  explicit arguments (planned binding, request defaults: executables map,
  env/target/walltime defaults, program). **MUST NOT import `V4RunRequest` or
  application.** Planned binding fields win; request defaults fill missing
  fields; env/target/walltime/executable identity preserved end-to-end.
- C publishes the concrete function signature in `V4_REPAIR_C_REPORT.md` for D
  to call. Binding excluded from scientific digests; measured environment is
  the reuse axis (§6). Remote target-side resolution never requires local
  absolute paths to function remotely.

### 3.4 Stochastic seed (FROZEN — fixes 22)

- One authority: the step seed. Executor threads it into digest + native
  rendering + remote handoff; a disagreeing native seed key ⇒
  `native_input_error`. Stochastic gating follows actual scientific capability
  (including GOAT), not the broad enum alone.

### 3.5 Ownership — stream C (EXCLUSIVE, wave 1)

`execution/{work_item_executor,native,profiles,profile_standard,recovery,
recovery_standard}.py`, NEW
`execution/{binding_resolution,confgen_executor,transform_executor}.py`,
`programs/gaussian/adapter.py`, `programs/orca/adapter.py`, native syntax
helper extraction as required; NEW `tests/v4/test_repair_executors.py`;
report `V4_REPAIR_C_REPORT.md`.
DO NOT edit `execution/contracts.py` (propose shared changes via lead),
registry, batch, domain identity, assembly, application, or existing tests.

---

## 4. Stream D — Durable execution, resume, publication (drifts 2, 10, 18; carries 3, 4, 14, 19)

### 4.1 Durable protocol (FROZEN)

- `execute_step_resumable` is the only production step-execution path.
- NO published-file early return before current-item reuse checks: resume
  confirms run/definition/input generation (definition + step-semantic digests
  vs stored registration + published `StepProvenance`), assembles CURRENT
  items, then per-item register → `evaluate_reuse` → REUSE / RETRY_FAILED /
  RECOVER_ABANDONED / BLOCKED / INVALIDATE_* / EXECUTE_NEW → commit.
- Strict item durable commit before verified `StepResult` publication; no gap
  exception (`verify_for_publication` failure refuses publication, full stop).
- `RunState` is lifecycle only; application MUST
  `ensure_step/transition_step/save_run_state`. New definition generations
  refuse-or-branch explicitly; history is never overwritten.

### 4.2 Analysis durability (FROZEN — fixes 10; item adapter owned by F)

- Default is ONE explicit whole-set aggregation WorkItem via existing SINGLE
  bindings. **No new mandatory document aggregation setting.** The pure
  analysis core may compute groups INSIDE that WorkItem. Per-group work items,
  if ever needed, must be represented in typed binding/item contracts — never
  an application merge/copy loop. No application copy of one output to many
  items. F owns the analysis item adapter + partial acceptance semantics; D
  routes it through the same claim/commit/publish path.

### 4.3 Item directories (C creates, D drives)

- D passes the real current attempt number into C's `ItemExecutionContext`; C
  creates a bounded-length collision-resistant hashed item directory plus a
  distinct attempt child. `sanitize` on native basenames is presentation only.
  Unbounded nested logical keys are never encoded directly as filenames. Old
  layouts are read-only.

### 4.4 Manifest (drift 8; implementation finalized at integration)

- Per-step `digest` = `typed_digest(STEP_RESULT_DIGEST_KIND, step_result.
  to_dict())` (the `publish_step_result` return), never a status string.
- `counts` exactly `{completed, failed, cancelled}`; full
  `{code, severity, message, step_id, field_path}` diagnostics; `analyses`
  with `{capability, step_id}` + `ResultRef` refs; `artifacts` with
  `{role, checksum, run-relative locator}`; validated against
  `run_result_json_schema()`; atomically published to
  `<run_root>/run_result_manifest.json`. No fake placeholders.

### 4.5 Ownership — stream D (EXCLUSIVE, wave 1)

`execution/batch.py`, `persistence/*`,
`domain/{publication,step_result,completion}.py`, `application/v4_run.py`
INCLUDING importer; NEW `tests/v4/test_repair_persistence.py`; report
`V4_REPAIR_D_REPORT.md`.
DO NOT edit work_item_executor, registry, `execution/contracts.py`, domain
identity, assembly, native, or old entrypoints.

---

## 5. Environment identity (FROZEN — freeze §7)

- Measured for production reuse: real binary digest + program version +
  scientifically relevant explicit environment.
- Operational provenance (NOT reuse identity): endpoint hostname/paths,
  scheduler width. **Target locator is excluded from environment reuse
  identity when the measured execution environment is equivalent — this rule
  itself is versioned** (B versions the rule with the digest contracts).
- `None`/unknown environment NEVER silently stands in for verified
  equivalence (fail closed / explicit record).
- Pure executors (analysis/confgen/transform without native launch) record
  their implementation environment without requiring a Gaussian/ORCA
  executable.

---

## 6. Wave-2 streams (boundaries; no wave-2 implementation until the integration gate)

- **E remote** owns `remote/*` INCLUDING `envelope.py`: the envelope VERSION
  MAY AND MUST change on wire-semantic change. Preserve ports for all input
  kinds, true step/item digests, refs/provenance, binding request, and attempt
  reconciliation. Local/shared-fs transport stays a testable delivery
  implementation only.
- **F analysis** owns `analysis/*` + analysis-specific recipe changes: item
  adapter, `policy_from_native`/assignment/partial split (energy keys vs
  endpoint assignment vs partial policy; never drop `analysis_step_id`),
  unique energy selection (zero ⇒ `energy_missing`, ambiguous levels ⇒
  explicit ambiguity failure — order-swaps never silently re-pick), lineage-
  keyed grouping, TSPES chain completeness, PES from real results.
- **G native** owns Gaussian/ORCA IRC/NEB/GOAT rendering/parsing AFTER C
  stops: installed ORCA `/opt/orca611/orca` (6.1.1); Gaussian `/opt/g16`
  (read/execute may require supervising-tool approval). Inspect actual
  versions + official manuals; no guessed syntax or fabricated fixtures;
  explicit Gaussian version support; task temp dirs allowed.
- **H adversarial tests** owns NEW repair tests and updates incorrect old
  tests ONLY with explicit expected-final-contract evidence — never
  production code.
- Real JobDesk consumers (read + test, never modify without supervisor
  coordination): producer/editor contract
  `/opt/jobdesk-v2-v4/src/jobdesk_v2/application/editor/contract/v4.py`,
  results consumer `application/runs/v4_results.py` (path relative to the
  JobDesk repo root).
- Baseline evidence: full `tests/v4` pre-edit = 2369 passed, 2 failed,
  3 skipped (`test_v44_faults.py::TestFaultCProducerCrash::
  test_remote_replay_after_crash_imports_once`,
  `TestFaultEPreCommitCrash::test_reimport_after_precommit_crash`; logs show
  unexpected installed-ORCA invocation; full log
  `/tmp/confflow-v4-baseline-tests.log`). Fix transport binding/test
  isolation root cause; never adjust expected completion to accept failure.

---

## 7. Exclusive ownership and integration order (wave 1, truly concurrent)

| Stream | Owns (write) | Must not touch |
|---|---|---|
| A | `execution/registry.py`, `execution/contracts.py`, `workflow/v4/{validation,plan,compiler,schema,diagnostics}.py`, capability-only `producer/contract.py` sections, NEW `tests/v4/test_repair_capabilities.py` | program/profile/check/recovery impls, fingerprint, assembly, application, batch, domain, existing tests |
| B | `domain/{structure,result,binding,work_item}.py`, `workflow/v4/{assembly,fingerprint}.py`, `execution/{output_identity,named_structures,atom_mapping,execution_adapters}.py`, NEW `tests/v4/test_repair_identity.py` | application/importer, batch, profile, registry, contracts, existing tests |
| C | `execution/{work_item_executor,native,profiles,profile_standard,recovery,recovery_standard}.py`, NEW `execution/{binding_resolution,confgen_executor,transform_executor}.py`, `programs/gaussian/adapter.py`, `programs/orca/adapter.py` (+ extracted syntax helpers), NEW `tests/v4/test_repair_executors.py` | `execution/contracts.py` (propose), registry, batch, domain identity, assembly, application, existing tests |
| D | `execution/batch.py`, `persistence/*`, `domain/{publication,step_result,completion}.py`, `application/v4_run.py` (incl. importer), NEW `tests/v4/test_repair_persistence.py` | work_item_executor, registry, contracts, domain identity, assembly, native, old entrypoints |

Workers read other interfaces + the freeze but never write another owner's
files. A required-but-absent shared symbol is implemented against per the
frozen signature and recorded as an exact integration dependency — never a
fallback/shim. Each worker writes its `V4_REPAIR_{A,B,C,D}_REPORT.md` and runs
meaningful scoped tests; no commits/push. The lead resolves shared-contract
edits and integrates ALL wave-1 changes BEFORE wave 2. Final lead owns entry
cutover, Producer Contract/manifest, legacy removal, full gates, and ROOT
CAUSE/FIX/TEST/STATUS for 1–22 plus new drift.

---

## 8. Concrete open issues (for supervising agent)

1. Resolver names: if A ships different names than
   `resolve_executor/resolve_profile/resolve_check/resolve_recovery/
   resolve_program`, integration needs the documented rename map.
2. `binding_resolution` concrete signature: C's report must fix it; D codes
   against the freeze shape until then (planned-binding-wins + explicit
   request defaults, no application imports).
3. `ResultRef` exact field list (producer context + semantic discriminator)
   is fixed by B's report; C/F stamp it per §2.1.
4. Environment-equivalence version string for the target-exclusion rule (§5):
   B must name it so reuse decisions are auditable.
5. Gaussian `/opt/g16` access approval (read/execute via supervising tools)
   gates G's version inspection.
6. Legacy physical deletion stays deferrable; formal-entry unification (drift
   1) lands in wave 3 — no interim dual-runtime claims.
7. Cross-restart remote proof harness (complete-but-uncommitted race) is H's
   process-level build; no static-inference closure.

---

## 9. Freeze statement

The signatures, shapes, digest axes, gating rules, file ownership, and order
in §0–§8 are reconciled to `V4_REPAIR_EXECUTION_FREEZE.md`, which remains
authoritative on any residual difference. Workers A–D implement exactly
these. Production/test code is untouched by this file. Nothing is committed
or pushed. No credentials were read or exposed.
