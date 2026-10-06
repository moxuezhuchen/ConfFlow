# D3 DIFF (science, preview rehearsal — NOT submitted)

Base: `29f1ac2b8d0ce0a5ecced722e02753529009cb54` (`fix/confgen-1d`, D2+sibling+L-D3 seam).
Proto: `/tmp/fix1d-d3-proto` branch `refactor/fix1d-d3-proto` only; formal line untouched.
Frozen patch: `/tmp/fix1d-d3-output/D3.patch` (`git diff --binary`, all new files/mode).

## Production: `confflow/science/confgen/coordination/stage.py` only

- `report_statistics(snapshot)`: read-only aggregation into
  `scope.component_statistics.coordination` =
  `{start_statistics.{input,sigma_image,sibling}.{attempts,solve_successes,accepted},
  skip_diagnostics[]}`. Input from the engine input gate (real `realize` entries;
  policy/suppression never enter); sigma/sibling from D3 phase payloads (one row per
  real candidate call, failures included; success explicit `success_index`; zero-attempt
  diagnostic rows `attempts=successes=0 accepted=False` with verbatim
  `sigma_skip_diagnosis`/`sibling_skip_diagnosis` reasons). Empty snapshots return
  `None` (non-coordination goldens unchanged). Snapshots with generic legacy `"retry"`
  phase (old `retry_solve` override, per-candidate count unobservable) return `None`
  (no `sigma 0` masquerade). No instance/global cache; stable sort.
- `retry_solve_phase`: `sigma` uses instrumented `_sigma_retry_phase` for the default
  built-in (same order/budget/solver path as legacy `retry_solve`, single solve, no
  re-solve); when a subclass overrides public legacy `retry_solve` (MRO probe
  `_has_legacy_sigma_override`), it delegates plain outcome/`None` single-solve via the
  generic legacy path (opaque candidate count not masqueraded). `sibling` uses
  `_sibling_retry_phase` (same `<=3` ordinal order, dedup, backend-faithful path,
  cancel propagates). Already-successful/non-retryable decline with plain `None`, no row.
- Legacy `retry_solve` signature/return (plain outcome/`None`) verbatim; direct callers
  unaffected; `retry_phases()==("sigma","sibling")` unchanged.
- Untouched: kernel/model/wire/accounting/registry/tolerances/witness/fixtures/schema/
  symmetry; no new `retry_statistics.py` (see MANIFEST reason).

## Tests: `tests/v4/test_confgen_retry_statistics.py` new, 10 nodes

Real runtime reports throughout (never helper-only): input-vs-real-calls, no-witness skip
(30/29/29 input, sigma 0, sibling 2/1/1 + 1 sigma skip), sigma success (1/1/1 + retry_start
`sigma_image:coordination:000018`), failure+diag binding, exhausted sibling, cancel,
repeat/parent isolation, sigma-before-sibling order, two-candidate index binding, legacy
plain-API pin. Old sigma/sibling/telemetry/batch assertions unweakened.

## Golden direction

- TS1 default after (single collection): 4 leaves / 8 `failed_numerical` (unchanged counts);
  `start_statistics` input 12/3/3, sigma 0/0/0, sibling 25/1/1; skips 17 (9 sigma
  no-witness + 8 sibling exhausted). After stripping `scope.component_statistics`, all
  targets/leaves/certificate/report bytes equal the frozen before; leaves/targets equal;
  only `report_sha256` differs (`ea4e46eb…` → `25d6abea…`). Contract/boundary unchanged.
- Collect/out: 5077 = 5067 + 10 (new file only).
