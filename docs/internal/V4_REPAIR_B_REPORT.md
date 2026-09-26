# V4 Repair — Worker B Report: identity / ResultRef / Binding / mapping

Scope: wave-1 ownership per `docs/internal/V4_REPAIR_EXECUTION_FREEZE.md`:
`confflow/domain/{structure,result,binding,work_item}.py`,
`confflow/workflow/v4/{assembly,fingerprint}.py`,
`confflow/execution/{output_identity,named_structures,atom_mapping,execution_adapters}.py`
plus supervisor-authorized expansion: `workflow/v4/graph.py` RESULT-IDS
selector support ONLY;
NEW `tests/v4/test_repair_identity.py`.
No importer/application edits (D owns them). No other-owner files or existing
tests modified. No commit/push/merge/tag/release.

## ROOT CAUSE

1. **Result value-identity conflation (plan §2 items 7, 13).**
   `ScientificResult` had no independent reference identity; `value_digest`
   (value-only) and ad-hoc `kind:subject` strings stood in for entity
   identity, so equal values from different producers/subjects/methods
   collapsed, multi-level results could not be uniquely referenced, and
   `ResultSet.first()` positional first-match let input order pick the
   theory level.
2. **Producer-generation aliasing (supervisor correction).**
   `WorkItem.id` is `wi:<logical_key>`, not semantic identity, and
   method/basis strings alone do not capture solvent/native/input settings:
   an id built only from step/item/kind/subject/method/basis aliases
   results across changed scientific generations. Deterministic identity
   must bind the complete producer generation digest.
3. **Provenance-blind digests (items 6, 12).**
   Assembly `_structure_payload` hashed only geometry/charge/multiplicity/
   freeze; `group_key` changes reused old digests, and `Result.digest_payload`
   hashed only kind/value/subject, so provenance/source changes were invisible
   to reuse.
4. **Assembly wiped unrelated work and consumed blindly (items 3, 4).**
   Any single error cleared the whole plan (`AssemblyResult((), …)`), a
   global `any(is_error)` gate skipped all later steps after the first bad
   step, producer failed/partial/cancelled/unknown state was never checked
   at run time, and IDS selectors on result ports were silently ignored
   (all results passed through) and rejected at compile time.
5. **Mapping vetoed before it was applied (item 16).**
   `_check_paired_structures` demanded identical element order before
   consulting the explicit permutation, so legal O/H-reordered QST inputs
   with a valid mapping were rejected; one uniform permutation covered all
   non-reference slots with no per-slot (QST3 product≠guess) expression.

## FIX (B-owned files only)

- `confflow/domain/result.py`
  - `ResultRef(result_id)` typed reference; `ScientificResult.result_id:
    str | None` (legacy `None` allowed for read-only records only).
  - `make_result_id(*, step_id, work_item_id=None, kind,
    subject_structure_id=None, discriminator=None, program=None, method=None,
    basis=None, producer_digest) -> str`: deterministic `typed_digest` over
    the full producer/subject/kind/discriminator tuple PLUS the producing
    work item's `semantic_digest` passed verbatim as **`producer_digest`**
    (required, `sha256:<hex>`-validated fail-closed). Retries/reordering of
    the same semantic item retain refs; any changed science moves the
    producer digest and mints new refs. Method/basis alone, value equality,
    and paths/ordinals never substitute.
  - `identity_digest` (producer context + kind + provenance; `None` for
    legacy), `ref`, `is_production`; `digest_payload` now carries
    `result_id` + `identity_digest` + `provenance` + sources
    (`value_digest` stays value-only).
  - `ResultSet.select_ids(ids) -> (selected, missing, ambiguous)`:
    per-requested-id resolution; only zero-candidate (missing) or
    multi-candidate-for-the-SAME-id (ambiguous) fail; distinct ids on MANY
    ports each resolve once; no global exactly-one rule.
  - `ResultSet.select_unique(kind, *, subject_structure_id=None,
    result_id=None)`: zero/ambiguous both raise `InvalidResultError`.
  - `require_production_ids(results)`: fail-closed gate for C/F emission
    paths (legacy `result_id=None` rejected; stamping stays C/F-owned).
  - `find_duplicate_result_ids(results) -> tuple[str, …]`: emission-time
    collision detector.
