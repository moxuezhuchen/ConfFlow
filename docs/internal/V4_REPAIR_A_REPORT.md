# V4 Repair — Worker A Report: capability registry / compiler

Owner: OpenCode MS 1.3 repair subagent A. Scope: freeze wave-1 ownership only —
`confflow/execution/registry.py`, `confflow/workflow/v4/{diagnostics,validation}.py`
(semantic additions), new `tests/v4/test_repair_capabilities.py`, this report.
`confflow/producer/contract.py` reviewed: no change required (it already
generates every capability section from the registry; see FIX-6).
`confflow/execution/contracts.py`, `workflow/v4/{schema,plan,compiler}.py`
reviewed: no change required (shape/vocabulary injection and plan threading
already single-source the registry).
No commits/pushes. No existing tests edited. No other owners' files touched.

## ROOT CAUSE

1. **Dual capability authority (plan issues 9, 20).** The execution registry
   held descriptor tables while the real implementations lived in independently
   consulted module dicts (`profile_standard.PROFILES`,
   `checks_standard.CHECKS`, `recovery_standard.RECOVERIES`), plus unconnected
   program/adapters. Application, batch, and remote each imported those tables
   directly (`application/v4_run.py:259-261`, `remote/worker.py:943-1019`),
   so a published capability could have no runtime behind it.
2. **Declared-but-unexecutable vocabulary.** The `opaque` result profile and
   the `native_template` adapter were published (registry, schema enum,
   producer contract) with no runtime implementation anywhere; steps using
   them compiled and then failed at runtime lookup. Existing tests pinned the
   compilability (`test_opaque_profile_without_checks_compiles`).
3. **Unknown programs accepted.** Validation never resolved `program` against
   `programs.registry`; any string compiled. Pinned by
   `test_real_validator_accepts_unknown_program_name`.
4. **GOAT stochastic seed gap (plan issue 22).** Seed enforcement keyed only on
   the broad `calculation`/`confgen` enum flag, so a stochastic GOAT
   calculation (`native.goat`) compiled with `seed=None` (shipped `goat`
   recipe), and a conflicting native `Seed` vs step seed was never compared.
5. **Silent mode/profile mismatches.** Nothing checked native-mode
   (`irc`/`neb`/`goat`) against the result profile, so e.g. a GOAT ensemble
   rendered into the `standard` profile would silently drop member
   structures; nothing enforced the adapter's own at-most-one-mode rule at
   compile time.

## FIX

`confflow/execution/registry.py` — atomic single-authority entries:
- New private `_RegisteredCapability(spec, implementation)` (generic over the
  spec type); the five registry dicts hold entries, and every `register_*`
  call takes `(spec, implementation)` together. Descriptor accessors
  (`executor()`, `find_*()`) and the resolve API read the **same** entry.
- `build_default_registry()` wires each descriptor with its real
  implementation in one place (imports are function-local, read-only; no
  C-owned file edited): `WorkItemExecutor` / `ConfgenExecutor` /
  `TransformExecutor` / `AnalysisExecutor` classes for executors;
  `resolve_standard_structure` / `resolve_named_slot_sets` for adapters;
  the `PROFILES` / `CHECKS` objects for profiles/checks; recovery
  **factories** (`lambda adapter: NoneRecoveryPolicy()`,
  `TsRescueScanPolicy` itself) so `recovery_implementation(name, adapter=…)`
  genuinely binds the step's program adapter instead of constructing policies
  ad hoc. A missing implementation at build time is a loud `KeyError`.
- `opaque` and `native_template` descriptors **omitted** (not flagged):
  no runtime exists and no wave-1 owner builds one. Schema enum, producer
  contract, and validation follow automatically from the single source.
- No availability shims: no `implemented` flags, no parallel tables.

`confflow/workflow/v4/validation.py` — compile-time enforcement through the
unified API (`resolve_executor`, program/adapter/profile/check/recovery
resolution), plus: unknown program → `unknown_program`; GOAT-native
calculation without step seed → `seed_required`; native `goat.Seed` ≠ step
seed → `seed_conflict`; mode/profile mismatch, multiple modes, or
non-mapping mode section → `incompatible_capability_combination`.
Role remains label-only and is never dispatch input.

`confflow/workflow/v4/diagnostics.py` — four additive reasons only:
`unknown_program`, `seed_conflict`, `missing_capability_implementation`
(for custom registries with implementation-less entries),
`incompatible_capability_combination`.

## TEST

- New `tests/v4/test_repair_capabilities.py`: **28 passed**. Covers
  resolve/implementation identity from the same entry, executor-class
  resolution for all four capabilities, adapter-bound recovery factories,
  omission of `opaque`/`native_template` from registry and contract,
  unknown-program rejection, GOAT seed/seed-conflict rules, mode/profile
  combination matrix, and preserved confgen/executor-block behavior.
