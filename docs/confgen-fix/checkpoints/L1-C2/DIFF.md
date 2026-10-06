# L1-C2 DIFF (rehearsal, capability handler + explicit descriptor registry + root dispatch fix)

Base: formal C1 `01a260d149c99ab8c0571e7eebb6f1bab827d504` (`refactor/l1-intent`, C1 root cmp).
Worktree `/tmp/l1-c2-proto`, branch `refactor/l1-c2-proto`. No commit/push/merge/tag.
PLAN L1 + `/tmp/l1-card-design-output/{L1-DESIGN-v2.md,ROOT-REVIEW.md}` (root ruling first);
C0/C1 not redone. Root dispatch review `/tmp/l1-c2-output/ROOT-DISPATCH-REVIEW.md`
applied before freeze; prior evidence kept, no new tree.

## Root dispatch gap fixed (no executor hardcoding)

- Before: `compiler.py` `if executor == "calculation"/elif confgen/elif structure_transform`
  chose the wire slot and handled transform `_preset`. Only swapping inner calls to
  `entry.intent_handler` does not meet L1 "compiler only generic flow".
- After: handlers return the complete capability-owned fragment:
  calculation `{"calculation": payload}`, confgen `{"confgen": payload}`,
  transform `{"transform": payload, "_preset_ref": preset}`.
  Old `_wire_calculation` / `_wire_confgen` (2-arg) / `_wire_transform` keep verbatim
  return shape with same objects (`is`) and old `__module__`; new adapters
  `calculation_fragment` / `confgen_fragment` / `transform_fragment` live in their
  capability modules (explicit NEW, recorded). Compiler does a single
  `fragment = entry.intent_handler(user, card, step_id)` with no executor branch
  and no `{"calculation":...}[executor]` hardcoded dict.
- `CapabilityDescriptor.fragment_keys` declares owned keys
  (`calculation` / `confgen` / `transform+_preset_ref`); compiler validates:
  fragment is Mapping, non-empty, keys subset of declared, no undeclared keys,
  no overwrite of generic `_RESERVED_FRAGMENT_KEYS`
  (`id/executor/label/resources/scheduler/bindings/_from/execution/`
  `reuse_checkpoint/_card_type/_card_version/_role_default/_named_card`),
  no duplicate merge. `_preset_ref` is capability-owned private metadata declared
  in the transform descriptor and carried generically; compiler never interprets
  `_preset`.
- True custom test returns a complete fragment (`{"calculation": ...}` with
  `probe_` role) through real `compile_intent -> compile_workflow` with same-input
  `sp` contrast (content + rejection), not spy-called. Production default has no
  probe key.

## What moved (byte-mechanical, AST-equal unless noted NEW)

- `confflow/producer/intent/compiler.py` (1666 -> 1664 lines): only light import
  change (`parse_preset_ref` dropped, `__getattr__` lazy compat added),
  `_allocate_ids(..., intent_registry=None)` registry-first extraction,
  `_parse_card_ref_with_registry` / `_resolve_card_and_entry` generic helpers,
  `compile_intent(..., intent_registry=None)` single generic dispatch + merge,
  `_RESERVED_FRAGMENT_KEYS` generic boundary, recipe `_resolve_program` uses
  function-local capability import. All remaining bodies, error order/messages,
  ID/card expansion, scheduling/seed/checkpoint flow unchanged.
- NEW `intent/capabilities/__init__.py` (12 lines): light marker only, no imports.
- NEW `intent/capabilities/descriptor.py` (124 lines): frozen `CapabilityDescriptor`
  (`key/executor/intent_handler/card/fragment_keys/description`), deep freeze
  (`MappingProxyType`+`tuple`) + `card_dict()` thaw. `fragment_keys` non-empty,
  no duplicates, frozen to tuple.
- NEW `intent/capabilities/registry.py` (142 lines): frozen `IntentRegistry`,
  `build_intent_registry` (reject conflict/unknown executor/no handler/empty
  fragment), `FRAGMENT_KEYS_BY_EXECUTOR` default composition,
  `build_default_intent_registry()` explicit 14 with function-local lazy imports.
- NEW `intent/capabilities/calculation.py` (123 lines): `_resolve_program` +
  `_wire_calculation` verbatim (same `is`/`__module__`); NEW `calculation_fragment`.
- NEW `intent/capabilities/confgen.py` (150 lines): `_LEGACY_*` +
  `_legacy_paths_to_v3` + `_wire_confgen` verbatim; NEW `confgen_fragment`.
