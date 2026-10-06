# J1-projection LOG — parsing-only proto v2 unit repair

- Parent `ee21e8c34b2df0033cd8707e6c361f0a547a2256` verified (`git rev-parse HEAD`),
  branch `refactor/j1-projection-proto` verified (`git branch --show-current`),
  clean verified before apply (`git status --porcelain --branch` empty); original `FREEZE.diff`
  from `/tmp/j1-projection-output` applied clean (`git apply --check` then `git apply`) before v2 repair.
  Every command run with explicit `cd`. Source `/tmp/j1-parser-exec` read-only.
- Output `/tmp/j1-projection-output` kept intact; `/tmp/j1-projection-output-v2` created exclusively.
- Main `c772844…` clean except pre-existing untracked `confgen_realization_handoff.tar.gz`, `research/`
  (tracked files zero change). No JD tree writes. Missing AGENTS recorded only.
- Steps: strict seam in `core/gaussian_input.py` (`strict=False` default, old path byte-identical);
  new `producer/structure_preview.py` thin projection; new `tests/v4/test_structure_preview_parsing.py` (43 nodes).
  No authoring/boundary/contract edits; no science/engine edits.
  v2 repair: route-only `_strict_route_text` plus `_UNITS_PAREN_RE`/`_AU_TOKEN_RE`; old v1 probe on 4 root
  negatives proved BUG-ACCEPTED (`OLD_COUNTEREXAMPLE_PROBE.txt`), then repaired; default AST v1 vs v2 identical
  and default output/exception probe identical; no third parser copy.
- Checks: new 43 passed; ORCA 63 preserved; combined 106 passed; CLI `tests/test_cli.py` 52 passed + 1 skipped,
  three-file inclusive 158 passed + 1 skipped; collect 5053 → 5096 (+43/-0, new-only list verified, 0 removed);
  `ruff`/`mypy`/`black --check` pass.
  Fixed env `PYTHONPATH=tools/refactor-acc/noeditable:<root>`, `pytest -o addopts= -p no:cacheprovider`.
- Freeze: `git add -N` then `git diff --binary HEAD` verbatim to exclusive `/tmp/j1-projection-output-v2/FREEZE.diff`
  (canonical patch format per `/tmp/j1-parser-v2-root-canonical.patch`; no separators/reorder/normalize).
- Restore plan: delete only self-built paths (`producer/structure_preview.py`,
  `tests/v4/test_structure_preview_parsing.py`, `docs/confgen-fix/checkpoints/J1-projection/`,
  output dir files if created here), then verify parent HEAD/branch/clean. No `git clean`, no whole-tree `rm`.
  Restore previewed clean (HEAD `ee21e8c`, 6 whitelisted paths only); no formal commit/push/merge/tag.
- Trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Unsubmitted (no commit/push/tag).
  For root review; J1 GUI/authoring not claimed complete. Each card one commit left for root next-stage authorization.
