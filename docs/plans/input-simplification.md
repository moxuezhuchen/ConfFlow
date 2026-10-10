# Input Simplification Implementation Plan (saved roadmap)

> superseded 2026-10-11: ConfGen now has a single DG search engine (schema_version 4); the ring, torsion, path and coordination-realization engines and their gates described here no longer exist.

Owner: parent integration with parallel Muse Spark 1.3 implementation sessions.
Status: **Phases 0–8 accepted after independent review on 2026-10-01**.
Parent coordinates shared interfaces and verifies actual code and test exits.

## Phase 0 — PathResolver (ACCEPTED)

New authoring/native input `paths: [{start, end, move}]` (1-based endpoints,
`move` REQUIRED exactly `start`/`end`). Pure resolver
(`confflow/science/confgen/torsion/paths.py`): bridge-only unique-path
resolution on the final working topology, cut-rule moving sets, canonical
unordered-bond dedup with oriented axes, deterministic declaration-order
ordinals, element-aware short-bond warnings with `strict_path_bond_check`,
pre-geometry raw-size guard. Legacy native mixed `paths`+`chains` mode uses
the oriented legacy adapter (`legacy_oriented_grid_geometries`); pure chains
stay bit-for-bit. Typed v3 `paths` expand to relative-rotation torsions in
`build_context` with `ROTOR_SAMPLING_CONFLICT` on explicit collisions.
Reports/provenance audit resolution; digests cover declarations, never
machine paths. `PATH_AMBIGUOUS` reserved (no valid fixture: forests admit at
most one simple path); `waypoint` deferred. Freeze stays fail-closed.

Round-2 review fixes (accepted defects): 0.92x-radii `WARNING_SHORT_BOND`
heuristic with stable diagnostics; canonical (post-dedup/post-exclusion)
task counts beside declaration counts; conflict-before-exclusion with
shared-exclusion merge; explicit single-state grids executable;
all-excluded still fail-closed; valid V4 YAML examples compiled in tests;
per-source resolved routes + driving identity in both reports and runtime
diagnostics; `_run_legacy_paths` refactored into preparation plus the one
shared legacy grid pipeline (pure chains re-routed through the same
oriented numeric loop with identical ordinals -- verified by parity tests).

Honest limitations: per-work-item pre-geometry guard only (no
workflow-total multi-structure budget); typed paths require measurable
dihedral frames (terminal bonds fail; legacy mode is frame-free);
`waypoint` multi-endpoint routes deferred; typed path/explicit-torsion
same-bond collisions always conflict; `normalize_executor_native` does not
pass `paths` through (test-only helper).

Tests: `tests/v4/test_confgen_paths_phase0.py` (59 tests) plus
`tests/v4/test_confgen_paths_audit.py` (27 review-fix regressions). Docs:
`docs/CONFGEN_PATHS.md`. Contract `confgen.description` + editor manifest
fields `confgen.v3.paths`, `confgen.v3.strict_path_bond_check`.

Parent verification: the broader scoped battery passed 428 tests with one
architecture import failure; after fixing the import placement, all 138
architecture checks passed. Final runtime-diagnostic changes passed 161
tests (all 86 path tests, legacy executor and typed integration). Full
repository Ruff and mypy passed (258 source files); Black checked all 13
changed Python files. Both actual documentation YAML examples compiled.
All 19 golden fixture hashes matched and the fixture tree/ZIP remained
unchanged. The full repository suite was not run. The backend implementation
is committed and pushed on `implementation/input-simplification` as
`89303ac`; the prior ConfGen v3 baseline is `d5a40ae`.

## Phase 1 — Structure TopologyPatch (ACCEPTED)

Structure TopologyPatch with add/delete/provenance; explicit base-topology
vs correction-intent inheritance semantics; scientific/reuse payload and
serialization covered; conflict with legacy corrections fails closed.

## Phase 2 — Hidden deterministic seed (ACCEPTED)

Hidden deterministic seed in the producer for stochastic paths; canonical
SHA256 on seed-free scientific identity (no Python hash, no circular
digest); versioned derivation; provenance; advanced override. Full
enumeration stays deterministic without a needless seed. GOAT has no native integer seed control; its derived seed covers workflow
identity only and does not promise native sampling determinism.

## Phase 3 — Calculation Cards (ACCEPTED)

Explicit program + card type/role + native + resource preset; compile
existing CalculationModel; role/program capability-driven
profiles/checks/recovery; advanced fields preserved.

## Phase 4 — Typed bindings and role plumbing (ACCEPTED)