- `ruff check`, `ruff format --check`: clean on all four touched files.
  `mypy`: clean on all three production files (remaining tree errors are in
  concurrently edited C-owned files, untouched).
- Existing suites: `test_compiler` + `test_v46_producer_contract` +
  `test_v46_recipes` + `test_v46_cross_repo_e2e` → 124 passed; 6 failures,
  all classified below. The B-owned work-item digest golden was deliberately
  not chased (B is editing identity contracts concurrently).

## STATUS

Implemented and scoped-tested. NOT an architecture-closure claim: integration
items below belong to the lead / workers D, H, and C/F wave-2.

- Pinned-bug tests that now fail and need H disposition (do not revert the
  fix to satisfy them): `test_opaque_profile_without_checks_compiles`,
  `test_check_not_supported_by_profile` (both assert on the omitted `opaque`
  profile), `test_real_validator_accepts_unknown_program_name` (asserts
  unknown programs validate ok),
  `test_adapter_change_moves_step_digest` (uses omitted `native_template`).
- `TestRecipesCompile::test_every_recipe_parses_and_compiles` and
  `test_every_recipe_validates_from_bytes`: fail **only** on the shipped
  `goat` recipe (`seed_required`). Lead must add an explicit seed to the
  `goat` recipe in `producer/recipes.py` (not A-owned) — the recipe is
  otherwise combination-clean (orca/ensemble/single `goat` mode).
- `TestDisabledStepSemantics::test_disabled_passthrough_assembly_uses_upstream_outputs`:
  fails inside B-owned `assembly.py` (`producer 's_a' has no recorded
  status`) while B edits concurrently; unrelated to this wave-1 path
  (validation output for that test is unchanged). Left for B/lead.
- `RECOVERIES` / `PROFILES` / `CHECKS` dicts in C-owned files are now inert
  lookup-wise (registry holds the same objects and is the only consulted
  authority); removal or retention is C/lead's call, recorded here, not
  patched across ownership.

## CONCRETE INTEGRATION DEPENDENCIES (for lead; D dispatch signatures)

D (batch/application/remote) must migrate dispatch to these exact registry
APIs; until then the old direct `PROFILES[...]` / `CHECKS[...]` /
`RECOVERIES[...]` imports remain live duplicates — reported unresolved, not
papered over:

1. `registry.executor_implementation(capability) -> type`
   Returns `WorkItemExecutor` (calculation), `ConfgenExecutor` (confgen),
   `TransformExecutor` (structure_transform), `AnalysisExecutor` (analysis).
   Calculation/confgen/transform classes share
   `execute(work_item, context, *, should_cancel=None) -> WorkItemResult`.
   The analysis core does **not**: `AnalysisExecutor.execute(inputs,
   definition=None) -> AnalysisStepResult` over whole-set `AnalysisInputs`;
   its per-work-item adapter is F's wave-2 deliverable (`analysis/*` owned
   by F). D must not call the analysis core through the work-item seam.
   Status: RESOLVED (signatures verified against landed C/F classes);
   D's call-site migration PENDING.
2. `registry.profile_implementation(name)`, `.check_implementation(name)`
   return the runtime objects from the same entry the compiler validated.
   D migration PENDING.
3. `registry.recovery_implementation(name, *, adapter)` returns the bound
   policy; D/C must pass the step's **program adapter** (from
   `registry.resolve_program(program)`) so the scan rescue renders through
   it. Unbound (`adapter=None`) scan policy declines per its own contract;
   `none` ignores the adapter. D migration PENDING.
4. `registry.adapter_implementation(name)` returns the input-shape resolver
   (`standard`, `named_structures` only). D migration PENDING.
5. `registry.resolve_program(name)` accepts aliases (`g16` → gaussian);
   unknown programs raise `RegistryLookupError`. Remote target-side
   resolution (C-owned `binding_resolution.py`) must use this, never local
   absolute paths. E/C integration PENDING.
6. Contract/validator impact of omission: `opaque` and `native_template`
   no longer appear in schema enums or the producer contract. Any JobDesk
   fixture or doc referencing them fails closed with `unknown_result_profile`
   / `unknown_execution_adapter`; lead to confirm no real consumer depends
   on them. Re-introduction requires implementation-first atomic
   registration (C builds impl, A/lead registers entry).
7. Seed threading (C/D): validation guarantees a single effective step seed
   for GOAT (and rejects native `Seed` conflicts), but threading that seed
   into native rendering / remote envelope / digests is C (rendering,
   `apply_seed_to_native`) + B (digest axes) + D/E (transport); A asserts
   only the compile-time contract here.
