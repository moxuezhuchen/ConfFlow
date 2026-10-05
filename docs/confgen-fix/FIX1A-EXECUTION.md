# FIX1A-EXECUTION.md — 执行澄清（不改 PLAN.md 整体结构或其他卡片）

> 以下§0–4是补修前55b42a3的历史阶段快照；当前阶段以§5为准。
> BASE（正式树 /tmp/fix1a-exec，分支 fix/confgen-1a）：`55b42a3fe6265bbfc67a78ffda50389b991bec9e`
> 正式 collect：4802（AG2 stage 值；见 /tmp/fix1a-ag2-root-output/ROOT-STAGE-ACCEPTANCE.json）。
> 本文件只新增执行澄清；PLAN 其他结构不动。所有数据以根记录为准，不信 executor 原报告数字。
> stage 接受 ≠ full/golden。A6 与全量 full/golden 仍 pending，未知值一律写 pending，不编造。

## 0. 提交链（只读核对，git log reverse 自 B1 起）

| 序 | 卡 | commit | parent | patch SHA256（根） |
|---|---|---|---|---|
| B1 基线 | — | `1e7718c0652fe33d79bfc15d77833c4de7c1be56` | L0 merge `1038e4abc705b781aa26f497f777d6b6feb0396e` | —（冻结见 /tmp/fix1a-baseline-final-output/ROOT-ACCEPTANCE.json） |
| A1 | registry 发现 | `ad678f4e874c1bc63a56f12394c4bc7aefd8e8b7` | B1 | `b0fa685ecd6811aefa851a7746ac3bee25102979d26b61ad84696061759a0601` |
| A2 | wire 隔离 | `97143eb292da49b6949674e07a5347527304482a` | A1 | `4ebe1c30fed0b6d1415fc472320d7a97c48293525ae7fe6623ec25ca8a521a67` |
| A3 | fallback 移动 | `062a28595f6bd857b65a9654dae4b6dab8da5914` | A2 | `de76a29cbb6d2dcd42c276d16b1d41df503bdaea8b2dc291a6e328d37dba2664` |
| CAP-SIG | 签名保持 | `e10e161d2d11b4785b2df34d0971f789f3056d7d` | A3 | `b540c9d68562f0e9845cac1e4f0283d690ecbd928751d81ac7d31e63ac137bec` |
| A4a | registry 通道 | `f98a50a4a3c8c5d1b68460b494a59efa30a64c43` | CAP-SIG | byte equal（见 formal ROOT-ACCEPTANCE.json） |
| A4b | spec 归一下放 | `47e913a1ed71841f68ec6b02424e8bb4378af8f1` | A4a | byte identical |
| A4c | topology 覆盖下放 | `6182d46da09c786143d65788a3eccaf56f0307b9` | A4b | byte identical |
| lazy | 懒兼容导出 | `ed4be360a630548c4f29dd7edf7ce30665ae0754` | A4c | byte identical |
| A4d | 继承 scope 搬迁 | `26476f5d3a061a538c37e91e5e3feb003d51dccd` | lazy | `b991544c6c2c248736c547dbc31258c699b056dd846e5f6a6db3a49e3a5c0d24` |
| A5 | 轻词汇表 | `71b742af2798df6a94fefef28d8d1f682325c2b6` | A4d | `86900a53027d8c702c1b6ae2b0400c13700e9ee8d9dbec03c1312703334da9de`（root A5-v2 patch；formal diff byte identical） |
| AG1 | 上下文准备下放 | `65bfd890649b4fc4ec6446006351cbb990e22ecd` | A5 | `a683e0c03f373f3658a66cdc865363bf0b78cb2ced89291b5d79453cca89813c`（AG1-v3） |
| AG2 | 兼容声明决议 | `d5e096cc561dc06a4a71f09bc4cd0bbc1e7f19f2` | AG1 | `3baab871f90b72a1d4565ca06df937d84041db29443a5999bade775e7e6cd863`（AG2-v3，9 files） |
| CAP-cleanup | 报告 scope+别名恢复 | `00a798e0fbd3b82ac3ee52ce01de34c2bf4dc42a` | AG1 本体（`65bfd89…`；与 AG2 非重叠，已由根合入为55b42a3） | `93f338de0e8177cbdcab15ccbf96fd9a2933513e07cf72fb5d42fbe17836873c`（3-file root patch） |
| 集成 BASE | 最终 | `55b42a3fe6265bbfc67a78ffda50389b991bec9e` | — | 正式树 HEAD（`git rev-parse HEAD` 实测一致） |

