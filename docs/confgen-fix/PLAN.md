# ConfFlow 修复与 locality diet 方案（FIX-1 + L）

> 状态：**草案 v3.3（2026-10-04）**，已纳入六轮外部评审与本轮精确修订（§0.0）。L0 可以按当前文本执行；FIX-1A 在 Q5 确认后可以冻结。§0.2 中的决定**按里程碑分别确认**：每个里程碑开工前，只需确认表中"确认时机"列属于它的那几项（V46）。
> 作者角色：方案与验收（不执行）。执行模型只按本文件工作；验收协议沿用 `docs/refactor/ACCEPTANCE.md`（L0.1 之后为 `docs/process/ACCEPTANCE.md`），本文件 §2.4 写明了不同之处。
> 事实基准提交：ConfFlow `main` = **`ce0dd996af057e63904370e0da294caf3ae3ca2e`**（Plan2 集成，PR #103 合并）。本版 L0 从该提交切出。早期评审记录中明确标注 `@d05927c` 或 `@afdf9df` 的行号是历史引用；本轮新增与修订的行号锚点在 V54 起按上述新 HEAD 核实。执行时一律先用卡片给出的符号锚点重新定位，行号只作核对。
> JobDesk 仓库：`moxuezhuchen/jobdesk-v2`，当前 `master` 基准 **`5c90732da78f096de7c77924eaaa64e105dbd221`**（根已核实；`main`/`master` 以该仓库实际分支名为准）。J 卡和 L2 的具体锚点仍在各自开工时核实。
>
> **执行节奏**：一次只把一个里程碑交给执行模型。L0 合并后开始 FIX-1A；FIX-1A 合并后，FIX-1R、FIX-1D、L1 各自开工前单独再做一次评审。

**目标（第四轮评审提出，本方案采纳为总体验收口径）**：

- 新增一个 ConfGen 组件或一种 producer capability 时，需要修改的中央文件 ≤ 1；
- 开发 coordination 时不需要读取 ring、torsion 的实现；开发 ConfGen 组件时不需要读取 producer 编译链；
- 完成一个组件的开发，所需的生产代码上下文约 5–10 个文件。

FIX-1A 的 A6（DummyComponent）和 L1 的 DummyCapability 测试，分别用来证明 ConfGen 和 producer 达到这一点。

**里程碑**（每个里程碑有自己的分支、基线和验收，出了问题可以明确归因）：

| 里程碑 | 内容 | 行为 | 分支 / 前置 |
|---|---|---|---|
| **L0** | 清理准备：搬迁验收工具、迁移仍在使用的 golden、归档 `docs/refactor/`、合并架构守卫；并行做元素表与 `v4_entry` 去重、测试间交叉引用整理 | 100% 不变 | `diet/l0`，从 `ce0dd996af057e63904370e0da294caf3ae3ca2e` 切出 |
| **FIX-1A** | B1 + A1–A6（A4 拆为 A4a–A4c、A4c-lazy、A4d）：ConfGen 组件化 | 100% 不变 | `fix/confgen-1a`，L0 合并后切出 |
| **FIX-1R** | F1 + R1–R7 + K0，K1/K2 视 K0 而定 | ring 行为替换 | `fix/confgen-1r`，1A 合并后切出 |
| **FIX-1D** | D1–D3：coordination 多起点 | TS1 REALIZED 数只增不减 | `fix/confgen-1d`，1A 合并后切出 |
| **L1** | producer 按 capability 拆分；Gaussian checkpoint 策略下沉到 `programs/gaussian/` | 100% 不变 | 1A 合并后；开工前展开卡片 |
| **L2** | 跨仓：退休旧 service 兼容链（`workflow_stats`、`.workflow_state`、`output_manifest` 等），与 JobDesk 成对 | 删除兼容读取 | L1 之后；开工前展开卡片 |
| **L3** | 收尾：TS1 原始日志外移、测试按行为重组、可选的目录搬迁 | 100% 不变 | 所有里程碑之后；开工前展开卡片 |

E、J、G 卡（recipe、contract、JobDesk、文档）在相关里程碑合并后单独排期。对称转子（原 K3）移到 FIX-2（§11）。

---

## 0. 问题清单与决策

### 0.0 v1 → v2 修订记录（对外部评审的处理）

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V1 | 6 元环正则形式全集不是 26 个，而是 **38 个**：2 C + 6 B + 6 TB + **12 E + 12 H**（每个原子都可以作为 envelope 的 flap，向上或向下；每根键都可以作为 half-chair 的平面对，向上或向下） | **接受**。v1 把 E、H 各写成 6 个是错的。Q1 的默认集合（2 C + 6 TB）不变 | §0.2 Q1、R1、R4 |
| V2 | R3 要求在所有种子中保持 rpdd C2 的"金字塔方向"，与 R2 判定 C2 是 sp² 自相矛盾，会把截取造成的假畸变锁成一个假手性 | **接受**。符号体积的手性锁只施加在**拓扑上的立体中心**；拓扑 sp² 中心只做平面性审计，不做手性锁。删除该测试。**v2.1 中"立体中心"改为局部定向锁，见 V16** | R3、R4 |
| V3 | Q 完全不约束时会退化到 Q→0：环几乎是平的，但 CP 方向仍"对准"，`cp_reached` 通过 | **接受**。增加审计 `Q ≥ Q_min`；Q_min 由 R7 基准标定（必须低于 CREST3 的 0.32）；perception 中 Q < Q_min 判为 `flat`（ambiguous）。**v2.1 中标定方式由 V17 取代** | R3、R4、R7 |
| V4 | rpdd 验收不应要求"published 恰好 2 个"：ConfGen 没有能量模型，几何上合法但优化后落回已知盆的种子应当允许 | **接受**，并保留一条不变量：每个 published 种子都必须通过平面性审计（这条足以排除 GPT 原型那种"酯扭 50° 仍算合格"的种子） | R4 |
| V5 | registry 在组件 `__init__.py` 里做 import 时自注册，又让 `model.py` 从 registry 派生 `AXIS_ORDER`，会形成 `model → registry → 组件 → model` 的循环依赖；全局可变单例也不利于测试 | **接受**。registry 改为显式构造的实例（`build_default_registry()`），由 engine 持有；`model.py` 不 import registry；数据类中的轴名合法性检查移到 kernel | A1、A2 |
| V6 | A6 要求 DummyComponent 出现在 engine 报告中，与 Q5"wire 只有三个顶层键"矛盾 | **接受**。分成两层：内部 `ComponentStateKey`（任意组件 id）和 v3 wire 适配器。适配器遇到未知组件 fail-closed。A6 只证明"加组件不改 kernel"，不证明"新组件自动成为 v3 公开能力" | A2、A6 |
| V7 | A4 只按 `wire_section` 描述组件输入不够：torsion 还拥有 `paths`、`strict_path_bond_check`；coordination 还向 typed graph 贡献配位边 | **接受**（已在 `planner.py:47-49、503-535` 核实）。descriptor 增加 `spec_keys`，组件接口增加 `normalize_spec` 与 `contribute_topology` | A1、A4 |
| V8 | K1 的 API 需要在后续组件枚举 target 之前就给出上界，所以 `moved_atoms(target)` 不够 | **接受**，改为 `potentially_moved_atoms(context)` | K1 |
| V9 | K1 的 clash 规则会跳过当前组件内部真正的 clash；应当"当前–当前、当前–固定永远检查，只推迟当前–后续可动" | **部分接受**。评审给出的规则会重新引入 P10：例如环上的苯基（由 ring 刚性跟随，属于"当前移动"）与环上 H 的 clash，苯基之后会被 torsion 转开，按评审的规则仍会被当场拒绝。**正确的判据是看距离会不会被后续自由度改变**：只有当原子对 (i, j) 分处某个后续 torsion 轴的两侧，或分属后续 coordination 的不同可动片段时才推迟；同一个后续刚性片段内的原子对、以及后续不会改变距离的原子对，当场检查。这个判据同时满足评审的目标（组件内部明显的坏结构当场拒绝）和 P10 的修复 | K1 |
| V10 | K3 对称转子不是简单的 bug 修复，会牵动枚举、StateKey、perception、parent lock、contract、JobDesk 和大量 golden；卡片还承认 golden 清单要执行时才知道，违反 science 卡纪律；而且应当复用已有的对称证明体系（`coordination/symmetry.py` 的 `search_automorphisms` + witness 校验），而不是另造一套 | **接受**，移到 FIX-2。当前 torsion 是正确的，只是有冗余 | §11 |
| V11 | K1/K2 只有在真实的组合 fixture 失败时才需要 | **接受**。新增诊断卡 K0：先用 rpdd 的 R×T 组合测是否真的发生误杀，再决定是否做 K1/K2 | K0 |
| V12 | D1 的 σ 是位点级置换（N17↔N19、O45↔O46），不等于完整分子的原子自同构；没有完整 witness 就置换坐标会生成错误结构 | **接受**（已核实仓库中有 `validate_full_witness`，`coordination/symmetry.py`，它明确规定"只有位点置换、没有完整原子 witness 支撑的一律 fail-closed"）。D1 只有在完整原子 witness 校验通过时才使用 σ-像，否则跳过，直接做 D2 | D1 |
| V13 | 方案太大，A→K→R→D→E→J→G 全绑在一个分支上 | **接受**，拆成 1A/1R/1D 三个里程碑 | 全文 |

**第二轮评审（v2 → v2.1）**

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V14 | A2 删除 `AXIS_ORDER` 违反"FIX-1A 行为 100% 不变"：它在 `model.py` 的 `__all__` 中，是公开导出 | **接受**（已核实 `model.py:39`）。常量移到无依赖的 `wire_v3_constants.py`，`model.py` 保留兼容导出并标记 deprecated；kernel 不再使用它 | A2、G13 |
| V15 | 自定义 registry 传不到 planner：`build_context` → `normalize_spec` 不接受 registry，DummyComponent 会在 engine 之前就被 planner 当作未知顶层键拒绝 | **接受**（新 HEAD 已核实 `model.py:459`）。**另外发现评审未提到的第二个调用方** `execution/transform_executor.py:492`（旧评审基于 `d05927c` 时为 L488）。A4 必须让同一个 registry 实例贯穿 normalize → typed graph → context → preflight → engine，并覆盖两个 executor | A4、A6 |
| V16 | R2"4 配位 sp³ = 立体中心"过宽：CH₂、C(CH₃)₂XY 都不是立体中心；本轮也不做通用 R/S | **接受**。R2 不做通用立体化学识别，改为 `LocalOrientationLock`：只保护环原子处由"环前邻、环后邻、外部取代基"构成的局部定向，前提是中心不是 sp²、输入符号体积明显非零。通用 CIP 交给以后的 stereochemistry 组件 | R2、R3 |
| V17 | Q_min 由 R7 标定并修改，而 R7 不是 `science` 卡；且用同一批 CREST 数据既标定又验证，容易过拟合 | **接受**。Q_min 重新定义为**相位可定义的数值门槛**（`phase_defined_q_min`），不承担"物理上合不合理"的含义；按无量纲量 Q / r̄（r̄ 为平均环键长）定义，在 R3 之前冻结；R7 只做验证，不合理则 R7 判失败，另开 `science` 卡修改 | §0.2 Q13、R3、R4、R7 |
| V18 | R1 不应让执行模型用理想几何"测出"E/H 的 CP 位置再填表，这会重现"错误几何 → 反推错误 CP → 测试自证"的旧模式 | **接受**。方案作者已用 CP 反解析式确认：E 位于 θ=54.7°/125.3°、φ=60°·k，H 位于 θ=50.8°/129.2°、φ=30°+60°·k（§0.3）。R1 以这张表为权威，几何只作为测试对象 | R1、§0.3 |
| V19 | D3 在 engine 报告中新增 `start_statistics`，会改变 TS1 报告字节，却标为 `logic` | **接受**，改为 `science` | D3 |
| V20 | `forms: [B]` 的含义没有写明 | **接受**。`"B"` 为族选择器，展开为全部 `B_k`；`"B_2"` 为精确选择器 | R4 |
| V21 | G1 不应写"输入应为已优化结构"；TS、中间体、约束优化结构中出现真实畸变是正常的 | **接受**，改为"推荐使用局部几何可信的输入；`distorted_input` 是诊断，不等于错误" | G1 |
| V22 | Q8 已可关闭：`moxuezhuchen/jobdesk-v2`，main=`3addb94…` | **接受**，但方案作者无权访问该私有仓库，未独立核实 | 文首、§0.2 Q8 |

**第三轮评审（v2.1 → v2.2）**

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V23 | A2 只拆了 StateKey：`WorkingRealization`、`GenerationTarget`、`OrbitIdentity`、`MolecularContext.input_state_key` 仍绑定三轴；而 v2.1 的做法是删掉这些公开类型的构造校验，这本身改变了公开行为 | **接受**（已核实 `model.py:191、254、505-511、528`）。旧类型一字不改；新增 kernel 记录类型（`ComponentStateKey`、`KernelGenerationTarget`、`KernelWorkingRealization`），kernel 内部使用新类型，在边界上经 wire 适配器转换。已核实的前提：内置 stage 只读 `parent.structure`；`WorkingRealization` 只在 engine 中构造；`EngineRun.leaves` 与 accounting 是唯一的下游消费方 | A2、A6 |
| V24 | A4 把 `registry` 改成必传参数，`normalize_spec(spec)`、`build_context(structure, spec)` 这类现有调用会全部报错，本身就是 API break | **接受**。公开入口的 `registry` 默认 `None`，经 `resolve_registry` 取缓存的不可变默认实例；入口之后的内部函数要求显式传入；实例一致性测试保留 | A1、A4 |
| V25 | `component.py` 必须是轻量描述模块，否则 import schema 就会连带加载整套求解器 | **接受**，并补充一处评审未提到的原因：`ring/__init__.py`、`torsion/__init__.py` 在顶层就 import 了 stage 和 realization，所以光把 `component.py` 写成懒加载不够，组件包的 `__init__.py` 也要改为 PEP 562 懒导出。已实测基线：schema 导入时不加载 confgen；`_confgen_section()` 目前会加载 `ring.realization` 等 | A5 |
| V26 | ring 还缺反向遍历的测试：`atoms` 反向书写也合法，CP 的 θ、φ 会发生规范变换 | **接受**。方案作者推出了循环移位与反向遍历下的 CP 变换公式并写进 R1；R1、R4 增加协变测试 | R1、R4 |
| V27 | D1 若给已有的 REALIZED 记录加 `start: "input"`，就违反了"已 REALIZED 逐字节不变" | **接受**。第一遍成功的记录保持旧格式，只有重试成功的才加 `retry_start` | D1 |

**第四轮评审（locality diet，v2.2 → v3）**：评审基于 `d05927c`，目标是"以后新增功能时，模型只需要读局部代码"。

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V28 | ConfGen 中央 dispatch 去组件名化是最值得做的一刀 | 与 FIX-1A 的范围一致，不改 FIX-1A 的设计；把评审提出的度量写进总体验收口径 | 文首 |
| V29 | 合并分散的架构守卫（`tests/v4/test_architecture_boundaries.py` 1912 行、`test_v42_debt.py`、`test_v46_debt.py`、`scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py`）为一个声明式 policy | **接受，并排在 FIX-1A 之前**：A6 要新增 kernel 纯度规则，先合并，A6 就能直接写进统一的 policy，否则会出现第四套扫描实现 | L0.4、A6 |
| V30 | 把 `docs/refactor/`（150 个文件，4.5 MB）移出主开发树 | **接受，并排在 FIX-1A 之前，但必须先做两件事**（评审未提到）：① FIX-1 的验收依赖 `docs/refactor/tools/` 和 `ACCEPTANCE.md`，要先搬走；② 现行测试 `tests/v4/test_intent_legacy_paths_to_v3.py:24` 在运行时从 `docs/refactor/paths_equivalence/` 加载代码和 golden，直接归档会让它失败，要先迁到 `tests/fixtures/` | L0.1–L0.3 |
| V31 | 压缩 `tests/fixtures/`，3 个日志占 4.58 MB | **更正事实后部分接受**：大文件实际只有一个，就是 4.58 MB 的 `tests/fixtures/confgen/coordination/ts1/source/si-rr-salanal2-r-end-spdd-ts1.log`。它是 TS1 golden 的原始来源，登记在 `MANIFEST.json` 中并带 sha256 校验，而 TS1 是 FIX-1 全程的行为基线。所以移到 L3（FIX-1D 合并之后），外移时 MANIFEST 改为记录 sha256 和外部位置 | L3 |
| V32 | `domain/elements.py` 与 `core/data.py` 各存一份完整元素表；当初不能互相依赖的原因已经消失 | **接受**（已核实：`core/__init__.py` 已是 PEP 562 懒加载；两表格式相同，都以 `""` 占位开头）。`domain.elements.ELEMENT_SYMBOLS` 作为唯一来源，`core.data.PERIODIC_SYMBOLS` 从它导出 | L0.5 |
| V33 | `v4_entry.formal_v4_runner()` 手写了一遍 `_build_run_inputs()` 的逻辑 | **接受**（已核实：`_build_run_inputs` 在生产代码中没有调用方，只被 `tests/test_v4_entry_helpers.py` 引用；两段逻辑等价） | L0.6 |
| V34 | `producer/intent.py`（2001 行）按 capability 拆 handler；`producer/authoring.py` 改为 registry 的投影；`producer/checkpoints.py` 中的 Gaussian 规则下沉到 `programs/gaussian/` | **接受**，作为 L1。和 ConfGen 无关，但 A5 会改 `producer/contract.py`，所以排在 FIX-1A 之后顺序执行 | L1 |
| V35 | 退休旧 service 兼容链（`workflow_adapter.py` 的 `_load_artifacts_v1/v2`、`_load_stats`，`confflow/contract.py` 发布的旧 schema） | **接受**，作为 L2。需要先确认 JobDesk 不再消费旧 capability artifact 字段，跨仓成对提交 | L2 |
| V36 | 测试按行为重组（`test_v42_*` … `test_v46_*` 共 57 个文件）；测试之间不应互相 import | **接受，但重组必须放在最后**：重命名测试文件会改变 pytest 节点 ID，而验收工具正是逐个比较节点 ID 来判断测试有没有被删，中途做会让每张卡的验收失效。测试间交叉 import 的整理不改节点 ID，可以放进 L0 | L0.7、L3 |
| V37 | 目录改为 `kernel/` + `components/` | **推迟到 L3（可选）**：模块路径是公开 import 路径，与 `AXIS_ORDER` 是同一类兼容问题；FIX-1A 在不搬目录的前提下就能达到去组件名化的目标。搬迁时旧路径保留兼容导出 | L3 |
| V38 | （方案作者自查）B1 原计划把 golden 检查点提交进 `docs/confgen-fix/baseline/`，这正是 `docs/refactor/baseline/` 膨胀到 3.5 MB 的同一种做法 | 检查点改为存放在仓库外，仓库内只提交 manifest（每个文件的路径与 sha256）和 `DIFF.md` | §2.1、§2.4、B1 |

