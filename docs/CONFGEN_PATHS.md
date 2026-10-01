# ConfGen Path-Based Rotor Declarations (Phase 0)

Phase 0 of the input-simplification roadmap adds endpoint-pair rotor
declarations (`paths`) to ConfGen. Instead of spelling every chain bond,
declare the two endpoints and the moving side. Both spellings below are
complete, valid V4 documents (legacy `paths` accept `confgen.paths` or
`confgen.native.paths` with `confgen.seed`; typed `paths` live under `confgen` with
`schema_version: 3` and explicit sampling):

```yaml
# Legacy native mode: bare paths fall back to angle_step (120 here).
# confgen.seed stays required (single stochastic authority until Phase 2).
schema: confflow.workflow.v4
inputs:
  structures: {kind: structure, cardinality: many}
global:
  scientific_defaults: {charge: 0, multiplicity: 1}
steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure:
        source: {run: structures}
    confgen:
      seed: 11
      paths:
        - {start: 81, end: 92, move: end, step: 120}
```

The `confgen.native.paths` spelling (inside `native:`) is equivalent;
declaring `paths` (or `strict_path_bond_check`) at both levels fails
closed. Both compile into the existing native runtime -- bare paths are
never silently routed into the strict typed scope.

```yaml
# Typed v3 scope: explicit sampling is required (no silent defaults).
schema: confflow.workflow.v4
inputs:
  structures: {kind: structure, cardinality: many}
global:
  scientific_defaults: {charge: 0, multiplicity: 1}
steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure:
        source: {run: structures}
    confgen:
      schema_version: 3
      index_base: 1
      paths:
        - {start: 81, end: 92, move: end, angles: [0.0, 120.0, 240.0]}
```

A bare `confgen.paths` spelling without `schema_version: 3` compiles into
the legacy native runtime (never into the strict typed scope, which keeps
requiring explicit sampling). Simple authoring therefore routes either
into `confgen.paths` (legacy, defaults from `angle_step`) or into the
strict typed scope above with visible sampling. `confflow v4 validate`
accepts all documents above; full route resolution stays deferred to
execution (advisory at submission).

Endpoints are **always user-facing 1-based atom numbers**, in both the legacy
native mode and typed v3 scopes. `move` is **REQUIRED** and exactly `start`
or `end`: scientific intent is never inferred.

## Declaration shape

| Key      | Required | Meaning                                                        |
| -------- | -------- | -------------------------------------------------------------- |
| `start`  | yes      | 1-based atom number (integer; bools/floats rejected)           |
| `end`    | yes      | 1-based atom number, distinct from `start`                     |
| `move`   | yes      | `start` or `end`: the endpoint whose side moves                |
| `angles` | no\*     | explicit grid, e.g. `[0.0, 120.0, 240.0]`                      |
| `step`   | no\*     | shorthand expanding to `range(0, 360, step)`                   |
| `id`     | no       | provenance label                                               |

\* Exactly one of `angles`/`step` may be given. A bare declaration
(`{start, end, move}`) falls back to the legacy `angle_step` default (120)
in the **legacy native mode only**. Typed v3 scopes fail closed on bare
paths so the scientific grid stays explicitly declared. Unknown keys,
non-integer endpoints, `bool` endpoints, same endpoints, non-finite angles,
periodic angle duplicates, and `step` outside 1..360 all fail closed.

`waypoint` (multi-endpoint routes) is deferred: only `start`/`end` pairs
are supported in Phase 0.

## Resolution rules

Resolution runs **per work item on the final working topology** (covalent
perception plus declared `add_bond`/`del_bond` corrections) of the driving
structure, which may be an upstream product. Submission-time document checks
are structural only (advisory); the same pure resolver runs at execution.

1. Endpoints are classified on the **full graph** first: different
   components fail with `PATH_DISCONNECTED`.
2. Cycle edges are removed (bridges kept). Endpoints connected there resolve
   to the **unique** bridge-only path. Full-graph connected but bridge-only
   disconnected fails with `PATH_CROSSES_RING`: ring bonds never rotate
   independently. Ring atoms may still be *endpoints* of a bridge-only path.
3. `PATH_AMBIGUOUS` is reserved and never emitted: a forest admits at most
   one simple path between two nodes, so no valid ambiguity fixture exists.
4. Each rotor bond is cut; the component containing the explicitly chosen
   endpoint moves. **Axis atoms never move.** Branch atoms ride rigidly
   inside their component but their internal bonds never become extra DOFs.
   Unrelated disconnected components stay unmoved. Output atom order is
   preserved.