- `confflow/domain/structure.py`
  - `StructureRecord.reuse_payload(effective)` / `structure_reuse_payload`:
    entity id + geometry + effective charge/multiplicity/freeze + group +
    role + parent_ids + lineage root. Locators/scheduler/GUI excluded.
    `scientific_payload()`/`has_same_content()` (pure content equality)
    unchanged.
  - `check_structure_id_conflicts(records)`: same id on multiple ports is
    legal only with identical reuse payloads, else conflict triple.
- `confflow/domain/work_item.py`: `WORK_ITEM_DIGEST_KIND =
  "confflow.work_item.v2"` (`WORK_ITEM_DIGEST_KIND_V1` retained as a named
  constant). Payload shape unchanged, so v1 digests never equal v2: old
  generations fail closed. `work_item_semantic_digest` signature unchanged.
- `confflow/workflow/v4/assembly.py`
  - `StepOutputs` gains `status: StepStatus | None`,
    `producer_step_digest`, `item_statuses`, plus `has_known_status`.
    **No fail-open default**: `status=None` is a read-only legacy marker;
    assembly rejects consuming it with a scoped `cardinality_mismatch`
    error (`producer_status: "unknown"`). Run inputs are unaffected (not
    producer outputs, need no status).
  - `_apply_selector`: IDS on result ports filters via `select_ids`
    (missing + `ambiguous:<id>` reported); structure/artifact IDS unchanged.
  - `_producer_state_diagnostic`: unknown/FAILED/CANCELLED consumers get
    scoped errors; PARTIAL requires `partial_consumption == ACCEPT_SUBSET`.
  - Per-step scoping: waiting stays a `not_assemblable` warning skip;
    an errored step yields zero items for itself with per-item
    `logical_key` diagnostics, but healthy steps' items are retained
    (the old whole-plan wipe and global `any-error` skip are gone).
    Legal-empty MANY still completes.
  - `_structure_payload` delegates to `reuse_payload(effective)`.
  - `_parse_step_atom_mapping` + `_check_paired_structures(…, mapping)`:
    mapping validated FIRST via the shared helper; identity disagreement
    maps to `atom_count_mismatch`/`element_mismatch` (existing reasons
    preserved); valid explicit mappings accept reordered slots.
  - Per-item structure-id conflict check via `check_structure_id_conflicts`.
- `confflow/workflow/v4/graph.py` (authorized expansion, minimal diff):
  `_validate_selector` no longer rejects `IDS` on `RESULT` ports; IDS is
  valid on every port kind with per-id missing/ambiguous enforcement at
  assembly.
- `confflow/execution/atom_mapping.py`
  - `AtomMapping` gains `permutations` (FrozenDict, default empty).
    `permutation` (uniform shorthand) and `permutations` (per-slot) are
    mutually exclusive (dual authority → `atom_mapping_invalid`).
  - `normalize_to_per_slot(mapping, slots)`: identity → `{}`; uniform →
    every non-reference slot; per-slot → as-is. Single normalization point.
  - `validate_mapping_for_slots(mapping, slot_atoms) -> per-slot dict`:
    the ONE shared helper assembly and executor call BEFORE compatibility.
    `validate_atom_mapping` delegates to it (uniform semantics unchanged).
  - `atom_mapping_digest_payload`: legacy shape preserved when no per-slot
    mapping; per-slot adds sorted `permutations` member.
  - `reorder_slot_to_reference(…, slot=None)`: per-slot mappings reorder
    with `permutations[slot]`.
- `confflow/execution/named_structures.py`
  - `validate_slots_with_mapping(resolved, mapping)`: explicit
    mapping-before-`validate_named_compatibility` ordering contract for C.
- Untouched by design: `domain/binding.py` (model already expresses
  IDS/cardinality/pairing/partial_consumption), `workflow/v4/fingerprint.py`
  (step axis already covers science; no version change needed),
  `execution/output_identity.py` + `execution_adapters.py` (main-frozen).
- `confflow/domain/__init__.py`: re-export new symbols only.

## TEST

- NEW `tests/v4/test_repair_identity.py` (**23 tests, all pass**):
  identity vs value separation, producer-digest generation binding
  (retry-retains / changed-science-renews / shape validation), legacy-has-
  no-identity, per-id select, unique-selection ambiguity, production gate +
  duplicate detection, reuse-payload axes, id-conflict detection,
  provenance in digest, v2 kind, uniform/per-slot mapping, dual-authority
  rejection, shared mapping-before-compatibility ordering, failed /
  cancelled / unknown-status gating with healthy-step retention, partial
  reject (compile `partial_consumption_undefined`) and partial accept
  (assembles), ResultRef IDS end-to-end through compile→assembly,
  group-key digest sensitivity.