**第五轮评审（v3 → v3.1）**：结论为"L0 可执行；FIX-1A 修改 3 个 blocker 后可冻结"。

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V39 | A2 要求 `run_kernel()` 能运行未知组件，却又规定 accounting 写报告时调用 `to_wire_key`，于是 DummyComponent 进入 accounting 时 `run_kernel()` 自己就会失败 | **接受**。kernel 层任何地方都不调用 `wire_v3`；新增 `KernelRun`（叶节点、target 记录、报告、证书均为通用表示）；accounting 写报告的函数改为接收 `serialize_key` 参数；`run()` = `project_v3(run_kernel(...))`。**另外修正一处评审未指出的同类问题**：v2.2 中根节点的 key 取自 `from_wire_key(context.input_state_key)`，这同样是 kernel 在调用 wire 层；现在改为由 v3 入口转换后作为 `initial_key` 传入 **v3.2 中 accounting 参数化与重算由 V48 取代，`run()` 的位置由 V47 澄清** | A2 |
| V40 | A4 的 `normalize_spec(..., n_atoms)` 与"`normalize_spec(spec)` 旧调用必须可用"矛盾；现有 `normalize_spec(raw)` 本来就与结构无关 | **接受**（已核实 `planner.py:393`）。`normalize_spec` 去掉原子数；新增可选的 `validate_context(resolved, context)`，只承接现在本来就在 context 阶段执行的检查 | A4b |
| V41 | `InheritedLock` 是未定义的抽象；现在只有 torsion 有 `InheritedTorsionLock`，若再造各组件的锁类型和联合类型，kernel 又会认识组件 | **接受**（已核实 `engine.py:116、187`）。继承状态改为组件持有的不透明载荷：`serialize_inherited_state` / `verify_inherited_state`，kernel 只保存 `ComponentInheritedState(component_id, payload)` | A4d |
| V42 | L0.3 要求摘要不超过 1 页，又要求写入上百个文件的 sha256，自相矛盾；另外删除工作树文件不会缩小 git 历史 | **接受**。摘要与机器可读的 manifest（`docs/archive_manifests/architecture_diet_1.json`）分开；卡中写明本卡的目的是局部性，不改写历史 | L0.3 |
| V43 | G13 的检查应基于 AST，不应扫描文本，否则 docstring 和注释会造成误报，执行模型会为了通过扫描去改注释 | **接受**，写进 G13 与 L0.4b；policy 对每条规则附一个"只在注释中出现"的正例，验证不误报 | G13、L0.4b |
| V44 | L0.4 与 A4 单卡过大 | **接受**。L0.4 拆为 L0.4a（只出清单，确认后才继续）、L0.4b（静态规则）、L0.4c（运行时 import 隔离）、L0.4d（删除旧守卫）；A4 拆为 A4a（registry 贯穿）、A4b（normalization）、A4c（拓扑贡献）、A4d（继承状态） | L0.4、A4 |
| V45 | A1 改用 registry 排序后，`ConfgenEngine(stages=[...])` 显式路径对未知 axis、重复 axis 的处理必须与 B1 一致 | **接受**（已核实：仅 `tests/v4/test_confgen_v3_core.py` 就有约 50 处 `ConfgenEngine(` 调用，多数传入伪造的 stage） | A1 |
| V46 | 不应要求"§0.2 全部确认后才能开工"；既然一次只执行一个里程碑，确认也应按里程碑分开 | **接受**，§0.2 新增"确认时机"表 | 文首、§0.2 |

**第六轮评审（v3.1 → v3.2）**：结论为"L0 可执行；FIX-1A 修改以下各项后可冻结"。

| # | 评审意见 | 处理 | 修改位置 |
|---|---|---|---|
| V47 | A2 一边禁止 `engine.py` 依赖 `wire_v3`，一边要求 `run()` 调用它，而 `run()` 就在 `engine.py` 中 | **接受**。`ConfgenEngine.run` 定义为唯一的 v3 兼容入口，只有它的方法体可以懒加载 `wire_v3`；policy 按 AST 作用域检查。不为形式纯度拆 engine | A2、G13 |
| V48 | accounting 不必参数化、也不必重算：`complete_key` 本来就是通用 Mapping，证书 digest 不依赖它，v3 专用的 `stamp_production_results` 只在 executor 发布时调用 | **接受**（历史评审锚点 `@d05927c`：`accounting.py:82、561-579、582-641`，当时 `confgen_executor.py:293`；新 HEAD executor 因此前删行现为 L283）。`accounting.py` 移出 A2 白名单；`project_v3` 只做数据投影；删除对应的风险登记 | A2、§12 |
| V49 | `run_kernel(initial_key=None)` 默认为空 key，链式运行忘传时继承状态会被静默丢弃 | **接受**，`initial_key` 改为必传 | A2、A6 |
| V50 | A4a 声称只铺通道，却测试探针组件的 `normalize_spec` 被调用，而分派要到 A4b 才实现，A4a 不可能通过自己的测试 | **接受**。A4a 只测实例一致性；钩子调用的断言移到 A4b；A4a 中 `build_typed_graph` 只传入 registry、不调用钩子 | A4a、A4b |
| V51 | `normalize_spec(section: Mapping)` 不成立：一个组件可能拥有多个键，值也不一定是 Mapping | **接受**。输入为整份原始 spec，输出为 `{自己拥有的键: 值}`，planner 合并并检查越界 | A4b |
| V52 | `contribute_topology(resolved, graph_builder)` 中的 `graph_builder` 在现有代码里不存在 | **接受**。定义一个与化学无关的 `TopologyBuildContext`，字段以 `build_typed_graph` 现有的局部变量为准 | A4c |
| V53 | `verify_inherited_state` 缺少继承的状态值；若组件自己去 `context.input_state_key` 里找，又会耦合回 v3 | **接受**。kernel 从 `ComponentStateKey` 中取出 `state_value` 一并传入；组件不得读取 `context.input_state_key` | A4d |

**本轮修订（v3.2 → v3.3，基于 Plan2 合并后的新 HEAD）**：下列锚点均已在 `ce0dd996af057e63904370e0da294caf3ae3ca2e` 定点核实；import 基线以独立子进程实测。

| # | 本轮确认项 | 处理 | 新 HEAD 已核实的锚点 | 修改位置 |
|---|---|---|---|---|
| V54 | ConfFlow 与 JobDesk 基线均须同步到已核实的新提交；执行行号不能把历史评审引用误写成当前核实 | L0 从完整 SHA `ce0dd996af057e63904370e0da294caf3ae3ca2e` 切出；JobDesk `master` 基准为根已核实的 `5c90732da78f096de7c77924eaaa64e105dbd221`。早期带 `@d05927c` / `@afdf9df` 的引用保留其历史语境。本轮只将能定点核实的锚点标为新 HEAD | `model.py:133-179`；`engine.py:956-961`；`planner.py:393,963` | 文首、L0.0、Q8、受影响卡片 |
| V55 | FIX-1A 的 kernel/wire 边界、通用继承错误类型和旧入口兼容需要精确定义 | `InheritedScopeError` 归 `kernel_records` 并由 engine 正常导出；`as_kernel_target` 只归 `kernel_records`；`ConfgenEngine.run(context, should_cancel=None)` 保持现有签名。G13 对 `model.ConfgenStateKey` 仅类体豁免，并补充对三个组件状态属性的 AST 限制 | `model.py:133-179`；`engine.py:111,116,187,390,956-961`；`confgen/__init__.py:28-40` | A2、G13 |
| V56 | 继承 API 的兼容导出须保持惰性且对象一致，不能把通用错误类型也延迟加载 | engine 与 `confgen/__init__.py` 仅对 `InheritedTorsionLock`、`inherited_torsion_locks`、`check_inherited_torsion_locks` 用模块级 `__getattr__` 懒导出；组件包懒导出拆为 A4c-lazy。独立子进程在新 HEAD `import confflow.science.confgen` 后的组件模块集合实测为 `[]` | `engine.py:77-87`；`confgen/__init__.py:13,28-40`；A4d 基线导入实测 `[]` | A4c-lazy、A4d、A5 |
| V57 | Plan2 合并后元素表与验收工具布局已变化 | L0.1 同时搬迁 `tools` 与 `tools-acc` 到 `tools/refactor`、`tools/refactor-acc` 并同步捕获清单、runner/checker、工具测试与路径说明；L0.5 实际将 `science/data.py` 的表转为 `domain/elements.py` 的同一对象，保留 `core/data.py` 薄转发 | `capture_provenance.py:31-39`；`run_sharded.py:62-68`；`golden_check.py:123-125`；`test_capture_pipeline.py:20-22,85-90`；`science/data.py:136`；`core/data.py:12-20`；`domain/elements.py:25` | L0.1、L0.5 |
| V58 | 清零类源码规则和不可达分支结论需要证据匹配 | AST policy 承担源码无违规项的验收；L0.3 另保留删除前对旧路径实际引用的明确 grep。`reachability.py` 提供模块 import 可达性参考，不能证明函数调用或函数内部条件分支不可达；删除分支必须有真实调用/运行证据。`ConfgenEngine.run` 的 `should_cancel` 原签名不变 | `engine.py:956-961`；L0.3 路径引用清查步骤 | L0.3、L0.4b、L0.7、A2、A3、A6 |
| V59 | `docs/refactor/` 归档不打 tag，使用可校验的提交、manifest 与仓库外 bundle 记录 | 采用 L0.3 的 `archive_parent` 完整 SHA、文件 manifest、仓库外 bundle 路径与 SHA-256；Q14 已确认，不需 L0 开工时重复确认 | `docs/ARCHITECTURE.md:95,122`（已读；现有架构/迁移史引用） | §0.2 Q14、L0.3、§12 |

### 0.1 已发现问题

"来源"列标明谁发现、是否已在代码上核实。

| # | 问题 | 证据（P1–P23 的行号引用保留其历史评审语境；当前执行锚点以 V54 起的新 HEAD 定点核实为准） | 来源 / 核实 | 处理 |
|---|---|---|---|---|
| P1 | 6 元环 `boat_6` 环角为 60°/128°，Q=1.09（环己烷约 0.63） | `ring/templates.py:209-231`；实测见 §0.3 | GPT review；本方案重测 ✅ | R4、R6 |
| P2 | `twist_boat_6` 环角 46–60°，有 **180° 的环内扭转角**，θ=41°，不在赤道上 | `ring/templates.py:234-255` | 本方案新发现 ✅ | R4、R6 |
| P3 | `twist_5` 与 `envelope_5` 伪旋转相位相同（φ=180°），实际是更大振幅的 envelope，不是 twist | `ring/templates.py:130-179` | 本方案新发现 ✅ | R4、R6 |
| P4 | 所有模板环键统一 1.54 Å；realization 用 Kabsch 刚性替换整个环，然后要求环键漂移 ≤ 0.08 Å，因此含 C–O / C=O 的环必然失败（rpdd：0/4） | `ring/templates.py:53`；`ring/realization.py:356-399`；`RingTolerances.ring_bond_atol=0.08`（L163） | GPT review；本方案核实 ✅ | R3 |
| P5 | perception 用绝对扭转角序列匹配，锚点固定，不做循环匹配。结果是：同一个 envelope，只要 flap 不在锚点原子上，就被判成 `planar_5/ambiguous`；boat 换一个取向，就被判成"像 chair 但 ambiguous"；5 元环只枚举了 10 个 envelope 中的 1 个，6 元环只枚举了 3 个 boat 取向中的 1 个，**而且具体是哪一个，取决于 `atoms` 列表的顺序** | `ring/perception.py:75-145`；实测见 §0.3 | 本方案新发现 ✅ | R4 |
| P6 | 没有共轭单元平面性审计：GPT 原型的 12 个相位点中，有 6 个酯键扭了 48–59°，仍被判为"合格" | GPT 原型输出 | 本方案新发现 ✅ | R3 |
| P7 | 没有环角漂移审计 | `ring/realization.py:384-445` | GPT review ✅ | R3 |
| P8 | 没有输入质量审计：截取或未优化结构的畸变会原样传给所有种子 | — | 本方案新发现 ✅ | R2、R5 |
| P9 | 固定 θ=90° 的相位网格会漏掉椅式；对可行区域按连通分量聚类，会把环己烷 6 个 TB 合并成 1 个（Sachse 柔性族） | 设计层面 | 本方案新发现 | R1、R4 |
| P10 | ring/torsion 阶段对整个结构做 clash 审计，而取代基此时仍刚性跟随、后续 torsion 尚未枚举，可能误杀 | `ring/realization.py:427-438`；`torsion/stage.py:366-391` | 本方案新发现；**代码层面成立，尚未在真实体系上复现** | K0 → K1/K2 |
| P11 | torsion 不识别对称转子（苯基 0°/180° 等），产生冗余 | `torsion/paths.py` 只检查周期重复值（L251） | 本方案新发现 ✅ | FIX-2 |
| P12 | coordination 每个 target 只有一个起点，TS1 12 个 target 中只有 3 个 REALIZED | `coordination/realization.py:316-400、562-660` | GPT review；本方案核实 ✅ | D1–D3 |
| P13 | `AXIS_ORDER` 写死 | `model.py:65` | ✅ | A2 |
| P14 | `ConfgenStateKey` 字段固定为 coordination/rings/torsions | `model.py:133-179` | ✅ | A2 |
| P15 | engine 按轴名分派；engine 中轴名字面量 29 处 | `engine.py:455-480、631-700、1361、1664-1671、1877、2575-2580` | ✅ | A1–A3 |
| P16 | planner 中轴名字面量 14 处；`paths` 等 torsion 输入由 planner 直接处理 | `planner.py:47-49、393、503-535、775、806` | ✅ | A4 |
| P17 | executor 拼装和恢复 inherited scope 时知道各轴细节 | `execution/confgen_executor.py:414-549`（新 HEAD；旧评审引用 424-559） | ✅ | A4 |
| P18 | coordination 组件自己检查 rings/torsions 是否存在 | `coordination/stage.py:838` | ✅ | A5 |
| P19 | schema 和 contract 直接 import 各组件内部模块 | `workflow/v4/confgen_schema.py:33,68,75,396`；`producer/contract.py:331-344` | ✅ | A5 |
| P20 | JobDesk：GJF/INP 输入不能驱动 atom picker | GPT review | ❌ 未核实 | J1 |
| P21 | JobDesk：confgen 不支持 freeze，但 GUI 没有置灰 | 后端 ✅ / GUI ❌ | GPT review | E2、J2 |
| P22 | 能力边界没有成文 | — | GPT review | G1 |
| P23 | 单体构象搜索缺少"预优化 → ConfGen"的标准流程；仓库中没有 xTB 适配器 | `confflow/programs/` 只有 gaussian、orca | ✅ | E1 |
| P24 | `docs/refactor/` 150 个文件 / 4.5 MB（其中 `baseline/` 3.5 MB、`paths_equivalence/` 852 KB）留在主开发树中 | `du`@d05927c | 第四轮评审；本方案核实 ✅ | L0.1–L0.3 |
| P25 | 现行测试在运行时从 `docs/` 下加载代码和 golden | `tests/v4/test_intent_legacy_paths_to_v3.py:24-38` | 本方案新发现 ✅ | L0.2 |
| P26 | 架构守卫分散在多个测试和脚本中；`tests/v4/test_v46_production_gates.py:945-951` 以模块方式和子进程方式各调用一次 `scripts/v4_arch_scan.py` | 见左 | 第四轮评审；本方案核实 ✅ | L0.4 |
| P27 | 元素符号表有两份 | 新 HEAD：`science/data.py:136`；`domain/elements.py:25`；`core/data.py:12-20` 为薄转发；`tests/v4/test_elements_drift.py` | 新 HEAD 定点核实 ✅ | L0.5 |
| P28 | `formal_v4_runner` 与 `_build_run_inputs` 重复；后者在生产代码中没有调用方 | `application/v4_entry.py:181-215、217-300` | 第四轮评审 ✅ | L0.6 |
| P29 | 测试模块之间互相 import 辅助函数（例如 `test_confgen_paths_audit.py:31`、`test_confgen_paths_phase0.py:42` 从 `test_repair_executors` 导入；`test_audit_redteam_r2.py:40` 从 `test_audit_regressions_r2` 导入） | 见左 | 第四轮评审；本方案核实 ✅ | L0.7 |
| P30 | `producer/intent.py` 2001 行，同时承担 normalization、card 展开、recipe、ID 分配、各类 wiring、资源、调度、自动绑定、checkpoint、编译；`producer/authoring.py` 另有一套 step/binding 知识；`producer/checkpoints.py` 含 Gaussian 规则 | 第四轮评审 | ❌ 未逐行核实 | L1 |
| P31 | service 仍保留旧 artifact 读取链与旧 schema | `workflow_adapter.py`、`confflow/contract.py` | 第四轮评审；❌ 未逐行核实 | L2 |
| P32 | 测试按历史阶段命名（`test_v42_*` … `test_v46_*`） | `tests/v4/` | 第四轮评审 ✅ | L3 |

### 0.2 待用户确认的决定

**确认时机（V46）**：一次只执行一个里程碑，所以只需在该里程碑开工前确认属于它的几项。尚未提供的数据（例如 Q9 的 CREST 参考集）不阻塞其他里程碑。

| 里程碑 | 开工前需要确认 |
|---|---|
| L0 | Q12、Q15（Q14 已确认，不需重复确认） |
| FIX-1A | Q5；以及 A2 的 kernel/v3 投影边界、A4b 与 A4d 的接口设计（v3.1 新定） |
| FIX-1R | Q1、Q2、Q3、Q4、Q6、Q13；Q9 只在 R7 之前需要 |
| FIX-1D | Q10 |
| J 卡、L2 | Q8 |


