# Workflow V3 Runtime Implementation Plan (R4)

Status: **plan — awaiting runtime review** (this document has not been
implementation-reviewed yet; nothing in it is normative until that review
accepts it).

Scope: the durable V3 runtime — `workflow_state.v2`, `workflow_binding.v2`,
stable-ID runtime identity, `steps/<id>/` layout, Execution Fingerprint C,
V3 resume / rerun / pause / cancel, checkpoint runtime resolution, output
artifact identity, and the final capability flip. This plan is **docs only**;
no runtime code changes are made in this round.

Out of scope (later rounds, unchanged from the R3 plan): structured calc
`params.theory` (R5), effective-dataflow capability validation (R6), JobDesk
V3 contract publication (R7), editor manifest V3, recipe instantiate semantics.

Sources of truth used throughout (no memory-derived claims):

- `docs/rfc/WORKFLOW_V3_RFC.md` §11 (checkpoint layers), §12 (disabled), §16.A–D
  (fingerprints/resume), §17 (state and directory identity), §20 (duplicate/insert
  rules), §26 (decision log).
- Real code at base `248f31a`: `workflow/{engine,plan,state,runtime_context,
  resume_validation,step_naming,step_handlers,stats,finalize,presenter,
  rerun_failed,dry_run,config_show,supervisor}.py`,
  `application/execution/{workflow_adapter,service,sqlite,state_root,models,
  ports}.py`, `worker_attempt.py`, `control_worker.py`, `worker_handoff.py`,
  `worker_staging.py`, `contract.py`, `config/canonical/{workflow,v3_graph,
  validation,fingerprint,execution_versions}.py`.

Where this plan makes a decision the RFC does not state verbatim, the decision
is marked **[PD-n]** (plan decision) with its RFC/code derivation. Review must
explicitly accept each PD.

---

## 1. Runtime reality check — V1 Assumption Inventory

Every place the current runtime assumes V1 name/dirname identity, the V3
impact, and the slice that resolves it.

