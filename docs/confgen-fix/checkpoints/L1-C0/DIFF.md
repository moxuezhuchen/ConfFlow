# L1-C0 DIFF (pre-rehearsal, mechanical package)

Base: ConfFlow main `c7728447162ee76877e38966f96e0417e779f39c` (FIX-1R merged).
Worktree `/tmp/l1-c0-proto`, branch `refactor/l1-c0-proto`. No merge/publish.

## What moved (byte-mechanical)

- `confflow/producer/intent.py` (2000 lines) -> `confflow/producer/intent/compiler.py`
  (2000 lines). Body verbatim; only the relative-import level changes:
  top-level and lazy `from .cards/.presets/.recipes/.machine/.checkpoints/.seeds`
  gain one dot (`..`), and `from ..workflow/..programs/..execution/..domain`
  gain one dot (`...`). 17 import sites, 18 changed lines (18+/18-).
- NEW `confflow/producer/intent/__init__.py` (47 lines): compatible facade.
  Re-exports the four public symbols (`INTENT_SCHEMA`, `IntentCompilationError`,
  `compile_intent`, `intent_catalog`, same `__all__` order) plus the actually
  imported private `_recipe_cards_to_role_cards`. Same objects (`is` holds).
  Per root ruling, sets `__module__ = "confflow.producer.intent"` on the four
  callables as mechanical compat metadata (no parallel types, no behavior
  change); pickle of `IntentCompilationError` round-trips via the old path.
- NEW `tests/v4/test_l1_intent_package_compat.py` (117 lines, 6 tests).
- `tools/architecture_policy.py` and `tests/v4/test_architecture_policy.py`:
  ZERO change. `SCANNER_SCOPE` walks the `confflow/producer` directory
  (`rglob`), so the new package is covered with no path edit; no rule mentions
  `intent.py` literally. Rule scope/assert strength untouched.
- No change to `execution/`, `programs/`, `workflow/`, or any other producer file
  (`contract.py:633` and `authoring.py:1245` keep their `from .intent import ...`,
  which resolves to the facade). No bindings/resources/handlers/registry split,
  no empty shells, no production dummy, no self-registration.

## Why no production-doc/tool change was needed

- `docs/ARCHITECTURE.md:51` and `docs/PRODUCER_INTENT.md:7` name the module
  `confflow.producer.intent` / `producer.intent`, which is unchanged (now a
  package). `intent.py:932` comment already points at `tests/fixtures/`.
  Full `grep` inventory found production `.intent` imports only in
  `contract.py:633`, `authoring.py:1245`, and five test files (four public +
  one private at `boundary_coverage.py:955`); monkeypatch/importlib/pickle
  production uses: none (pickle compat still preserved per root ruling).

## Verification (executor self-check; root acceptance separate)

- New compat: 6 passed (identity, `__module__`, pickle old-path, real
  facade-vs-compiler compile equality, light top imports + no-science runtime,
  producer relative imports).
- Existing: producer_intent + regressions 64 passed; boundary_coverage +
  legacy_paths 290 passed; checkpoints + terminal diagnostic 75 passed;
  architecture_policy 333 passed; boundaries + g13g14 30 passed.
- Byte equality: real `compile_intent` wire + `intent_catalog` OLD (`/opt/ConfFlow`)
  vs NEW equal (sha256 `33240346...`, 9116 bytes); contract/boundary four
  digests OLD == NEW.
- Collect: OLD 4990 -> NEW 4996 (+6, exactly the new file; 0 removed).
- Static: `ruff check` clean (3 files); `ruff format` clean on 2 new files
  (`compiler.py` keeps the base pre-existing 1056 long-line state, untouched);
  `black --check` clean (3 files); `mypy` clean (2 production files).
- Full-repo pytest and full golden NOT run here (milestone root only).

## Raw freeze

- `/tmp/l1-c0-output/L1-C0-proto.diff` (`git diff --binary HEAD`, XML-escaped
  paths none; `new file mode 100644` x2 for the facade + compat test plus
  checkpoints; rename `intent.py -> intent/compiler.py`
  similarity 98%; sha recorded in `/tmp/l1-c0-output/SHA256SUMS`).
- External artifacts + sha256: see `MANIFEST.json` and `/tmp/l1-c0-output/SHA256SUMS`.