| # | 问题 | 推荐默认值 | 影响的卡 |
|---|---|---|---|
| Q1 | 6 元环默认枚举哪些正则形式？全集为 2 C + 6 B + 6 TB + 12 E + 12 H = 38 | **2 C + 6 TB**。B、E、H 只在显式声明，或由 R4 的"受约束形式"规则加入时才枚举 | R4 |
| Q2 | 5 元环默认枚举哪些？全集为 10 E + 10 T | **全部 20 个** | R4 |
| Q3 | 旧模板名怎么处理 | 保留为别名：`chair_A_6/chair_B_6` → 两个 C；`boat_6` → 锚点处的 B；`twist_boat_6` → 最近的 TB；`envelope_5` → 锚点处的 E；`planar_*`、`pucker_*_4` 保留；**`twist_5` → 锚点处的 E 并给出 deprecated 警告**（它实际一直是 envelope）。contract 保留 `ring_templates_by_size`（只列别名），新增 `ring_forms_by_size` | R4 |
| Q4 | ring 的 StateKey 载荷改为 `{form, index, anchor, direction}`；所有带环的 engine 报告 golden 都会变 | 同意 | R4 |
| Q5 | StateKey 的 wire 格式本轮是否改为 `components: {...}` | **本轮不改**，由 v3 wire 适配器负责转换（V6） | A2 |
| Q6 | `distorted_input` 命中时报告还是拒绝 | **只报告** | R5 |
| Q8 | JobDesk 仓库的位置、基准提交、本轮是否允许修改 | 仓库为 `moxuezhuchen/jobdesk-v2`，当前 `master`=`5c90732da78f096de7c77924eaaa64e105dbd221`（根已核实；V22 的 `3addb94…` 是历史基准）。是否允许本轮修改仍需用户确认；J 卡开工前重新确认基准是否前进 | J1–J3 |
| Q14 | `docs/refactor/` 如何移出主开发树 | **已确认：不打 tag**。删除前记录父提交完整 SHA；生成覆盖父提交 `docs/refactor/` 全部文件的 manifest，并将父提交历史打包到仓库外 bundle，记录路径、SHA-256 与 `git bundle verify` 结果；随后从工作树删除目录。历史对象不改写 | L0.3（已确认） |
| Q15 | 架构守卫合并后的 policy 放在哪里 | `tools/architecture_policy.py`，不属于 `confflow` 包，不进入发布 wheel；`scripts/` 中的两个脚本改为调用它的薄 CLI | L0.4 |
| Q13 | `phase_defined_q_min` 的取值 | **Q / r̄ ≥ 0.05**，4/5/6 元环统一使用这个无量纲门槛（r̄ 约 1.45 Å 时对应 Q≈0.07 Å，约为坐标噪声 0.005 Å 的 14 倍）。rpdd 中最小的参考值 CREST3 为 0.32 / 1.45 ≈ 0.22，远高于门槛。这是数值定义域门槛，不是物理判据 | R3、R4、R7 |
| Q9 | 基准体系的参考构象集（CREST） | 需要用户提供：甲基环己烷、环己烯、THF、脯氨酸衍生物、一个吡喃糖。rpdd 已有 | R7 |
| Q10 | coordination 多起点的预算 | 每个 target 最多 `1 + |σ-像| + 3` 个起点，按固定顺序尝试；本轮不接 Molassembler | D1–D2 |
| Q12 | 执行模型、分支名、推送授权 | 沿用原 `docs/refactor/PLAN.md` §2.8（L0.1 后在 `docs/process/RULES.md`）；分支见文首表格 | 全部 |

（v1 的 Q7、Q11 随 K3 移出和 K1 改写而取消。）

### 0.3 编写方案时的实测

模板几何（`ring/templates.py`@afdf9df）：

```text
              环键        环角                                 CP
boat_6        1.540      60.0 127.8 127.8  60.0 127.8 127.8   Q=1.09 θ=90  φ=180
twist_boat_6  1.540      60.0  60.0  45.9  60.0  60.0  45.9   Q=1.15 θ=41  φ=30   扭转角含 180°
envelope_5    1.540     108.0 102.5 108.0 108.0 102.5         Q=0.33       φ=180
twist_5       1.540     102.4  97.8 106.2 106.2  97.8         Q=0.53       φ=180   ← 与 envelope 同相位
chair_*_6     1.540     109.5 ×6                              Q=0.63 θ=0/180
```

perception 对锚点移位的响应（同一几何，循环移动锚点）：

```text
envelope_5   移位 0: envelope_5/reported    移位 1–4: planar_5/ambiguous (24°)
boat_6       移位 0,3: boat_6/reported     移位 1,2,4,5: chair_A_6/ambiguous (79°)
twist_boat_6 移位 0,3: 认得               移位 1,2,4,5: chair/ambiguous (92°)
```

6 元环正则形式的 CP 位置（由 Cremer–Pople 反解析式 z_j = √(2/6)·q₂·cos(φ + 4πj/6) + √(1/6)·q₃·(−1)^j 得到，平面投影取正六边形，Q=0.6 Å；"零扭转键"指扭转角 |τ| < 3° 的环键，原子按 0–5 编号）：

```text
族   个数  θ              φ              零扭转键数  对称性来源
C    2     0° / 180°      —              0
B    6     90°            60°·k          2（相对）   镜面过两个原子
TB   6     90°            30° + 60°·k    0           C2 轴过两个原子
E    12    54.7° / 125.3° 60°·k          2（相邻）   镜面过一个原子（flap）；5 个原子共面
H    12    50.8° / 129.2° 30° + 60°·k    1           C2 轴过一根键的中点
```

例：θ=54.7°、φ=0° 的 E，零扭转键为 2-3、3-4；θ=50.8°、φ=30° 的 H，零扭转键为 3-4；θ=90°、φ=0° 的 B，零扭转键为 1-2、4-5。

rpdd（扁桃酸–乙醇酸不对称交酯；用户提供的输入是从 TS 中截取的未优化结构）：

- 截取片段中 C2 键角和 339°，C2–O3 1.32 Å，C2–O4 1.41 Å，即被扭成四面体；C6 平面，C6=O7 1.21 Å。
- 把 C2 修复为平面酯后，对 CP 球面做约束扫描（θ、φ 均按 15° 步长）：两个酯同时保持平面的区域只有 φ≈0° 和 φ≈180° 两个 boat 盆。
- CREST（3 个构象）：CREST1 φ=179°，Q=0.53，苯基准直立，0.00 kcal/mol；CREST2 φ=0°，Q=0.51，苯基准平伏，+2.00；CREST3 φ=176°，**Q=0.32**，苯基准直立，转角与 CREST1 相差约 78°，+3.13。即 **2 个环盆，3 个构象**，第 3 个来自苯基转子。

---

## 1. 总览

### 1.1 依赖图

```
L0:      L0.0 ─> L0.1 ─> L0.2 ─> L0.3 ─> L0.4a ─(确认)─> L0.4b ─> L0.4c ─> L0.4d   (行为不变)
                 ‖ 并行: L0.5、L0.6、L0.7
                                     │  合并到 main
FIX-1A:  B1 ─> A1 ─> A2 ─> A3 ─> A4a ─> A4b ─> A4c ─> A4c-lazy ─> A4d ─> A5 ─> A6 ──┐   (合并到 main，形成新基线)
                                                   │
FIX-1R:  F1 ─> R1 ─> R2 ─> R3 ─> R4 ─> R5 ─> R6 ─> R7
                            └──> K0 ──(若出现误杀)──> K1 ─> K2 ─> 回到 R4
FIX-1D:  D1 ─> D2 ─> D3
L1:      producer capability handler、Gaussian checkpoint 策略下沉
                                                   │
                                                   v
L2:      跨仓退休旧 service 兼容链（与 JobDesk 成对）
L3:      TS1 原始日志外移、测试按行为重组、可选目录搬迁
另排期:  E1、E2 ─> J1 ─> J2 ─> J3 ─> G1
```

- FIX-1R、FIX-1D、L1 都从 FIX-1A 合并后的 main 切出，三者互不依赖，但一次只交给执行模型一个。
- K0 在 R3 完成后运行，结果决定是否插入 K1/K2；插入时 K1/K2 必须在 R4 之前完成，因为 R4 会重建 ring golden。
- L3 中的测试重组必须在所有其他里程碑之后（V36）。

### 1.2 卡片类型

沿用 `docs/process/RULES.md`（L0.1 从 `docs/refactor/PLAN.md` §1.4 搬来）中的类型（`delete`、`move`、`logic`、`test-only`、`baseline`、`doc`），新增：

| 类型 | 允许的改动 |
|---|---|
| `science` | 科学行为改变。卡片必须在**开工前**列出：预期改变的 golden 文件清单、每个文件预期的差异方向，以及必须不变的 golden 清单。清单需要先运行才能确定的，拆出一张前置的 `baseline` 卡专门生成清单，交验收方确认后才能开始 `science` 卡。验收方逐项比对，任何未声明的 golden 差异都退回。 |

---

## 2. 执行环境与通用规则

### 2.1 仓库与工作树

```bash
# L0
git -C /opt/ConfFlow worktree add -b diet/l0 /opt/cf-worktrees/diet-l0 ce0dd996af057e63904370e0da294caf3ae3ca2e
# FIX-1A（L0 合并后）
git -C /opt/ConfFlow worktree add -b fix/confgen-1a /opt/cf-worktrees/fix-1a <L0 合并后的 main>
export CF=/opt/cf-worktrees/fix-1a           # 按当前里程碑替换
export FIX=$CF/docs/confgen-fix
export TOOLS=$CF/tools/refactor              # L0.1 之前为 $CF/docs/refactor/tools
export CKPT=/opt/cf-worktrees/checkpoints    # 检查点放在仓库外（V38）
export BASE=$CKPT/<里程碑>/baseline
```

1R、1D、L1 的工作树在 1A 合并后从新的 main 创建。验收工具原样复用 `tools/refactor/`；Plan2 分片 runner 与权重工具位于并列目录 `tools/refactor-acc/`。

**检查点存放规则（V38）**：golden、engine 报告、测试清单等检查点**不提交进仓库**，存放在 `$CKPT/<里程碑>/<卡片>/`。仓库内只提交 `docs/confgen-fix/checkpoints/<卡片>/MANIFEST.json`（每个检查点文件的相对路径与 sha256）和 `DIFF.md`（science 卡）。验收方用 manifest 核对 `$CKPT` 中的文件没有被改动。

### 2.2 通用禁止事项

沿用 `docs/process/RULES.md`（L0.1 从 `docs/refactor/PLAN.md` §2.5 搬来）的 G1–G10，但 **G6 改为 G6'**，**G5 改为 G5'**：

- G5' 不得修改 `$CKPT` 下已有的检查点文件；每张卡只能新增自己的检查点目录。

- G6' `logic`、`move`、`delete`、`test-only` 卡不得改变科学行为（golden 必须不变）。`science` 卡只能改变开工前声明的 golden。发现任何未声明的差异，停止并报告，不得自行判定为"预期变化"。

新增：

- G11 不得修改 `tests/fixtures/confgen/` 中已有的 fixture；新 fixture 只能由 F1 及卡片声明的 `test-only` 步骤添加。
- G12 不得通过放宽容差让 golden 或测试通过。容差的改动必须是卡片步骤中写明的。
- G13 kernel 文件（`engine.py`、`planner.py`、`accounting.py`、`perception.py`、`model.py`、`kernel_records.py`、`execution/confgen_executor.py`、`execution/transform_executor.py` 中 confgen 相关部分）不得出现轴名字面量 `"coordination"`、`"rings"`、`"torsions"`，也不得 import 任何组件模块。仅允许 `wire_v3.py`、`wire_v3_constants.py` 和 `model.ConfgenStateKey` **类体**保留这些 v3 兼容名字；`model.py` 的其余模块顶层、函数和类体均受规则约束。`ConfgenEngine.run` 是唯一允许调用 `wire_v3` 的方法（V47）。除 `ConfgenStateKey` 类体、`ConfgenEngine.run` 方法体及 `wire_v3.py` 外，kernel 任何代码都不得访问 `.coordination`、`.rings`、`.torsions` 属性（AST 检查 `Load`、`Store` 与 `Del` 上下文）。A4d 为旧继承 API 增加唯一的惰性 import 例外：仅 `engine.py` 与 `science/confgen/__init__.py` 的模块级 `__getattr__` 可按需导入 `torsion.inherited`，且只为 `InheritedTorsionLock`、`inherited_torsion_locks`、`check_inherited_torsion_locks` 三个名字提供兼容对象。`model.py` 中 `AXIS_ORDER` 的兼容导出只能是从 `wire_v3_constants.py` 重新导出。A6 将这些约束写进 L0.4 建立的 policy；检查基于 AST（字符串常量、属性访问、名称、import、字典键、比较及作用域），不检查 docstring 和注释（V43）。
- G14 不得新增 import 时自注册（模块被 import 就修改全局状态）的代码。

### 2.3 每张卡的通用验收

沿用 `docs/process/RULES.md` 中的"标准验收"（原 `docs/refactor/PLAN.md` §2.6），工具路径改为 `$TOOLS`。golden 比对基准：L0 各卡用 L0.0 的检查点；FIX-1A 各卡用 B1 检查点；FIX-1R/1D 各卡用"上一张已验收卡"的检查点加上本卡声明的差异。

### 2.4 与 `docs/process/ACCEPTANCE.md`（原 `docs/refactor/ACCEPTANCE.md`）的不同

1. `science` 卡的 golden 比对：验收方运行 `golden_check.py` 得到差异集合 Δ，要求 Δ ⊆ 声明集合，且声明为"必须不变"的文件不在 Δ 中。每一项声明的差异，验收方都要人工打开报告核对差异方向。
2. `science` 卡交付时必须附：`$CKPT/<里程碑>/<CARD>/` 下的新 golden 文件；仓库内 `docs/confgen-fix/checkpoints/<CARD>/MANIFEST.json` 与 `DIFF.md`（逐项说明每个 golden 文件为什么变、怎么变）。
3. LOG 写在 `$FIX/LOG.md`。

### 2.5 提交信息

沿用 `docs/process/RULES.md` 中的提交格式（原 `docs/refactor/PLAN.md` §2.7）。`science` 卡另加 `Golden-Changed:` 与 `Golden-Unchanged-Asserted:` 两段。

---

# L0：清理准备（行为 100% 不变）

**本里程碑所有卡的 golden（TS1、engine 报告、contract、boundary）都必须与 L0.0 逐字节相同。** 测试清单只允许出现卡片声明的增删。

### L0.0 — 冻结 Plan2 集成基线

- 类型：`baseline` ／ 前置：无
- 目标：从 `ce0dd996af057e63904370e0da294caf3ae3ca2e` 开始，用现有的 `docs/refactor/tools/` 与 Plan2 的 `docs/refactor/tools-acc/run_sharded.py`（此时尚未搬迁），在 `$CKPT/l0/baseline/` 生成与原 B0.1 相同的产物：TS1 三种 backend、全部 engine 报告、contract/boundary 摘要、测试 collect 清单与结果。按 Plan2 捕获协议运行；权重从文档树复制到仓库外的本次运行目录，使用 runner 提供的 noeditable `PYTHONPATH`。每项运行两次，逐字节相同才写入。
- 允许修改：`docs/confgen-fix/checkpoints/L0.0/MANIFEST.json`、`docs/confgen-fix/LOG.md`
- 验收：产物齐全；两次运行逐字节相同；测试失败集合写入 LOG（预期为空）。

### L0.1 — 搬迁验收工具与通用规则

- 类型：`move` ／ 前置：L0.0
- 背景（V30、V57）：FIX-1 的验收依赖 `docs/refactor/tools/`；Plan2 集成后，分片 runner、权重、`noeditable/` 和它们自己的测试位于 `docs/refactor/tools-acc/`，捕获协议也引用这两棵工具树。本方案还引用 `docs/refactor/PLAN.md` 中的 G1–G10、卡片类型、标准验收和提交格式，以及 `docs/refactor/ACCEPTANCE.md`。归档之前必须把工具与通用规则搬进现行位置。
- 步骤：
  1. `git mv docs/refactor/tools tools/refactor`；`git mv docs/refactor/tools-acc tools/refactor-acc`。两目录保持并列；runner、`weights.json`、`noeditable/sitecustomize.py` 与工具自身测试一并迁移。
  2. 同步搬迁后的布局关联：`capture_provenance.py` 的 `TOOL_RELPATHS` 改为 `tools/refactor/...` 与 `tools/refactor-acc/...`；`run_sharded.py` 的 `TOOLS.parent / "tools"` 改为 `TOOLS.parent / "refactor"`；`golden_check.py` 的 noeditable 路径改为 `TOOLS.parent / "refactor-acc" / "noeditable"`。更新 `test_capture_pipeline.py` 的 `TOOLS_ACC.parent / "tools"`、仓库根目录 `parents[3]`→`parents[2]`、fixture 与路径清单；更新 `test_reachability_scripts.py` 的仓库根目录 `parents[3]`→`parents[2]`、被测脚本和调用示例，保持两套工具的捕获布局与测试语义不变。
  3. 新增 `tools/refactor/README.md`：把原 `docs/refactor/PLAN.md` B0.1 第 2–9 步中各工具的用法说明**逐字搬运**过来，并补入 Plan2 runner 的用法：权重先复制到仓库外的本次运行目录；`run_sharded.py` 自动注入 `PYTHONPATH=<tools/refactor-acc>/noeditable:<CF 根目录>`；checker 与 runner 的调用位置使用新并列目录。
  4. `git mv docs/refactor/ACCEPTANCE.md docs/process/ACCEPTANCE.md`；文中的工具路径改为 `tools/refactor/`，并明确 runner 与 weights 位于并列的 `tools/refactor-acc/`。
  5. 新增 `docs/process/RULES.md`：把原 `docs/refactor/PLAN.md` 的 §1.4（卡片类型）、§2.5（G1–G10）、§2.6（标准验收）、§2.7（提交格式）、§2.8（执行模型的调用方式）**逐字搬运**过来，路径改为新位置。
  6. 更新引用：`docs/DEVELOPMENT.md:60、67、96`，`docs/TESTING.md:61-62`，工具测试与 Plan2 验收说明 `docs/refactor/plan2/CAPTURE_PIPELINE.md`、`docs/refactor/plan2/ACCEPTANCE_FAST.md` 中的 runner、weights、`PYTHONPATH` 和命令路径，以及 `git grep -n "docs/refactor/tools\|docs/refactor/tools-acc\|docs/refactor/ACCEPTANCE"` 盘点出的其他**可执行**引用。此处 grep 只用于盘点路径引用；旧目录是否还被运行依赖引用，在 L0.3 按专门步骤核验。
- 允许修改：上述文件与目录、`docs/DEVELOPMENT.md`、`docs/TESTING.md`、两份 Plan2 验收说明及其路径引用方。
- 验收：标准验收（工具路径用新位置）；运行迁移后的工具自身测试，并核对 `TOOLS`、capture provenance 与 noeditable 的解析路径都指向并列新目录；用新位置的工具重跑 `golden_check.py`，与 L0.0 逐字节相同；collect 清单不变。

