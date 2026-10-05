# L1-C1 DIFF (pre-rehearsal, mechanical bindings/resources split)

Base: formal C0 `78ab3d5773c304a11f71f06775b9ef136cf80700` (parent `c772844...`).
Worktree `/tmp/l1-c1-proto`, branch `refactor/l1-c1-proto`. No merge/publish.
PLAN L1 + `/tmp/l1-card-design-output/L1-DESIGN-v2.md`: v1 C1 main-loop
`resolve` proposal belongs to C2; C1 introduces no dispatch/registry/descriptor
and no capability science behavior.

## What moved (byte-mechanical, AST-equal)

- `confflow/producer/intent/compiler.py` (2000 -> 1666 lines; 10+/344-):
  only new light imports (`from .bindings/.common/.resources`, `noqa F401`
  for compat re-export) plus removal of 8 moved definitions. All remaining
  bodies, exception order/messages, ID/card expansion, scheduling/seed/
  checkpoint compile flow unchanged.
- NEW `confflow/producer/intent/common.py` (48 lines): only
  `IntentCompilationError` + `_fail` (stdlib `typing` only). Real dependency:
  the 6 helpers need `_fail`/`IntentCompilationError`; keeping them in
  `compiler` and importing `compiler` back would cycle
  (`compiler -> helpers -> compiler`); duplicating `_fail` would break
  `is`-identity/pickle. No solver/handler/registry/card/program/workflow/
  science knowledge (root review boundary).
- NEW `confflow/producer/intent/bindings.py` (129 lines):
  `_registry_input_ports` + `_auto_bindings`, verbatim (AST-equal).
  Top imports light (`copy`/`collections.abc`/`typing` + `.common`); no
  back-import of `compiler`; registry knowledge arrives only via the
  `registry` call argument.
- NEW `confflow/producer/intent/resources.py` (236 lines): `_wire_resources` +
  `_wire_scheduler` + `_apply_machine_profile` + `_apply_checkpoints`,
  verbatim (AST-equal). Checkpoint here is generic orchestration only
  (strip `_`-keys, call `wire_checkpoint_reuse`, clear/update); Gaussian
  details stay single-owner in G1, not mixed. Heavy lanes
  (`producer.machine`, `producer.checkpoints`, `workflow.v4.schema`) stay
  function-local lazy, verbatim.
- Compat: `compiler._auto_bindings is bindings._auto_bindings` (etc. for all
  6 + 2 common symbols); no new wrappers, signatures identical
  (`inspect.signature` equal). Moved helpers keep
  `__module__ == "confflow.producer.intent.compiler"`; facade 4 public +
  `_recipe_cards_to_role_cards` keep `__module__ == "confflow.producer.intent"`
  and pickle old-path roundtrip. `__init__.py` unchanged (still only
  `from .compiler`, so C0 `fmods == {".compiler"}` holds).
- NEW `tests/v4/test_l1_intent_helpers_compat.py` (156 lines, 5 tests).
- `tools/architecture_policy.py` + `tests/v4/test_architecture_policy.py`:
  ZERO change. No new `science.*`/registry exemption; policy scope conflict:
  none (333 + 7 + 25 passed). No `__init__`/old-test edits; no other producer/
  execution/programs/workflow production files touched.

## Old private-import / monkeypatch inventory (no break)

- Sole private import `tests/v4/test_producer_authoring_boundary_coverage.py:955`
  `from confflow.producer.intent import _recipe_cards_to_role_cards` stays in
  `compiler` (not moved); facade re-export unchanged.
- Production lazy `from .intent import ...` only in `contract.py:633`
  (`intent_catalog`) and `authoring.py:1245` (`compile_intent`); both resolve
  to the facade, unchanged.
- `grep monkeypatch|sys.modules` on producer/intent paths: zero hits (hits
  only in `test_architecture_policy.py`: `RUNTIME_RULES`/`subprocess`/
  `sys.modules["metrics_thin"]`, unrelated). No module-path patch to sync.

## Verification (executor self-check; root acceptance separate)

- New helpers compat: 5 passed; C0 package compat still 6 passed (total 11).
- Existing: producer_intent+regressions+machine+intent_boundary+p0boundary
  130 passed; authoring_boundary+legacy_paths+checkpoint_boundary+checkpoints
  +terminal 433 passed; architecture_policy 333 + fix1a 7 + g13g14 25 passed.
- Byte equality: real `compile_intent` wire + `intent_catalog` OLD vs NEW
  equal (file sha256 `6ed711d1...`, 10151B); contract.full
  `2fe92022...` and boundary.full `ee811b99...` OLD == NEW; digests
  `08d51085...` OLD == NEW.
- Collect: OLD 4996 -> NEW 5001 (+5 exactly the new file; -0).
- Static: `ruff check` clean; `ruff format` clean; `black --check` clean;
  `mypy` clean (4 production files).
- Full-repo pytest and full golden NOT run here (milestone root only).

## Raw freeze

- `/tmp/l1-c1-output/L1-C1-proto.diff` (`git diff --binary HEAD`, includes
  new-file modes; sha in `/tmp/l1-c1-output/SHA256SUMS`).
- `/tmp/l1-c1-output/L1-C1-production-only.diff`
  (`git diff HEAD --binary -- confflow/producer/intent`, production whitelist
  only; sha recorded in MANIFEST-adjacent `SHA256SUMS`, not self-referenced).
- External artifacts + sha256: see `MANIFEST.json` and
  `/tmp/l1-c1-output/SHA256SUMS`.