Automatic typed bindings and role plumbing; reuse existing producer recipes
and strict V4 ports/cardinality/grouping.

## Phase 5 — Resource resolver (ACCEPTED)

Per-item cores/memory + actual machine allocation; floor capacity limits;
oversize fails; operational resolution/provenance excluded from scientific
identity; machine profiles hold executable/env/target/scheduler.

## Phase 6 — Semantic checkpoint/Hessian edges (ACCEPTED)

Gaussian standard-adapter relationships compile to existing artifact staging
and native input. ReadFC targets Opt; RCFC targets IRC. Method/native-input
compatibility and bound charge/spin lineage fail closed; runtime artifact
pairing and staging retain atom authority.

## Phase 7 — Versioned Refine/Dedup presets (ACCEPTED)

Explicit visible strategy, advanced overrides, existing strict execution.

## Phase 8 — JobDesk UX simplification (ACCEPTED)

Structure atom selection, charge/spin controls, endpoint-only ConfGen,
card and recipe authoring, resources and semantic checkpoint controls.
JobDesk implementation in `/opt/jobdesk-v2-v4` preserves the existing strict
submission path. Normal Run compiles intent automatically, with producer
path estimates, explicit moving side and freeze actions, per-input state,
card/template controls and optional machine/checkpoint/preset panels.
Advanced documents remain lossless; invalid or pending normal edits and
stale asynchronous replies fail closed. Reauthoring preserves explicit
seeds and operational overrides while rederiving generated values.

Independent final acceptance: all 2570 JobDesk tests passed, with 4 skipped
(actual process exit 0). The final 135 new intent/live-GUI tests and 4
existing frozen-validation SP tests also passed independently. The rebuilt
wheel passed all 7 source/version/content verification checks. Scoped Ruff
and mypy passed; the type check excludes existing missing YAML stubs and
their associated unused-ignore diagnostics.

The atom picker currently supports XYZ through a 2D XY projection plus an
accessible atom table; GJF/INP files can be submitted but cannot drive atom
picking. Native GOAT sampling has no integer seed control. Unsupported
energy-window presets and waypoints remain deferred as documented above.

## Parallel ownership (2026-10-01)

- Topology lane: structure state, scientific identity, serialization, inherited outputs and existing topology consumers.
- Authoring lane: intent compiler, deterministic seed materialization, calculation cards, typed wiring and supported transform presets.
- Resources lane: machine profiles, capacity resolution and existing Gaussian artifact checkpoint relationships.
- JobDesk lane: preserve prior edits and integrate simple normal-mode controls with the live producer authoring boundary.
- Parent: additive `compile_intent` authoring operation, producer catalog publication, cross-repository integration and independent acceptance checks.

No phase is marked accepted merely because a subagent launched or reported completion.

## Backend integration acceptance

All implementation sessions for phases 1–7 actually exited with code 0.
The parent scoped battery passed 665 tests, skipped one, and found one
authoring layering violation. The request-shape parsing was moved to the
path-preview authority; all 41 affected authoring/preview/boundary tests
then passed. Whole-source Ruff and mypy passed (267 source files).
All 19 supplied scientific golden files, their manifest hashes, and ZIP
matched the baseline. A broader V4 run reached its 300-second timeout
in existing coordination tests; it is not claimed as a full-suite pass.

Structure topology/state is scientific identity and crosses persistence,
worker transport and result lineage. Checkpoint compatibility proves bound
structure lineage, never fabricates state from global defaults; actual
profile → assembly → scientific resolution → Gaussian rendering verifies
source state 1/2 is inherited under global defaults 0/1. Machine/checkpoint
acceptance covers 105 tests, authoring covers 64 tests, topology adds 39
tests, and paths cover 86 tests. These counts overlap the scoped battery.

Producer transport exposes additive `compile_intent` and `preview_paths`
operations and publishes a versioned card/preset catalog. Endpoint-only
preview uses the same working topology, path resolver, rotor deduplication
and raw-space limit as execution; it never generates conformers.
The `6a9c1cb` follow-up persists nonempty structure patches through the shared
input-topology authority before previewing. Independent tests verify the
preview and executor agree on graph digest, moving atoms, angles and count
under a nondefault bond scale (43 boundary tests and 46 topology/preview
tests passed, with overlap).

Unsupported energy-window presets are omitted. Waypoints, automatic rotor
perception and workflow-total conformer budgets remain outside this scope.
All simplified inputs compile into the existing strict V4 execution path.