### L0.2 — 把仍在使用的 paths 等价 golden 迁到 tests/fixtures

- 类型：`move` ／ 前置：L0.1
- 背景（P25）：`tests/v4/test_intent_legacy_paths_to_v3.py:24-38` 在运行时从 `docs/refactor/paths_equivalence/` 加载 `run_equivalence.py` 和 golden 数据，直接归档会让这个现行测试失败。`producer/intent.py:932` 的注释也引用了这个路径。
- 步骤：
  1. `git mv docs/refactor/paths_equivalence tests/fixtures/paths_equivalence`，内容逐字不变。
  2. `tests/v4/test_intent_legacy_paths_to_v3.py:24` 的 `GOLDEN` 路径改为新位置。
  3. `producer/intent.py:932` 注释中的路径改为新位置（只改注释）；`docs/TESTING.md:64` 同步。
  4. 执行前 `git grep -n "paths_equivalence"`，列出全部引用并逐一更新。
- 允许修改：上述文件与目录。
- 验收：标准验收；该测试文件的节点 ID 与结果和 L0.0 完全相同；golden 不变。

### L0.3 — 从主开发树移除 docs/refactor

- 类型：`delete` ／ 前置：L0.2 ／ Q14
- 步骤（不打 tag）：
  1. 将本卡父提交的**完整 40 位 SHA**记为 `archive_parent`。在仓库外的 `$CKPT/archive/architecture-diet-1.bundle` 创建只读归档 bundle，内容包含该父提交及其可达历史；运行 `git bundle verify` 并记录 bundle 的外部路径与 SHA-256。该父提交完整保留 `docs/refactor/`，作为归档来源。
  2. 生成两份文件（V42：人读的和机器读的分开）：
     - `docs/ARCHITECTURE_DIET_1.md`（不超过 1 页）：第一轮瘦身的摘要、`archive_parent` 完整 SHA、manifest 路径、仓库外 bundle 路径与 SHA-256；
     - `docs/archive_manifests/architecture_diet_1.json`：`{"archive_parent": "<完整 40 位 SHA>", "bundle": {"path": "<仓库外路径>", "sha256": "<bundle sha256>"}, "files": {"<docs/refactor 下的相对路径>": "<sha256>", ...}}`，覆盖父提交中 `docs/refactor/` 下全部文件。
     - 说明：删除工作树中的文件能缩小工作树、搜索范围和模型上下文，**不会**缩小 git 历史对象。本卡的目的是局部性，不改写历史。
  3. 删除前运行 `git grep -n "docs/refactor"`，逐条确认生产代码、测试、脚本、CI 与当前使用说明中的**实际运行引用**已迁出。历史评审说明、归档来源记录与本 PLAN 中明确的归档/来源引用可保留，并标明它们不构成运行依赖。特别核对 `confflow/`、`tests/`、`scripts/`、`.github/` 中无引用；已有 L0.1/L0.2 搬迁后的工具和 fixture 引用均指向新路径。
  4. `git rm -r docs/refactor`。
  5. 更新 `docs/ARCHITECTURE.md:95、122` 中对 `docs/refactor/` 的引用，改为指向 `docs/ARCHITECTURE_DIET_1.md` 与 manifest/bundle 记录。
- 允许修改：`docs/refactor/**`（删除）、`docs/ARCHITECTURE_DIET_1.md`（新）、`docs/archive_manifests/architecture_diet_1.json`（新）、`docs/ARCHITECTURE.md`
- 验收：标准验收；golden 与 L0.0 相同；collect 清单不变；重新验证归档 manifest 覆盖父提交中 `docs/refactor/` 下的全部文件，`archive_parent` SHA、bundle SHA 与 `git bundle verify` 结果一致；删除前的路径 grep 已逐条审阅，运行引用已迁出。

### L0.4a — 架构规则清单（只写清单，不改代码）

- 类型：`doc` ／ 前置：L0.3 ／ Q15
- 背景（V29、P26）：架构规则现在分散在 `tests/v4/test_architecture_boundaries.py`（1912 行，含 `TestStaticImports`、`TestStaticCodeVocabulary`、`TestStaticLegacyFilenameContracts`、`TestExtendedV4ProductionRoots`、`TestRemoteBoundary`、`TestRuntimeIsolation`、`TestRetiredRuntimeBoundary`、`TestV3PublicWireRetired`、`TestV1V2PublicWireRetired`、`TestConsolidatedHelperAuthorities`、`TestProducerImportIsolation`、`TestFacadeLazyIsolation` 等）、`tests/v4/test_v42_debt.py`、`tests/v4/test_v46_debt.py`、`scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py`；`tests/v4/test_v46_production_gates.py:945-951` 还以两种方式调用扫描脚本。
- 目标：逐条列出上述文件中的每一条断言，写进 `tools/architecture_policy_inventory.md`。每条标注：规则内容；所在文件与测试名；类别（静态 import 约束、禁用词汇、禁用文件名、运行时 import 隔离、权威来源唯一性、度量）；与哪些条目重复；计划由 L0.4b 还是 L0.4c 承接，或者"不是架构规则，原样保留"。
- 允许修改：`tools/architecture_policy_inventory.md`（新）
- 验收：**清单交验收方确认后才能开始 L0.4b**（V44）。验收方抽查：从每个源文件中随机选 5 条断言，必须都能在清单中找到。

### L0.4b — 静态规则 policy

- 类型：`test-only` ＋ 工具 ／ 前置：L0.4a 已确认
- 目标：新增 `tools/architecture_policy.py`，用数据声明静态规则（import 约束、禁用词汇、禁用文件名、权威来源唯一性、度量），并提供扫描器。新增 `tests/v4/test_architecture_policy.py`，按规则参数化。
- **扫描基于 AST，不扫文本（V43）**：检查模块中真实存在的字符串常量、名称与属性、import 目标、字典键、比较表达式中的常量；**排除模块、类、函数的 docstring 和注释**。否则执行模型会为了通过扫描去改注释，产生纯噪声。词汇类规则若确实需要扫描注释（例如禁止某个旧名字出现在任何地方），必须在规则上单独声明。
- **每条规则都附一个反例**：放在 `tests/v4/architecture_policy_cases/`，是一小段违反该规则的合成代码，测试断言扫描器能报出它；同时附一个"只在 docstring 或注释中出现关键字"的正例，断言扫描器不误报。
- 本卡**不删除**任何旧测试，旧测试与新 policy 并存。
- 允许修改：`tools/architecture_policy.py`（新）、`tests/v4/test_architecture_policy.py`（新）、`tests/v4/architecture_policy_cases/**`（新）
- 验收：标准验收；golden 不变；新测试全部通过；清单中标为 L0.4b 的每条规则都有对应的规则条目和反例。

### L0.4c — 运行时 import 隔离 policy

- 类型：`test-only` ＋ 工具 ／ 前置：L0.4b
- 目标：把清单中运行时 import 隔离类的规则（例如 `TestRuntimeIsolation`、`TestFacadeLazyIsolation`、`TestProducerImportIsolation` 中"导入某模块后 `sys.modules` 中不得出现某些模块"的断言）加入 policy；扫描器在子进程中执行导入并检查 `sys.modules`。同样每条附反例。本卡也不删除旧测试。
- 允许修改：`tools/architecture_policy.py`、`tests/v4/test_architecture_policy.py`、`tests/v4/architecture_policy_cases/**`
- 验收：标准验收；golden 不变；清单中标为 L0.4c 的规则全部覆盖。

### L0.4d — 删除被 policy 完全覆盖的旧守卫

- 类型：`delete` ／ 前置：L0.4c
- 步骤：删除清单中已被 policy 完全覆盖的旧测试，逐条在提交信息中声明（G3）；`scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py` 改为调用 policy 的薄 CLI，命令行接口与输出格式不变；`tests/v4/test_v46_production_gates.py:945-951` 改为调用 policy。清单中标为"不是架构规则"的断言原样保留。
- 允许修改：清单中列出的旧测试文件、两个脚本、`tests/v4/test_v46_production_gates.py`
- 验收：标准验收；golden 不变；collect 的删除节点与声明完全一致；验收方抽查：从清单中随机选 10 条规则，在临时分支上手工引入违反，policy 测试必须失败。

### L0.5 — 元素符号表只保留一份

- 类型：`logic`（Behavior-Change: none）／ 前置：L0.0（可与 L0.1–L0.4 交替进行）
- 背景（V32、P27、V57）：新基线上 `science/data.py:136` 定义 `PERIODIC_SYMBOLS`，`domain/elements.py:25` 定义 `ELEMENT_SYMBOLS`；两份表内容相同且都以 `""` 占位开头。`core/data.py:12-20` 已是向 `science.data` 转发的兼容薄层，不应再改成第二个数据来源。唯一权威改为 `domain.elements.ELEMENT_SYMBOLS`，由 `science.data.PERIODIC_SYMBOLS` 转发；`domain` 继续保持零依赖。
- 步骤：
  1. 在 `science/data.py` 从 `domain.elements` 导入 `ELEMENT_SYMBOLS`，并令 `PERIODIC_SYMBOLS` 直接指向该对象；`SYMBOL_TO_ATOMIC_NUMBER` 仍由它推导。名称、值、类型与导出保持不变。
  2. 保持 `core/data.py` 的现有转发结构不变；它导出的 `PERIODIC_SYMBOLS` 必须与 `science.data.PERIODIC_SYMBOLS` 和 `domain.elements.ELEMENT_SYMBOLS` 是同一个对象。
  3. 更新 `domain/elements.py` 开头注释，使其说明 domain 是表的权威来源，science/core 通过转发复用。
  4. `tests/v4/test_elements_drift.py` 将元素表一致性断言改为验证 `science.data.PERIODIC_SYMBOLS is domain.elements.ELEMENT_SYMBOLS` 且经 `core.data` 导出的对象也 `is` 同一对象；其余测试不动。
  5. `core/elements.py` 的 `canonicalize_element_symbol`（被 `core/io.py` 公开导出）**不动**：它与 `domain.elements.canonical_element_symbol` 的错误信息可能不同，合并属于行为变化，记入 L3 候选。
- 允许修改：`confflow/science/data.py`、`confflow/domain/elements.py`（只改注释）、`tests/v4/test_elements_drift.py`；`confflow/core/data.py` 保持原样。
- 验收：标准验收；golden 不变；断言三个公开位置的表对象 `is` 相同；`python -c "import confflow.domain"` 之后 `sys.modules` 中不出现 `confflow.core` 或 `confflow.science`（保持 domain 零依赖）。

### L0.6 — formal_v4_runner 复用 _build_run_inputs

- 类型：`logic`（Behavior-Change: none）／ 前置：L0.0（可交替进行）
- 背景（V33、P28）：`application/v4_entry.py` 中，`formal_v4_runner`（L217 起）手写了一遍 `_build_run_inputs`（L181-215）的逻辑；后者在生产代码中没有调用方。
- 步骤：`formal_v4_runner` 读完 XYZ 文本后，改为调用 `_build_run_inputs(document, xyz_texts, source_names={})`，用其返回的 `RunInputs` 与 sources 构造请求。执行前逐行核对两段逻辑：输入名为空时的默认值、单输入时的合并与 `source_name`、多输入时的数量检查与错误信息。任何一处不等价都停止报告（G9），不得调整 `_build_run_inputs` 去迁就。
- 允许修改：`confflow/application/v4_entry.py`
- 验收：标准验收；golden 不变；`tests/test_v4_entry_helpers.py` 与所有调用 `formal_v4_runner` 的测试结果不变。

### L0.7 — 测试模块之间不再互相 import

- 类型：`test-only` ／ 前置：L0.0（可交替进行）
- 背景（V36、P29）：例如 `tests/v4/test_confgen_paths_audit.py:31`、`test_confgen_paths_phase0.py:42` 从 `test_repair_executors` 导入 `_butane, _ctx, _item, _sci`；`test_confgen_paths_audit.py:310` 从 `test_confgen_paths_phase0` 导入；`test_audit_redteam_r2.py:40` 从 `test_audit_regressions_r2` 导入。这会妨碍 L3 的测试重组。
- 步骤：执行前用标准库 `ast` 解析 `tests/` 下的 Python 源文件，定点列出 `ast.Import`/`ast.ImportFrom` 中指向其他测试模块的绝对 `tests.v4.test_*` 与相对 `.test_*` 引用；被共享的辅助函数原样搬到 `tests/v4/_helpers/`（按主题分文件，函数体逐字不变），原测试模块与使用方都改为从那里导入。只搬非测试函数（不以 `test_` 开头），**测试函数本身不移动**。完成后用同一 AST 检查复查。
- 允许修改：`tests/v4/_helpers/**`（新）、涉及的测试文件（只改 import 和删除被搬走的辅助函数定义）
- 验收：标准验收；collect 清单与 L0.0 **完全相同**（节点 ID 不变）；golden 不变；标准库 AST 复查不再有测试模块之间的 import。

---

# FIX-1A：ConfGen 组件化（行为 100% 不变）

**本里程碑所有卡的 golden 都必须与 B1 逐字节相同。**

### B1 — 冻结 FIX-1A 的行为基线

- 类型：`baseline` ／ 前置：L0 已合并
- 目标：在 L0 合并后的 main 上，于 `$CKPT/fix-1a/baseline/` 生成：全量测试 collect 与结果、contract/boundary 摘要、TS1 三种 backend 的 engine 结果、`tests/v4/test_confgen_*.py` 捕获的全部 engine 报告。
- 允许修改：`docs/confgen-fix/checkpoints/B1/MANIFEST.json`、`docs/confgen-fix/LOG.md`
- 步骤（工具用法见 `tools/refactor/README.md`，由 L0.1 从原 B0.1 卡整理而来）：
  1. `ts1_engine.py --backend {default|rigid|flexible}` 各运行两次，逐字节相同后写入；
  2. `capture_engine_reports.py` 运行两次，`diff -r` 相同后写入；
  3. `contract_digests.py`、`test_inventory.py collect/run`；
  4. 与 L0.0 检查点比较：TS1、engine 报告、contract/boundary 摘要必须逐字节相同（L0 不改变行为）；测试清单的差异必须恰好等于 L0 各卡声明的增删。
- 验收：产物齐全；两次捕获逐字节相同；第 4 步全部满足；manifest 与 `$CKPT` 中的文件一致。

### A1 — 显式组件注册表实例

- 类型：`logic`（Behavior-Change: none）／ 前置：B1
- 背景：`GenerationStage` 接口（`model.py:625-707`）和 `verify_locked` 钩子（`ring/stage.py:476`、`coordination/stage.py:1027`、`torsion/stage.py:567`）已经存在。缺的是：engine 不通过注册表发现组件，而是按轴名逐个写死（`engine.py:631-700`）。
- 新增 `science/confgen/registry.py`（**不得被 `model.py` import**，避免 `model → registry → 组件 → model` 的循环）：
  ```python
  @dataclass(frozen=True)
  class ComponentDescriptor:
      id: str                        # 组件 id
      order: int                     # 执行顺序：coordination=10, rings=20, torsions=30
      spec_keys: tuple[str, ...]     # 该组件拥有的 resolved spec 顶层键
                                     #   coordination: ("coordination",)
                                     #   rings:        ("rings",)
                                     #   torsions:     ("torsions", "paths", "strict_path_bond_check")
      state_merge: Literal["replace", "merge"]   # coordination=replace，其余 merge
      is_active: Callable[[Mapping[str, Any]], bool]
      factory: Callable[[Mapping[str, Any]], GenerationStage]

  @dataclass(frozen=True)
  class ComponentRegistry:
      descriptors: tuple[ComponentDescriptor, ...]
      def resolve(self, resolved) -> list[tuple[str, GenerationStage]]   # 按 order 排序
      def ids(self) -> tuple[str, ...]
      def with_component(self, d: ComponentDescriptor) -> "ComponentRegistry"  # 返回新实例
      def owner_of(self, spec_key: str) -> str | None

  def build_default_registry() -> ComponentRegistry   # 显式列出三个组件，不依赖 import 副作用

  @functools.cache
  def default_registry() -> ComponentRegistry         # 返回同一个不可变实例（缓存的是不可变对象，不是可变单例）

  def resolve_registry(r: ComponentRegistry | None) -> ComponentRegistry:
      return default_registry() if r is None else r
  ```
  - 三个组件各自提供 `descriptor()` 函数（`coordination/component.py`、`ring/component.py`、`torsion/component.py`，均为新文件），函数体逐字搬运 `engine.py:631-678` 中对应分支的逻辑（包括 coordination 的 `adapt_to_core` 和 `UnsupportedAxisError` 转换，以及 `_levels` 中 `treatment != "preserve_input"` 的判断）。
  - `build_default_registry()` 在函数内部 import 这三个 `descriptor()`。
  - `ComponentRegistry` 构造时检查：id 唯一、order 唯一、`spec_keys` 互不重叠。
- 修改 `engine.py`：engine 构造函数新增仅关键字参数 `registry: ComponentRegistry | None = None`，内部用 `resolve_registry(registry)`（A4 会增加"与 context 持有的实例必须相同"的检查）；`_load_stage`（L631-678）与 `_levels`（L679-700）改为 `self._registry.resolve(...)`；显式 stage 列表（L681-688）按 registry 的 order 排序。
- **显式 stage 列表的兼容（V45）**：`ConfgenEngine(stages=[...])` 在测试中大量使用（仅 `tests/v4/test_confgen_v3_core.py` 就有约 50 处 `ConfgenEngine(` 调用，多数传入伪造的 stage），走的是 `engine.py:681-688` 的独立路径：按 `AXIS_ORDER` 排序，未知轴排在最后，重复轴报 `duplicate stage axes in explicit stage list`。A1 之后：
  - 显式 stage 的 axis 若在 registry 中，按 registry 的 order 排序（对三个内置 id 与旧顺序相同）；
  - 未知 axis 的处理（排在最后、之后在何处以何种错误失败）必须与 B1 **完全一致**；
  - 重复 axis 的错误信息逐字不变；
  - 显式 stage **不经过** registry 的 `is_active`、`factory`。
  执行前 `grep -rn "ConfgenEngine(" confflow tests` 列出全部调用方，写进提交信息。
