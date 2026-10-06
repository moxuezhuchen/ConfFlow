# D0 DIFF v3 (logic, no golden change; supersedes the v2 draft)

- `confflow/science/confgen/model.py`: `GenerationStage` gains the optional
  `retry_solve(parent, target, context, should_cancel, first_pass)` hook
  (default decline) plus the frozen `RetryFirstPass` snapshot row. No axis
  literals, no component imports (same shape as the A3 hooks). Unchanged
  since v2.
- `confflow/science/confgen/engine.py` v3 deltas over v2:
  - retry relocation by stored first-round record OBJECT identity
    (`_record_index` at retry time), never fixed indices or id-string
    search, so earlier replacements cannot shift later ones (root blocker);
  - `_supersede_failure` counts declared types directly with loud
    `EngineConsistencyError` on missing/negative (no `max(0)`, no
    try/except swallowing); diagnostic-list purges removed: real first-pass observations remain
    historical attempt diagnostics, even when later preservation fails
    and a retry succeeds. Root runtime evidence is recorded separately;
    a target-id-only purge would affect other parents with the same id;
  - cancel propagates only on the retry path (conditional guard); legacy
    path including stage-raised cancel recorded as `stage_error` unchanged.
- `tests/v4/test_confgen_batch_stage.py`: 20 self-contained tests through
  real `run_kernel`, incl. the root multi-failure repro (6 leaves), mixed
  heal/fail_again/decline, two-level deferred revoke/retain, certificate
  equations. No fixtures, no TS1 solve, no tolerance change.
- Golden: unchanged by construction — no shipped stage overrides the hook,
  so every golden run takes the verbatim legacy loop.

Root supplement: the architecture guard calls the existing unified AST policy instead of a text regex. Independent stage: 82 tests, ruff/mypy/black pass; 16 captured nodes emit 23 reports byte-identical to FIX-1R. Full milestone acceptance remains pending until D1–D3 integration. Root initially omitted the capture plugin and created an invalid preliminary patch after the capture assertion failed; these artifacts are retained and labelled INVALID. Corrected capture v2 passed. The first diagnostic probe also used an incorrect evidence injection and retry-id condition; corrected audit/preserved-state runtime probe passed.
