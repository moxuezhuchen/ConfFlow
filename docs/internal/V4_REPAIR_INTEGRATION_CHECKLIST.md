# V4 Repair — Wave-1 Integration Checklist

Scope: verify the four concurrent workers' outputs (A/B/C/D) against the
authoritative `V4_REPAIR_EXECUTION_FREEZE.md` and the reconciled
`V4_REPAIR_CONTRACT_DECISIONS.md`, before any wave-2 work. No production/test
edits here; lead integrates. Wave-2 (E/F/G/H) starts only after this gate.

Baseline: `bfdae1c` / `feat/workflow-v4-greenfield`. Pre-edit `tests/v4`:
2369 passed, 2 failed, 3 skipped —
`test_v44_faults.py::TestFaultCProducerCrash::test_remote_replay_after_crash_imports_once`,
`TestFaultEPreCommitCrash::test_reimport_after_precommit_crash` (unexpected
installed-ORCA invocation; fix transport/test isolation, never soften
completion). Full log: `/tmp/confflow-v4-baseline-tests.log`.
Real native: ORCA `/opt/orca611/orca` (6.1.1); Gaussian `/opt/g16` (approval
may be required). Real JobDesk consumer:
`/opt/jobdesk-v2-v4/src/jobdesk_v2/application/editor/contract/v4.py` +
`application/runs/v4_results.py` (JobDesk repo-relative); read-only without
supervisor coordination.

---

## 1. Exact cross-owner APIs (signatures the lead verifies verbatim)

### A → all (registry; A owns `execution/registry.py`, `execution/contracts.py`)

- [ ] `resolve_executor(capability) -> ExecutorContract` (or A-documented equivalent)
- [ ] `resolve_profile(name) -> ResultProfileSpec`
- [ ] `resolve_check(name) -> CheckSpec`
- [ ] `resolve_recovery(name, *, adapter=None) -> RecoverySpec`
- [ ] `resolve_program(name) -> ProgramAdapter` (program ↔ adapter ↔ capability triple-check)
- [ ] Application/batch/remote resolve ONLY via the above; no direct
  `PROFILES`/`CHECKS`/`RECOVERIES` consultation (those dicts are
  implementation suppliers only).
- [ ] A did NOT touch C-owned impl files, fingerprint, assembly, application,
  batch, domain, or existing tests.
- [ ] Report present: `docs/internal/V4_REPAIR_A_REPORT.md` + NEW
  `tests/v4/test_repair_capabilities.py` only.

### B → all (identity/binding/mapping)

- [ ] `ResultRef` typed + producer-scoped `result_id` on `ScientificResult`
  (producer context + semantic discriminator; value digest stays value-only;
  legacy ID-less records read-only, never silently valid).
- [ ] `ResultSet.by_id(result_id)`; result-port IDS selects on `result_id`;
  per-ID rule: MANY-port multi-ID selection legal; duplicate/ambiguous
  candidates for the SAME requested ID fail.
- [ ] Assembly: `StepOutputs` carries actual `StepStatus` + completion
  provenance (NO `ProducerStatus` type exists anywhere); healthy-step items
  retained with scoped errors; actual partial/failed/cancelled gating;
  pairing validated AFTER atom mapping.
- [ ] Mapping: `permutations: Mapping[str, tuple[int, ...]]` per-slot form;
  single permutation is a documented uniform shorthand; ONE shared validation
  helper used by assembly AND executor before element compatibility.
- [ ] Digests: structure payload = entity + geometry + effective
  charge/multiplicity + freeze + group + role + parents/root; result payload =
  `ResultRef` + scientifically relevant provenance; locators/paths/GUI/
  scheduler excluded. Version bumps owned by B; old generations fail closed.
- [ ] B did NOT touch application/importer, batch, profile, registry,
  contracts, or existing tests.
- [ ] Report present: `V4_REPAIR_B_REPORT.md` + NEW
  `tests/v4/test_repair_identity.py` only.

### C → D/batch/remote (executors; C owns work_item_executor + binding_resolution)

- [ ] All four executors: `execute(work_item, context, *, should_cancel=None)
  -> WorkItemResult` (NEW `execution/confgen_executor.py`,
  `execution/transform_executor.py`; calculation = `WorkItemExecutor`).
- [ ] NEW `execution/binding_resolution.py` takes domain/execution types +
  explicit args ONLY (no `V4RunRequest`/application import); planned fields
  win, request defaults fill gaps; env/target/walltime/executable preserved;
  concrete signature published in `V4_REPAIR_C_REPORT.md`.