- 允许修改：`science/confgen/registry.py`（新）、`science/confgen/{coordination,ring,torsion}/component.py`（新）、`science/confgen/engine.py`、`tests/v4/test_confgen_registry.py`（新）
- 新增测试：显式 stage 列表含未知 axis、含重复 axis、顺序打乱三种情况，行为与 B1 相同（旧实现的排序逻辑复制进测试作为参照）；默认注册表恰好包含 3 个组件且顺序为 C→R→T；对 B1 所有 engine 报告对应的 spec，`resolve` 与旧 `_levels` 结果相同（参数化）；`with_component` 不改变原实例；`spec_keys` 重叠时构造失败。
- 验收：标准验收；golden 与 B1 相同；`python3 -c "import confflow.science.confgen.model"` 之后，`sys.modules` 中不含 `registry` 与三个组件模块（证明没有循环依赖、没有 import 副作用）。

### A2 — kernel 记录类型与 v3 兼容记录分层

- 类型：`logic`（Behavior-Change: none）／ 前置：A1
- 背景（V23）：只拆 `ConfgenStateKey` 不够。`WorkingRealization`（`model.py:492-515`）要求 `state_key` 必须是 `ConfgenStateKey`，并按三轴校验 `generation_axis`、`locked_axes`；`GenerationTarget`（L518-538）、`OrbitIdentity`（L183-196）按三轴校验 `axis`；`MolecularContext.input_state_key`（L219、L254）要求是 `ConfgenStateKey`。这些都是公开类型（`science/confgen/__init__.py:50-108` 导出），**构造时的校验行为不得改变**：例如 `GenerationTarget(axis="nonsense", ...)` 现在立即抛 `ValueError`，A2 之后也必须如此。
- 已核实的使用面：三个内置 stage 只读 `parent.structure`，不读 `state_key`/`locked_axes`/`generation_axis`；`WorkingRealization` 只在 engine 中构造（`engine.py:740、945、1014、1679、1698`）；`GenerationTarget` 由三个 stage 构造；`EngineRun.leaves`（`engine.py:485-488`）是公开的 `tuple[WorkingRealization, ...]`，`accounting.py:583-641` 通过 `leaf.state_key.to_dict()` 写报告；仓库内没有在 model 之外构造 `OrbitIdentity` 的代码。
- 目标：**旧类型一个字不改，kernel 改用新类型，两者在边界上转换。** `ConfgenStateKey` 继续定义在 `model.py`，不得搬进 `wire_v3.py`；其 `__module__` 必须保持 `confflow.science.confgen.model`。
  1. 新增 `science/confgen/kernel_records.py`：
     - `ComponentStateKey`：`components: FrozenDict[str, Any]`，键为任意组件 id；
     - `KernelGenerationTarget`：字段同 `GenerationTarget`，但 `axis` 只要求是非空字符串；
     - `KernelWorkingRealization`：字段同 `WorkingRealization`，但 `state_key` 为 `ComponentStateKey`，`generation_axis`/`locked_axes` 只要求是非空字符串。
     - `InheritedScopeError(ValueError)`：通用、与组件无关的错误类型；由 `engine.py` 与 `science/confgen/__init__.py` 正常导入/导出，不做懒导出。
     - `as_kernel_target(t)`：接受 `KernelGenerationTarget` 或旧 `GenerationTarget`，统一转为 `KernelGenerationTarget`；它只定义在 `kernel_records.py`。
     - 这些类型**不做组件 id 合法性校验**；engine 在构造它们的地方检查 `registry.ids()`。
  2. 新增 `science/confgen/wire_v3_constants.py`（无任何依赖）：`V3_AXIS_ORDER = ("coordination", "rings", "torsions")`。`model.py:65` 改为 `from .wire_v3_constants import V3_AXIS_ORDER as AXIS_ORDER`，仍在 `__all__` 中；旧类型的校验继续使用它，**行为不变**（V14）。
  3. 新增 `science/confgen/wire_v3.py`（v3 适配器，G13 中唯一允许出现三个轴名字面量的模块之一）：
     - `to_wire_key / from_wire_key`：`ComponentStateKey ↔ ConfgenStateKey`；
     - `to_legacy_realization(r)`：`KernelWorkingRealization → WorkingRealization`；
     - 遇到 v3 之外的组件 id，一律抛 `UnsupportedWireComponent`（fail-closed）。
  4. **kernel 与 v3 投影分开（V39、V47）**。`wire_v3` 只能在两处被调用：`wire_v3.py` 自身，以及 `ConfgenEngine.run` 的**方法体内**（懒 import）。`run` 是 v3 的兼容入口，不为了形式上的纯粹再拆 engine（拆出 facade 记入 L3 候选）。engine 的其余部分、`accounting.py`、`kernel_records.py` 一律不得调用或 import `wire_v3`：
     ```text
     ConfgenEngine.run(context, should_cancel=None) -> EngineRun  # 现有公开方法签名与 should_cancel 通道不变；唯一允许调用 wire_v3 的方法
       ├─ initial = wire_v3.from_wire_key(context.input_state_key)
       ├─ kernel_run = self.run_kernel(context, initial_key=initial, should_cancel=should_cancel)
       └─ return wire_v3.project_v3(kernel_run)

     ConfgenEngine.run_kernel(context, *, initial_key: ComponentStateKey, should_cancel=None) -> KernelRun  # 新增内部方法；initial_key 必传，取消探针沿用可选默认值
     ```
     - **`initial_key` 必传（V49）**：若允许省略并默认为空 key，链式运行的调用方一旦忘传，继承状态就会被静默丢弃。新运行的调用方显式传 `ComponentStateKey()`。内部 `run_kernel` 接收并使用 `should_cancel`；公开 `run` 保持 `should_cancel: Callable[[], bool] | None = None` 原签名并将探针原样传入。
     - `ConfgenEngine.run` 与 `wire_v3.py` 可以访问旧 `ConfgenStateKey` 上的 `coordination`、`rings`、`torsions` 属性；除二者外只豁免 `model.ConfgenStateKey` 类体。kernel 其他代码不得访问这些组件状态属性。
     - 新增 `KernelRun`（`kernel_records.py`）：`leaves: tuple[KernelWorkingRealization, ...]`、`target_records`、`report`、`certificate`。
     - engine 内部：上述 5 处构造改为 `KernelWorkingRealization`；根节点的 key 取 `initial_key`；stage 返回的 target 一律经过 `kernel_records.as_kernel_target`（只读字段，不涉及 wire）；`combine_state_key`（`engine.py:455-480`）改为在 `ComponentStateKey` 上的通用实现，替换/合并语义取自 descriptor 的 `state_merge`；传给 stage 的 `parent` 为 `KernelWorkingRealization`，`GenerationStage` 中 `parent` 的类型注解改为只含 `structure`、`provenance` 的 Protocol（只改注解）；`engine.py:1671` 的 `locked` 排序、报告中的 `axis_order`（`engine.py:831、2502`）取自 `registry.ids()`。`ConfgenEngine.run` 保持现有 `should_cancel` 参数及签名不变。
  5. **accounting 不改、不重算（V48）**。历史核实（`@d05927c`）：`TargetRecord.complete_key`（`accounting.py:82`）本来就是通用 Mapping；`build_certificate`（L561-579）的 digest 只用 `target_id`、`axis`、`ordinal`、`status`、`reason`，不依赖 `complete_key`；`stamp_production_results`（L582 起，L641 处调用 `leaf.state_key.to_dict()`）只在 executor 发布结果时调用。新 HEAD 的该调用锚点为 `confgen_executor.py:283`（旧锚点 L293），它接收的是 `EngineRun.leaves`，即投影后的旧类型。所以 kernel 内照常做 accounting，只是 engine 中 6 处 `complete_key=FrozenDict(<key>.to_dict())`（`engine.py:762、1217、1327、1720、1734、2137`）写入的是 `ComponentStateKey` 的通用序列化。
  6. `wire_v3.project_v3(kernel_run) -> EngineRun`**只做数据投影**：叶节点经 `to_legacy_realization` 转换；每条 `TargetRecord` 的 `complete_key` 从通用形式转为 v3 形式（`to_wire_key(...).to_dict()`），其余字段原样保留；`certificate` 原样保留（它的 digest 不依赖 `complete_key`）；`report` 中凡由 `complete_key` 或叶节点 key 派生的部分同步转换，其余原样保留。执行前逐个核对 `EngineRun.report_json()`（`engine.py:499` 起）输出中哪些字段来自 state key，列进提交信息。遇到 v3 之外的组件抛 `UnsupportedWireComponent`。
- 允许修改：`science/confgen/kernel_records.py`（新，含 `KernelRun`、`as_kernel_target`）、`science/confgen/wire_v3.py`（新，含 `project_v3`）；`science/confgen/accounting.py` **不在白名单内**（V48）、`science/confgen/wire_v3_constants.py`（新）、`science/confgen/model.py`（只改第 65 行的常量来源与 `GenerationStage` 的注解）、`science/confgen/engine.py`、`science/confgen/registry.py`、`science/confgen/__init__.py`（新增导出 kernel 类型）、`tests/v4/test_confgen_registry.py`
- 新增测试：
  - **旧类型行为不变**：`GenerationTarget(axis="nonsense")`、`WorkingRealization(generation_axis="nonsense")`、`WorkingRealization(state_key=<非 ConfgenStateKey>)`、`OrbitIdentity(axis="nonsense")` 仍抛 `ValueError`，错误信息逐字不变；`model.AXIS_ORDER` 仍可导入，值与类型不变，仍在 `__all__` 中；`ConfgenStateKey.__module__ == "confflow.science.confgen.model"`，类型仍由 `model.py` 定义。
  - **取消兼容**：`ConfgenEngine.run(context, should_cancel=probe)` 仍将原探针传到 kernel，触发时取消行为与 B1 相同；`run` 不新增或改变任何公开参数。
  - 对 B1 全部 engine 报告中的每个 StateKey：`to_wire_key(from_wire_key(x)) == x`，且 `to_dict` 字节不变。
  - `combine_state_key` 在三种轴上与旧实现逐例相同（旧实现复制进测试作为参照）。
  - `EngineRun.leaves` 的元素类型仍是 `WorkingRealization`。
  - 架构测试：用标准库 AST 检查三个内置 stage，不存在 `parent.state_key`、`parent.locked_axes`、`parent.generation_axis` 这些 `Attribute` 访问（锁定"stage 只读 structure"这个前提）。
  - `to_wire_key` 遇到未知组件抛 `UnsupportedWireComponent`。
  - 对 B1 中每个 engine 报告对应的运行：`project_v3(run_kernel(...))` 与 `run(...)` 的结果逐字节相同。
  - 架构测试（写进 L0 的 policy，按 AST 检查作用域）：`accounting.py`、`kernel_records.py` 不得 import 或调用 `wire_v3`；`engine.py` 中只有 `ConfgenEngine.run` 的方法体可以引用 `wire_v3`，其他任何函数、方法和模块顶层都不得引用；除 `ConfgenStateKey` 类体、`ConfgenEngine.run` 方法体与 `wire_v3.py` 外，kernel 不得访问 `.coordination`、`.rings` 或 `.torsions` 属性。`model.py` 的类体例外不扩展到模块中其他代码。
  - `run_kernel` 不传 `initial_key` 时抛 `TypeError`。
- 验收：标准验收；golden 与 B1 相同。

### A3 — engine 中按轴名的特殊处理下放到组件钩子

- 类型：`logic`（Behavior-Change: none）／ 前置：A2
- 把下列分支改为读取组件声明，engine 中不再出现轴名：

  | 位置 @d05927c | 现状 | 改为 |
  |---|---|---|
  | `engine.py:1361` | `check_bond_integrity=(axis == "torsions")` | `GenerationStage.check_bond_integrity: bool = False`，TorsionStage 设为 `True` |
  | `engine.py:1664` | `for lock in self._inherited if axis == "torsions"` | 钩子 `carries_inherited_locks: bool = False`，TorsionStage 设为 `True` |
  | `engine.py:1877` 起 | ring 锁匹配的回退分支 | `reachability.py` 只提供模块 import 可达性参考，不能证明此函数内分支不可达。先用真实调用/运行证据核实分支条件是否会成立；只有证据表明 B1 实际调用不会进入该分支时才删除。分支可达或无法取得证据时停止并报告（G9），不得靠推断删除 |
  | `engine.py:2575-2580` | preserved 扫描按 rings/torsions 写死 | 钩子 `preserved_entries(resolved) -> list[dict]` |
  | `engine.py:2484-2502` | 报告中写死 coordination 段 | 钩子 `report_section(resolved) -> dict \| None`，由 wire 适配器按 v3 键名拼进报告 |
  | `engine.py:210-380` | scope 辅助函数按轴拼装 | 搬到对应组件，作为 `describe_scope(resolved)`（A4d 继续） |

- 允许修改：`science/confgen/engine.py`、`science/confgen/model.py`（`GenerationStage` 增加带默认值的属性和钩子）、三个组件的 `stage.py`、`science/confgen/wire_v3.py`、`tests/v4/test_confgen_registry.py`
- 验收：标准验收；golden 与 B1 相同；L0 的 AST policy 检查 engine 及其他 kernel 源码的轴名字面量、组件属性读取和导入边界，不以文本 grep 判定清零。

### A4a — registry 贯穿各层

- 类型：`logic`（Behavior-Change: none）／ 前置：A3
- 本卡只建立通道，不搬任何组件逻辑（V44：A4 拆成 4 张卡，每张卡都必须保持 golden 与 B1 相同，出现差异时能立即定位到是哪一步）。
- 步骤：
  1. **registry 贯穿（V15、V24）**：旧调用方式保持兼容，同时保证一次流水线内只有一个 registry 实例。
     ```text
     公开入口（参数可省略，省略时 resolve_registry(None) → default_registry()）
       ├─ planner.normalize_spec(raw, *, registry=None)     planner.py:393
       ├─ model.build_context(..., *, registry=None)        model.py:423
       └─ ConfgenEngine(..., *, registry=None)              A1
     入口解析出 registry 之后，内部各层只接收显式传入的实例：
       model.build_context
         ├─ planner.normalize_spec(raw, registry=r)         model.py:459
         ├─ planner.build_typed_graph(..., registry=r)      planner.py:963（本卡只传入，不调用任何组件钩子）
         ├─ MolecularContext.registry = r                   （context 持有同一个实例）
         ├─ planner.preflight(context, stages)              planner.py:1277，从 context 取
         └─ ConfgenEngine(registry=context.registry)
     两个 executor 不需要改调用形式（不传即默认），但为了可测试性，增加可选的 registry 透传参数：
       ├─ execution/confgen_executor.py:194
       └─ execution/transform_executor.py:492
     ```
     - `normalize_spec(spec)`、`build_context(structure, spec)` 这类旧调用**必须继续可用**（V24：强制传参是 API break）。
     - 内部私有函数（`_` 开头，以及 `build_typed_graph` 等只被入口调用的函数）的 `registry` 参数为**仅关键字、无默认值**，漏传会直接报错。
     - `default_registry()` 用 `functools.cache` 返回同一个不可变实例，所以默认路径下各入口拿到的也是同一个对象。
     - engine：若同时拿到 context 和显式 `registry`，二者必须是同一个对象（`is` 比较），否则抛错。
     - 执行前用 `grep -rn "normalize_spec(\|build_context(" confflow tests` 列出全部调用方并写进提交信息。
- 允许修改：`science/confgen/planner.py`（只改函数签名与 registry 传递）、`science/confgen/model.py`（`build_context` 与 `MolecularContext.registry`）、`science/confgen/engine.py`、`science/confgen/registry.py`、`execution/confgen_executor.py`、`execution/transform_executor.py`（只增加可选透传参数）、`tests/v4/test_confgen_registry.py`
- 新增测试：
  - **旧调用兼容**：`normalize_spec(spec)` 与 `build_context(structure, spec)` 不传 registry 时，结果与 B1 逐字节相同。
  - **实例一致性**（V50）：构造一个自定义 registry（`build_default_registry().with_component(...)`），经 `build_context(..., registry=custom)` 走完，只断言 `context.registry is custom`、engine 持有的 registry `is custom`。**本卡不断言任何组件钩子被调用**：normalization 的分派在 A4b 才实现，拓扑贡献在 A4c 才实现。
  - 默认路径：两次 `build_context` 不传 registry，`context.registry is default_registry()`。
  - 向 engine 传入与 context 不同的 registry 对象时抛错。
- 验收：标准验收；golden 与 B1 相同。

### A4b — spec normalization 归组件所有

- 类型：`logic`（Behavior-Change: none）／ 前置：A4a
- 组件接口（挂在 descriptor 上，因为它在 stage 构造之前运行）：
  ```python
  normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]   # 与结构无关
  validate_context(resolved: Mapping, context: MolecularContext) -> None   # 可选；需要原子数或图时在这里检查
  ```
  - **输入是整份原始 spec，输出是 `{自己拥有的键: 规范化后的值}`（V51）**：一个组件可能拥有多个键，而且值不一定是 Mapping（`rings`、`torsions`、`paths` 是列表，`strict_path_bond_check` 是布尔值）。组件只能读取自己声明的 `spec_keys`，返回值的键必须恰好是其 `spec_keys` 中出现在输入里的那些；planner 负责合并，并检查返回的键没有越界。
  - **`normalize_spec` 不接收原子数（V40）**：现有 `planner.normalize_spec(raw)`（L393）本来就与结构无关，原子数和索引范围检查发生在之后构建 context 的阶段。
  - `validate_context` 在 `build_context` 构建完 typed graph 和 context 之后调用；只有现在确实在 context 阶段执行的检查才搬到这里，不得把原本在 normalize 阶段执行的检查挪后（否则错误出现的时机会变）。
- 步骤：`normalize_spec`（L393 起）只保留通用部分：索引基转换、`topology`（bonds/add_bond/del_bond/atoms）、sampling、limits。其余按 `spec_keys` 分派：
  - torsion：`resolve_torsion_axes`（L224）、`paths` 与 `strict_path_bond_check` 的处理（L503-535）；
  - coordination：`_convert_coordination`（L775）、`_overlay_declared_coordination_scope`（L806）；
  - ring：ring 段的 normalization。
  函数体按 `move` 的标准搬到各组件的 `spec.py`，错误信息逐字不变，**校验顺序不变**（按原先 planner 中各段的顺序调用组件；与 descriptor 的 order 不一致时，按原顺序为准并停止报告）。输入中出现无主顶层键时仍由白名单拒绝，白名单改为"通用键 ∪ registry 中所有 `spec_keys`"。