根正式验收外部路径（逐卡）：
A1 `/tmp/fix1a-a1-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a1-output/ROOT-STAGE-ACCEPTANCE.json`）；
A2 formal `/tmp/fix1a-a2-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a2-output/ROOT-STAGE-ACCEPTANCE.json`）；
A3+CAP-SIG formal `/tmp/fix1a-a3-cap-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a3-v2-output/ROOT-STAGE-ACCEPTANCE.json`、`/tmp/fix1a-cap-sig-output/ROOT-STAGE-ACCEPTANCE.json`）；
A4a formal `/tmp/fix1a-a4a-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a4a-root-output/ROOT-STAGE-ACCEPTANCE.json`）；
A4b formal `/tmp/fix1a-a4b-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a4b-v2-root-output/ROOT-STAGE-ACCEPTANCE.json`）；
A4c formal `/tmp/fix1a-a4c-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a4c-root-output/ROOT-STAGE-ACCEPTANCE.json`）；
lazy formal `/tmp/fix1a-lazy-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-lazy-root-output/ROOT-STAGE-ACCEPTANCE.json`）；
A4d formal `/tmp/fix1a-a4d-v3-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a4d-root-output/ROOT-STAGE-ACCEPTANCE.json`，patch `/tmp/fix1a-a4d-root-output/A4d-v3.patch`）；
A5 formal `/tmp/fix1a-a5-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-a5-root-output/ROOT-STAGE-ACCEPTANCE.json`，patch `/tmp/fix1a-a5-root-output/A5-v2.patch`）；
AG1 formal `/tmp/fix1a-ag1-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-ag1-root-output/ROOT-STAGE-ACCEPTANCE.json`，patch `/tmp/fix1a-ag1-root-output/AG1-v3.patch`）；
AG2 formal `/tmp/fix1a-ag2-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-ag2-root-output/ROOT-STAGE-ACCEPTANCE.json`，patch `/tmp/fix1a-ag2-root-output/AG2-v3.patch`）；
CAP-cleanup formal `/tmp/fix1a-cap-cleanup-formal-output/ROOT-ACCEPTANCE.json`（stage `/tmp/fix1a-cap-cleanup-root-output/ROOT-STAGE-ACCEPTANCE.json`，patch `/tmp/fix1a-cap-cleanup-root-output/root.patch`）。
最终 capture scope `/tmp/fix1a-final-capture-scope.json`；pending minor `/tmp/fix1a-pending-minor.json`。

## 1. 真实必要补卡（澄清口径）

- **CAP-SIG（preserve signature）**：capture 插件只包 metadata，`ConfgenEngine.run` 公开签名不改；原 capture wrapper 遮蔽真实签名致 A3 初跑 246 pass / 2 fail，根独立在 capture 外复测 2 pass 定位，CAP-SIG 独立实测修复后 2 真 capture 签名 pass + 工具 24 pass。功能：wrap metadata only，原 return/capture payload 逻辑 byte 不变。
- **公开 `run.should_cancel` 签名不改**：新树锚点 `confflow/science/confgen/engine.py:860` 仍为 `def run(self, context, should_cancel=None) -> EngineRun`；`run_kernel` 私有通道另行传 `initial_key/stage_overrides`，不视为公开破坏。
- **`build_typed_graph` 兼容裁决（A4a 根裁决）**：公开 `planner.build_typed_graph(structure, topology, resolved, *, registry=None)` 可省 registry（默认走共享不可变 default；新树 `planner.py:765` 实测 3 位置参数 + 关键字 registry）；私有 `_build_typed_graph` 必传已决议 registry。`build_context` 恒传已决议实例。
- **A3 fallback 真实可达所以移动而非删除**：`ring/component.py:168` 与 `ring/scope.py:99,139` 的 `ImportError → generic comparison` 路径经采样证实可达（初版 A3 漂移为 ambiguous，v2 恢复旧 ok 并加 missing/drift 回归）；AST 约束：five legacy model records、既有 75 stage 方法体、run 签名、accounting 字节不变。
- **A4d（两模块 PEP562 恰 3 名字 + 通用 ValueError 保源标识）**：`engine.py:87-145` 与 `__init__.py:79-…` 各自仅懒服务 `InheritedTorsionLock / inherited_torsion_locks / check_inherited_torsion_locks` 恰 3 名，其他 miss 抛 `AttributeError`（永不 `ImportError` 掩盖拼写错）；正常错误（`InheritedScopeError` 等）保持 eager 同一对象。通用 `ValueError`（未知 axis / parent_key 类型 / active_components 类型等）保留源标识文本，不改 fail-closed 语义。
- **AG1**：组件 `spec_defaults` 声明顺序 + fresh copy；`has_indices` probe 经 `build_context`；context expansion 按 registry order 跑组件 expand hook；graph metadata（coordination `graph_metadata` 懒代理）custom channel 逐字。深 freeze（构造时深冻）/ thaw（每次 normalize 深解冻）使缓存不可变；恢复**原空 paths guard**（空 paths 不建 expansion metadata，2 tests 锁定；v2 曾把 BASE-context 差异说成预先存在，根不接受，v3 已修）。
- **AG2**：旧 `ConfgenStateKey`（legacy）分支忽略传入 custom registry（`engine.py:157,198 resolve_registry(None)`，冻结 replace-vs-merge）；generic `ComponentStateKey` 尊重 custom `state_merge`。generic compat 走组件 declarations + explicit topology keys（`merge/replace` 各一测 + legacy `empty/changed_modes` 冻结语义）。唯一 helper owner：`as_kernel_target` 唯一显式导出归 `kernel_records`（`kernel_records.py:27,167`），去掉本轮误加的 `__init__.py` facade 导出（旧 B1 facade 未动，`engine.py` 内复用 import 保留）。
- **CAP-cleanup（显式 capture scope 仅 report write-out）**：`run_sharded.py:210-213` 保留默认 broad glob，显式 `CAP_SCOPE_GLOB` 仅用于报告写出过滤；fixture 对全部 collect 节点仍执行，仅过滤捕获写出。根最终 scope：`tests/v4/test_confgen_[pv]*.py` 精确对应冻结 **93 基线**（`baseline_reports: 93`，见 `/tmp/fix1a-final-capture-scope.json`）；所有产品 tests 仍执行并留在 collect/out 精确节点门禁；old B1 不可变；tool7 摘要更新留待根最终集成 manifest 重算（formal 内不私自改 B1/digest）。