- [ ] `ItemExecutionContext` accepts D-passed real attempt number; C creates
  bounded hashed item dir + distinct attempt child (sanitize =
  presentation only; no unbounded logical-key filenames).
- [ ] Native boundary: adapter-only syntax; recovery never sets
  route/env/workdir/walltime; checkpoints require
  `{checksum, subject, role, run-relative locator}` + verified + consumed.
- [ ] Single step-seed authority threaded into digest/native/handoff;
  stochastic gating by actual scientific capability (incl. GOAT).
- [ ] C did NOT edit `execution/contracts.py` (proposals via lead), registry,
  batch, domain identity, assembly, application, existing tests; no full
  IRC/GOAT/NEB rewrites.
- [ ] Report present: `V4_REPAIR_C_REPORT.md` + NEW
  `tests/v4/test_repair_executors.py` only.

### D → all (durable loop; D owns batch + application + importer)

- [ ] `import_xyz(text, *, source_name="<input>",
  entity_ids: tuple[str, ...] | None = None) -> StructureSet` (opaque fresh
  IDs incl. UUID-acceptable; explicit sequence count-validated; NO geometry
  map, NO `start_index`); per-named-input source digest + ordered entity
  records persisted pre-execution; same-bytes resume reloads; reordered raw
  XYZ without IDs fails/requires new import.
- [ ] Run loop: NO published-file early return before current-item reuse
  checks; per-item register → `evaluate_reuse` → commit; completed never
  recomputed; `ensure_step/transition_step/save_run_state` wired; new
  generations refuse-or-branch (history never overwritten).
- [ ] `verify_for_publication` failure refuses publication (no gap exception);
  blocked diagnostics stay lifecycle/report data, never publication.
- [ ] Analysis routed via F's in-package item adapter through the same
  claim/commit/publish path; default ONE whole-set aggregation WorkItem over
  SINGLE bindings (NO aggregation setting, NO app merge/copy loop).
- [ ] Manifest: real `typed_digest(STEP_RESULT_DIGEST_KIND, …)` digests,
  exact `{completed, failed, cancelled}` counts, full diagnostics,
  `{capability, step_id}` + `ResultRef` analyses, `{role, checksum, locator}`
  artifacts, schema-validated, atomically published; no placeholders.
- [ ] D did NOT touch work_item_executor, registry, contracts, domain
  identity, assembly, native, old entrypoints, or existing tests.
- [ ] Report present: `V4_REPAIR_D_REPORT.md` + NEW
  `tests/v4/test_repair_persistence.py` only.

### Cross-seam probes (lead runs)

- [ ] A-resolvers → B validation/fingerprint inputs; B `AssemblyResult` +
  digests → C contexts + D reuse/publication; C binding fn + dirs ← D attempt
  + request defaults; D application dispatches registry executors incl. F's
  analysis adapter. Any missing symbol recorded as integration dependency —
  no shim/fallback present in any report.
- [ ] `git status` shows ONLY the four NEW test files, four NEW reports,
  owner-listed source edits, and these two docs. No other modified files.

---

## 2. All 22 roots (ROOT CAUSE → FIX owner → TEST → STATUS gate)

