# J1-projection DIFF — structure_preview shared parsing (proto v2 unit repair, not submitted)

- Base `ee21e8c34b2df0033cd8707e6c361f0a547a2256` (formal parser parent, read-only source `/tmp/j1-parser-exec`).
  Proto branch `refactor/j1-projection-proto` at `/tmp/j1-projection-proto`,
  exclusive output `/tmp/j1-projection-output-v2` (original `/tmp/j1-projection-output` kept intact, never overwrite).
  No commit/push/merge/tag. CF `main` (`c772844…`) and JD `master` untouched; other trees unchanged.
- Root prompts override early `INP-only-reject` design. Frozen parser `ee21` already supports single inline `* xyz`
  default-Angstrom with all Bohr/quoted-unit fail-closed (63 nodes preserved, untouched).
- AGENTS: `/opt/ConfFlow/AGENTS.md` and `/tmp/j1-parser-exec/AGENTS.md` both absent (record only, no new file).
- v2 repairs root unit counterexample `/tmp/j1-projection-root-unit-counterexample.txt`: Gaussian route is
  multi-line and `Units(Bohr)`, `Units=(Bohr)`, `Units=(AU)` were misreturned as Angstrom. Necessary gate:
  4 negatives must reject, Angstrom paren/multiline and title positives must accept, `strict=False` unchanged.

## Scope (parsing only)

- New `confflow/producer/structure_preview.py`: pure `structure_preview_request(parameters)` projection.
  Params `{filename, content_text, optional source_format_hint}`; returns `{elements, coordinates,
  index_base=1, source_format, warnings}` plus source `charge`/`multiplicity` only when declared
  (XYZ omits; no fabricated defaults). GJF/GF/COM via `core.gaussian_input.parse_gaussian_input_text(strict=True)`
  same runtime authority; INP via `programs.orca.input_parsing.parse_orca_input_text` new shared authority;
  XYZ via `core.io` strict read through exclusive temp file (never opens `filename`).
  Single geometry only; multi-XYZ/Link1 refused (never first-frame-only); order/precision verbatim;
  label diagnostic-only; no sorting/re-perception/optimization; no new geometry parser.
- `confflow/core/gaussian_input.py`: only optional `strict=False` seam (old calls/errors/output strictly unchanged).
  Producer calls `strict=True`. Strict enforces whole coordinate block, finite/elements/charge-mult legal
  and unambiguous, thin unit/multi-geom guards; standard Cartesian succeeds (digits exact with runtime);
  Bohr/Z-matrix/Ghost/unsupported explicitly refused; warnings only factual.
  No copied parser, no runtime-default flip.
  v2 unit repair is strict-only route-boundary logic: complete route `#` through continuation lines until blank,
  newlines included; shapes `units=X`, `units X`, `units(X)`, `units=(X)` with equals/parens/case/quotes/newline;
  non-Angstrom and unknown units rejected (`Bohr`, `AU`, `NM`, `xyzzy`); bare `bohr`/`AU` in route refused;
  titles/free text never scanned so title `bohr`/`Units=Bohr` stays diagnostic-only; quoted route spans excluded
  lexically while quoted declaration values stay declarations; reuses `_parse_tail_coordinates`, no third parser.
- Tests `tests/v4/test_structure_preview_parsing.py`: 43 nodes (v1 35 plus 8 v2: 4 root negatives
  `Units(Bohr)` multiline, `Units=(Bohr)` multiline, `Units=(AU)` single, `Units=(AU)` multiline;
  Angstrom paren and multiline accepted; unquoted title `bohr` and title `Units=Bohr` free-text accepted;
  plus real three formats, numbering/float/count, trailing-misline/empty/NaN-Inf/title-numbers/units-quoted+route/
  Link1/multiframe-XYZ/external-INP routing, runtime field-by-field parity, legacy Gaussian partial snapshots,
  AST no-duplicate-parser).
- No `authoring`/`boundary`/`contract` changes (L1 concatenation + cross-warehouse pairing deferred in one later card).
  No CF science/engine-runtime changes.

## Evidence

- New file: 43 passed (v1 35 plus v2 8; old v1 run on 4 root negatives proved BUG-ACCEPTED before repair).
- ORCA frozen suite: 63 passed (untouched).
- Combined new+ORCA: 106 passed.
- CLI: `tests/test_cli.py` 52 passed + 1 skipped; three-file inclusive 158 passed + 1 skipped.
- Collect: parent `ee21` 5053 → proto 5096 (+43 new nodes only, 0 removed; measured, not copied).
- `ruff` / `mypy` / `black --check`: pass on all touched files.
- Runner: `pytest -o addopts= -p no:cacheprovider`, fixed noeditable `PYTHONPATH`. No full/golden runs.
- AST: preview reuses authorities only; no `_parse_tail_coordinates`/`float(parts[-3])` duplication;
  no solver/execution/workflow/science-confgen imports.
  Default Gaussian `parse_gaussian_input_text` AST v1 vs v2 identical; default output/exception probe identical
  (`OLD_DEFAULT_PROBE.txt` vs `NEW_DEFAULT_PROBE.txt`); old counterexample probe kept as `OLD_COUNTEREXAMPLE_PROBE.txt`.
- Necessary gates: 4 root negatives reject `unsupported_unit`; Angstrom paren/multiline accept; title `bohr` accept;
  `strict=False` AST/probe unchanged; new-node diff exact +43/-0.

## Freeze bytes

- Standard `git diff --binary HEAD` (after `git add -N`) stored as exclusive output `FREEZE.diff`
  under `/tmp/j1-projection-output-v2` (modes/new-file included, no title separators, no reorder, no normalize).
  Blob SHAs in `MANIFEST.json`. Restore deletes only self-built paths back to parent-clean
  (no `git clean`, no whole-tree removal). Restore previewed: HEAD still `ee21e8c`, only 6 whitelisted paths dirty.

## Future authoring contract (frozen here)

- Parameters/result/errors shapes are frozen in `structure_preview.py` module docstring.
  Consumers must read structured `code`/`line`/`field`/`source_label`, never parse message strings.
- This proto does not claim J1 GUI/authoring completion; for root review only.