- 允许修改：`science/confgen/planner.py`、`science/confgen/model.py`（`build_context` 中调用 `validate_context`）、三个组件的 `spec.py`（新）与 `component.py`、`science/confgen/registry.py`、`tests/v4/test_confgen_registry.py`
- 新增测试：对 B1 中所有 spec（包括失败用例），`normalize_spec(spec)` 的结果或异常与旧实现逐字相同；错误按原顺序出现（含两个以上错误的 spec，报出的是同一个）；从 A4a 移来：自定义 registry 中的探针组件，其 `normalize_spec` 经 `build_context` 入口被调用；组件返回越界的键时 planner 报错。
- 验收：标准验收；golden 与 B1 相同；`planner.py` 中轴名字面量为 0；`paths`、`strict_path_bond_check` 只在 torsion 组件中出现。

### A4c — typed graph 的拓扑贡献归组件所有

- 类型：`logic`（Behavior-Change: none）／ 前置：A4b
- 接口（V52：现有代码中没有 `graph_builder` 这个抽象，不能让执行模型自己设计）：
  ```python
  @dataclass
  class TopologyBuildContext:          # 放在 planner 或 kernel_records 中，与化学无关
      n_atoms: int
      adjacency: list[set[int]]
      edges: dict[tuple[int, int], <现有的 typed edge 值类型>]
      explicit_covalent: set[tuple[int, int]]
      def check_index(self, value: int, path: str) -> None   # 即现有 build_typed_graph 中的 _check

  contribute_topology(resolved: Mapping[str, Any], build: TopologyBuildContext) -> None
  ```
  字段名、类型以 `build_typed_graph`（`planner.py:963` 起）中现有的局部变量为准，执行前逐个核对；不一致时按现有代码命名并在提交信息中说明。
- 步骤：`build_typed_graph`（`planner.py:963`）中对 `_overlay_declared_coordination_scope`（L806）的直接调用，改为调用 coordination 的 `contribute_topology`，后者原样执行这段 overlay；通用拓扑（bonds、add_bond、del_bond、structure patch `_apply_structure_patch` L878）留在 planner。**边的加入顺序必须与旧实现相同**，否则 typed graph 的 digest 会变。
- 允许修改：`science/confgen/planner.py`、`science/confgen/kernel_records.py`（若 `TopologyBuildContext` 放在这里）、coordination 的 `component.py` 与 `spec.py`、`tests/v4/test_confgen_registry.py`
- 新增测试：对 B1 中所有带 coordination 的 spec，新旧 typed graph 的边列表（含顺序）与 digest 逐字相同。
- 验收：标准验收；golden 与 B1 相同。

### A4c-lazy — 组件包的懒兼容导出

- 类型：`logic`（Behavior-Change: none）／ 前置：A4c
- 目标：把组件包 `__init__.py` 的懒导出从 A5 拆出，单独验证公开 import 兼容与包级无副作用。
- 步骤：`coordination/__init__.py`、`ring/__init__.py`、`torsion/__init__.py` 改为 PEP 562 模块级 `__getattr__`；保留原 `__all__`；每个原导出名仍可 `from ... import`，并且与定义模块中的对象 `is` 相同。模块级懒加载只在请求对应名字时执行。
- 允许修改：三个组件包的 `__init__.py`、`tests/v4/test_confgen_registry.py`。
- 新增测试：在隔离子进程中，对 B1 `__all__` 中每个组件导出名验证旧包路径与定义模块路径得到同一对象；未请求的组件导出不加载其定义模块。
- 验收：标准验收；golden 与 B1 相同；三个组件包 `__all__` 与 B1 逐项相同，且所有旧路径对象身份保持一致。

### A4d — 继承状态由组件以不透明载荷持有

- 类型：`logic`（Behavior-Change: none）／ 前置：A4c-lazy
- 背景（V41）：现在只有 torsion 有具体的继承锁类型（新 HEAD `engine.py:116` 的 `InheritedTorsionLock`、`engine.py:187` 的 `inherited_torsion_locks`）；ring、coordination 的继承范围只由 executor 序列化（新 HEAD `confgen_executor.py:414-549`），没有对应类型。**不得新增中央的继承锁类型或联合类型**，否则 kernel 又会重新认识各个组件。
- 接口（挂在 descriptor 上）：
  ```python
  serialize_inherited_state(resolved: Mapping, state_value: Any, context) -> JSONValue
  verify_inherited_state(structure, state_value: Any, payload: JSONValue, context) -> VerificationResult
  ```
  kernel 只保存 `ComponentInheritedState(component_id: str, payload: FrozenDict)`，不解释 payload 的内容。调用 `verify_inherited_state` 时，kernel 从 `ComponentStateKey.components[component_id]` 取出 `state_value` 一并传入（V53）；组件**不得**再从 `context.input_state_key` 中查找自己的 v3 状态，否则刚解耦又会耦合回去。
- 步骤：
  1. 新 HEAD 的 `confgen_executor.py:414-549` 中 `_attach_inherited_scope` 改为遍历 registry，调用各组件的 `serialize_inherited_state`；写出到 v3 结果时仍由 `wire_v3` 按原格式拼装，字节不变。
  2. torsion：新 HEAD `engine.py:116` 的 `InheritedTorsionLock`、`engine.py:187` 的 `inherited_torsion_locks`、`engine.py:390` 的 `check_inherited_torsion_locks` 及 `_resolve_inherited_frame` 移进 `torsion/inherited.py`，成为 `verify_inherited_state` 的实现。`InheritedScopeError` 已由 A2 定义为 `kernel_records` 中的通用 `ValueError` 子类，留在 kernel；engine 正常导入/导出该错误，不懒加载它。
  3. 为兼容旧路径，从 `engine.py` 与 `science/confgen/__init__.py` 的模块顶层导入中移除上述三个 torsion 名字，再各自增加模块级 `__getattr__`（PEP 562），只对 `InheritedTorsionLock`、`inherited_torsion_locks`、`check_inherited_torsion_locks` 三个名字按需导入 `torsion.inherited` 并返回对应对象；其他名字一律按模块惯例抛 `AttributeError`。不得为其他名字增加该惰性兼容路径。
  4. ring、coordination：`verify_inherited_state` 只实现它们**现在实际执行**的检查（执行前逐行核实；如果现在没有任何检查，就实现为"总是通过"，并在注释中写明这是现状，不是新增的放宽）。
  5. `engine.py:1664` 起处理继承锁的循环改为：对每个带 payload 的组件调用其 `verify_inherited_state`。
- 允许修改：`science/confgen/engine.py`、`science/confgen/kernel_records.py`、`science/confgen/__init__.py`、`execution/confgen_executor.py`、三个组件的 `component.py` 与新文件 `inherited.py`、`science/confgen/wire_v3.py`、`tests/v4/test_confgen_registry.py`
- 新增测试：B1 中所有带继承状态的运行（链式 confgen），继承范围的序列化字节与旧实现相同；`InheritedScopeError` 的触发条件与错误信息逐字不变；`from confflow.science.confgen.engine import InheritedTorsionLock`、`from confflow.science.confgen import InheritedTorsionLock` 及其余两个兼容名仍可用，且各自与 `from ...torsion.inherited import ...` 得到的对象 `is` 相同；`InheritedScopeError` 则由 engine/package 的正常导出指向 `kernel_records` 中同一对象；子进程执行 `import confflow.science.confgen` 后，已加载组件模块集合与新 HEAD 基线完全相同（实测为空集合）。
- 验收：标准验收；golden 与 B1 相同；L0 AST policy 证明三个惰性导出只发生在两个指定的模块级 `__getattr__` 中、且只处理指定的三个名字；kernel 中不存在任何以具体组件命名的继承类型。

### A5 — 去掉组件之间、以及 schema/contract 对组件内部的直接依赖

- 类型：`logic`（Behavior-Change: none）／ 前置：A4d
- 目标：
  1. `coordination/stage.py:838`：coordination 检查"是否存在 rings/torsions"，用来决定单轴对称抑制是否适用。改为 kernel 在 `MolecularContext` 中提供 `active_components: tuple[str, ...]`，coordination 只判断 `active_components == (自己的 id,)`。
  2. `producer/contract.py:331-344`：改为遍历 registry，调用 descriptor 上新增的 `contract_options() -> dict`，按原键名合并。**contract 字节必须不变**。
  3. `workflow/v4/confgen_schema.py:33,68,75` 对组件内部模块的 import，改为调用 descriptor 的 `schema_constants()`。`confgen_schema.py:396` 的 `Literal["coordination","rings","torsions"]` 是 v3 wire 格式，保留；新增测试断言它与 `build_default_registry().ids()` 完全相同。
  4. **组件描述模块必须是轻量的（V25）**。现状（方案作者实测 @afdf9df；相关文件到 d05927c 未改动）：`import confflow.workflow.v4.confgen_schema` 并做一次最小校验时，不加载任何 confgen 运行时模块，这一点必须保持；而 `producer/contract.py` 的 `_confgen_section()` 现在会加载 `coordination.stage`、`coordination.realization`、`ring.stage`、`ring.realization` 和 `engine`。原因之一是组件包 `__init__.py` 会在模块顶层 import stage、realization 等模块；A4c-lazy 已将这些包初始化器改为 PEP 562 懒导出，使 A5 导入 descriptor 不会连带加载求解器。规则：
     - 每个组件新增 `constants.py`，只放 schema 与 contract 需要的常量（例如 coordination 的 `DEFAULT_SECTION_TOLERANCES`、`BACKEND_CHOICES`；ring 的模板名表；torsion 的模型名表），只依赖标准库。原定义处改为从 `constants.py` 重新导出，对象标识不变。
     - `component.py` 顶层只允许 import 自己的 `constants.py` 和 `registry` 中的类型；`factory`、`normalize_spec`、`contribute_topology` 等需要运行时的函数，在函数体内懒加载。`schema_constants()`、`contract_options()` 只读 `constants.py`。
- 允许修改：三个组件的 `constants.py`（新）、`science/confgen/ring/templates.py`、`science/confgen/torsion/stage.py`（常量改为重新导出）、`science/confgen/coordination/stage.py`、`science/confgen/model.py`（`MolecularContext` 增加字段）、`science/confgen/engine.py`、`science/confgen/registry.py`、三个 `component.py`、`producer/contract.py`、`workflow/v4/confgen_schema.py`、`tests/v4/test_confgen_registry.py`。组件包 `__init__.py` 已由 A4c-lazy 修改，本卡不再修改。
- 新增测试：
  - **import 纯度**（在子进程中运行，避免测试间互相污染）：
    1. 只 `import confflow.workflow.v4.confgen_schema` 并校验 `{"schema_version": 3}`：`sys.modules` 中不得出现任何 `confflow.science.confgen.*` 模块（与 B1 相同）；
    2. 校验一份包含 coordination、rings、torsions、paths 各段的完整 spec，再调用 `producer.contract._confgen_section()`、`build_default_registry()`，以及每个 descriptor 的 `schema_constants()`、`contract_options()`：`sys.modules` 中不得出现名字以 `.stage`、`.realization`、`.enumeration`、`.hgeom` 结尾的 `confflow.science.confgen.*` 模块。
- 验收：标准验收；golden 与 B1 相同；contract、boundary sha256 与 B1 相同。

### A6 — DummyComponent 验收与架构守卫

- 类型：`test-only` ＋ 架构测试 ／ 前置：A5
- 目标：证明"加一个组件不需要改 kernel"。**不**证明"新组件自动成为 v3 公开能力"（那需要改 wire 适配器，属于有意的边界，V6）。
- 步骤：
  1. `tests/v4/confgen_dummy_component.py`：`DummyComponent` 对一个声明的原子做 ±0.1 Å 平移，两个状态；`perceive` 测量平移方向；`verify_locked` 检查方向；`spec_keys=("dummy",)`，order=40。
  2. 测试中 `registry = build_default_registry().with_component(dummy_descriptor())`，从 `build_context(..., registry=registry)` 入口传入，由 A4 建立的通道一路到 engine。**不使用全局注册**（G14），也不得绕过 planner 直接构造 engine。
  3. 断言内部链路（通过 A2 新增的 `run_kernel(context, initial_key=ComponentStateKey())`）：spec（含 `dummy` 段）→ planner（`dummy` 段被接受并交给 dummy 的 `normalize_spec`）→ enumerate（dummy 产出 `KernelGenerationTarget`，axis 为 `"dummy"`）→ realize → `KernelRun` 的叶节点中 `state_key.components["dummy"]` 存在 → parent lock 审计 → 叶节点计数正确（与 torsion 组合时 = 2 × torsion 状态数）。
  4. 断言边界行为：对同一输入调用 `run(...)`（产出 v3 `EngineRun`）时，wire 适配器抛出 `UnsupportedWireComponent`，engine 将其转换为明确的 fail-closed 错误，没有静默丢弃 dummy 状态。
  5. 断言旧类型没有被放宽：用 `axis="dummy"` 构造旧的 `GenerationTarget` 仍然抛 `ValueError`。
  6. 把 kernel 纯度规则写进 L0.4 建立的 `tools/architecture_policy.py`（G13、G14），**不另建扫描实现**：按 AST 作用域禁止 kernel 轴名字面量和 `.coordination`、`.rings`、`.torsions` 属性访问（无论 `Load`、`Store` 或 `Del`）；唯一字面量例外是 `wire_v3.py`、`wire_v3_constants.py` 与 `model.ConfgenStateKey` 类体，唯一属性访问例外是该类体、`ConfgenEngine.run` 方法体与 `wire_v3.py`。禁止 kernel 导入组件模块，但允许 A4d 指定的两个模块级 `__getattr__` 仅为三个继承 API 名按需导入 `torsion.inherited`；其他模块、作用域或名称均报错。G13 同时检查 `as_kernel_target` 只在 `kernel_records.py` 定义/导出。G14 禁止 `science/confgen/**` 模块顶层调用任何 `register`。每条规则都要附一个能触发它的反例及注释/docstring 正例（L0.4 的要求）。
- 允许修改：`tests/v4/confgen_dummy_component.py`（新）、`tests/v4/test_confgen_dummy_component.py`（新）、`tools/architecture_policy.py`（只新增规则）、policy 的规则反例文件
- **如果为了让 DummyComponent 跑通，必须修改任何非测试文件，本卡不得自行修改**：停止并报告缺口，由方案方补一张 A 卡。
- 验收：标准验收；golden 与 B1 相同。**FIX-1A 合并后的 main 作为 FIX-1R、FIX-1D、L1 的基线（检查点 `$CKPT/fix-1a/A6/`）。**

---

# FIX-1R：ring 正确性

**目标**：用 Cremer–Pople（CP）正则形式作为状态，用约束 realization 生成种子，审计平面性、角度和振幅，perception 与锚点无关。

**适用范围**：孤立 4/5/6 元环，与现在相同。稠环、桥环、螺环、螯合环、大环继续 fail-closed。

### F1 — ring 基准 fixture

- 类型：`test-only` ／ 前置：FIX-1A 已合并
- 在 `tests/fixtures/confgen/ring/` 下新增：
  - `rpdd/input_ts_fragment.xyz`：用户的 `rpdd.gjf` 坐标（22 原子，C2 被扭成四面体，`distorted_input` 的负例）。
  - `rpdd/crest_conformers.xyz`：用户的 CREST 结果，3 个构象，带能量。
  - `rpdd/README.md`：来源；原子编号（1-based：环 1-2-4-5-6-8，C1 为手性中心，C12 起为苯基）；期望结论（2 个环盆，3 个构象）；§0.3 的实测数字。
  - 环己烷、甲基环己烷的理想几何由测试辅助函数生成，不需要外部数据。
  - Q9 中用户提供的体系，到位一个加一个。
- 允许修改：`tests/fixtures/confgen/ring/**`
- 验收：标准验收；collect 数不变。

### R1 — CP 坐标与 38 个正则形式（纯数学，暂无调用方）

- 类型：`logic`（Behavior-Change: none，新模块不被引用）／ 前置：F1
- 新增 `ring/puckering.py`：
  - `cremer_pople(coords) -> CPCoords`：n=4 为 (q, 符号)；n=5 为 (q₂, φ₂)；n=6 为 (Q, θ, φ)。**符号约定固定如下**（方案作者用这套约定验证了 §0.3 的表和下文的变换公式）：以 `atoms[0]` 为 j=0；R′ = Σ c_j sin(2πj/n)，R″ = Σ c_j cos(2πj/n)（c_j 为去质心坐标），法向 = R′×R″ 归一化，z_j = c_j · 法向；q₂cos φ = √(2/n) Σ z_j cos(4πj/n)，q₂sin φ = −√(2/n) Σ z_j sin(4πj/n)，q₃ = √(1/n) Σ z_j (−1)^j（n 为偶数时）。反解析式为 z_j = √(2/n)·q₂·cos(φ + 4πj/n) + √(1/n)·q₃·(−1)^j。约定必须写成测试，避免出现 GPT 原型中"标签 φ=0、实测 φ=180"的混乱。
  - **遍历顺序变换（V26）**：用户写环原子时，循环移位和反向都是合法写法，物理状态必须相同。在上述约定下，方案作者用随机 CP 点验证了以下变换（锚点后移一位指 `atoms` 从 `[a0,a1,…]` 变为 `[a1,a2,…,a0]`；反向指锚点不变、顺序反转，即 `[a0,a(n-1),…,a1]`）：
    ```text
    6 元环  锚点后移一位：θ' = 180° − θ，  φ' = φ + 120°
    6 元环  反向遍历：    θ' = 180° − θ，  φ' = 180° − φ
    5 元环  锚点后移一位：               φ' = φ + 144°
    5 元环  反向遍历：                   φ' = 180° − φ
    ```
    `canonical_forms` 必须提供 `relabel(form, shift, reverse) -> form`，按上式把一个正则形式映射成另一种写法下的对应形式（族不变，序号按公式变换），并写进测试。
  - `canonical_forms(n) -> tuple[CanonicalForm, ...]`：
    - n=4：`P`（平面）、`B+`、`B−`（蝶式），共 3 个；
    - n=5：`E_k`（10 个：每个原子作 flap，向上或向下）、`T_k`（10 个），共 20 个；
    - n=6：`C`（2 个，θ=0°/180°）、`B`（6 个，θ=90°）、`TB`（6 个，θ=90°）、`E`（**12 个**：每个原子作 flap，向上或向下；θ≈54.7° 或 125.3°）、`H`（**12 个**：每根键作扭曲对，向上或向下；θ≈50.8° 或 129.2°），**共 38 个**。
    - 每个形式记录 `(family, index, cp_target, zero_torsion_bonds)`，其中 `zero_torsion_bonds` 是该形式中扭转角为 0 的环键。R4 的受约束形式规则要用它。
    - **CP 目标表是权威来源（V18）**，直接按 §0.3 的表格定义：B 为 θ=90°、φ=60°·k；TB 为 θ=90°、φ=30°+60°·k；E 为 θ∈{54.7°, 125.3°}、φ=60°·k；H 为 θ∈{50.8°, 129.2°}、φ=30°+60°·k；C 为 θ∈{0°, 180°}。θ 值取 Boeyens 正则值，来源写进注释。5 元环同理：E 为 φ=36°·k，T 为 φ=18°+36°·k。
    - `zero_torsion_bonds` **由 CP 反解析式计算**（`z_j = √(2/n)·q₂·cos(φ + 4πj/n) + √(1/n)·q₃·(−1)^j`，平面投影取正 n 边形），不得手工填写，也不得从任何笛卡尔模板读取。
  - `cp_to_coords(cp, n, r̄)`：用上述反解析式构造理想几何，只用于测试和作为 R3 的可选起点。
  - `cp_distance(a, b)`：n=6 为球面角距离，n=5 为相位圆周距离。