| # | Root cause (plan §2) | Fix owner / contract § | Test (NEW file) | Gate |
|---|---|---|---|---|
| 1 | Formal entries run old engine (`cli.py`, `workflow_adapter.py`, `control_worker.py`) | Final lead, wave 3 (§0.10) | H (wave 2) full entry matrix | All formal entries reach V4; V2/V3 migration-only |
| 2 | File-exists `continue` skips reuse checks (`v4_run.py:210`) | D §4.1 | `test_repair_persistence.py` | Mutated definition/input/artifact never reuses; failed never returns old failure |
| 3 | Assembly errors ignored → empty success | B §2.4 + D caller gate | `test_repair_identity.py` + persistence | Missing-input Opt→SP errors; no zero-item completed; per-step retention |
| 4 | Producer status dropped; failed subsets consumed | B gating + D enforcement | identity + persistence | Partial matrix (COMPLETED/PARTIAL+subset/PARTIAL+require/FAILED/CANCELLED) |
| 5 | Handcrafted Gaussian/ORCA/GOAT dialects | G (wave 2); C bounds seam §3.2 | H real-format fixtures | Real-version render/parse; fakes speak real format |
| 6 | Subject chain broken (profile binds wrong geometry; TSPES mismatches) | B §2.2 + C impl + F chains | identity + executors (+F wave 2) | Measurement/lineage rule; full TS/endpoint chains |
| 7 | Analysis first-match (`thermochemistry.py:159`) | B per-ID selection + F ambiguity | identity (+F wave 2) | Order-swap invariant; ambiguity fails |
| 8 | Manifest shape drift + in-memory publish (`v4_run.py:386`) | D §4.4, finalized at integration | persistence | Schema-validated durable manifest; real digests/refs/artifacts |
| 9 | ConfGen/Transform no route (analysis/calculation branch) | A registry + C impls + D dispatch | capabilities + executors + persistence | 4-way dispatch; ConfGen/Transform never Calculation/legacy |
| 10 | Analysis memory bypass + copy loop | D route + F adapter (§4.2) | persistence (+F wave 2) | One aggregation item; durable commit; partial semantics from F |
| 11 | Whole-native → policy passthrough drops assignment/partial | F split (wave 2) | F wave-2 tests | Explicit assignment effective; `analysis_step_id` never None |
| 12 | `xyz:index` + digest-blind group/role/lineage | B identity/digests + D importer/persistence §2.2/2.3 | identity + persistence | Explicit-ID reorder; reordered-raw-XYZ fails; group changes invalidate |
| 13 | Value digest / `kind:subject` as identity; IDS no-op on results | B §2.1/2.3 | identity | Same-value distinct sources distinguished; per-ID cardinality |
| 14 | Hardcoded binding (`v4-run`, empty env, dropped target/walltime) | C `binding_resolution.py` + D wiring §3.3 | executors + persistence | Planned-wins resolution; measured env recorded |
| 15 | Remote single-structure/ports/digest/provenance/attempt gaps | E (wave 2; envelope MUST version) | E/H wave-2 parity | Named QST/NEB + multi-port + attempt reconciliation |
| 16 | Pre-mapping order check; one permutation for all slots | B §2.5 | identity | Per-slot maps; mapping-before-compatibility in both paths |
| 17 | Unverified/unconsumed checkpoints staged as success | C §3.2 | executors | Missing/unverified/unconsumed ⇒ `ARTIFACT_ERROR`/`artifact_unsupported` |
| 18 | Non-durable gap publishes; RunState unwired; attempt races | D §4.1 | persistence | Strict commit→publish; lifecycle wired; cross-restart races reconciled |
| 19 | `sanitize` collisions, no attempt isolation | C dirs + D attempt §4.3 | executors + persistence | Collision pair isolated; retries never share attempt dirs |
| 20 | Dual capability authority; opaque/unknown-program drift | A §1 | capabilities | One registry; opaque executes; unknown program rejected |
| 21 | Recovery rewrites native/binding; params override env | C §3.2 | executors | Recovery intent-only; adapter owns syntax; binding owns env |
| 22 | Dual GOAT seed; `seed=None` compiles | C §3.4 (capability-scoped) | executors | One seed across digest/native/remote; mismatch ⇒ `native_input_error` |

STATUS rule: each row needs ROOT CAUSE / FIX / TEST / STATUS with evidence;
single passing unit test ≠ integration. Any new drift blocks and is added
here. Final output is only `READY_FOR_FRESH_ASTRA_REVIEW = YES / NO`.

---

## 3. Wave-1 integration order (lead executes)

1. Collect `V4_REPAIR_{A,B,C,D}_REPORT.md`; confirm exclusive ownership
   (table §1) and no out-of-owner edits.
2. Lock shared edits: `contracts.py` (A applies C proposals), digest versions
   (B), `binding_resolution` signature (C→D), F-adapter shape (F consult, D
   routes — no wave-2 F implementation yet).
3. Merge A → B → C → D; resolve conflicts as interface decisions (record
   here), never parallel same-file edits.
4. Run: scoped NEW tests → `tests/v4` full → `ruff`/`format`/`mypy` →
   crash/resume matrix → production-path E2E (no fabricated intermediates).
5. Fill §2 STATUS column per drift; append new drift rows if found.
6. Publish wave-1 integration report; ONLY then authorize wave 2 (E/F/G/H
   with exclusive ownership: E `remote/*` incl. envelope; F `analysis/*` +
   analysis recipes; G parsers/rendering after C stops; H NEW tests +
   evidenced old-test updates, never prod code).