## 2. 阶段证据（根为准；executor 数字不采信）

- A1：根 165 pass（`/tmp/fix1a-a1-root-tests.log`），ruff/black pass，mypy 215 源文件；formal 新 34 pass，collect 4666→4700（added 34 / removed 0）。冻结 node 清单曾漏 1 项，根 erratum 以 pytest collect 为准。
- A2：根 pre_minimal 186 + 最终 registry 56 + a2 policy 4；collect 4700→4726（added 26 / removed 0）；AST（5 legacy 类一致、75 stage 方法体一致、run 参数/返回一致、accounting byte 一致）；static ruff/black12/mypy218。**口径红线**：不得声称 A2 全部 93 重算，只有 keys roundtrip 83/93 层面的键往返一致（报告 93/436 为该往返域内字节一致，非全产品 93 报告重算）。
- A3：根初跑 capture 下 246 pass/2 fail（签名遮蔽所致），capture 外真签名 2 pass；修正后 registry with capture 69 pass；旧报告 capture **仅 82/93**（matched 82，11 未覆盖：paths_audit/phase0/typed 共 11 个 report 名，见 stage JSON `not_covered_in_stage`）；collect 4726→4739（added 13）。完整 93 / fresh TS1 / contracts 由根最终验收补齐，本卡不冒充。
- CAP-SIG：根旧失败 2 产品签名断言（原失败日志保留），修复后真 capture 签名 2 pass + 工具 24 pass。
- A4a：根 132 tests；collect 4746（added 7 / removed 0）；ruff pass；patch graph AST equal。含 `public_build_typed_graph_compat_and_private_requires_registry` 兼容裁决锁。
- A4b：根 203 tests；collect 4759（added 13）；mypy 224；normal differential 精确 unsorted JSON。v1 曾因部分供给 specs 的 json key order 漂移被根打回，v2 以 exact hook key contract + sort_keys=False 字节序锁定。
- A4c：根 150 tests；collect 4764（added 5）；digest/edges/errors 13 cases 根 differential 一致；mypy 224。`registry descriptor contribute_topology` 为必要通道授权扩展。
- lazy：根 101 tests；collect 4771（added 7）；exact 原 7/28/22 names 有序；purity 无 solver 模块；mypy 224。
- A4d：根 218 tests + 修正 6 + 报告 7（formal：`root_tests 218 / corrected 6 / reports 7 / collect 4777`）；stage 6 A4d pass、7 capture byte equal、import identity pass、limited import AST 仅 `engine.py:145` + `__init__.py:167` 两处；mypy 225。
- A5：根 260 tests；collect 4783（added 6）；mypy 228；contract pin-bound 3 文件与 B1 byte identical。
- AG1：collect 4797（added 14：registry ag1 6 + defaults-freeze 6 + empty-paths 2）；v1 根 registry_paths 187 / preview_topology 67；v3 根 registry 127；type files 228；root differential normalize 207 / typedgraph 13 / context 4 与 A5 BASE byte identical。
- AG2：collect 4802（added 5）；根旧必要 149 pass（151 含 2 新 generic）+ 最终 5 API tests 单独 pass；mypy 228；differential 303 行 legacy API（含 empty/modified registries）byte equal；G13 7-file AST zero（A6 formal policy 待后）。
- CAP-cleanup：根 26 pass + static ruff/black；formal 仅 2 新节点（`test_capture_explicit_scope_filters_write_out_only` + `test_load_plugin_copy_restores_alias_modules`）pass；白名单恰 3 工具文件；与 AG1/AG2 非重叠。
- 最终门禁：`capture_write_scope = tests/v4/test_confgen_[pv]*.py`，`baseline_reports = 93`，`full_tests = all collected nodes, no subset`；根最终 capture 须恰冻结 93 名、字节相等、TS1/contracts 不变，缺一/多一均不容忍（pending，由根填）。