## Canonicalization (mixed `paths` + `chains`)

Declaring any `paths` entry opts the work item into the strict
canonicalization contract. Pure-`chains` items keep bit-for-bit legacy
behavior, including overlapping chains.

- Rotors order deterministically: paths in listed order (start→end bonds),
  then chains in listed order. First declaration wins position; output
  ordinals are row-major with the last rotor fastest (legacy-compatible).
- Canonical bond identity is unordered (`{a, b}`); the oriented axis
  (traversal order) carries the signed-angle meaning. Identical duplicates
  (same bond, orientation, moving side, grid, model) merge with **all**
  source provenance recorded.
- Opposite moving sides fail with `PATH_DIRECTION_CONFLICT`.
- Different grids, reversed traversals with asymmetric meaning, or
  absolute/relative/chemical model mismatches fail with
  `ROTOR_SAMPLING_CONFLICT`. Grids are never silently unioned.
- Conflict checks run on the **declared** grids first; only afterwards do
  legacy `no_rotate` exclusions collapse a bond to the single `(0.0,)`
  point (ordinals stay stable), recorded as `excluded:no_rotate`.
  Identical declarations under a shared exclusion therefore merge; genuinely
  contradictory grids still conflict even when excluded.
- An explicitly declared single-state grid (e.g. `angles: [0.0]`) is one
  valid state and executes normally; it is not an exclusion. Only the
  all-excluded case (every rotor collapsed via `no_rotate`) fails closed
  with "selected no rotatable bonds", matching the legacy contract.

## Equivalence and machinery

No second runtime exists: legacy `paths` items reuse the legacy Rodrigues
application and clash filter through an oriented adapter (per-rotor moving
sides). A nonoverlapping single legacy chain and its equivalent path
(`move: end` ≡ `rotate_side: right`, `move: start` ≡ `rotate_side: left`)
produce identical rotors, counts, ordinals, and coordinates. Typed paths
expand to `relative_rotation_grid` torsion axes and inherit typed
measurability (terminal bonds fail instead of inventing frames).

## Limits, warnings, freeze

- Two counts are recorded: `declared_cartesian_size` (pre-dedup declaration
  space) and `raw_cartesian_size`, the **final canonical task count**
  (post-dedup, post-exclusion) that actually executes. The canonical count
  (`∏ len(grid)`, arbitrary precision) is computed **before** any geometric
  generation and refused past the 10,000 pre-geometry guard (legacy) /
  `max_declared_states` (typed). The post-geometry `max_conformers`
  survivor cap is not compute safety. Typed reports additionally show the
  total C/R/T space (`counts.raw`) beside the path-only canonical count.
  The guard is per work item; no workflow-total multi-structure budget
  exists yet (remaining work).
- Suspiciously short newly path-expanded bonds (measured length below
  **0.92× the summed covalent radii**) emit `WARNING_SHORT_BOND` findings:
  1.34 A C-C and 1.33 A C-N warn, 1.52 A C-C stays quiet. This is a cheap
  distance heuristic, not chemistry: legitimately short bonds
  (multiple/aromatic character, strain) can also trip it, so findings are
  **warning only** -- no skipping, no bond-order inference. They surface as
  `warning_short_bond` diagnostics and report notes.
  `strict_path_bond_check: true` turns the warning into a `PATH_SHORT_BOND`
  error. Pure-legacy chains never warn.
- `freeze` stays fail-closed for ConfGen, with or without paths.
- Typed paths inherit typed measurability: a path bond without a dihedral
  frame (e.g. terminal bonds) fails instead of inventing a state key; use
  the legacy native mode where frame-free mechanics apply.

## Provenance

Reports (`confgen_report.json`, `ensemble_report.json`) record the driving
structure id and geometry digest, atom symbols, per-source resolved routes
(`declared_paths`: source, endpoints, move, ordered 1-based route, angles --
read from the single resolver authority, never re-derived), the
working-graph topology digest, ordered rotors (bonds, moving/fixed sets,
angles, sources), warnings, and both declaration and canonical counts.
Runtime `confgen_completed` diagnostics expose rotor counts, the raw task
count, the topology digest, and the warning count; short-bond findings add
`warning_short_bond` diagnostics. Each `confgen_path_resolved` informational
diagnostic also prints the ordered chain with atom symbols, the explicitly
chosen moving endpoint and its rotor bonds, without asking for confirmation.
Declared paths already participate in the
step semantic digest, so resolution identity affects reuse correctness; no
machine paths enter scientific digests.