| # | Location | Current V1 assumption | V3 problem | R4 slice |
|---|---|---|---|---|
| 1 | `workflow/state.py` `StepRecord.name` / `WorkflowState.steps` | state records keyed by **dirname**; record carries `name` | V3 durable identity is the stable id; dirname must not exist | R4.1 |
| 2 | `workflow/state.py` `WorkflowState.from_dict` | rejects any `content_schema` ≠ `confflow.workflow_state.v1` | already fail-closed for v2 content — good; v2 needs its own loader | R4.1 |
| 3 | `workflow/engine.py` `_initial_workflow_state` | records built `zip(step_dirnames, steps)`; keyed by dirname | V3 has no dirname; keys must be ids | R4.3 |
| 4 | `workflow/engine.py` `state.steps[step_dirname]` lookups | state lookup by dirname | V3 lookup by id | R4.3 |
| 5 | `workflow/engine.py` `build_workflow_binding(plan)` | V1 binding (`workflow_binding.v1`) built from V2 plan payload | V3 needs binding v2 (A/B/C), never v1 | R4.2 |
| 6 | `workflow/engine.py` `_compute_input_digests` | `f"{abspath}:{content_digest}"` ordered list | V3 C must bind contents (ordered) + cardinality; path is runtime bookkeeping [PD-2] | R4.2 |
| 7 | `workflow/engine.py` resume loop `name_to_dirname[step_name]` | resume locate by name→dirname | V3 resume locates by id | R4.4 |
| 8 | `workflow/engine.py` `step_started_callback(step_name, step_type, step_dir)` | callback carries name | V3 passes the **stable id** as `step_name` (callback does not persist it; only STOP-beacon injection) [PD-6] | R4.3 |
| 9 | `workflow/step_naming.py` `build_step_dir_name_map` | sanitized-name dirnames with collision suffixes | V3 forbids: label/index/name → dirname; `steps/<id>` instead | R4.1 (helper), R4.3 (wiring) |
| 10 | `workflow/step_handlers.py` `_resolve_chk_input_dir` | `params.chk_from_step` name/1-based index → dirname map → `<root>/<dirname>/backups` | V3 `checkpoint.from_step` is a stable id → `steps/<from_id>/backups`; no name/index guessing | R4.3 |
| 11 | `workflow/step_handlers.py` `run_calc_step(..., steps=...)` | `steps` V2 shape used for chk resolution | V3 resolves chk dir at runtime layer; handler needs an `input_chk_dir` override [PD-7] | R4.3 |
| 12 | `workflow/step_handlers.py` `run_calc_step(step_name=...)` | `step_name` labels calc requests/failure artifacts | V3 passes the stable id as `step_name` (display/keys inside step outputs) [PD-6] | R4.3 |
| 13 | `workflow/stats.py` `CheckpointManager(.checkpoint)` | `last_completed_step` = **execution-order index** | V3 topological order is frozen per definition, so the index stays valid across label rename/reorder; state v2 additionally stores the order for verification | R4.1/R4.4 |
| 14 | `workflow/stats.py` `FailureTracker(failed_dir)` | `failed/failed.xyz` per run (not per step name) | identity-neutral — reusable as-is | — |
| 15 | `workflow/finalize.py` `_build_output_manifest` | `terminals: {<step name>: [paths]}` `confflow.output_manifest.v1` | RFC §17: v2 manifest records `{id, label, artifacts}` keyed by id | R4.3 (write), R4.5 (load) |
| 16 | `workflow/presenter.py` / `finalize.py` `workflow_stats.json` | `steps[].name`, `terminal_outputs` keyed by name; `confflow.workflow_stats.v1` | v2 stats keyed/identified by id (+ label snapshot display) | R4.3 |
| 17 | `workflow/rerun_failed.py` `_select_step` | name/index selector; output dir next to V1 step dir | V3 selector = stable id only; reset/cleanup by graph successors | R4.4 |
| 18 | `workflow/resume_validation.py` | validates artifacts under V1 step_dir with V2 `params`/`steps` | V3 reuses validators with id-derived dirs and resolved params | R4.4 |
| 19 | `application/execution/workflow_adapter.py` `checkpoint_update` | `checkpoint.<record.name>.<fail_count>.<status>` — durable service checkpoint id from **record.name** | V3 must use the stable id; label must never enter it | R4.5 [PD-8] |
| 20 | `application/execution/workflow_adapter.py` `_load_artifacts` | hard-accepts `confflow.output_manifest.v1` only | needs version-aware loader (v1 + v2); v1 behavior unchanged | R4.5 |
| 21 | `application/execution/workflow_adapter.py` `_load_completed_stats` | checks `workflow_stats.json` v1 shape | must accept v2 stats for V3 runs | R4.5 |
| 22 | `application/execution/workflow_adapter.py` `run_workflow_through_service` | preflight guard before `build_workflow_service` | **worker path bypasses this function** (see #23) | R4.5 |
| 23 | `control_worker.py` → `worker_attempt.run_worker_attempt` → `build_workflow_service` | worker builds the service **directly**; today only the engine guard (Guard C) stops V3 — after state root/SQLite already exist | guard must move into `build_workflow_service` itself so both entry points are covered [PD-9] | R4.5 |
| 24 | `worker_handoff.py` / `worker_staging.py` | serialize/deserialize config digest + `input_xyz` tasks | config is content-digested YAML — V3 flows through unchanged; verified by W1/W3 tests | R4.5 |
| 25 | `contract.py` | `workflow_state.v1` / `workflow_stats.v1` / `output_manifest.v1` ids; `.workflow_state.json` name | adds v2 ids; **same filenames**, schema-dispatched content [PD-3] | R4.1 |
| 26 | `config/canonical/fingerprint.py` | `_EXECUTION_CLASS_STEP_PARAMS` + global split (single authoritative sets) | C reuses the same constants; no second classification [PD-4] | R4.2 |
| 27 | `application/execution/service.py` / `sqlite.py` / `state_root.py` | run-lifecycle only; **no step identity anywhere** | version-neutral — keep it that way; no second service | — |
| 28 | `workflow/supervisor.py`, pause/cancel beacons | PAUSE/CANCEL beacons keyed by run/work-dir only | identity-neutral — reusable | — |
| 29 | `calc/artifacts.py` manifests | per-step artifacts under the step dir (path opaque to the manifest) | step dir becomes `steps/<id>/`; manifest content unchanged | — |

Deliberately identity-free today (verified): `ExecutionService` state machine,
`SQLiteExecutionRepository`, `StateRoot` run paths, pause/cancel beacons,
`FailureTracker`, service request digests (config content digest — version
neutral).

---

## 2. A / B / C identity model (from RFC §16 — restated, not redefined)

| Layer | Purpose | Exact contents | Resume effect |
|---|---|---|---|
| **A — Workflow Definition Fingerprint** (`workflow_definition_fingerprint_v3`, RFC §16.A) | *what is the semantics of this workflow* — the semantic identity | `semantics_version: confflow.workflow-semantics.v3` + resolved scientific globals (the §16.A scientific table) + per-step records `{id, type, enabled, params (resolved, execution-class excluded per the frozen §16.A table), inputs (sorted by id order key), checkpoint.from_step, extensions}`; steps sorted by the id order key; canonical JSON, `sha256:` digest | A mismatch ⇒ **reject** (definition changed). Stored in state v2 and binding v2 for layered diagnostics. |
| **B — Schema / Canonicalization Binding** (RFC §16.B) | *which schema and canonicalizer interpreted this document* — producer/schema provenance, **not identity** | workflow schema identifier (`confflow.workflow.v3`), workflow schema digest (V3 DOCUMENT `workflow_schema_sha256_v3()`), canonicalization version constant, producer identity/version/commit/dirty | **Never inside A.** Persisted in binding v2 for audit. Schema-digest/producer change ⇒ recorded warning, resume allowed (§16.D: resume binds C). **Canonicalization-version change ⇒ reject** — digests are not comparable across canonicalizers, so a mismatch is fail-closed rather than a false pass [PD-1]. |
| **C — Resolved Execution Fingerprint** (RFC §16.C) | *what exactly will run; can a prior result be resumed* | `execution payload = definition semantic payload (A) + run context (ordered external-input content digests + input cardinality fact) + the execution-class global members (§16.A table) + resolved resources / runtime / executable settings (per-step execution-class params from the same frozen constant + resolved program settings: `iprog` + executable path per step)` [PD-2, PD-5] | **Resume binds C (RFC §16.D normative).** C mismatch ⇒ reject, with the layer named (A-part vs inputs vs resources) via the state-stored A and input/settings sections. |

Dependency: `C = H(A-payload ‖ run-context ‖ execution-class data)`; A is
embedded (the payload itself, not the digest) so one digest covers everything,
while the separately stored A digest gives mismatch layering.

### Input identity (No-TBD 12/13) [PD-2]

RFC §16.C: run context = *"external input digests, input cardinality facts"*.
Derived decision:

- **bound in C**: the **ordered list of external-input content digests**
  (sha256 of file bytes, in `input_files` order) and the **count**
  (cardinality). Order is bound because multi-input workflows consume inputs
  positionally (`workflow/validation.py::validate_inputs_compatible`,
  `force_consistency` semantics) — reordering changes what runs. Count is the
  RFC's "cardinality fact".
- **not bound in C**: absolute paths and basenames. V1 binds `abspath:digest`
  (engine `_compute_input_digests`), but RFC's wording is *digests*; a renamed
  input with identical bytes is the same execution for V3. Paths/basenames are
  recorded in state v2 (`input_files`) for provenance and artifact bookkeeping.
- **original vs converted**: C binds the inputs the engine actually consumes —
  the post-conversion list (CLI `.gjf/.com` conversion happens before
  planning; `original_input_files` is recorded in state, not bound).

**Review flag:** this is the one place V3 C is deliberately narrower than the
V1 binding. Acceptance of PD-2 is a review prerequisite.

### Executable identity (No-TBD 14) [PD-5]

- **chemistry executables**: C binds the **resolved settings** — `iprog` and
  the resolved executable path (realpath of `gaussian_path`/`orca_path` after
  the same resolution the engine uses). RFC §16.C says *"resolved resources /
  runtime / executable settings"* — settings, not binary content. Binary-digest
  binding is **out of R4 scope** (documented limitation; candidate R6+
  hardening). The existing launch-identity verifier (ConfFlow interpreter
  realpath/dev-inode/sha256) is a **service security control**, not chemistry
  identity — the two are not conflated.
- **service executable identity**: unchanged, version-neutral, stays in the
  service request as today.

---

## 3. Resume compatibility matrix

Resume = strict compare of **A**, then **C**, with **B** audited (§2). V2
resume semantics are untouched (v1 binding algorithm frozen).

| Change | A | B | C | Resume |
|---|---|---|---|---|
| `label` rename | – | – | – | **allowed** (RFC §16.D, §17) |
| step array reorder | – (order-key canonical) | – | – | **allowed** |
| annotations change | – | – | – | **allowed** |
| migration annotations change | – | – | – | **allowed** |
| YAML formatting / key order | – | – | – | **allowed** |
| stable ID change (delete+create) | ✓ | – | ✓ | **reject** — "definition mismatch (A)" |
| graph edge change | ✓ | – | ✓ | **reject** (A) |
| scientific param change | ✓ | – | ✓ | **reject** (A) |
| semantic extension change | ✓ | – | ✓ | **reject** (A) |
| checkpoint.from_step change | ✓ | – | ✓ | **reject** (A) |
| execution-class global change (e.g. `max_parallel_jobs`, paths) | – | – | ✓ | **reject** — "execution mismatch (C: resources)" |
| execution-class step param change (e.g. `cores_per_task`) | – | – | ✓ | **reject** (C: resources) |
| input file **contents** change | – | – | ✓ | **reject** (C: inputs) |
| input **filename only** (same bytes) | – | – | – | **allowed** [PD-2] |
| input **order** change (multi-input) | – | – | ✓ | **reject** (C: inputs) |
| input count change | – | – | ✓ | **reject** (C: inputs cardinality) |
| executable **path setting** change | – | – | ✓ | **reject** (C: resources) |
| executable **binary digest** change | – | – | – | **not bound in R4** [PD-5, documented limitation] |
| producer version/commit change | – | ✓(audit) | – | **allowed + recorded warning** [PD-1] |
| schema digest change (doc-only, S22) | – | ✓(audit) | – | **allowed + recorded warning** |
| canonicalization version change | – | ✓ | – | **reject** (digest comparability broken) [PD-1] |
| state written by v1 schema / state v2 found by V1 reader | — | — | — | **reject — incompatible state schema** (both loaders strict) |

---

## 4. State V2 (No-TBD 1, 2, 9, 10, 20, 22)

### Schema

- `content_schema`: **`confflow.workflow_state.v2`** (new constant in
  `contract.py`; v1 constant unchanged).
- Same filename **`.workflow_state.json`** [PD-3]. RFC §17 names
  `.workflow_state.json` as the V3 state file at the work-dir root;
  schema-dispatched content (each loader accepts exactly its
  `content_schema`). The v1 loader already hard-rejects any other
  `content_schema` (`state.py::from_dict`) and the v2 loader symmetrically
  rejects v1 — so an accidental cross-version read fails closed in both
  directions. File-naming alternative (`.workflow_state.v2.json`) rejected:
  it would fork tooling/cleanup paths that `contract.py` exists to prevent.

### Keying

`steps` is an object keyed by the **stable step ID** (the only durable
identity). No name/label/dirname key anywhere. `state.steps[s001]`.

### Root fields

| Field | Kind | Notes |
|---|---|---|
| `run_id`, `work_dir`, `config_file` | runtime | same semantics as v1 |
| `input_files`, `original_inputs` | runtime bookkeeping | recorded, **not** resume identity [PD-2] |
| `input_digests` | C-section mirror | ordered content digests; consumed by mismatch diagnostics |
| `definition_fingerprint` | identity | digest A |
| `binding` | binding v2 object | A digest + C digest + B section (see §5) |
| `steps` | `{<id>: StepRecordV2}` | durable identity = id |
| `execution_order` | `list[<id>]` | the frozen topological order used by the run — resume verifies the recomputed order equals it (label/reorder-stable; ID/graph change caught by A first) |
| `wavefront_index` | runtime | position in `execution_order` (v1 semantics preserved) |
| `started_at`, `last_updated_at`, `final_status` | runtime | v1 semantics |

### Step record fields (all V1 consumers checked)

| Field | Kind | Notes |
|---|---|---|
| `id` | identity | the durable key, duplicated inside the record for self-describing integrity (loader rejects id≠key) |
| `label` | **display snapshot** | never participates in resume identity (S3/S5 prove); may drift from the document freely |
| `type` | runtime | `calc`/`confgen` |
| `status` | runtime | same enum as v1: `pending / submitted / completed / failed / skipped` (§32: no new state machine) |
| `submitted_at`, `completed_at` | runtime | v1 semantics |
| `output_xyz` | runtime | path (usually inside `steps/<id>/`) |
| `error` | runtime | last failure text |
| `fail_count` | runtime | v1 semantics |
| `executor_handle_data` | runtime | v1 semantics (calc executor resume handle) |

No `attempt` counter (v1 has none; `fail_count` suffices). No checkpoint
metadata block — checkpoint provenance lives in the calc artifact manifests
under `steps/<id>/`, as in V1.

### Atomicity / corruption handling (No-TBD 9)

Reuse `artifact_json.write_atomic_json` (temp + replace) exactly like v1.
Corruption (truncated JSON, bad schema, unknown step record, id≠key mismatch,
order mismatch) ⇒ `WorkflowStateCompatibilityError`-equivalent v2 error ⇒
resume fails closed; nothing is mutated. Tests S9–S11.

### V1/V2 coexistence (No-TBD 22)

- V2 workflows: `workflow_state.v1`, v1 loader, v1 layout — unchanged.
- V3 workflows: `workflow_state.v2` only.
- **Cross-version workspace**: a V3 run whose work-dir contains a v1 state
  file ⇒ **fail closed** ("incompatible workflow state schema
  `confflow.workflow_state.v1`; explicit clean or new work-dir required"). A
  V1 (V2-workflow) run whose work-dir contains a v2 state file ⇒ symmetric
  fail-closed guard added to the v1 store's `save` [PD-10 — the single
  deliberate, minimal touch of v1 code: a refuse-if-v2-present check in
  `save` only; v1 load/resume/binding paths untouched, and no existing V2
  outcome changes because a v2 state file could never be resumed by the v1
  loader anyway]. No automatic state migration, ever (§12 of the prompt:
  identity domain, fingerprint layer, schema and layout all differ ⇒ old V2
  runs are not resumable after a V2→V3 config upgrade; RFC §16.D "V2 binding
  unchanged" implies no cross-resume).

---

## 5. Binding V2 (No-TBD 3, 4, 6, 7, 8, 15)

- Schema id: **`confflow.workflow_binding.v2`** (new constant).
- Stored inside state v2 (`binding` field); **no separate binding file** — the
  state document is the binding document for a run [PD-3 keeps the file
  surface minimal; RFC §16.B says the binding "travels in the run/provenance
  record (and, later, the binding document)" — state v2 is that record].

Payload:

```
{
  "schema": "confflow.workflow_binding.v2",
  "source_version": "confflow.workflow.v3",
  "definition_fingerprint": "sha256:…",        # A (also mirrored at state root)
  "execution_fingerprint":  "sha256:…",        # C
  "provenance": {                              # B — RFC §16.B verbatim intent
    "workflow_schema": "confflow.workflow.v3",
    "workflow_schema_sha256": "<V3 DOCUMENT digest>",
    "canonicalization_version": "<constant>",
    "producer_version": "…", "producer_commit": "…", "producer_dirty": …
  }
}
```

- Relationship to A/B/C: binding v2 carries the **digests of A and C** plus
  the **B section**. It is *not* `binding_v2 = fingerprint_c`; C is one member.
- Mismatch diagnostics (No-TBD 15): resume recomputes A, then C, then audits B:
  1. A mismatch ⇒ `definition fingerprint mismatch (A)`.
  2. C mismatch with A equal ⇒ compare the state-stored `input_digests` and
     execution-settings section ⇒ `execution fingerprint mismatch (C: inputs)`
     or `(C: resources)`.
  3. B producer/schema-digest difference with A and C equal ⇒ proceed with a
     recorded warning (binding provenance audit).
  4. B canonicalization-version difference ⇒ reject (PD-1).
- V2 (`workflow_binding.v1`) goldens, parse/build/validate paths: untouched
  (B10 tests).

---

## 6. Runtime layout (No-TBD 5)

```
<work_dir>/
  .workflow_state.json        # schema-dispatched v1 | v2
  output_manifest.json        # schema-dispatched v1 | v2
  workflow_stats.json         # schema-dispatched v1 | v2
  run_summary.json            # unchanged
  .checkpoint                 # last completed index in the frozen V3 topological order
  failed/                     # FailureTracker — unchanged semantics
  confflow.log
  steps/
    s001/                     # stable-ID dirs; search.xyz / result.xyz / backups/ …
    s002/
    s_abcd1234/
  .confflow_execution/        # ExecutionService state root — unchanged, separate
```

- Path identity **only** from the validated stable ID (RFC §17 normative:
  `steps/<id>/`; label never in a path).
- V2 keeps the flat V1 dirname layout, unchanged (§27) — layout is version
  branched, never mixed.
- **Path safety helper** (`workflow/plan.py` or a small `workflow/v3_paths.py`):
  `v3_step_dir(work_dir, step_id)` re-validates `is_persisted_id` at the
  filesystem boundary (grammar `^[a-z][a-z0-9_]{0,63}$` ⇒ no `/`, no `..`, no
  leading dot, no empty) before joining — traversal/normalization impossible by
  construction and re-checked at runtime (P1–P7). [PD-11 review note] Windows
  reserved device names (`con`, `prn`, …) are legal IDs; the durable service is
  POSIX-only today, and the helper documents/rejects reserved names when
  `os.name == "nt"`. Review must accept this portability stance.
- `backups/` (checkpoint artifacts) live under `steps/<from_id>/backups/`,
  matching V1's per-step `backups/` convention.

---

## 7. Execution projection (No-TBD 19, 20, 23)

`WorkflowV3Plan` (R3, planning-only) stays untouched — no dirname/state/bag
fields flow back into it (§24). R4.3 adds the runtime projection:

```
WorkflowV3Plan + Resolved Run Context
        ↓  build_workflow_v3_runtime_plan(...)
WorkflowV3RuntimePlan
```

Fields: validated definition + graph + stable IDs (from the plan), per-step
**resolved execution params** (full `CalcStepParams.canonical_dict()` /
`resolve_confgen_params` — execution-class members included here, unlike A),
runtime step dirs (`steps/<id>/`), external input binding (ordered, converted
list), **checkpoint runtime refs** (`from_step → steps/<from_id>/backups`),
`definition_fingerprint` (A), `binding` (B), `execution_fingerprint` (C).

No separate state store/artifact manager in the projection — the engine and
the (reused) state/artifact machinery consume it.

### Step execution reuse (No-TBD 18, 29)

- calc/confgen handlers are **not** forked. The existing handlers need:
  `step_dir`, inputs, resolved `params`, `typed_global`, a `step_name` label,
  `failure_tracker`, and (calc only) an `input_chk_dir`.
- V3 adapter passes `step_dir=steps/<id>/`, `step_name=<stable id>` (identity
  label for calc requests/failure artifacts; display-only) [PD-6], and
  pre-resolves the chk dir at the runtime layer.
- Minimal handler change: `run_calc_step(..., input_chk_dir: str | None = None)`
  override used instead of the V2 `_resolve_chk_input_dir` path when provided
  [PD-7, PROTECTED-MINIMAL — one optional parameter; V2 callers unaffected].
- `resolve_calc_step(params, typed_global, input_chk_dir=…)` already accepts
  the chk dir, so no resolver changes.

### Checkpoint runtime resolution (No-TBD 19)

`checkpoint.from_step` (validated strict calc ancestor, R3.4) →
`steps/<from_id>/backups` → `input_chk_dir`. Producer-step verification is
structural (the ref is the validated ancestor's own dir — no name/index/dirname
guessing, ever). File existence / program compatibility / digest checks remain
the existing runtime chk machinery, exactly as RFC §11 assigns to the runtime
layer. Resume consistency: the ref lives inside A, so any checkpoint change is
already a resume-rejecting change.

### Disabled runtime semantics (No-TBD 20)

Runtime behavior is defined now (R6 static capability system later):

- disabled step ⇒ state record `status="skipped"`, produces no artifact;
- its effective output = concatenation of predecessor outputs (external input
  if root), forwarded to successors — the exact V1 bypass branch
  (`engine.py` disabled paths, `_resolve_inputs_for_step`);
- multiple predecessors after bypass ⇒ forwarded as a list; a consuming calc
  with unsupported effective cardinality fails at the consuming step with its
  normal error (V1-identical). R6 adds the static pre-validation.

---

## 8. Pause / Cancel / callbacks (No-TBD 18)

- PAUSE/CANCEL beacons and the cancel path are run/work-dir keyed — reusable
  unchanged (PC1–PC4, V2 PC6).
- `step_started_callback(name, type, dir)`: V3 passes the stable id as `name`;
  the callback's only current use is STOP-beacon injection (does not persist
  identity) ⇒ API-compatible without a breaking change [PD-6]. A structured
  event object is deliberately *not* introduced in R4 (no consumer needs it).
- Service checkpoint callback: `checkpoint_update` currently builds
  `checkpoint.<record.name>.<fail_count>.<status>` — a V1 assumption. R4.5
  introduces a version-neutral accessor: v1 records answer `.name`, v2 records
  answer `.id`, via `step_record_identity(record)`; durable service checkpoint
  ids become stable-ID based for V3 (PC5). The label never enters a durable
  checkpoint id.

---

## 9. Artifact identity (No-TBD 16, 17)

### output_manifest

- **v2 manifest, same filename** `output_manifest.json`, `content_schema:
  confflow.output_manifest.v2` [PD-3]. RFC §17 is explicit about content:
  per terminal `{id, label, artifacts}` (machine identity `id`, display
  `label`). Shape:
  `{"content_schema": "confflow.output_manifest.v2", "terminals": [{"id":
  …, "label": …, "artifacts": ["<relative-to-workdir path>", …]}, …]}`.
- Terminal keys are stable IDs; duplicate labels harmless (A2); label rename
  changes only the label snapshot (A3); paths stay inside the work root
  (A4); digest/size computed as today (A5).
- v1 semantics (`terminals: {name: [paths]}`) unchanged; **no stable ID is
  smuggled into a v1-typed field** (§41).

### workflow_stats

- **v2** `confflow.workflow_stats.v2`, same filename: `steps[]` entries carry
  `id` (+ `label` snapshot) instead of `name`; `terminal_outputs` keyed by id;
  all counters/timing fields identical to v1. Written by the same
  finalize/presenter path, version-dispatched. The adapter's
  `_load_completed_stats` learns to validate both (R4.5).

### Service loader

`_load_artifacts` becomes version-aware on `content_schema` (v1 → today's
projection unchanged; v2 → id/label/artifacts projection, `Artifact.terminal`
carries the id). V1 loader behavior byte-identical (A7).

---

## 10. Worker / remote path (No-TBD 21)

- `worker_attempt` builds `WorkflowRunSpec` from the staged config and calls
  `build_workflow_service` **directly** — the only execution entry that
  bypasses `run_workflow_through_service`. Today a V3 attempt via the worker
  would create state root/SQLite before the engine Guard C fires.
- **R4.5 moves the preflight into `build_workflow_service` itself** [PD-9]:
  one gate, both entry points (`run_workflow_through_service` keeps its own
  earlier preflight for UX earliness; the builder gate is the durable-side
  backstop). Zero-side-effect worker test (50B extension) added before the
  flip.
- Handoff/staging carry the config as content-digested YAML and tasks as
  `input_xyz` paths — version-neutral; V3 config survives handoff (W1/W3).
- Worker execution uses the same engine ⇒ same state v2 / binding v2 /
  manifest v2; no V1 state leakage (W6). If any supported path cannot satisfy
  this, the capability flip does not happen (global stop gate G).

---

## 11. Failure atomicity (No-TBD 20/22 context)

Reuse V1 robust semantics without new silent partial state:

- state writes are atomic (write_atomic_json) at the same boundaries as v1
  (submit, complete, skip, fail);
- a step whose artifact succeeded but whose state write failed ⇒ the state
  shows the step not completed ⇒ strict resume revalidates the on-disk
  artifact through `validate_reusable_artifact` (which checks
  calc manifests/signatures) — existing v1 reconciliation, reused;
- manifest/stats are written once at finalize (as today); a finalize crash
  leaves state `final_status` unset ⇒ resume/attach fails closed;
- status callback failure: v1 semantics (lifecycle callback errors surface via
  the service; state is already persisted) — unchanged.

---

## 12. Slices (dependency-ordered)

Dependency graph:

```
R4.1 identity/state foundation ──► R4.2 fingerprint C + binding v2 ──►
R4.3 runtime projection + ID dirs + execution ──►
R4.4 resume/rerun/pause/cancel ──► R4.5 artifacts/service/worker + flip
```

(Adjusted from the prompt's default: the execution-class constants and state
schema must exist before C can be computed (hence state in R4.1), and the
runtime projection needs C to carry the binding — hence R4.2 before R4.3.
Artifacts/service go last because the manifest/stats v2 writers land with the
execution slice and the loader/callback adaptation only matters once runs
exist.)

### R4.1 — Durable Identity Foundation

- **Scope**: contract constants (`workflow_state.v2`, `workflow_binding.v2`,
  `output_manifest.v2`, `workflow_stats.v2` ids — flip nothing); V3 state
  dataclasses + store (schema dispatch, id-keyed records, atomic write,
  corruption handling, cross-version fail-closed guards incl. PD-10);
  binding-v2 dataclass + build/parse (digests carried, C filled in R4.2);
  `v3_step_dir` path helper + safety tests; `execution_order` verification
  data.
- **Files**: `confflow/contract.py` (ADD constants), `workflow/state.py`
  (ADD v2 types/store), `workflow/v3_paths.py` (ADD), `config/canonical/
  fingerprint.py` (ADD binding-v2 dataclass; NO-TOUCH v1), `contract.py`
  tests; `workflow/state.py` save-guard (PROTECTED-MINIMAL, PD-10).
- **Tests**: S1–S12 (state), P1–P7 (path), B10 (v1 binding goldens).
- **Commit**: `feat(workflow): add V3 durable state identities`
- **Acceptance**: v1 loader/store tests green unchanged; v2 state round-trips;
  cross-version workspace fails closed.
- **Stop gate**: any V1 state/binding/resume behavior change beyond the
  PD-10 refuse-if-v2 guard ⇒ STOP.

### R4.2 — Execution Fingerprint C + Binding V2

- **Scope**: `build_execution_fingerprint_v3(plan_or_definition, inputs,
  runtime_settings)` — A payload + ordered input content digests + cardinality
  + execution-class globals (same frozen constants, PD-4) + per-step resolved
  execution settings (`iprog`, resolved executable paths, resource fields via
  `_EXECUTION_CLASS_STEP_PARAMS`) + executable-settings digest section;
  binding v2 assembly/parse; resume comparison helper returning layered
  diagnostics (A / C:inputs / C:resources / B-audit / B-reject).
- **Files**: `config/canonical/fingerprint.py` (ADD v3 execution fingerprint;
  NO-TOUCH v1), `workflow/state.py` (binding v2 wiring), tests.
- **Tests**: B1–B10; matrix rows of §3 as parametrized cases.
- **Commit**: `feat(workflow): bind V3 resolved execution context`
- **Acceptance**: C changes exactly per the matrix; A goldens unchanged;
  `CAPABILITIES[V3].execute` still False.
- **Stop gate**: A/B/C roles cannot be stated without guessing ⇒ STOP; any
  need to modify Definition Fingerprint A ⇒ STOP (global gate H).

### R4.3 — V3 Runtime Projection + ID Layout + Step Execution

- **Scope**: `build_workflow_v3_runtime_plan`; engine V3 branch (post-guard,
  dead until flip): `run_v3_workflow` internal seam (No-TBD 23/51: the
  internal runner is directly callable for tests, plus a scoped autouse
  fixture that patches `CAPABILITIES` and **always restores** for end-to-end
  engine tests; the user-facing guard layers stay untouched); ID-based step
  dirs; disabled bypass (V1 semantics); checkpoint runtime resolution +
  `run_calc_step` `input_chk_dir` override (PD-7); state v2 writes at v1
  boundaries; stats/manifest v2 writers (version-dispatched);
  `step_started_callback` id-as-name (PD-6).
- **Files**: `workflow/engine.py` (MODIFY — V3 branch; PROTECTED-MINIMAL),
  `workflow/v3_runtime.py` (ADD runtime plan + adapters), `workflow/
  step_handlers.py` (MODIFY — optional param, PROTECTED-MINIMAL),
  `workflow/finalize.py` + `presenter.py` (MODIFY — version dispatch),
  tests.
- **Tests**: E1–E10 (fake handlers, no Gaussian/ORCA), P8, manifest/stats v2
  writer tests, DR/CS unchanged.
- **Commit**: `feat(workflow): execute V3 steps by stable ID`
- **Acceptance**: fake-handler V3 runs complete with state v2 keyed by IDs;
  V2 engine paths byte-identical; user-facing V3 execution still blocked.
- **Stop gate**: runtime path ever uses label/name/index ⇒ STOP (global E).

### R4.4 — V3 Resume / Rerun / Pause / Cancel

- **Scope**: strict V3 resume (recompute A → C → B-audit; artifact reuse via
  existing validators with V3 dirs/params; label/reorder/annotation neutrality;
  `execution_order` verification); rerun-failed V3 (selector = stable id;
  successor reset by graph relation; cleanup scoped to `steps/<id>/` dirs);
  pause/cancel over v2 state (beacons unchanged); corrupted/stale state
  rejection; V1-state-found ⇒ fail closed (§37).
- **Files**: `workflow/engine.py` (resume branch), `workflow/
  resume_validation.py` (MODIFY — accept V3 contexts), `workflow/
  rerun_failed.py` (MODIFY — V3 selector + reset), tests.
- **Tests**: R1–R10, RF1–RF6, PC1–PC6, S3–S10 exercised end-to-end.
- **Commit**: `feat(workflow): resume and rerun V3 workflows`
- **Acceptance**: matrix §3 enforced end-to-end; V2 resume/rerun suites green
  unchanged.
- **Stop gate**: label/order change causing resume failure ⇒ STOP (global D).

### R4.5 — Artifacts / Service / Worker Integration + Capability Flip

- **Scope**: `_load_artifacts` v1/v2 dispatch; `_load_completed_stats` v2;
  checkpoint callback identity accessor (PD-8); worker-path guard move into
  `build_workflow_service` (PD-9); W1–W6; A1–A7; release-facing assertions;
  **final commit**: `feat(workflow): enable Workflow V3 execution` — flips
  `CAPABILITIES[V3].execute=True` and nothing else. Rollback = flip back to
  False (kill switch); parse/validate/upgrade/show/dry-run unaffected.
- **Files**: `application/execution/workflow_adapter.py` (MODIFY),
  `application/execution/workflow_adapter.py::build_workflow_service`
  (MODIFY — preflight), `config/canonical/execution_versions.py` (flip, last
  commit), `worker_attempt.py` (NO-TOUCH), tests.
- **Commit(s)**: `feat(workflow): integrate V3 runtime with execution service`
  then `feat(workflow): enable Workflow V3 execution`.
- **Acceptance / flip precondition**: every R4 suite green on every supported
  execution path (local CLI, through-service, worker), zero-side-effect guard
  tests still passing for invalid V3, V2 suites green.
- **Stop gate**: any supported path still writing V1 state ⇒ STOP (global G).

---

## 13. File Change Matrix

| File | R4.1 | R4.2 | R4.3 | R4.4 | R4.5 | Reason |
|---|---|---|---|---|---|---|
| `confflow/contract.py` | ADD v2 schema ids | – | – | – | – | schema ids only |
| `confflow/workflow/state.py` | ADD v2 types/store; v1 save-guard (PD-10) | binding v2 wiring | – | resume use | – | durable state |
| `confflow/workflow/v3_paths.py` | ADD | – | use | – | – | path safety |
| `confflow/config/canonical/fingerprint.py` | ADD binding v2 | ADD execution fingerprint C | – | – | – | NO-TOUCH v1 |
| `confflow/workflow/v3_runtime.py` | – | – | ADD | use | – | runtime projection |
| `confflow/workflow/engine.py` | – | – | MODIFY (V3 branch) | MODIFY (resume) | – | PROTECTED-MINIMAL |
| `confflow/workflow/step_handlers.py` | – | – | MODIFY (optional `input_chk_dir`) | – | – | PROTECTED-MINIMAL |
| `confflow/workflow/finalize.py` | – | – | MODIFY (manifest/stats v2 write) | – | – | version dispatch |
| `confflow/workflow/presenter.py` | – | – | MODIFY (stats v2) | – | – | version dispatch |
| `confflow/workflow/resume_validation.py` | – | – | – | MODIFY (V3 contexts) | – | reuse validators |
| `confflow/workflow/rerun_failed.py` | – | – | – | MODIFY (V3 selector) | – | stable-ID rerun |
| `confflow/workflow/plan.py` | – | – | NO-TOUCH (planning boundary) | – | – | stays planning-only |
| `confflow/workflow/dry_run.py` / `config_show.py` | – | – | NO-TOUCH | – | – | R3 surface done |
| `confflow/workflow/step_naming.py` | – | – | NO-TOUCH | – | – | V2 allocator preserved |
| `confflow/application/execution/workflow_adapter.py` | – | – | – | – | MODIFY (loader v2, callback id, builder preflight) | integration |
| `confflow/application/execution/service.py` / `sqlite.py` / `state_root.py` | – | – | – | – | NO-TOUCH | version-neutral |
| `confflow/worker_attempt.py` / `worker_handoff.py` / `worker_staging.py` | – | – | – | – | NO-TOUCH | version-neutral |
| `confflow/control_worker.py` | – | – | – | – | NO-TOUCH | guard moved into builder |
| `confflow/config/canonical/execution_versions.py` | – | – | – | – | flip (last commit) | kill switch |
| `confflow/workflow/runtime_context.py` | – | – | NO-TOUCH | – | – | step-identity-free (work-dir/failed-dir/checkpoint/log only); verified reusable as-is |
| calc/confgen algorithms, ORCA/Gaussian policies | NO-TOUCH all slices | | | | | protected |

---

## 14. Test matrix (consolidated)

- **State**: S1–S12 (§52 of prompt, mapped to the schema above).
- **Binding/Fingerprint**: B1–B10 + §3 matrix rows parametrized.
- **Paths**: P1–P8.
- **Runtime execution**: E1–E10 (fake calc/confgen handlers; deterministic
  order; failure by ID; partial-completion retry).
- **Resume**: R1–R10.
- **Rerun**: RF1–RF6.
- **Pause/cancel**: PC1–PC6.
- **Artifacts**: A1–A7.
- **Worker/remote**: W1–W6 (synthetic producer / fixture-agent seam).
- **V2 regression**: existing v1 state/binding/resume/rerun/characterization/
  durable-digest suites stay green every slice; `CAPABILITIES[V3].execute`
  asserted False until the final flip commit.

---

## 15. Capability flip (No-TBD 23, 24)

- **Prerequisites**: R4.1–R4.5 all merged, every suite green on every
  supported execution path (local CLI, service, worker), zero-side-effect
  guard tests still pass (invalid V3 still blocked; valid V3 now runs),
  adversarial review of the runtime plan accepted.
- **Exact final change**: one commit —
  `feat(workflow): enable Workflow V3 execution` —
  `CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3] = VersionCapability(parse=True,
  execute=True)` plus release-note/capability-payload assertions
  (`_build_capability_payload` already exposes versions via the table).
  If the flip commit needs more logic than this, an earlier slice is
  incomplete.
- **Rollback**: flip `execute` back to `False` — every guard layer resumes
  blocking; parse/validate/upgrade/show/dry-run/planning remain available;
  existing V3 state/binding on disk are simply dormant (no cleanup needed).

---

## 16. R4 / R5 / R6 / R7 boundaries

- **R4**: durable execution identity/runtime only. No `params.theory`, no
  typed artifact capability beyond manifest v2, no editor manifest V3, no
  JobDesk V3 publication, no recipe GUI semantics.
- **R6** remains the owner of effective-dataflow cardinality validation;
  R4's runtime bypass behavior is the V1-equivalent, documented in §7.

---

## 17. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| PD-2 (input identity narrower than V1) accepted too casually | resume false-positive on renamed inputs | explicit review flag; recorded `input_files` paths keep provenance; conservative fallback is a one-line PD revision (bind paths) before flip |
| Worker path guard gap (PD-9) missed | remote V3 creates service state before failing | guard moved into `build_workflow_service` with its own zero-side-effect test before flip |
| Engine branch grows beyond minimal | V2 regression | engine changes kept to dispatch + resume branch; E10/V2 suites gate every slice |
| Manifest v2 consumer assumptions (JobDesk `Artifact.terminal`) | artifact identity drift | `Artifact.terminal` carries the id for v2; v1 unchanged; A-tests pin |
| Windows reserved-name IDs | path collision on nt hosts | helper rejects on `os.name == "nt"` (PD-11); POSIX-only service today |
| Canonicalization version churn | spurious mass resume rejection | version constant is code-frozen; B reject only on actual change |

---

## 18. Adversarial review checklist (roles → probes the plan must survive)

- **V2 existing user**: v1 state/binding/layout untouched; only the PD-10
  refuse-if-v2 save guard touches v1 code, and it cannot change any outcome
  where v1 state was loadable.
- **V3 fresh-run user**: clean work-dir → runs; `steps/<id>/` only.
- **V3 resume user**: A/C/B layering decides; mismatch names the layer.
- **V3 label-rename / reorder user**: resume succeeds (identity is id).
- **V3 multi-input user**: order-bound C; reorder rejects with reason.
- **V3 checkpoint user**: `steps/<from_id>/backups`, no name/index guessing.
- **V3 rerun user**: ID selector; successors reset; scoped cleanup.
- **worker/remote executor**: guarded at `build_workflow_service`; W1–W6.
- **ExecutionService**: stays step-identity-free; only checkpoint-id strings
  change vocabulary.
- **artifact consumer**: v1/v2 dispatch by `content_schema`; `terminal` = id.
- **future JobDesk (R7)**: manifest v2 records `{id,label,artifacts}` per RFC.
- **security/path-safety reviewer**: runtime re-validation of ID grammar;
  traversal impossible; Windows stance documented (PD-11).

---

## 19. No-TBD answers (1–24)

1. state v2 schema id — `confflow.workflow_state.v2`
2. state file naming — same `.workflow_state.json`, content-schema dispatched
3. binding v2 schema id — `confflow.workflow_binding.v2`
4. binding file naming — no separate file; embedded in state v2
5. stable-ID path layout — `<work_dir>/steps/<id>/` (+ run-level files at
   root per RFC §17)
6. A role — semantic identity; mismatch ⇒ resume reject
7. B role — producer/schema provenance; audit warning on producer/schema
   change, **reject on canonicalization-version change**
8. C role — execution identity; **the layer resume binds** (RFC §16.D)
9. resume compares — A digest, then C digest (with state-stored input/settings
   sections for layered reasons), then B audit
10. label rename resume — **succeeds** (no A/B/C change)
11. step reorder resume — **succeeds** (no A/B/C change)
12. input identity — ordered content digests + count; paths/basenames recorded
    but not bound (PD-2)
13. input ordering — **ordered** (positional multi-input consumption) [PD-2]
14. executable identity — resolved program setting (`iprog` + realpath);
    binary digest not bound in R4 (PD-5)
15. producer/schema/canonicalization change — producer/schema: allowed +
    audit warning; canonicalization: reject (PD-1)
16. output manifest — v2 schema id `confflow.output_manifest.v2`, same
    filename, terminals = `[{id, label, artifacts}]`; v1 unchanged
17. workflow stats — v2 `confflow.workflow_stats.v2`, same filename, id+label
    steps, id-keyed terminal outputs
18. callback identity — `step_started_callback` keeps its signature with the
    stable id as `name`; durable service checkpoint ids use a
    `step_record_identity` accessor (v1 name / v2 id); label never durable
19. checkpoint runtime lookup — `from_step` → `steps/<from_id>/backups` via
    validated ancestor id; runtime file/program checks stay in the calc
    machinery
20. disabled runtime behavior — v1-identical bypass: `skipped` record,
    predecessor-output forwarding, consuming-step failure on unsupported
    effective cardinality (R6 static validation later)
21. worker/remote V3 support — supported in R4 via the builder-level guard +
    W1–W6; if any supported path cannot comply, the flip does not happen
22. cross-version workdir — fail closed both directions (v3 refuses v1 state;
    v1 save refuses a v2 file); explicit clean/new-run required
23. capability flip condition — all R4 suites green on all supported paths +
    adversarial plan review accepted; flip is a single one-line commit
24. rollback — flip `execute=False`; guards re-block; inspection surfaces
    stay available; on-disk V3 state needs no cleanup