## 3. executor / root 疏漏（已留痕，不掩盖）

- A4d root v2 只重跑 static/import，未复跑新 AST test；formal 检出失败拒停、无提交、原证据保留 → v3 修（去 TYPE_CHECKING 组件 imports 与一切具体 Name 声明，仅留 2 处精确 F822 注释）。
- A5 executor 8-file mypy 遗漏，root full mypy 发现 Any 返回；以 `cast(float)` 修（无运行时强制），root 初错测试路径保留、修正后 260 pass。
- AG1 v1 共享 mutable defaults（根 repro 实证，要求 deep freeze/thaw + 6 tests）；v2 executor 把空 paths context 与 BASE 差异说成“v1 预先存在”，根不接受 → v3 恢复原组件调用 guard + 2 tests。另 executor “final full 255” 指 scoped 255，非产品全量，根已披露。
- AG2：valid empty / changed_modes legacy 差异由根 303 probe 定案；根新 test 初漏既有 `schema_version=3`，后补并 5 pass（失败日志保留）；B009 同属性查找路径修正后 5 重跑。
- 根初始错误测试路径更正日志保留（A5 wrong path、CAP-cleanup wrong reachability path 均保留原日志再纠 26 pass）。
- CAP executor 遗漏 card/proto 清理（留 proto 脏、缺 card/prompt/restore），根补齐 paperwork 并在字节核对后清理。
- A1 冻结 node 清单漏 1 项、A2 根初版把含 warnings 的 collect.txt 当节点清单，均已根勘误并以 `collect-nodes-sorted.txt` 精确节点相等为准。

## 4. 补修前待办快照（现状以§5为准）

- `A6_dummy_formal_sha`：pending（以 A6-ready 为准，BASE 应为本 BASE `55b42a3…`）。
- `A6_G13_G14_policy_tests`：pending；`A6_collect_delta`：pending。
- `FIX1A_full_tests_passed/skipped/collect/wall`：pending（根里程碑末全量）。
- `FIX1A_golden`：pending（true/false 由根判）。
- `FIX1A_final_capture_93_names_byte_equal`：pending；`TS1_contracts_unchanged`：pending。
- `tool7_digest_recomputed`：pending（根最终集成 manifest 重算时填）。

> 小报告 stats/文案不罗列垃圾：以上数字均为门禁必需（commit/patch/collect/added/关键 pass 数），其余过程日志只给外部路径，不抄正文。

## 5. 最终补修与根独立阶段验证

- POLICY-COMPAT：来源提交57091f599c84eb9ff990252ddb0c6de284b96e42，集成931b02cf3a100e7b7ec3f2577006376a3974742c。contract使用不可变缓存default_registry；AP002仅transform_executor.py的registry精确import许可，其他science仍拒。根7回归通过，契约三摘要与B1逐字一致。
- A6根最终冻结补丁SHA256 c834b52426ec030a4ec1e0aa1dd21d123290d4c1a83dab9bad818426976aece7，基点931b02c，六文件。根实际组合491 passed；ruff、mypy完整228文件、black8文件通过。正式提交912e545807f81688236d1214a571e9a6e45ce312，根commit diff逐字一致且树干净。
- collect4859，相对4802新增58、移除1，唯一移除为test_no_g13_g14_rules_are_enabled改名test_g13_g14_rules_are_enabled，断言随里程碑启用规则转换，无科学行为变化。
- 根发现并修复A6普通第四名导出与顶层if注册漏检；继续补齐类体、装饰器、函数/lambda默认参数的import执行反例，普通函数体不扫描。
- 根AG2移除误加facade导出后漏同步旧A4d导出数量断言，最终65改64，并断言helper仅kernel_records导出，已由根回归验证。A4d218与7capture属于先前运行证据，不冒称最终v3全部重跑。
- A6正式执行提示词曾把环境简写成/usr/bin/noeditable/jd-pin，执行器误搜索不存在路径；根中断无效运行并用完整环境变量续接，原日志保留。
- 全量、93份capture、TS1、golden仍待根最终运行。上述阶段通过不替代最终验收。