- NEW `intent/capabilities/transform.py` (103 lines): `_wire_transform` verbatim;
  NEW `transform_fragment` owns `_preset` -> `_preset_ref`.
- NEW `tests/v4/test_l1_intent_capabilities.py` (435 lines, 12 tests).
- `intent/{common.py,__init__.py}`, `tools/architecture_policy.py`,
  `tests/v4/test_architecture_policy.py`, other producer/execution/programs/
  workflow/fixtures/old tests: zero change.

## Residual central-capability inventory (NOT claimed zero; for A1/C2b, root final audit)

- `_reject_misplaced_fields` (`compiler.py`, `if executor == ...`): generic workflow
  syntax boundary (which user fields each executor can consume). Kept; field
  order/messages unchanged. Future A1 may project it from descriptors, but this
  card keeps it central and generic.
- `wire_adapters` extraction reading `calculation.execution_adapter`
  (recipe-base + appended paths): generic binding support needs the adapter to
  resolve ports via the execution registry, but peeks into the calculation payload.
  Kept; future C2b may have handlers supply adapter metadata explicitly.
- Checkpoint provisional-seed blocks iterating `("calculation", "confgen")`:
  only these blocks carry seeds (transform never does). Generic seed handling
  with executor knowledge; kept, byte-identical. Future C2b may ask descriptors
  whether their fragment is seed-bearing.
- `_seed_scope` (`confgen` -> `native_sampling`, `calculation`+`goat` ->
  `workflow_identity_only`): capability-specific scope rule (peeks at
  `calculation.native.goat`). Kept; future science-adjacent C2b to revisit.
- Recipe lanes (`_require_recipe_assignment`, `_patch_recipe_step`,
  `_apply_role_cards` touching `calculation.program/native/adapter/profile/
  checks/recovery/overrides`): calculation-specific assignment. Kept verbatim
  except lazy `_resolve_program` import; authoring projection (A1) and further
  capability down-moves are separate cards. This card does NOT claim all
  capability knowledge is zero.
- Field order / old error priority unchanged; default 14-card product/catalog/
  contract/boundary byte-identical (see verification).

## Verification (executor self-check; root acceptance separate)

- New C2: 12 passed; C0 package 6 + C1 helpers 5 still pass (23 total compat).
- Necessary: producer_intent+regressions+machine+intent_boundary+p0boundary 141 passed
  (with C2: 141+12=153 for the 8-file set; full necessary set
  producer_authoring_boundary+legacy_paths+checkpoint_boundary+checkpoints 302 passed
  in the 3-file slice); architecture_policy 333 passed.
- Byte equality: real `compile_intent` wire + `intent_catalog` OLD vs NEW equal
  (sha256 `9b062a30c38124748f9451c2454fa7d9cb419ab16ad72a43d5c1e02b0d2533dc`);
  `contract.full` `2fe92022d2e9fa537b8ad11152d2b173c6f6572e0b0bd3950c7148c332ab43bc`
  and `boundary.full` `ee811b9995f49dec9668693d58e9f4d68fa170bfa232837486a8998b6aed9d20`
  OLD == NEW; digests equal.
- Collect: OLD 5001 -> NEW 5013 (+12 exactly the new file; -0).
- Static (single worker): `ruff check` clean; `ruff format --check` clean;
  `black --check` clean; `mypy` clean (6 production files).
- Purity: `import confflow.producer.intent` loads no `confflow.science`;
  `import ...capabilities.descriptor` loads no handler modules; no import side
  effects; helpers never back-import `compiler` (AST).
- Full-repo pytest and full golden NOT run here (milestone root only).

## Raw freeze

- `/tmp/l1-c2-output/L1-C2-proto.diff` (`git diff --binary HEAD`, includes
  new-file modes; sha in `/tmp/l1-c2-output/SHA256SUMS`).
- `/tmp/l1-c2-output/L1-C2-production-only.diff`
  (`git diff HEAD --binary -- confflow/producer/intent`, production whitelist
  only; sha recorded in MANIFEST-adjacent `SHA256SUMS`, not self-referenced).
- External artifacts + sha256: see `MANIFEST.json` and
  `/tmp/l1-c2-output/SHA256SUMS`.
- Formal prompt: see `/tmp/l1-c2-output/FORMAL-PROMPT.md` (HEAD clean, branch,
  one-card-one-commit, not push, number-deviation stop, Co-Authored-By trailer).
