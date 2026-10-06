# L-D3 DIFF (logic seam, preview-only — NOT submitted)

Base: `51246ced0c190c2325a1a72b4e59f9965bcd645c` (`fix/confgen-1d`).
Branch: `refactor/fix1d-ld3-proto`. Production files are disjoint from D2
(`coordination/*` untouched). Frozen patch: `/tmp/fix1d-ld3-output/L-D3.patch`.

## 1. `confflow/science/confgen/kernel_records.py` (new types only)

- `TelemetryError(ValueError)`: independent generic error; engine re-raises
  it visibly, never as `stage_error`; science judgements unchanged.
- `TelemetryRow` (frozen+slots): component-owned `kind/attempts/
  solve_successes/diagnostic`. Kernel validates ranges
  (`solve_successes <= attempts`) and JSON-freezes `diagnostic`; never
  interprets `kind`, never aggregates axis names.
- `BoundTelemetryEvent` (frozen+slots): engine-bound identity
  (`component_id/parent_target_id/target_id/ordinal/phase`) plus the opaque
  payload columns; `solve_successes` (geometry success) and `accepted`
  (audited publication) are separate columns, never merged.
- `RetryResult(outcome, telemetry, success_index=None)` (frozen+slots): the
  only unwrapped type (legacy plain outcome/`None` unchanged; arbitrary
  tuples rejected). `success_index` (root-authorized, optional) names the
  successful-start row and must point at a row with `attempts > 0` and
  `solve_successes > 0`; it is forbidden with `outcome=None` and out of
  range. Without it the engine applies the unique-success rule.

## 2. `confflow/science/confgen/model.py` (additive hook + annotation)

- `GenerationStage.report_statistics(snapshot) -> Mapping | None`, default
  `None`. Engine passes a read-only per-component snapshot tuple; `None`/
  empty means no fragment, so all existing goldens stay byte-identical.
- `retry_solve_phase` return annotation widened to
  `RealizationResult | RetryResult | None`. Old `retry_solve` signature and
  direct-API returns unchanged.

## 3. `confflow/science/confgen/engine.py` (run-local ledger + gates)

- `_RunState` gains a run-local `telemetry: list` ledger plus a
  `_pending_retry_selection` side channel `(span_start, span_end,
  selected_idx, phase)` written synchronously by `_solver` at solve return
  and consumed immediately by the calling `_expand_target`. No stage/global
  cache; ledger dies with the run on cancel.
- `_solver` unwraps only exact `RetryResult`, binds rows with
  engine-side identity, and derives the selected successful row: explicit
  `success_index`, else exactly-one successful row (0 or ambiguous counts
  raise `TelemetryError`). Failure outcomes and declines select nothing.
  Unknown return types raise `TelemetryError` (fail-closed, never
  `stage_error`).
- `_expand_target` input gate counts the attempt at actual `stage.realize`
  entry (placeholder `attempt=1 success=0`), then updates the success flag
  after return; throwing stages keep `attempt=1 success=0` while the legacy
  `stage_error` record path is verbatim. Policy/suppression/deferred gates
  return before entry (attempt 0).
- Publication attributes `accepted=True` to exactly the own selected event
  (input event, legacy synthesis, or wrapper selected row) via
  `_mark_telemetry_accepted`, which re-validates identity and requires
  `attempts > 0` and `solve_successes > 0`. No global-last inference, so a
  descendant event appended during child recursion can never steal the
  parent's acceptance, and trailing zero-attempt diagnostics stay `False`.
- `finish()` builds `scope["component_statistics"][component_id]` only when
  at least one bound hook returns a non-empty mapping; never overwrites
  existing scope/terminal fields; callers cannot choose `component_id`.

## 4. `confflow/science/confgen/wire_v3.py` (adapter forwarding only)

- `LegacyStageAdapter.report_statistics`: forwards iff the wrapped stage
  overrides the hook (required because the adapter otherwise shadows the new
  default and `_hook_impl` MRO detection would never reach the legacy
  override); fallback stays `None`, no component dispatch, registry/wire
  otherwise untouched.

## 5. Tests + docs (new files only)

- `tests/v4/test_confgen_retry_telemetry.py`: 13 nodes — plain bytes,
  wrapper-None, heal attribution, input-gate exclusion, own-parent (two
  levels), trailing diagnostic + ambiguity rejection, throwing-input vs
  screened, immutability/bad payload/success_index pins, multi-parent/reuse,
  cancel, additive report, duck compat, wire adapter.
- `docs/confgen-fix/checkpoints/L-D3/{MANIFEST.json,DIFF.md}` (this card set)
  and the `L-D3` entry appended to `docs/confgen-fix/LOG.md`.

## Root blockers (this revision)

1. `_mark_retry_accepted(mark)` (global-last) deleted; replaced by own-index
   `_mark_telemetry_accepted` + synchronous `_pending_retry_selection`.
2. `RetryResult.success_index` added per root authorization (explicit index or
   unique-success validation; ambiguous/zero-success payloads rejected).
3. Input attempt moved to call entry (`attempt=1` before `realize`,
   success updated after; throw keeps `success=0`, legacy `stage_error`
   record verbatim).
