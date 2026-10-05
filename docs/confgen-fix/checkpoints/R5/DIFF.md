# R5 DIFF — distorted_input reporting (generic hook)

## Science rule (Q6 report-only, R2 authority)

- `distorted_input` is a diagnostic only. No terminal state, count, reason,
  certificate, threshold, coordinate, state-key, fixture, contract, schema,
  or wire change. `ConfgenEngine.run(context, should_cancel=None)` signature
  unchanged; cancel semantics unchanged.
- R2 `analyze_rigid_units` stays the single authority. The stage pure helper
  reads the driving `context.structure` with the stage covalent graph plus
  `context.graph` typed edges, covers `preserve_input` and `enumerate`, uses
  global 0-based atom ids as-is (no solver-ordinal re-mapping), emits
  explicit `index_base=0` with deterministic `(ring_id, atom)` sort.
- Empty distorted never serializes: engine omits `report.component_diagnostics`
  when every hook returns empty/None; perception omits `distorted_input` when
  empty; executor omits ensemble warnings and the ensemble-report key when
  empty. Existing report bytes are therefore preserved.
- Analysis errors never become silent empty: the stage helper raises, the ring
  component hook maps failures to explicit `{"index_base": 0, "error": ...}`,
  and the kernel maps hook exceptions to `{"error": ...}` entries. Errors are
  visible diagnostics and do not alter science final states.

## Generic wiring (kernel purity)

- `registry.py`: new optional `ComponentDescriptor.report_diagnostics(context)`
  field, default `None`.
- `ring/component.py`: lazy `_report_diagnostics(context)` proxy to the stage
  pure helper; error mapped explicitly; registered on the rings descriptor.
- `engine.py`: generic `_collect_component_diagnostics(registry, bound, context)`
  loop (registry order, stage-override-first then descriptor, no component names,
  no component imports). Wired into both the normal `finish()` report and the
  `preserve_input` report; key added only when non-empty. `donor_configuration`
  path untouched.
- `ring/perception.py`: `RingPerception.distorted_input` defaults to `()`;
  `ring_diagnostics(perception, distorted_input=())` keeps the old single-arg
  call compatible and serializes the key only when non-empty. State identity
  stays `form/index/anchor/direction`.
- `execution/confgen_executor.py`: generic `_component_diagnostic_warnings`
  (distorted rows to `confgen_distorted_input` WARNING with explicit
  `index_base=0` in message and details, hook errors to
  `confgen_component_diagnostics_error` ERROR) wired into both COMPLETED and
  zero-leaf FAILED paths; serialized from the same helper into the
  ensemble artifact as `warnings[]` (WARNING rows only, non-empty only)
  plus `errors[]` (ERROR rows only, non-empty only, never relabeled as
  normal warnings); `component_diagnostics` retained; empty omits all keys.
- `ring/stage.py`: new pure `analyze_ring_input_diagnostics(context)`; no
  solver/R2/CP threshold, coordinate, state-key, or audit-threshold change.
- No `schema`/`contract` constant-module import change (import purity intact).
- v2 followup scope: only this executor file plus the R5 test and this
  manifest/DIFF changed relative to v1; engine/registry/ring files identical
  to v1 (v1 evidence in `/tmp/fix1r-r5-output` retained read-only).

## Golden

- `Golden-Changed = []`: all 93 pre-existing reports, TS1x3, contract/boundary
  unchanged. Verified: 21 allowed ring reports byte-identical after capture
  against both `/tmp/fix1r-r5-input-scan-output/cap_reports` and
  `/tmp/fix1r-r4-root-checkpoint/engine_reports` (0 mismatches); `contract.full.json`
  and `boundary.full.json` match the root checkpoint; `contract_digest`
  `02cfa496...` matches.
- New regressions are separately declared and do NOT expand `CAP_SCOPE`:
  `tests/v4/test_confgen_r5_diagnostics.py` (15 tests, nodes unchanged; the 2
  executor tests now assert serialized artifact `warnings[]` with code
  `confgen_distorted_input`, severity WARNING, details
  `index_base=0/atom=1/ring_id/observed/expected`, plus runtime/artifact
  consistency, on success and zero-leaf FAILED) plus external artifacts
  in `/tmp/fix1r-r5-v2-output/checkpoints` (v1 dir retained read-only;
  engine reports identical hashes, ensemble reports new hashes with the
  `warnings[]` field). This manifest records only
  SHA/path/raw evidence; artifacts stay outside the repo.
- v1 root evidence `/tmp/fix1r-r5-root-warning-probe/old-artifact-fail-corrected.log`
  (success + zero-leaf FAILED, 2 failures on v1 for the missing `warnings`
  field) is preserved; the first probe teardown `KeyError` log is retained
  and not claimed as valid.
- Remaining 72 no-ring reports were not loaded with the new hook change in this
  rehearsal; the full suite stays for the milestone.

## Files

- Modified (whitelist, 6): `ring/stage.py`, `ring/perception.py`,
  `execution/confgen_executor.py`, `registry.py`, `engine.py`, `ring/component.py`.
- Added: `tests/v4/test_confgen_r5_diagnostics.py`,
  `docs/confgen-fix/checkpoints/R5/MANIFEST.json`, `docs/confgen-fix/checkpoints/R5/DIFF.md`.
- No other production, fixture, contract, schema, or wire file touched.
