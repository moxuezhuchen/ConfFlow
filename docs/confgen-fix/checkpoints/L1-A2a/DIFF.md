# L1-A2a DIFF — seed/resource plumbing + A1 compat fallback closure

Base: `ebbba518c437c8c1692b3517cd74a30c5f2e18db` (A1 formal).
Tree: `/tmp/l1-a2a-proto`, branch `refactor/l1-a2a-proto`.
Output: `/tmp/l1-a2a-output`. No commit/push/merge/tag.

Scope (only): seed/resource plumbing and A1 compat fallback closure.
Recipe long-chain is A2b, analysis/Dummy final verification is A3 —
both explicitly excluded here, no cancellation of PLAN scope.

## 1. `intent/compiler.py` — reject delegation

- `_reject_misplaced_fields` 4-arg fallback no longer imports
  `capabilities/{calculation,confgen,transform}` nor holds the builtin3
  dict. It delegates to
  `capabilities.registry.rejected_step_keys_for_legacy_fallback`.
- Unknown executor still empty; misplaced text/sort/step timing unchanged
  (`step '<id>' (<card>) cannot consume fields: <sorted>` after card
  resolve, before handler). Default run path still passes the resolved
  `entry`.
- Import/invalid-metadata failures now fail closed via `_fail` with step
  context, never collapse into an empty set (no silent science loss).
- Source contains no `== "calculation"` / `== 'calculation'` /
  `== "confgen"` / `== 'confgen'` / `== "structure_transform"` in the
  reject path (locked by old A1 test + new A2a test).

## 2. Provisional checkpoint seed blocks — descriptor-driven

- Both provisional loops (`copy-in` and `strip`) no longer iterate the
  literal `("calculation", "confgen")`. They iterate
  `seed_block_keys_for_registry(intent_registry)` — the union of each
  descriptor's effective seed keys.
- `CapabilityDescriptor.seed_block_keys` defaults to `()` (no new
  required field). Empty derives the builtin default: calculation/confgen
  derive `(fragment_keys[0],)`, transform derives `()` (no slot).
  Explicit values must live inside `fragment_keys`.
- Default 3 executors: calc `(calculation,)`, confgen `(confgen,)`,
  transform `()`. `build_default` passes them explicitly;
  `build_intent_registry` validates them and fails closed on
  per-executor divergence (`conflicting seed_block_keys`).
- Order/state preserved verbatim: provisional `assign_seeds` →
  copy only `derived` ids → `_apply_checkpoints` → strip only derived
  (explicit untouched) → final `assign_seeds` stays after checkpoints
  (position not advanced). Multi-error still surfaces the original
  checkpoint/seed error (`IntentCompilationError` re-raised unchanged).
- `seeds.needs_seed/_current_seed/_set_seed` remain the sole authority;
  no new science criteria added.
- Measured: multi-edge capped confgen derived seed `1687161672`
  identical base→patch; explicit `12345` preserved; bindings
  `checkpoint: one` unchanged.

## 3. `_seed_scope` relocation

- Compiler-local `_seed_scope` logic moved verbatim into
  `producer/seeds.py::seed_scope_for_step(step, source)` and exported.
- Compiler calls it generically (import inside `compile_intent` +
  module-level `_seed_scope` compat wrapper delegating without branches).
- Values unchanged: `none`→`None`, confgen→`native_sampling`,
  calculation+`goat`→`workflow_identity_only`, else `None`.
- No change to `needs_seed` derivation/tolerance/fixtures; no GOAT
  judgment moved into a new intent table.

## 4. `resources._apply_machine_profile` — declarative program

- `calculation.program` direct read replaced by assembly metadata query:
  `wire_block_key_for_executor(None, step.executor)` then read `program`
  from the owner block only. Today only calculation blocks carry
  `program`, so confgen/transform still resolve `None` exactly as before.
- Private signature `(wire_steps, machine_profile)` unchanged;
  `compiler._apply_machine_profile is resources._apply_machine_profile`
  (`__module__` still `confflow.producer.intent.compiler`).
- No extra optional context param introduced (no evidence one is
  required). No new program/port legitimacy table; strict V4 builtin wire
  keys via the same assembly point. `compile`/`authoring`
  `default_registry()` remains the same cached instance.
- Metadata conflict uses the assembly builtin-default fallback, never
  silent `sorted-first`; strict compiler stays downstream authority.
- Measured: `orca→/opt/orca`, `gaussian→/opt/g16`, binding/target/env
  behavior unchanged.

## Explicitly untouched

- Recipe `role_cards`/`_apply_role_cards`/`_patch_recipe_step`,
  validation registry/schema/program rules are not copied into intent.
- C0/C1/C2/A1 test nodes/functions unchanged (collect `5025→5037`,
  `+12/-0` only from the new file).

## Verification (measured, not prefilled)

- New: `tests/v4/test_l1_intent_seed_plumbing.py` 12 passed.
- Necessary: 287 passed (new + compat + intent + machine + seed +
  checkpoint + boundary).
- `ruff check` clean; `ruff format` clean; `black --workers 1` clean;
  `mypy --num-workers 1` clean (5 prod files).
- `contract_digests --cf <tree> --jd-src /opt/cf-worktrees/jd-pin/src
  --out <exclusive>/contract.json` patch vs same-param base:
  `contract.json`, `contract.full.json`, `boundary.full.json` all
  byte-identical (`contract.full 2fe92022…`, `boundary.full ee811b99…`).
- No full/golden run here.

## v2补修（根冻结反例后，不覆盖v1证据）

- `resources._apply_machine_profile`新增仅keyword `intent_registry=None`，
  两位置调用仍有效；`compile_intent`真实传入唯一`intent_registry`。
  Assembly import/query一律fail-closed（`_fail`带step），不再
  `except->None`继续。根反例`descriptor binding broken`旧父取
  `/opt/g16`、冻结v1静默丢`executable`、v2报
  `IntentCompilationError step 's': machine program binding broken`。
  自定义`a2a_machine_probe`真实编译机器解析证明同实例通道必要。
- `test_a2a_reject_fallback_delegates_no_branches`改AST作用域检查
  （import目标/字符串常量/Compare/调用，忽略docstring/comments），
  保留未知/排序/文案断言，不删旧测试。
- `wire_block_key_for_executor`去尾部`sorted(found)[0]`，同executor冲突
  一律`ValueError` fail-closed；无条目回builtin默认、未知回`None`保持。
  `seed`元数据区分旧无字段兼容与声明坏字段，坏字段raise，
  已构造验证不放宽。`compiler._wire_block_key_for_executor`同样透传冲突。