- Scoped runs (`.venv/bin/python`):
  - repair + domain-identity + atom-mapping + named + binding:
    **191 passed**.
  - `test_assembly`: 24 passed, 2 failed (both intended, see STATUS).
- `ruff check` + `ruff format`: clean on all B-owned files. `mypy` on
  B-owned files: clean (remaining tree errors are in A/C-owned files,
  e.g. `execution/registry.py`).

## STATUS

Implemented, scoped-tested, uncommitted. Both supervising-review shims
removed: (1) no `None`-as-`COMPLETED` anywhere (`effective_status`
deleted; unknown status is a scoped assembly error); (2) no
compile-fallback in tests — RESULT IDS goes through the real compiler
(graph fixed) and the end-to-end test asserts `compiled.ok`. NOT a
whole-architecture repair claim: executor stamping (C), analysis
stamping (F), persistence/application (D), remote (E), native parsers
(G), and final integration stay with their owners + lead.

Expected old-test breaks (do NOT "fix" by restoring old semantics or
editing old tests; H updates with explicit freeze evidence):

- B-intended, freeze-mandated:
  - `test_assembly.py::…::test_identical_content_keeps_reuse_digest_across_entity_ids`
    (entity id now in reuse identity).
  - `test_digest_axes.py::…::test_work_item_digest_golden` (v2 digest).
  - Status-less `StepOutputs` producers now fail closed:
    `test_assembly.py::…::test_downstream_subject_matching_from_materialized_outputs`,
    `…::test_result_content_feeds_downstream_digest`,
    `test_compiler.py::…::test_disabled_passthrough_assembly_uses_upstream_outputs`.
    Each must stamp the real `StepStatus`.
- Concurrent-owner, not B (A registry/capability changes):
  `test_two_per_structure_drivers_rejected`, `test_adapter_change_moves_step_digest`,
  `test_check_not_supported_by_profile`,
  `test_opaque_profile_without_checks_compiles`.
- Integration-pending (D already enforces B's `require_production_ids` /
  `find_duplicate_result_ids` in `domain/publication.py` +
  `persistence/publication.py`; C has not stamped yet, so real-execution
  suites such as `test_v45_tspes`, `test_v45_reaction_chain`,
  `test_v44_resume_remote`, `test_v46_analysis` fail at publication with
  identity-less results): unblocks when C stamps every emitted result
  (see dependency 2).

## Concrete integration dependencies (for lead; no cross-file patches made)

1. **Done by B (was lead/A): graph RESULT IDS** — `graph.py`
   `_validate_selector` allows IDS on all port kinds. No further action.
2. **C — executor stamping (unblocks publication)**: stamp every emitted
   profile result with `make_result_id(...)` passing the emitting work
   item's `semantic_digest` VERBATIM as **`producer_digest`** (exact
   keyword; no introspection shim — call it
   `producer_digest=work_item.semantic_digest`). Include a semantic
   `discriminator` for multi-output siblings (endpoint direction,
   image/conformer ordinal, TS-candidate role) so siblings never share an
   id. Call `require_production_ids()` before publish. Executor +
   assembly must both call `validate_mapping_for_slots`
   (assembly already does; wire executors via
   `validate_slots_with_mapping`). C report should publish the concrete
   `ItemExecutionContext` signature for D.
3. **F — analysis stamping**: same stamping/gating duty for
   analysis-emitted results (also with real `producer_digest`); replace
   `ResultSet.first()` first-match consumption with
   `select_unique`/explicit `ResultRef` selectors.
4. **D — persistence/application**: reuse checks compare v2 digests (v1
   never matches → fail closed); pass real current attempt to C; never
   early-return on published files before current-item reuse; consume
   `StepOutputs.status` (B's assembly already gates on it;
   materialization must stamp real statuses, never `None`).
5. **H — test updates with explicit freeze evidence only**: the
   B-intended breaks listed under STATUS (golden digest, entity-identity
   test, three status-less `StepOutputs` tests). No production-semantics
   restoration, no allowlists.
6. **A — registry**: `two_drivers`/`native_template` test adapters and
   `opaque`-profile expectations vs the new
   `missing_capability_implementation`/`unknown_result_profile` gates;
   decide whether test-only adapters register runtime impls or tests
   target the new reasons explicitly.

