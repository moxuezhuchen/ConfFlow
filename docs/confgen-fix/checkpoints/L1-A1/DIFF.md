# L1-A1 DIFF (rehearsal; R1 rejected metadata + R2 generic adapter + same-authority authoring)

Base: formal C2 `eb74a05e31a1e48560cfeaed2ef14d6d8184475b`.
Worktree `/tmp/l1-a1-proto`, branch `refactor/l1-a1-proto`. No commit/push/merge/tag.
Source `/tmp/l1-c2-exec` read-only; output `/tmp/l1-a1-output` exclusive.
Design `/tmp/l1-a1-design-output/{DESIGN.md,WHITELIST.md,DUMMY-ACCEPTANCE.md}` v2 read;
root corrections in the task card take precedence over any mechanical design text.

## Scope (this card only)

- R1: misplaced-field metadata (`rejected_step_keys` owned by each capability
  module, descriptor carries the assembled value, compiler reads it generically).
- R2: compiler adapter generic extraction (`wire_block_key` effective key +
  executor-consistent registry query, total read-only, legality stays in bindings).
- Authoring same-authority measurement (one explicit `ExecutionRegistry` instance
  flows to both `compile_intent(registry=)` and `describe_step(registry=)`).
- Checkpoint/seed/recipe science stays A2; Gaussian stays G1b parallel (not redone).
- `resources.py` untouched; `authoring.py` untouched (already same
  `ExecutionRegistry`, no formal change for form's sake).
- This card adds no `analysis` public card. L1 still has A2/A3 plus the analysis
  naming/module requirement unmet; nothing here cancels PLAN scope. Follow-ups
  must verify the real analysis descriptor->handler path (runtime already has the
  analysis capability) and must not omit it.

## Root corrections applied (design v2 deviations, deliberate)

- Production whitelist is exactly 6 files (not the document's "3 files but other 3
  allowed" contradiction):
  `intent/compiler.py`,
  `capabilities/{descriptor,registry,calculation,confgen,transform}.py`.
- Three capability modules add only their own `REJECTED_STEP_KEYS` constant plus a
  wire-block comment. Existing `_wire_*` bodies, public/private signatures, and
  `__module__` compat are untouched (verified by diff: tail-only additions).
- `descriptor.py` new fields both have compat defaults: `rejected_step_keys=()`,
  `wire_block_key=""`. C2-era custom descriptors without the new fields still
  construct and compile. `build_intent_registry` does NOT reject empty
  `wire_block_key` (that would break the 12 C2 tests); the empty default derives
  from `fragment_keys[0]` (empirically locked for all default handlers and custom
  non-empty shapes, no hardcoded executor in the compiler). Only an explicit
  non-empty key outside `fragment_keys` is rejected (descriptor + registry).
- `rejected_step_keys` default empty; builtin three sets are verbatim the old
  branches: calculation `{"preset"}`; confgen 9 keys
  (`_CALC_ONLY_FIELDS|{"preset"}`); transform 9 keys
  (`_CALC_ONLY_FIELDS|{"program","seed"}`, no `preset`). `_CALC_ONLY_FIELDS` is
  kept (compat re-export face) and equals `confgen-{"preset"}==transform-{"seed"}`.
- `_reject_misplaced_fields` keeps the old 4 positional params usable plus an
  optional 5th `entry`. Validation stays after card resolve, before the handler
  (call-site order unchanged; never moved into/after the handler). The compiler
  has no three-executor condition branches in the reject path; unknown executors
  stay empty (old semantics preserved).
- Recipe base steps carry no `_card_type` and are already wire: the compiler never
  guesses a card type. Adapters resolve by executor-consistent metadata query
  (`registry.wire_block_key_for_executor`): same-executor entries must agree
  (assembly fail-closed on divergence, declared strategy); missing-builtin
  registries fall back to the assembly-point builtin default
  (`FRAGMENT_KEYS_BY_EXECUTOR` first item), so a shipped `calculation` adapter is
  never dropped to `None`. Only the assembly point knows builtin names; the
  compiler does generic data access and introduces no second port/adapter
  legality authority (total reads, `bindings.requires_adapter` stays sole judge).
- Dummy uses the legal native selector mapping (`dummy_scale_selector` ->
  legitimate `bond_scale`, `fragment_keys=("transform",)`, no `_preset_ref`
  fiction). A1 does compile/authoring only; the full `execute()` is the A2 hard
  gate and is never replaced by a class-instantiation check here.
- Counting: framework development (this card's 6-prod whitelist) is NOT the final
  experiment. The final extension experiment counts
  `git diff --stat <framework-base>..<experiment-HEAD> -- confflow/` non-test
  production files as modified+added `<= 1` (target 0, budget 1 is tolerance).

## What changed

- `capabilities/descriptor.py` (+39): two compat-default fields, duplicate/empty
  validation, explicit-illegal-wire-block rejection, `effective_wire_block_key`
  property (explicit or `fragment_keys[0]`); `card_dict`/freeze semantics unchanged.
- `capabilities/registry.py` (+108): `build_default` passes handler + sorted
  rejected tuple + `wire_block_key=fragment_keys[0]` in the same `if/elif` (no new
  executor branch); `build_intent_registry` validates rejected items, rejects only
  explicit-illegal wire keys, enforces per-executor consistency; new total helpers
  `_effective_wire_block_key`, `_check_executor_wire_consistency`,
  `wire_block_key_for_executor` (builtin fallback, unknown -> `None`).
- `capabilities/calculation.py` (+6), `confgen.py` (+18), `transform.py` (+20):
  tail-only `REJECTED_STEP_KEYS` + wire-block comment; `_wire_*` untouched.
- `compiler.py` (+112/-19): kept `_CALC_ONLY_FIELDS` with canonical-source comment;
  new total helpers `_effective_wire_block_key`, `_adapter_from_wire`,
  `_wire_block_key_for_executor`; `_reject_misplaced_fields` generic (entry or
  builtin-table dict lookup, no `==` branches); base-wire adapters via
  executor-consistent query; appended adapters via entry effective key; reject
  call passes `_entry` (timing unchanged).
- `tests/v4/test_l1_intent_capabilities.py` (+230, append-only): 7 differential
  probes (rejected verbatim, defaults derivation, explicit-illegal + divergence,
  misplaced-before-handler order + 4-arg/unknown semantics, no-branch AST lock,
  recipe-base adapter + missing-builtin fallback, C2 custom compat).
- `tests/v4/test_l1_authoring_projection.py` (new, 226 lines, 5 tests): same
  execution-registry identity, Dummy compile + `compile_workflow` ok, authoring
  projection (`capability`, shared `contract_version`), default-registry
  fail-closed wording, Dummy dedicated rejections (builtin contrasts).
- `resources.py`, `authoring.py`, `checkpoints/seeds/recipes/cards/contract/
  boundary/validation`, `execution/*`, `workflow/*`, `programs/*`, `science/*`:
  zero change.

## Differential probes (root will rerun independently)

- Order: confgen `program` + empty `native` -> `cannot consume fields: program`
  with `step_id`; empty `native` alone -> handler `non-empty native mapping`.
- Recipe base: `single_point` recipe adapter stays `standard` and compiles;
  transform-only custom registry still resolves `calculation` default key and
  preserves a shipped adapter (helper-level, never `None` by omission).
- Custom compat: C2-style descriptor without new fields constructs (empty
  defaults, derived `calculation` key) and compiles with observable `probe_` role.

## Residual inventory (NOT claimed done; A2/A3 + analysis)

- Checkpoint/seed/recipe-science lanes (`1473-1492` block names, `_seed_scope`,
  `_patch_recipe_step`, role-card lanes) stay central/generic byte-identical.
- `analysis` naming/module + real descriptor->handler verification pending
  (runtime `AnalysisItemAdapter` exists; intent has no card yet).
- Closed-enum new runtime executor vocabulary stays uncovered (honest boundary).