- 测试（方向是"CP 表 → 构造几何 → 反算 CP → 必须恢复目标"，不允许反过来）：
  - n=6 正则形式恰好 38 个，各族数量 2/6/6/12/12；n=5 恰好 20 个；
  - 对每个正则形式：`cremer_pople(cp_to_coords(form.cp_target))` 恢复目标，误差 < 0.5°；
  - 对每个正则形式：构造几何的实测零扭转键（|τ| < 3°）与 `zero_torsion_bonds` 一致；与 §0.3 的三个示例逐字一致（E θ=54.7° φ=0° → 2-3、3-4；H θ=50.8° φ=30° → 3-4；B θ=90° φ=0° → 1-2、4-5）；
  - 各族的对称性符合 §0.3 表中的"对称性来源"一列（例如 E 关于过 flap 原子的平面镜像对称）；
  - 对 B、TB、E 的构造几何循环移动锚点：族不变，相位序号按预期平移（P5 的回归测试）。
  - **反向遍历协变（V26）**：对全部 38 个 6 元环形式和 20 个 5 元环形式，把构造几何的原子顺序反转后重新计算 CP，结果必须等于 `relabel(form, reverse=True)` 的目标，误差 < 0.5°；循环移位同理，覆盖所有移位量。
- 允许修改：`science/confgen/ring/puckering.py`（新）、`tests/v4/test_confgen_ring_puckering.py`（新）
- 验收：标准验收；golden 不变。

### R2 — 刚性平面单元与局部定向锁（基于拓扑，几何只做交叉验证）

- 类型：`logic`（Behavior-Change: none，新模块不被引用）／ 前置：R1
- 新增 `ring/rigid_units.py`：
  - 基于 typed graph 和价态识别环内的平面单元：酯（C(=O)–O）、酰胺（C(=O)–N）、烯烃（C=C）、亚胺、芳香稠合边、sp² 环原子。依据是元素、邻居数和推断的键级（GJF/XYZ 没有键级，价态不饱和时用距离辅助判断，阈值写进代码并测试）。
  - 每个单元输出：涉及的环原子、它钉住的环键（扭转角 0° 或 180°）、中心原子是否刚性。
  - **局部定向锁 `LocalOrientationLock`**（供 R3 的定向审计使用；V16）。本卡**不做**通用立体中心识别（CH₂、C(CH₃)₂XY 有四根键但不是立体中心；通用 R/S 留给以后的 stereochemistry 组件）。只保护 ring realization 本身可能破坏的东西：环原子 c 处，由"环前邻 p、环后邻 n、每个外部取代基根原子 s"构成的局部定向，即符号体积 V(c; p, n, s) 的正负号。对环原子 c，满足以下全部条件时才生成锁：
    1. c 在拓扑上不是 sp²（不是平面单元的中心）；
    2. c 至少有一个外部取代基；
    3. 输入几何中 |V| 明显非零（阈值写进代码并测试，例如 0.3 Å³）；
    4. p、n、s 在拓扑上明确（没有歧义的连接关系）。
    锁的含义是"这个局部定向在种子中不得翻转"，**不声称 c 是立体中心**，也不进入 StateKey。CH₂ 这类非立体中心也会被加锁，但这是无害的：两个 H 的定向互换得到的是同一个结构，而 ring realization 本来就不应该让取代基穿过环平面，锁住它们只是多一道检查。
    - **拓扑判定为 sp² 的中心，无论几何上是否金字塔化，都不生成定向锁，只做平面性审计**（V2）。
  - 几何交叉验证：拓扑判为 sp² 的中心，输入几何中键角和应 ≥ 350°。不一致时输出 `distorted_input` 记录（原子、实测值、期望），**不改变拓扑判定**。
- 测试：
  - rpdd 截取片段：C2 判为 sp² 酯碳，**不生成**定向锁；报 `distorted_input`（键角和 339°）；C1（连着苯基和 H）生成定向锁；C6 正常。
  - rpdd CREST1：两个酯都被识别，钉住 C2–O4 和 C6–O8 两根环键；无 `distorted_input`。
  - 环己烯、δ-戊内酯、吗啉（无平面单元）。
- 允许修改：`science/confgen/ring/rigid_units.py`（新）、`tests/v4/test_confgen_ring_rigid_units.py`（新）
- 验收：标准验收；golden 不变。

### R3 — 约束 realization

- 类型：`logic`（Behavior-Change: none，新函数暂不被 stage 调用，R4 切换）／ 前置：R2
- 在 `ring/realization.py` 中新增 `realize_cp_target(coords, elements, adjacency, spec, form, *, rigid_units, tolerances) -> (coords, state, audit)`，旧的 `realize_single_system` 暂不删除：
  1. **变量**：只有环原子的坐标（3n 个）。
  2. **约束**（加权最小二乘，SciPy `least_squares`，`trf`）：
     - 环键长锚定**输入值**，σ=0.01 Å；
     - 环内 1-3 距离锚定输入值，σ：刚性中心 0.02 Å，其余 0.15 Å；
     - CP 方向对准目标，σ=0.02；**Q 不锚定某个固定值**，但加一个弱的下限项：Q / r̄ < `phase_defined_q_min`（Q13）时产生罚项，防止求解器往平环方向漂移；
     - `rigid_units` 钉住的环键，扭转角趋向 0°/180°，σ=7.5°；
     - 环质心不动，σ=0.05 Å。
  3. **多起点**：输入几何；输入几何沿环平均平面的镜像；（R4 中由 stage 提供）相邻正则形式已求出的解。按顺序尝试，取第一个通过审计的；全部失败时报告残差最小的起点的审计结果。
  4. **取代基**：沿用 local-frame 刚性跟随（`ring/realization.py:366-383` 的逻辑，抽成函数复用）。
  5. **审计**（全部写进 audit；任何一项失败即 `RingGeometryFailure`，reason 写明是哪一项）：
     - `ring_bond_drift` ≤ 0.02 Å（相对输入）；
     - `ring_angle_drift`：刚性中心 ≤ 3°，其余 ≤ 12°；
     - `conjugation_planarity`：每根被钉住的环键偏离 0°/180° ≤ 15°；
     - `puckering_amplitude`：**Q / r̄ ≥ `phase_defined_q_min`**（V3、V17；平面形式 `P` 除外）。这是"θ、φ 能否稳定定义"的数值门槛，不是物理判据；
     - `cp_reached`：实际 CP 与目标的 `cp_distance` ≤ 15°（只在振幅审计通过时有意义，所以排在它之后）；
     - `local_orientation`：沿用符号体积检查（`ring/realization.py:408-426`），**只对 R2 生成的 `LocalOrientationLock`**（V2、V16）；
     - clash：沿用现有的全结构 clash（K0 决定是否改）。
  6. `phase_defined_q_min` 按 Q13 取值，**在本卡中冻结**，写进 `ConfgenTolerances` 并注明来源和含义。之后的卡（包括 R7）不得修改它；如果 R7 的验证表明它不合理，R7 判失败，另开一张 `science` 卡修改（V17）。
- 测试：
  - rpdd CREST1 为输入：`B` 中两根零扭转键与两个酯键重合的那两个形式成功；`C`、`TB` 因 `conjugation_planarity` 失败。
  - 环己烷：2 C + 6 B + 6 TB 全部成功，振幅审计均通过。
  - 退化测试：构造一个近平面输入，目标为 TB，检查求解结果要么 Q / r̄ ≥ `phase_defined_q_min`，要么以 `puckering_amplitude` 失败，**不得**出现振幅低于门槛而 `cp_reached` 通过。
  - rpdd 截取片段：C2 不参与定向锁；所有种子都能生成或以明确的审计项失败；audit 和 state 中都不出现任何 C2 的立体或定向状态。
- 允许修改：`science/confgen/ring/realization.py`、`science/confgen/ring/geometry.py`（抽出的辅助函数）、`science/confgen/tolerances.py`、`tests/v4/test_confgen_ring_v2_realization.py`（新）
- 验收：标准验收；golden 不变。

### K0 — 诊断：组合轴下是否真的存在 clash 误杀

- 类型：`test-only` ／ 前置：R3
- 目标（P10、V11）：在接入 R4 之前，判断"ring 阶段的全结构 clash 误杀后续 torsion 能解开的结构"是否在真实体系上发生。
- 步骤：
  1. 用 rpdd CREST1，构造 R×T：ring 用 R3 的新 realization，对两个 B 形式各生成一个种子；torsion 声明 C1–C12 苯基转子，角度 0/60/120/180/240/300°。
  2. 分别记录：ring 阶段因 clash 被拒的种子数；在 ring 阶段关闭 clash、只在叶节点检查时，被拒的种子中有多少在某个苯基角度下 clash 消失。
  3. 对甲基环己烷（甲基转子）和一个邻位取代苯基环己烷（构造几何）做同样的统计。
- 输出：`docs/confgen-fix/checkpoints/K0/REPORT.md`（报告本身很小，提交进仓库）。
- **判定**：只要有任何一个种子"ring 阶段被拒、叶节点可以通过"，就插入 K1、K2，并且必须在 R4 之前完成；否则 K1、K2 移到 FIX-2，R4 照常进行。
- 允许修改：`tests/v4/test_confgen_k0_clash_diagnostic.py`（新，可以标记为 `slow`）、`docs/confgen-fix/checkpoints/K0/**`

### K1 — （条件卡）按"后续自由度能否改变距离"推迟 clash

- 类型：`science` ／ 前置：K0 判定需要
- 目标：
  1. `GenerationStage` 新增钩子 `potentially_moved_atoms(context) -> PotentialMotion`，在任何 target 枚举之前给出上界（V8）。`PotentialMotion` 不只是原子集合，还描述**哪些原子对的距离可能被改变**：torsion 给出每个轴的两侧（左侧原子集合、右侧原子集合）；coordination 给出可动的刚性片段划分；ring 给出环原子及其取代基片段。
  2. kernel 在调用组件 realization 前，在 `MolecularContext` 中提供 `deferrable_pairs` 判定：原子对 (i, j) 可以推迟，**当且仅当**某个后续组件的 `PotentialMotion` 能改变 d(i, j)：i、j 分处某个后续 torsion 轴的两侧，或分属后续 coordination 的不同可动片段。
  3. 组件内部的 clash 审计跳过可推迟的原子对，其余全部当场检查。这意味着：
     - 当前组件内部、且后续不会改变距离的原子对：当场检查（满足 V9 的目标）；
     - 当前移动的原子与固定原子：除非后续自由度能改变它们的距离，否则当场检查；
     - 同一个后续刚性片段内部的原子对：距离不会被后续改变，当场检查；
     - 环上的苯基与环上的 H，苯基之后会被转开：推迟（修复 P10）。
- 预期行为变化：只有组合轴受影响；单组件运行时没有后续组件，结果必须不变。
- Golden-Changed：开工前由一张 `baseline` 前置步骤列出所有组合轴报告（`test_confgen_v3_combination.py` 捕获的部分），只允许 `failed_geometry → 其他状态` 方向的变化。
- Golden-Unchanged-Asserted：所有单组件报告、TS1 三份、contract、boundary。
- 允许修改：`science/confgen/model.py`、`science/confgen/engine.py`、三个组件的 `stage.py`/`realization.py`、相关测试

### K2 — （条件卡）kernel 在叶节点统一做全局 clash 审计

- 类型：`science` ／ 前置：K1
- 目标：叶节点发布前，kernel 对完整结构做一次全局 clash 审计（阈值复用 `ConfgenTolerances.clash_threshold`）。失败时终态为 `failed_geometry`，reason 为 `leaf_clash`，evidence 中记录原子对及其被推迟的来源组件。
- Golden：同 K1。
- 新增测试：K0 中"ring 阶段被拒、叶节点可以通过"的例子，K2 后为 published；一个怎么转都 clash 的例子，以 `leaf_clash` 终止。

### R4 — 枚举与 perception 切换到正则形式；新 StateKey 载荷

- 类型：`science` ／ 前置：R3（以及 K1/K2，若 K0 判定需要）／ Q1–Q4
- 步骤：
  1. **枚举**（`ring/stage.py` 的 `enumerate_targets`/`_options` L135 起）：默认集合按 Q1/Q2。spec 中新增 `forms: [...]` 用于显式指定，语法为：族名（如 `"B"`、`"TB"`、`"E"`）是族选择器，展开为该族全部形式；带序号的名字（如 `"B_2"`）是精确选择器，只取该形式；未知名字 fail-closed（V20）。旧字段 `templates: [...]` 按 Q3 的别名表映射。枚举仍然是纯符号的，不做几何。不可行的形式由 realization 审计以 `failed_geometry` 终止并写明原因，从而保证记账诚实，不会被静默丢弃。
  2. **受约束形式**：R2 给出被钉住的环键集合 Z 时，把满足 `Z ⊆ form.zero_torsion_bonds` 的正则形式（R1 中已记录）加入枚举。若没有任何形式能覆盖整个 Z，则加入覆盖 Z 中键数最多的那些形式。示例：
     - 交酯（Z = 两根相对的酯键）→ 恰好覆盖它的是 2 个 B；
     - 环己烯（Z = 双键）→ 覆盖它的有 H 和 E 中的若干个；
     - 饱和环（Z 为空）→ 不加入任何形式，只用默认集合。
  3. **perception**（`ring/perception.py` 重写）：计算 CP；Q / r̄ < `phase_defined_q_min` 时判为 `flat`，confidence 为 ambiguous（除非 spec 显式声明了 `P`）；否则找 `cp_distance` 最近的正则形式。match/margin/reject 三档语义保留，数值改为 CP 角距离（初值 15°/8°/35°，写进 `ConfgenTolerances`）。**与锚点无关**：移动锚点只改变 `index`，不改变 `family`，也不产生 ambiguous。
  4. **StateKey**：ring 载荷改为 `{"form": family, "index": k, "anchor": a, "direction": d}`（Q4）；实测 CP 写进 diagnostics。v3 wire 适配器同步更新 ring 段的载荷格式。
  5. `verify_locked`（`ring/stage.py:476`）改为 CP 距离判定。
  6. `RingStage.BACKEND_NAME`（L72）改为 `"cp-constrained-v2"`。
- Golden-Changed（开工前由 `baseline` 前置步骤生成精确清单，交验收方确认）：所有含 ring 轴的 engine 报告；contract（新增 `ring_forms_by_size`；`ring_templates_by_size` 改为别名表，键名保留）。
- Golden-Unchanged-Asserted：TS1 三份（无环）；torsion 单轴报告；coordination 单轴报告。
- 旧测试中对模板名、模板扭转角的断言，按 Q3 的别名改写；**不得删除测试**（G3）。每一处改写在 `DIFF.md` 中列出改写前后。
- 新增测试（验收必过）：
  - **rpdd CREST1 输入**（V4）：
    - 环层面召回：CREST1、CREST2、CREST3 的环 CP，每个都与某个 published 种子的距离 < 15°（即两个 B 盆都被覆盖）；
    - 每个 published 种子：两个酯 |τ| < 15°，环角漂移在 R3 阈值内，振幅审计通过，C1 的局部定向不变；
    - **不要求 published 数量恰好为 2**。额外的种子允许存在，但必须满足上一条。
  - 环己烷（默认集合）：2 C + 6 TB 全部 published；显式 `forms: ["B"]`（族选择器）时 6 个 B 全部 published；`forms: ["B_2"]`（精确选择器）时只枚举 `B_2`。
  - 环己烯：受约束形式规则加入了 H，至少一个 H 被 published。
  - 锚点与方向不变性：同一结构，把 `atoms` 循环移位、或反向书写后重跑，published 种子在 `relabel` 映射回原写法后，(family, index) 集合与原写法完全相同；对应种子的坐标 RMSD < 0.01 Å。
  - rpdd 截取片段：所有种子中 C2 都不出现立体状态；报告中有 `distorted_input`（R5 接入前，先断言 `rigid_units` 层面的记录）。
- 允许修改：`science/confgen/ring/**`、`science/confgen/wire_v3.py`、`science/confgen/tolerances.py`、`workflow/v4/confgen_schema.py`（`forms` 字段与别名）、`producer/contract.py`（经由 `contract_options`）、`tests/v4/test_confgen_v3_*.py`（只按 Q3 改写）、新测试、`docs/confgen-fix/checkpoints/R4/`（manifest 与 DIFF.md）
- 验收：§2.4。JD 是否需要配对：Q3 保证旧键不变，新增键 JD 应当忽略；执行前核实 JD contract 解析器对未知键的行为（Q8），若会拒绝则先做 J 侧前置。

### R5 — `distorted_input` 进入报告

- 类型：`science` ／ 前置：R4 ／ Q6
- 目标：R2 的 `distorted_input` 记录写进 ring perception 的 diagnostics、engine 报告的 ring 段、以及 ensemble report 的 warnings。不改变任何终态。
- Golden-Changed：开工前用 R2 扫描所有 ring golden 的输入，列出会出现 `distorted_input` 的报告（预期为空或很少）。
- Golden-Unchanged-Asserted：其余所有报告。
- 允许修改：`science/confgen/ring/stage.py`、`science/confgen/ring/perception.py`、`execution/confgen_executor.py`（warnings 透传）、相关测试

### R6 — 删除旧模板实现

- 类型：`delete` ／ 前置：R5
- 删除：`ring/templates.py` 中的笛卡尔模板构造（`_planar_4` … `_twist_boat_6`、坐标注册表、`template_coords`、`template_bond_spread`）；`ring/realization.py` 中旧的 `realize_single_system` 与 `ring_bond_atol`；只被它们使用的 `geometry.py` 函数（`reachability.py` 确认）。Q3 的别名表移到 `ring/forms.py`。
- 允许修改：`science/confgen/ring/**`、只测旧实现的测试（逐条声明）
- 验收：标准验收；golden 与 R5 检查点相同。

### R7 — 基准工具与验证报告（只验证，不调参）

