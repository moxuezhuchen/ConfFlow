# J1-parser DIFF v2 — quoted-unit lexical repair (not submitted)

- Base `c7728447162ee76877e38966f96e0417e779f39c` (`main`, read-only).
  Proto branch `refactor/j1-parser-proto` at `/tmp/j1-parser-proto`,
  v2 output `/tmp/j1-parser-output-v2` (v1 `/tmp/j1-parser-output`
  preserved, never overwritten). No commit/push/merge/tag.
- Root review `/tmp/j1-parser-root-review` applied the v1 `FREEZE.diff`
  read-only; its frozen probe `/tmp/j1-parser-root-unit-frozen-probe.json`
  is the only valid counterexample (the earlier actor-cleared-proto
  `ModuleNotFoundError` probe is void, not evidence).
- Whitelist still exactly the same 4 new paths (no LOG, no old-file edits).

## Root counterexample (frozen)

- `units="bohr"`: v1 ACCEPTED `[[1.0,0,0]]` (wrong, must refuse).
- `units "bohr"`: v1 ACCEPTED `[[1.0,0,0]]` (wrong, must refuse).
- `units=bohr`: v1 REJECTED `unsupported_unit` (correct).

## Root cause

- `_check_structured_unit_declarations` deleted quoted spans from the
  whole line (`_strip_quoted`) before running the value regex, so
  `units="bohr"` became `units= ` and the value group naturally missed.
- Fix: strip trailing `#` comments outside quotes; run the
  `units = X` / `units X` patterns on the code region with possibly-quoted
  values; ignore a match only when the `units` keyword itself lies inside
  a quoted title span (`%base "units=bohr"`); quoted values stay real.
  `!`-line tokens are checked on quote-stripped code; `source_label` is
  never scanned. Scope unchanged: single inline `* xyz` default-Angstrom
  only, no new ORCA syntax.

## Parser delta (v1 → v2, same 4 paths)

- `confflow/programs/orca/input_parsing.py`: added
  `_strip_inline_comment` + `_quoted_spans`; rewrote the unit-scan loop to
  `finditer` with keyword-span exclusion. Header/atom/closure logic
  untouched.
- `tests/v4/test_orca_input_parsing.py`: original 50 nodes/assertions
  preserved byte-for-byte in intent; appended 13 v2 nodes
  (`test_v2_quoted_and_plural_units_rejected` ×8,
  `test_v2_comment_and_quoted_title_positive` ×4,
  `test_v2_source_label_never_scanned` ×1).
- `MANIFEST.json` / `DIFF.md`: v2 freeze records in the same 2 paths.

## Evidence

- New file: 63 passed (50 preserved + 13 new).
- V1-source proof (direct `PYTHONPATH=/tmp/j1-parser-root-review` import):
  all 8 quoted-decl cases ACCEPTED on v1 (new tests would fail 8/8).
- Units equivalence: default-Angstrom renderer water input yields
  byte-identical JSON on v1 vs v2 module bytes (isolated subprocess proof).
- Existing ORCA (`-k orca`): 230 passed (217 + 13 new), 4160 deselected.
- Collect: baseline 4990 → v2 5053 (+63 new nodes only).
- `ruff` / `mypy` / `black --check`: pass on both new files.
- Runner: `pytest -o addopts= -p no:cacheprovider`, fixed noeditable tree
  per case. No full/golden; no tolerance/fixture changes.

## Freeze bytes v2

- Full `git diff --binary --no-index` for the 4 paths is stored as
  exclusive output `FREEZE-v2.diff` (sha in `CARD-v2.md`). Modes `644`
  with blob SHAs in `MANIFEST.json`. Restore deletes only the self-built
  paths back to parent-clean (no `git clean`, no whole-tree removal).
