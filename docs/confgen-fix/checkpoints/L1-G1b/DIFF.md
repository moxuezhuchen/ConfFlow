# L1-G1b DIFF (v2补修预演，含进仓检查点)

基点 `ff8eff1d6173ddaa96a6ce3ed8631e63ff845f21` 只读源 `/tmp/l1-g1a-exec`。
工作树 `/tmp/l1-g1b-proto` 分支 `refactor/l1-g1b-proto`。不提交/不推送。

## 下沉（WL-G1b，行为不变）

- `checkpoint_policy.py` 新增 10 阶段：`require_gaussian_program`（`is not
  ProgramName.GAUSSIAN`，Enum 对象，禁 `.value`）、`require_standard_adapter`
  （原值 `!= "standard"`，无强转）、`require_no_qst`、
  `require_standard_checkpoint_role`、`require_artifact_checkpoint_role`、
  `ensure_source_write_chk`（无写回）、`check_target_link0_core`
  （`ValueError` 直传）、`check_unsupported_method`、`check_route_cores`、
  `check_native_payload_cores` + `native_scientific_core(native, *,
  managed_keys)`。文案逐字节沿旧体。
- `producer/checkpoints.py` 五 wrapper 原签名交错：S-loop（resolve→Gaussian→
  adapter）→双边 native/keyword 形门→Q-loop→R1（standard resolve→role）→R2
  （executor resolve→artifacts role）；W 写回留编排；M1→M2→944-945 native
  抽取→M3；`frozenset(_NATIVE_MANAGED_KEYS)` 传值。删 `ProgramName`/
  `energy_semantics`/`rendering` 直引，wire 调用行文本不变，
  `__all__/VERSION/REUSE_MODES/charge-spin` 原样。真委托，非骗清零。
- `tools/architecture_policy.py` 新增 `g1_*` 四门（AST，prose 豁免以构造）。

## 异常顺序（契约，探针 9 项旧新一致）

- source-ORCA + target-unknown ⇒ ORCA 拒（S-loop source 先）。
- source-QST + standard 未注册 ⇒ QST 拒（Q-loop 先于 R1）。
- roles 缺失 + executor 不可解析 ⇒ roles 拒（R1 先于 R2 resolve）。
- target 方法族（MP2 post_scf）+ source native 缺失 ⇒ 方法族拒（M1 先于
  native 抽取，直接 `_check_method_compatibility` 调用实证）。
- 钉：target-QST、unknown-program、differing-keyword、checkpoint-role-missing；
  正：checkpoint ok（binding=true）。

## 阶段断言迁移（根授权恰 2 处，节点保留）

- `test_policy_imports_contain_no_producer_workflow_science`：保留
  producer/workflow/science 禁止；G1b 允许 `ProgramName` Enum 身份，仍禁
  `ExecutionRegistry`/`Callable`-lambda；补 `IsNot` 实证 + `.value` 缺席 +
  伪同 value 运行时仍拒；不删测试。
- `test_producer_keeps_g1b_gaussian_branches`：节点 ID 保留（注释留历史待
  L3 重组）；由 G1a“保留分支”反转为 G1b“零 Gaussian 条件/authority 调用”，
  附 10 阶段真实委托证据；与统一 `g1_*` 门一致；不 skip、不标 G1a-only。
- pure 三函数体/同对象及固定向量断言全保留。v1 补丁真实重跑
  `v1_2FAILED_evidence.log`（2 failed / 6 passed）为原证据保留。

## 根疏漏记录

G1a 卡新增了未来 G1b 必然失效的阶段性断言，G1b 白名单遗漏
`tests/v4/test_l1_gaussian_policy_move.py`。现按阶段目标授权增强并保留
节点，不将疏漏归执行器。

## v3 补修（root duck 兼容反例，去 assert，不扩白名单）

- 反例：`producer._native_scientific_core(Duck(items=>[keyword,basis]))`
  旧经旧 helper 路径返回 `{"basis":"x"}`；v2 新经
  `policy.native_scientific_core` 因 `assert Mapping/frozenset` 抛
  `AssertionError`。`object()` 无 `.items` 时旧为 `AttributeError:
  'object' object has no attribute 'items'`，v2 前为 `AssertionError`。
  记录为真实兼容反例，不归“仅类型非法可忽略”。证据
  `/tmp/l1-g1b-root-duck-probe.py` + `...-duck-old/new.json`。
- 修：仅 `policy.native_scientific_core` 去运行时 asserts，原 `.items` +
  成员关系原样；静态 `Any/Mapping+cast`；`managed_keys` 不收紧。其它
  规则/顺序/wrapper/`native_of` 形门原样。生产修改仅此 pure helper。
- 回归：`test_l1_gaussian_policy_apply` 新增 Duck 经旧 helper 路径成功 +
  plain-object 同类同文对照（原 8 断言不放宽）；双树 probe 一致，不把
  Duck 转 dict 绕过旧调用。
