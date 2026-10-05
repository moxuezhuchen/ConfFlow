# D1 DIFF v2 (science; preview, not submitted)

Base: `9cf3f7c` (`fix/confgen-1d`, D0 seam). Preview branch
`refactor/fix1d-d1-proto` only; formal line untouched.

## `confflow/science/confgen/coordination/realization.py`

- `realize_target` / `realize_flexible` gain optional `initial_coordinates`
  (default `None`). `None` keeps the legacy input-only path: reference
  tables (bond/angle-1-3/reaction/stereo/planar restraints, target
  positions) and the terminal `_audit_geometry` stay anchored at
  `coordinates` (true parent input); the solver start alone (rigid
  base/centroids, flexible warm rigid start + `x0`) uses the sigma image.
- Retry evidence key `sigma_start_applied: True` is emitted ONLY for an
  explicit start (conditional spread). Root blocker fixed: the first version
  wrote `False` unconditionally and changed legacy record bytes. Proof:
  synthetic tetrahedral default-path full-`to_dict()` dual-tree diff,
  sha256 `7eaee718…` byte-identical both backends; explicit-start positive
  control carries the key (`/tmp/fix1d-d1-output/01-DEFAULT-PATH-DIFF-PROOF.md`).
- No tolerance, threshold, or audit-gate change.

## `confflow/science/confgen/coordination/stage.py`

- New `CoordinationStage.retry_solve` (D0 hook): per-parent `first_pass`
  table only, no instance/global cache. Sources are first-pass
  `published_leaf`/`expanded` in source-ordinal order; witnesses are the
  runtime-spec `site_group.witnesses` in declared order, each strictly gated
  by the read-only `validate_full_witness` (`authority_valid` required;
  stereo/geometric stay UNVERIFIED = start-point qualification only).
- A (source, witness) pair qualifies only when the induced site action maps
  the source binding placement onto the pending target (canonical comparison
  under the shape proper-rotation group). Full-atom coordinate image
  (`y[perm[i]] = x[i]`) is start-only via `initial_coordinates`.
- Order: source ordinal outer, witness declaration inner; images deduped by
  bytes; budget per target `1 + |distinct sigma images|` (D2 `<=3` sibling
  slot not implemented, fail-closed; D3 `start_statistics` not added).
- Backend-faithful: explicit `rigid` tries rigid only (never switches to
  flexible; rigid failure keeps input-only); `flexible`/`rigid_then_flexible`
  run the full flexible solve. Engine post-solve path (drift/lock/
  perception/audit/expansion) traverses every retry outcome unchanged.
- Success stamps `retry_start="sigma_image:<source>"` in structure metadata
  (new leaves only; first-pass records verbatim). Decline (`None`) keeps the
  single original failure record. Cancel probe checked between candidates;
  `EngineCancelledError` propagates unwrapped.
- `sigma_skip_diagnosis`: pure in-process branch label for tests ONLY. It is
  NOT a runtime report (root batching decision: report wiring closes in D3
  with `start_statistics`; tracked for removal/wiring there). No reporting
  completion is claimed for D1.
- `symmetry.py`, `component.py`, kernel/model/registry/tolerances/fixtures:
  untouched. `component.py` needed no change (no skip-diagnosis declaration
  required it).

## `tests/v4/test_confgen_retry_sigma.py` (new, 17 tests)

- Control column (mock solving, real `retry_solve` + real `run_kernel`):
  first-pass-before-retry + `published_leaf`/`expanded` sources from the
  real table; per-target `1+|images|` budget with image fingerprints;
  duplicate-witness dedup; multi-source ordinal-major order across two
  DIFFERENT site actions (base (2,3,0,1,4,5), alt (3,2,1,0,4,5)) with
  declaration-order determinism; decline keeps single failure; cancel
  between candidates propagates with no partial second solve; direct probe
  cancel; two-parent isolation + repeatability + no instance state; heal
  marks only new leaves. Explicitly not science success.
- Science column (unmodified stage + real solvers, synthetic
  reflection-symmetric octahedral, single-methyl system = the proven
  30-leaf baseline): input-only failure `000017` recovered as
  `sigma_image:000018` (rigid: `000017`+`000024`); stripped-witness run is
  an exact leaf/record subset (first-pass bytes preserved, `retry_start`
  only on new leaves); repeatability; rigid-backend honesty (rigid native
  backend pinned, no flexible switch); `initial==input` reproduces default
  dict plus exactly one key; bond-stretched sigma start fails the
  parent-anchored audit (start/reference separation).
- Multi-witness control fixtures (`_witness_variants`) are isolated from
  the science molecule: the science system/numbers were restored after a
  shared-builder methyl experiment shifted them (evidence kept, tolerances
  untouched).

## Golden direction

- 26 candidate reports: recovery-only (first-pass successes identical, only
  FAILED->REALIZED additions). 67 + contracts/boundary: immutable.
  collect/out: +17 nodes exact accounting, no byte claim. TS1 three-backend
  stage runs byte-identical (that spec carries no runtime full witness:
  honest zero-change, not a recovery proof; the synthetic system above is
  the recovery proof).

## Phase seam (from D0.2 design notes + 01-SEAM)

- D0 exposes exactly one retry pass (`_retry_level`). D2 needs a second
  slot AFTER the D1 sigma pass (D1-full-sigma then D2-full-sibling,
  ≤3 siblings by target ordinal). Not implemented here; no D2 code.