- 类型：`baseline` ＋ `test-only` ／ 前置：R6 ／ Q9
- 目标：
  1. `$FIX/tools/ring_benchmark.py`：对 F1 中的每个体系跑 ring 组件，输出种子 xyz，以及每个种子的 (family, index, Q, θ, φ, 平面性, 角度漂移) 和与参考构象集（CREST）的 CP 匹配表。
  2. **`phase_defined_q_min` 验证（不调参，V17）**：统计全部参考构象（CREST）的 Q / r̄ 分布与全部 published 种子的 Q / r̄ 分布，写进报告。判定：所有参考构象都必须高于门槛，且没有任何 published 种子是"振幅刚过门槛、但明显是平面化失败"的情况。任一条不满足，**R7 判失败**，在 LOG 中记录，由方案方另开 `science` 卡修改门槛；本卡不得修改任何常量。
  3. 召回率与冗余率需要对种子做 xTB 优化，再与 CREST 构象做 RMSD 匹配。xTB 不在本仓库中，也不能安装（G10）：工具输出种子文件和一个 `match_after_opt.py`，由用户在本地优化后计算。
  4. 仓库内的测试只断言 CP 层面的环召回：每个 CREST 构象的环 CP，与某个 published 种子的距离 < 15°。
  - **2026-10-06 修订（用户决定）**：葡萄糖不要求 strict CP recall 达到 100%。`< 15°` 的 strict CP 召回保留为严格诊断指标，并列输出 basin recall（参考构象归属的最近正则形式是否有已发布种子）与 miss 分类；记录值：strict 127/155、basin 155/155。是否需要新增 ring seed，由 published 种子优化后的 CP 召回（D 诊断）决定；含氧六元环默认形式集加 B（A）另开卡评估、本缺口不以其关闭；畸变椅种子（C）在缺少稳定畸变盆能量证据前冻结。这些并列指标只进入 benchmark 工具输出，不进入 engine 报告，不改 golden。
- 允许修改：`docs/confgen-fix/tools/**`、`tests/v4/test_confgen_ring_benchmark.py`（新）
- 验收：rpdd、环己烷、甲基环己烷的环层面召回 100%；其余体系按 Q9 数据到位情况补充（葡萄糖按上方 2026-10-06 修订：strict 与 basin 并列报告，不要求 strict 100%）。
- 沉淀：β-D-吡喃葡萄糖缺口诊断见 `docs/confgen-fix/R7-GLUCOSE-RECALL.md`，脚本归档于 `docs/confgen-fix/tools/r7_glucose/`。

---

# FIX-1D：coordination 多起点

**不变量（每张卡都适用）**：已经 REALIZED 的 target，结果必须逐字节不变；TS1 的 REALIZED 数只能增加。

### D1 — 有完整原子 witness 时，用 σ-像作为起点

- 类型：`science` ／ 前置：FIX-1A 已合并 ／ Q10
- 背景（P12、V12）：`realize_target`/`realize_flexible`（`coordination/realization.py:316、562`）对每个 target 只从输入几何出发一次。仓库中已有完整原子 witness 的校验 `validate_full_witness`（`coordination/symmetry.py`），它检查拓扑、键级、位点作用闭合、反应边和片段角色，并明确规定只有位点置换、没有完整 witness 支撑的一律 fail-closed。
- 目标：
  1. 只有当一个**完整原子置换** π 通过 `validate_full_witness`，且 π 在结合位点上诱导的作用把 target T 映射到 T′ 时，才允许把"T 的 REALIZED 坐标按 π 置换"作为 T′ 的起点。
  2. 置换后的坐标**只作为起点**，仍要走完整的柔性求解和全部审计，不能直接当结果。原因是 `validate_full_witness` 给出的 `stereo_status` 和 `geometric_status` 都可能是 UNVERIFIED。
  3. 没有通过校验的完整 witness 时，D1 对该体系什么都不做（记录 `sigma_image: skipped (no full-atom witness)`），直接进入 D2。
  4. 执行顺序必须确定：先按原顺序跑一遍只用输入起点的求解，再对未 REALIZED 的 target 按序号尝试 σ-像起点。
  5. provenance：**第一遍只用输入起点就已 REALIZED 的 target，记录完全保留旧格式，不增加任何字段**（V27），以保证"已 REALIZED 的逐字节不变"。只有重试成功的 target，provenance 中新增 `retry_start: "sigma_image:<target_id>" | "sibling:<target_id>"`。全局的 `start_statistics` 由 D3 统一加入（D3 已声明报告格式变化）。
- **执行前核实**：TS1 fixture 中是否存在能通过 `validate_full_witness` 的完整原子 witness（fixture 中的 σ 是位点级的还是完整原子的）。若不存在，D1 在 TS1 上什么都不做；本卡仍然实现，并用一个构造的、确实具有完整对称的小配合物测试。
- Golden-Changed：TS1 三份（只允许 REALIZED 数增加；已 REALIZED 的逐字节不变）；若 TS1 没有完整 witness，则 TS1 不变，Golden-Changed 只有新增测试。
- Golden-Unchanged-Asserted：所有非 coordination 报告。
- 允许修改：`science/confgen/coordination/**`、相关测试

### D2 — 已 REALIZED 的兄弟结构作为起点

- 类型：`science` ／ 前置：D1
- 目标：D1 之后仍未 REALIZED 的 target，依次用已 REALIZED 的结构（按 target 序号，最多 3 个，Q10）作为起点，走完整求解和审计。这条路径不依赖任何对称性假设，最终由 target 自己的审计决定是否接受。
- Golden：同 D1 的不变量。

### D3 — 统计与报告

- 类型：`science`（V19：报告格式改变，结构不变）／ 前置：D2
- Golden-Changed：TS1 三份 coordination 报告（只新增 `start_statistics` 段；结构、终态和其余字段逐字节不变）。Golden-Unchanged-Asserted：所有非 coordination 报告。
- 目标：engine 报告的 coordination 段新增 `start_statistics`（每种起点的尝试次数与成功次数）。把 TS1 最终的 REALIZED 数写进 `$FIX/LOG.md`，并与此前 spike（SCINE 初始化 12/12）对比；明显低于 12 时记录下来，作为是否推进 ConstraintCompiler 的依据（§11）。

---

# 之后排期：E、J、G

### E1 — "预优化 → ConfGen" recipe

- 类型：`logic` ／ 前置：FIX-1R 已合并
- 背景（P23）：仓库中没有 xTB 适配器；ORCA 支持 `XTB2` 方法关键字（调用外部 xtb 可执行文件）。
- 步骤：
  1. **执行前核实**：ORCA calculation card 能否用 `XTB2` 做 Opt；`producer/recipes.py` 中 recipe 的声明格式。任一不满足则停止报告（G9），由方案方决定是否新增 xTB 适配器（§11）。
  2. 新增 recipe `monomer_conformers`：`ORCA XTB2 Opt → ConfGen（ring + torsion）→ 去重`。

### E2 — contract 标注 confgen 不支持 freeze

- 类型：`logic` ／ 前置：FIX-1A 已合并
- 目标（P21 后端部分）：contract 中给出每个 executor 支持的全局选项，confgen 的 `freeze` 标为不支持，供 JD 置灰。后端 fail-closed 行为不变。contract、boundary 变化，需要 JD 配对（J2）。

### J1–J3 — JobDesk（前置 Q8）

方案编写时无法访问 JobDesk，以下只写目标和接口，获得访问后再补锚点和白名单。

- **J1** GJF/INP 输入驱动 atom picker（P20）：JD 不自己解析 GJF/INP。ConfFlow producer 新增 authoring 请求 `structure_preview`，返回 `{elements, coordinates, index_base, source_format, warnings}`，使用与 ConfFlow 运行时相同的解析器，从而保证原子编号只有一个来源。ConfFlow 侧单独一张卡，与 J1 成对提交。
- **J2** confgen 下置灰 freeze（按 E2 的 contract 字段）。
- **J3** ring 表单使用 `ring_forms_by_size`，旧 `templates` 按别名显示。

### G1 — 能力边界文档

- 类型：`doc` ／ 前置：FIX-1R、FIX-1D 已合并
- 新增 `docs/CONFGEN_CAPABILITIES.md`，按"支持 / 有限支持 / 不支持（fail-closed）"三栏列出：
  - ring：孤立 4–6 元环，CP 正则形式（6 元环 38 个、5 元环 20 个）；稠环、桥环、螺环、螯合环、大环不支持。种子的局部几何（键长、杂化）继承自输入，所以推荐使用局部几何可信的输入；`distorted_input` 是诊断，不等于错误：TS、中间体、约束优化结构中的真实畸变是正常的，会被如实保留。
  - coordination：单金属；多金属、haptic 不支持；realization 成功率按 D3 的统计写明。
  - torsion：paths 仅端点模式；waypoint、自动转子识别不支持；对称转子会产生冗余（FIX-2）。
  - sampling：精确的条件枚举支持；条件树 + 全局 cap 会 fail-closed（有意为之）。
  - 对称抑制：只支持单轴。
  - checkpoint：Gaussian standard（`%OldChk`）、Opt（`ReadFC`）、IRC（`RCFC`）支持；ORCA、QST2/QST3 不支持。
  - freeze：Calculation 支持，ConfGen 不支持。
- 同步更新 `docs/plans/confgen-v3-validation.md` 中关于 ring 可用范围的结论。

---

# 后续里程碑大纲：L1、L2、L3

> 以下只写目标、边界和验收口径。每个里程碑开工前，在当时的 main 上核实锚点，再展开成卡片并单独评审。

### L1 — producer 按 capability 拆分

- 前置：FIX-1A 已合并（A5 会改 `producer/contract.py`）。
- 目标（V34、P30）：
  1. `producer/intent.py`（2001 行）拆为 `producer/intent/{compiler,bindings,resources}.py` 与 `producer/intent/capabilities/{calculation,confgen,transform,analysis}.py`；compiler 只做通用流程，按 step 的 capability 分派给 handler（`handler = intent_registry.resolve(step.role)`）。
  2. capability 描述符尽量与 `execution.registry` 共用同一份：同一个描述符同时提供 runtime executor、workflow contract、intent handler、authoring 元数据。`producer/authoring.py`（1285 行）改为这份描述符的投影，不再维护自己的一套 step/binding 知识。
  3. `producer/checkpoints.py` 中的 Gaussian 规则（`_check_gaussian_sides`、`_check_write_chk`、`_add_opt_option`、`_add_irc_rcfc` 等）下沉为 `programs/gaussian/checkpoint_policy.py`；producer 只调用 `program.checkpoint_policy.apply(...)`。
- 约束：行为 100% 不变；intent 编译产物、contract、boundary 字节不变；所有科学规则不得放在 intent 层（这条写进 L0.4 的 policy：intent 层不得 import `confflow.science.*`）。
- 验收口径：与 A6 对应，用一个 `DummyCapability` 证明"新增一种 capability 只需新增描述符和 handler，中央文件改动 ≤ 1"。

### L2 — 跨仓退休旧 service 兼容链

- 前置：L1 已合并；JobDesk 仓库可访问（Q8）。
- 目标（V35、P31）：`workflow_adapter.py` 中的 `_load_artifacts_v1`、`_load_artifacts_v2`、`_load_artifacts`、`_load_stats`，以及找不到 `run_result.json` 时回退到 `workflow_stats.json`、`.workflow_state.json`、`output_manifest.json` 的读取链；`confflow/contract.py` 发布的 `RUN_SUMMARY_SCHEMA`、`WORKFLOW_STATS_SCHEMA(_V2)`、`WORKFLOW_STATE_SCHEMA(_V2)`、`OUTPUT_MANIFEST_SCHEMA(_V2)` 等。`service` 直接调用带类型的 V4 runner，删除 `formal_v4_runner(**kwargs)` 对旧签名的模拟。
- 顺序：① 在 JobDesk 中确认当前路径不再消费旧 capability artifact 字段；② contract 切换为只描述 V4 原生产物；③ 删除 ConfFlow 的旧读取链；④ 删除相关测试。①② 与 JobDesk 成对提交。
- 验收口径：已完成的 V4 run 只经 `run_result.json`、`run_generation.json` 读取；两仓跨仓测试通过。

### L3 — 收尾

- 前置：FIX-1R、FIX-1D、L1、L2 全部合并。
- 目标：
  1. **TS1 原始日志外移**（V31）：`tests/fixtures/confgen/coordination/ts1/source/si-rr-salanal2-r-end-spdd-ts1.log`（4.58 MB）移到 release asset 或独立存储；`MANIFEST.json` 中该条目改为记录 sha256 与外部位置；`benchmark/verify_fixture.py` 对外部条目改为"存在则校验，不存在则跳过并说明"。TS1 的 xyz、拓扑、benchmark 文件不动。
  2. **测试按行为重组**（V36、P32）：先做断言清单（完全重复、同一 bug 的多轮回归、只为覆盖率存在、只守护已删除对象、独有的 crash/race/security 用例），再把 `test_v42_*` … `test_v46_*` 等按行为重组到 `test_execution/`、`test_persistence/`、`test_remote/`、`test_analysis/`、`test_confgen/`、`test_producer/`。独有的 crash/race/security 用例必须保留。节点 ID 会整体变化，所以验收要提供"旧节点 ID → 新节点 ID 或删除理由"的完整映射表。
  3. **可选的目录搬迁**（V37）：`science/confgen/` 改为 `kernel/` + `components/`；旧模块路径保留兼容导出。
  4. `core/elements.canonicalize_element_symbol` 与 `domain.elements.canonical_element_symbol` 的合并（L0.5 第 4 步留下的项），需要先确认错误信息差异是否有外部依赖。

---

## 11. 移交 FIX-2（本轮不做）

| 项 | 说明 |
|---|---|
| 对称转子（原 K3） | V10。必须复用 `coordination/symmetry.py` 已有的 `search_automorphisms` 与 witness 校验，作为唯一的对称证明来源，不得另造一套；开工前先生成 golden 清单 |
| K1/K2 | 仅当 K0 判定不需要时移到这里 |
| ConfGen wire 格式改为 `components: {...}` | Q5；FIX-1A 之后再评估 |
| 稠环、桥环、螺环、大环 | 需要联合 CP 或运动学闭环；大环交给下游 CREST/GOAT |
| 螯合环 | 与 coordination 组件重叠，需要两个组件共同声明原子所有权 |
| ConstraintCompiler / Molassembler 初始化 | 视 D3 的统计结果决定 |
| xTB 程序适配器 | 视 E1 的核实结果决定 |
| waypoint path、自动转子识别 | 合理限制 |
| 条件树加权采样 | 保持 fail-closed |
| 多轴联合对称抑制 | 保持保守，宁可多算 |
| ORCA checkpoint 复用 | 需要先定义 ORCA 侧的输入词汇 |
| `core/` 改名为 `infrastructure/`；`application/execution` 改名为 `application/control_runtime` | 只是命名，不影响局部性 |
| stereochemistry、reactive_face、开环位点等新组件 | 在 FIX-1A 之后按组件接口新增 |

---

## 12. 风险登记

| 风险 | 涉及卡 | 缓解 |
|---|---|---|
| 归档 `docs/refactor/` 时漏掉仍在使用的文件 | L0.1–L0.3 | L0.1、L0.2 先搬工具和 golden；L0.3 删除前逐条审阅实际运行引用，历史评审说明、归档来源和本 PLAN 的明确历史引用可保留；`confflow/`、`tests/`、`scripts/`、`.github/` 中不得保留运行依赖 |
| 合并架构守卫时规则被悄悄削弱 | L0.4 | 先出规则清单并确认；每条规则附反例；验收方随机抽查 10 条做人工违反 |
| 元素表合并后 `domain` 开始依赖 `science` 或 `core` | L0.5 | 方向固定为 domain 持有来源、science 和 core 转发；验收检查 `import confflow.domain` 不加载 `confflow.core` 或 `confflow.science` |
| A2 分层后 StateKey digest 改变 | A2 | `to_dict` 字节不变是硬性验收条件；B1 全部报告做往返测试 |
| `AXIS_ORDER` 的外部引用方 | A2 | 本轮保留兼容导出（V14），值与类型不变 |
| 某一层漏传 registry，自行取默认值，导致自定义组件在中途丢失 | A4、A6 | 只有公开入口可以省略 registry；内部函数为无默认值的仅关键字参数；默认值只经 `resolve_registry` 取得；A4 的实例一致性测试 |
| 组件描述模块把整套运行时连带加载 | A5 | 组件包 `__init__.py` 改为懒导出；A5 的 import 纯度测试 |
| 旧记录类型的构造校验被改变 | A2 | 旧类型一字不改；A2 测试断言非法轴名仍立即抛错 |
| `project_v3` 漏转换报告中某个由 state key 派生的字段 | A2 | 执行前逐个核对 `report_json()` 的字段来源；`run()` 输出与 B1 逐字节比较 |
| 继承状态搬进组件时，ring/coordination 被顺手加上原本没有的检查 | A4d | 只实现现有检查；现在没有检查的写明"总是通过，这是现状" |
| 数据类不再自行校验轴名后，非法轴名漏到 engine 之外 | A2 | 执行前确认这些数据类只在 engine 与测试中构造 |
| A4 搬运 normalization 时改变校验顺序或错误信息 | A4 | `move` 标准；错误信息逐字不变；golden 中含失败用例 |
| `contribute_topology` 改变 typed graph 中边的顺序，进而改变 digest | A4 | golden 逐字节比对；必要时在 kernel 中对边排序，但必须与现有顺序一致 |
| R3 求解器收敛到局部解，把可行形式误判为不可行 | R3、R4 | 多起点；failed_geometry 写明审计项；R7 召回率 |
| `phase_defined_q_min` 过高，误杀合法的平坦构象（如 CREST3，Q / r̄ ≈ 0.22） | R3、R7 | Q13 取 0.05，远低于已知参考值；R7 只验证，不合理时判失败并另开 `science` 卡 |
| E/H 的相位约定与文献不一致 | R1 | 用理想几何实测并写进测试 |
| R4 受约束形式规则在多个平面单元互相冲突时产生空集 | R4 | 退化为"覆盖最多键的形式"；空集时只用默认集合，并在报告中记录 |
| R4 改写旧测试时混入断言放宽 | R4 | 验收方逐条核对 `DIFF.md` |
| D1 中 σ-像把错误的原子映射当成合法起点 | D1 | 只接受通过 `validate_full_witness` 的完整原子置换；结果仍走完整审计 |
| contract 变更与 JD 不兼容 | R4、E2 | Q3 保留旧键；执行前核实 JD 对未知键的行为 |
