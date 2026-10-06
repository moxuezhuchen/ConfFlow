# D2 DIFF (science; preview rehearsal, not submitted)

Base: `51246ced0c190c2325a1a72b4e59f9965bcd645c` (`fix/confgen-1d`, D1+D0.2).
Proto: `/tmp/fix1d-d2-proto` only; formal line untouched. Evidence:
`/tmp/fix1d-d2-output/`. Root preallowlist `/tmp/fix1d-d2-root-preallowlist.json`
(26 may-change / 67 must-remain, actual-target-axis classification) verified
93/93 against `/tmp/fix1r-final-root-run/baseline` before science.

## `confflow/science/confgen/coordination/stage.py` (only production file)

- Callback cancellation fix, BOTH retry loops (root-confirmed defect: a
  probe raising `EngineCancelledError` itself hit `UnboundLocalError`
  because the lazy import sat inside `try` after the callback call):
  `from ..engine import EngineCancelledError as _Cancel` moved before the
  `if should_cancel is not None:` check in the sibling-candidate loop and
  in the sigma-candidate loop; `except _Cancel: raise` rethrows
  cancellation, `except Exception: pass` keeps the established
  non-cancellation probe policy. No solver/audit/backend change.
- `retry_phases()` returns `("sigma", "sibling")` (D0.2 stage-owned
  declaration; engine runs every sigma attempt before every sibling
  attempt; no instance/global cache).
- `retry_solve_phase(...)` dispatches `"sigma"` to the unchanged legacy
  `retry_solve` hook with the permanent `first_pass` table (D1 bytes
  preserved), `"sibling"` to the new `_sibling_retry` with the frozen
  `phase_snapshot` table, `RETRY_DEFAULT_PHASE` to `retry_solve` for
  compatibility, anything else declines fail-closed.
- `_sibling_retry(...)`: sources are `phase_snapshot` published/expanded
  records (audited structures only, sigma recoverers included — proven by
  `test_sigma_heal_visible_in_sibling_snapshot`), sorted by source ordinal,
  at most 3 per target (Q10), byte-deduped, no witness required. Current
  target must still be a solve failure in the snapshot (already-healed
  never re-solved; solver-error never retried). Each start goes through
  `_realize_with_sibling` (same backend-faithful solver path, explicit
  `rigid` never switches); success stamps `retry_start="sibling:<id>"`
  via `_with_sibling_start`; decline returns `None` (original record
  stands); cancel propagates between candidates.
- `_realize_with_sigma` keeps its signature and now delegates to the
  shared `_realize_from_start` (solver call arguments unchanged);
  `_realize_with_sibling` is a separate overridable hook over the same
  shared path (control doubles script each path independently).
- `_with_retry_start` keeps its signature and now delegates to
  `_with_retry_start_kind(..., "sigma_image")`; `_with_sibling_start`
  stamps `"sibling:<id>"` (metadata + first evidence entry, new leaves
  only; first-pass records verbatim). Added `sibling_skip_diagnosis`
  pure count label (in-process test pin only, NOT a runtime report —
  D3 wires skip diagnostics with `start_statistics`; no D3 code here).
- `retry_solve` docstring seam note updated (sigma-only hook retained;
  sibling lives in the later phase). No logic change inside `retry_solve`.
- Untouched: `realization.py` (D1 `initial_coordinates` interface reused
  as-is), `symmetry.py`, kernel/model/registry/fixtures/schema,
  tolerances, Molassembler (not introduced).

## `tests/v4/test_confgen_retry_sigma.py` (root-narrowed helper isolation)

All 17 node IDs and every expected assertion unchanged; only helpers:

- New test-only `_SigmaOnlyStage(CoordinationStage)`: narrows the phase
  declaration to `("sigma",)`; isolated helper body, no production
  fallback to old behavior.
- `_run` now runs the real engine path with an explicit real sigma-only
  stage: public `run_kernel` (never auto-wrapped) + the same `project_v3`
  `engine.run()` applies. (Explicit stages via `engine.run()` would be
  wrapped in `LegacyStageAdapter`, which forwards no retry hooks —
  verified in `wire_v3.py`, no `retry` forwarding — so that route would
  silently drop sigma retry. Registry construction order, `build_context`
  input path and all audits unchanged.)
- `_MockSigmaStage` declares `retry_phases() -> ("sigma",)` plus a
  one-line docstring note (no sibling attempt can fire in this scope).
- Result: 17/17 pass, all D1 sigma assertions intact (was 11/17 with 6
  sibling-recovery-direction failures before isolation).

## `tests/v4/test_confgen_retry_sibling.py` (new, 16 tests)

- Science (real solvers, synthetic reflection octahedral, self-contained):
  no-witness recovery `000017 <- sibling:000001`; byte proof vs a real
  sigma-only input-only baseline (29 leaves, zero `retry_start`):
  every baseline leaf id/coords/metadata exact in the full run, every
  baseline target record status/reason exact except `000017`
  `failed_numerical -> published_leaf`, exactly one `sibling:` leaf;
  with-witness sigma heals `000017 <- sigma_image:000018` and sibling
  skips it; rigid backend honesty (no flexible switch, direct
  `_realize_with_sibling` pins backend); `retry_phases ==
  ("sigma","sibling")` + `sibling_skip_diagnosis` branches.
- Control (mock geometry, real `retry_solve_phase` + real `run_kernel`
  dispatch; never science): all-sigma-before-all-sibling; sigma heal
  visible in sibling snapshot (selective sigma heal of 000017, sibling
  heals 000024 only from the snapshot); `<=3` stable ordinal order
  (`000000,000001,000002` for failed 000029, rerun-stable); decline keeps
  single failure; no-witness sibling heals while empty-source snapshot
  declines without solving; cancel between sibling candidates (1 attempt,
  no partial); probe-raises-cancel propagates on BOTH sibling and sigma
  paths (callback-defect regressions); truthy cancel propagates both
  paths; two-parent isolation + repeatability + no instance state; mock
  heal stamps only the new leaf.
- Result: 16/16 pass. Static: ruff/black/mypy pass on all three files.

## Golden direction (frozen for root full capture)

- TS1 default (real `ts1_engine.py`, patched proto): 3 -> 4 REALIZED;
  only `coordination:000001` `failed_numerical -> published_leaf`, the
  other 11 targets status/reason/`evidence_sha256` identical, old 3 leaf
  `coords_sha256` preserved, +1 new leaf. TS1 spec carries no runtime full
  witness, so sigma honestly declines there; the recovery is consistent
  with the sibling path. Flexible/rigid TS1 and the 93-report capture are
  NOT rerun here (root full/golden owns them); candidate direction stays
  the root preallowlist (26 recovery-only / 67 immutable).
- Collect/out: +16 sibling nodes (new file), sigma file node IDs
  unchanged; byte-immutability is NOT claimed for collect/out metadata.
- D0 batch file: 31/31 pass (20 legacy + 11 phased; generic phases
  untouched). No whole-warehouse run, no install.
