# 使用中发现问题的修复方案（2026-10-26）

来源：`C:\dft\jzw\cfr\CONFFLOW-NOTES.md`（2026-10-24—26，Zr7 site1 TS1/TS2 实际使用）。
本方案只修使用问题，不重开搜索策略（2026-10-26 已冻结）。下表把 notes 每一条归类；代码位置均已在 `ba7fe58f` 上核对。

## 1. 分类总览

| 类 | 条目 | 处理 |
|---|---|---|
| **A 缺陷（先修）** | A1 BREAKING 键不进 reaction_pairs；A2 release 阶段不查配体–金属距离（O39 脱离仍 passed）；A3 `validate` 必须带 `--json` | 本轮修 |
| **B 报告/易用性（小改）** | B1 每个结构报告最终反应对距离 + 可选更硬约束；B2 CN≥7 未显式设 arrangement 时警告；B3 控制台摘要显示输入差距、state_changes、每个状态最低；B4 structures.xyz 每帧状态标签；B5 run_result.json 稳定的"发布结构"指针；B6 文档写明 `execution.walltime_seconds` | 本轮修 |
| **C 新功能（另立方案）** | C1 MLIP(UMA) 步骤；C2 对称感知去重/比较命令；C3 可选配位位点（半不稳定配体）；C4 TS 步骤（模式检查、可续算） | 只定范围，不在本轮实现 |
| **D 非 ConfFlow 问题** | g16 重启段错误、nohup PID、UMA 线程悬崖、ORCA rc=0、g-xTB 溶剂、集群安装、成本数据 | 不改代码；部署相关一条写进文档 |

## 2. A 类：缺陷

### A1 BREAKING 键没有距离约束（已核实）

- 现象：`topology.add_bond kind: BREAKING` 只进类型图，不进 `reaction_pairs`。xtb 无约束 → 键重新成键 → 拓扑审计（只豁免 reaction_pairs）几乎全拒（TS2 smoke 11/12 topology）。
- 原因：`confflow/science/confgen/topology.py:298`（`bonds` 路径）和 `:394`（`add_bond` 路径）都只在 `kind is EdgeType.FORMING` 时追加。
- 修复：两处改为 `kind in (FORMING, BREAKING)`；去重（用户同时在 `reaction_pairs` 里写了同一对时不重复）。`dg_seed.py:165` 已把 BREAKING 当反应对处理，无需改。更新 `topology.py:43` 注释。
- 测试：BREAKING 声明 → `reaction_pairs` 含该对、xtb `$constrain` 含该距离；同对重复声明只出现一次；TS2 型小例子审计不再报 topology。

### A2 release 后配体可以离开金属（已核实）

- 现象：504 个 released 帧中 15 个 Zr–O39 2.79–2.97 Å（输入 2.23 Å），全部 `passed=true`。
- 原因：release 阶段跳过 `metal_donor_distance`（`search.py:52 _SKIPPED_FREE`），没有任何上限；只有 `explore.py:868` 的 unclassified 路径用 `_DONOR_SLACK=1.3`。
- 矛盾点：notes 10-26 又写"GFN2 帧里 O39 拉长可能是 GFN2 假象，MLIP 弛豫后再过滤更安全"。所以默认直接拒绝可能误杀。
- 方案（推荐）：
  1. 每个发布结构计算 `max_donor_stretch`（各配体 M–D 距离 / 输入距离的最大值，及对应原子），写进 structures.xyz 注释和 summary。
  2. 超过 1.25× 的结构打标签 `donor_stretched`，摘要给 WARNING（数量 + 原子），**默认不拒绝**。
  3. 新增 `search.max_donor_stretch`（默认 `null` = 只报告；设数值则 release 审计按此拒绝）。
- **已定（2026-10-26）**：默认只标记，阈值 1.25×；拒绝需显式设 `search.max_donor_stretch`。

### A3 `confflow v4 validate` 不带 `--json` 报错（已核实）

- 原因：`v4cli.py:63` `--json` 设了 `required=True`。
- 修复：去掉 `required`；不带时打印人可读结果（通过/错误列表），返回码不变。COMMAND_REFERENCE 同步。

## 3. B 类：报告与易用性

- **B1 反应对距离漂移**：`$constrain force constant=1.0`（`confgen_xtb.py:118`）是弹簧约束，两次运行 1.898–1.917 Å。
  (a) 每帧注释和 summary 写最终反应对距离；(b) 新增 `search.reaction_force_constant`（默认 1.0 不变），需要时可调高到例如 5–10。
  实施前用 TS1 小例子测一次调高后的漂移和收敛步数，再写文档建议值。xtb 没有严格的距离约束，文档里写清楚这一点。
  **已定（2026-10-26）**：做 (a) 和 (b)；更硬的约束只通过调高力常数实现。
- **B2 CN≥7 警告**：用户未显式写 `arrangement`（pydantic `model_fields_set` 判断）且输入 CN≥7 时，运行开头给 WARNING："CN≥7 的排列标签定义不清，建议 `arrangement: keep`"。只警告，不改默认。
- **B3 控制台摘要**：运行结束打印三行：输入结构比最低高 X kcal（及状态 `outside_energy_window` 等）；`state_changes` 每个单元的翻转情况；每个状态（fixed_states/state_scan 标签）的最低能量。
- **B4 每帧状态标签**：structures.xyz 注释加 `states=<标签串>`（如手性 +++/---、arrangement aN），用 state_scan 已有结果，不新增检测逻辑。
- **B5 发布结构指针**：`run_result.json` 是带校验的契约文件，不改其结构；改为在文档写明定位规则（`artifacts[].locator` 以 `search_run/structures.xyz` 结尾）和注释行字段（`energy_eh`、`rel_kcal`，以及 B1/B4 新增字段）。
- **B6 文档**：CONFGEN_CAPABILITIES / USAGE 的 confgen 部分写 `execution.walltime_seconds`（0.9× 时停止并照常写结果）；加一节"集群离线安装：`pip wheel --no-deps`，setuptools ≥ 82"。
- **暂不做**：每个状态单独能量窗口（notes 第 4 条后半）。B3/B4 先让状态可见，确有状态被 6 kcal 窗口整体丢掉的实例再做。

## 4. C 类：新功能（只定范围）

这些符合 2026-10-24 方向（ConfFlow 做 TS+极小的引擎，重复操作沉淀为小 CLI），但都比本轮大，各自另写方案、先确认再动：

- **C1 MLIP 步骤**：先做便宜漏斗——UMA 单点打全部 GFN2 极小 → 每状态/每簇挑少数做约束弛豫。必须记录优化器、评估次数、墙钟时间；约束用 `FixBondLengths(tolerance≈1e-8)`；PreconLBFGS 要盒子和显式 r_cut。notes 已证实 GFN2 窗口不能作预筛（差 8–10 kcal）、优化器影响结果（达 6.6 kcal）。
- **C2 去重/比较命令**：`confflow compare a.xyz b.xyz` / `confflow dedupe set.xyz`，对称感知（tBu/甲基/苯环置换），先用排序距离表做快速判同，再用置换 RMSD 确认。两次实例（ΔE 0.004 kcal 但 RMSD 1.01 Å；16 → 10）可作测试数据。**已定（2026-10-26）：C 类先做这一项。**
- **C3 可选配位位点**：`optional: true` 的配位原子，可上可下，标签记录状态。涉及审计和 coordination_class，改动较深；当前两次运行+下游合并可用。
- **C4 TS 步骤**：模式检查、成键距离检查、TS 后去重、可续算检查点、优雅停止。等真实路径跑通后再设计。

## 5. 实施顺序与验证

1. 一个分支 `refactor/usage-fixes-20261026`，一个 PR：A1、A3、B2、B5、B6（纯逻辑/文档，测试即可）。
2. 第二个 PR：A2、B1、B3、B4（改报告和审计输出）。用现有 TS1 金标准 fixture 回归：默认设置下发布结构集合和能量不变（A2 默认只标记、B1 默认 force constant 不变，所以应完全一致）；新增字段有测试。
3. B1 的力常数测试是小计算（TS1 几个结构，约 10 分钟内，本地），在方案批准后执行；不跑整批搜索。
4. 每个 PR 跑 changed-coverage 门和 loc_budget 检查。

## 6. 决定（2026-10-26 已确认）

1. A2：默认只标记，阈值 1.25×，`search.max_donor_stretch` 设值时才拒绝。
2. B1：报告最终距离；新增 `search.reaction_force_constant`（默认 1.0），收紧只靠调高力常数。
3. C 类先做 C2（对称感知去重/比较命令）。

## 7. PR1 任务分工（Haiku 子代理并行，文件互不重叠）

工作树 `/opt/cf-worktrees/usage-fixes`，分支 `refactor/usage-fixes-20261026`。测试命令：`/opt/ConfFlow/.venv/bin/python -m pytest -q -p no:cacheprovider <文件>`。

| 任务 | 内容 | 负责文件 | 验收 |
|---|---|---|---|
| H1 | A1 BREAKING 进 reaction_pairs | `confflow/science/confgen/topology.py`，新测试 `tests/v4/test_confgen_breaking_reaction_pair.py` | 新测试 + 现有 topology/search 测试通过 |
| H2 | A3 `validate` 可不带 `--json` | `confflow/v4cli.py`，`docs/COMMAND_REFERENCE.md`，新测试 `tests/test_v4cli_validate_text.py` | 新测试 + 现有 v4cli 测试通过 |
| H3 | B2 CN≥7 且未写 arrangement 警告 | `confflow/execution/confgen_search_run.py`，新测试 `tests/v4/test_confgen_high_cn_arrangement_diagnostic.py` | 新测试 + `tests/v4/test_confgen_search_step.py`、keep-scope 测试通过 |
| H4 | B5、B6 文档 | `docs/CONFGEN_CAPABILITIES.md`，`docs/USAGE.md` | 内容准确（我核对） |

之后我统一验收：black、changed-coverage、loc_budget、相关测试分片。

## 8. PR2 设计与分工（2026-10-26）

读代码后的调整：
- `confflow v4 run` 不带 `--json` 时只打印 `run X: completed`，所有 WARNING（含已有的 state_changes、arrangement 警告）都看不到。B3 改为：文本模式下逐步打印每一步的 warning/info 诊断；另加 `confgen_input_gap` 提示（输入比最低发布结构高多少，取 `input_baseline.lowest_final_delta_kcal`，与最终结构同口径）。
- xtb `$constrain` 一个块只有一个力常数，搜索阶段块里还有金属–配体距离。选项名改为 `search.restraint_force_constant`（release 阶段只剩反应对，此时只作用于反应对）。
- 新选项 `restraint_force_constant`、`max_donor_stretch` 在 schema 中默认 `None`（运行时解析为 1.0 / 不拒绝），不改变已有工作流的 wire 摘要。

接口（各任务按此对接）：
- `search.SearchRecord` 新字段（均有默认值）：`reaction_distances: tuple[float, ...] = ()`（Å，按 `graph.reaction_pairs` 顺序）、`donor_stretch: float | None`（最大 M–D/输入 M–D）、`donor_stretch_atom: int | None`（0 基）、`state_flips: tuple[int, ...] | None`（该结构相对输入翻转的 state_changes 单元编号，1 基；`None` = 未扫描）。`to_dict` 同名输出。
- `explore.SearchSettings.max_donor_stretch: float | None = None`；超限拒绝的检查名 `donor_stretch`。
- `XtbCalculator(..., restraint_force_constant: float = 1.0)`。
- structures.xyz 注释追加：` rxn=1.905,2.310`、` donor_stretch=1.083`、` flips=1,3`（无翻转为 `flips=-`）。

| 任务 | 内容 | 负责文件 |
|---|---|---|
| K1（我） | 每个发布结构计算反应对距离、donor_stretch、state_flips；`max_donor_stretch` 拒绝 | `science/confgen/explore.py`、`search.py`、`state_scan.py`，新测试 `tests/v4/test_confgen_record_labels.py` |
| K2 | schema 两个新字段；worker 解析并传递；xtb 力常数；注释行 | `workflow/v4/confgen_schema.py`、`execution/confgen_search_worker.py`、`execution/confgen_xtb.py`，新测试 `tests/v4/test_confgen_restraint_options.py` |
| K3 | `confgen_donor_stretched` WARNING（>1.25×）、`confgen_input_gap` INFO、成员元数据；`v4 run` 文本模式打印诊断 | `execution/confgen_search_run.py`、`v4cli.py`，新测试 `tests/v4/test_confgen_usage_diagnostics.py`、`tests/test_v4cli_run_text.py` |
| K4（我，最后） | 文档 | `docs/CONFGEN_CAPABILITIES.md`、`docs/USAGE.md` |

力常数实测（B1，2026-10-26，本地，21 次 xTB 优化约 4 分钟）：TS1 金标准 7 个结构只约束 O74–C79，力常数 1/2/5 时最大距离偏离 0.0135/0.0067/0.0027 Å，步数与耗时基本不变，能量相差 ≤0.33 kcal/mol。默认保持 1.0，文档建议跨运行比较时统一用 5。脚本与原始输出在会话 scratchpad `fc/`（临时）。

## 9. C2 去重/比较命令（2026-10-26）

问题（notes 10-25/10-26）：普通重原子 RMSD 不认对称置换（tBu/甲基、苯环邻/间位翻转），同一个 TS 的副本被当成不同结构（TS1 两帧 ΔE 0.004 kcal、RMSD 1.01 Å；TS2 16 个"唯一"实为 10 个）。

做法：复用 ConfGen 搜索内部已经在用的 `identity.Identity`（图自同构 + 镜像的 RMSD、距离轮廓下界），不新写判同算法。
- 只比较重原子、杂原子上的 H 和非共价边上的 H；C–H 氢（甲基、tBu）不参与，所以甲基转动和 tBu 置换不影响结果。
- 图从第一帧按距离感知（`bond_scale` 默认 1.15）；只有一个金属时其边记为配位；`--pair` 声明的反应对记为 FORMING 边。整组结构共用这一张图。
- 判同：对称 RMSD < `--rmsd`（默认 0.2 Å，同 ConfGen）且（两者能量都已知时）|ΔE| ≤ `--e-tol`（默认 0.5 kcal/mol，同 ConfGen）。镜像默认算同一个（同 ConfGen），`--no-mirror` 关闭。
- 能量从注释行读取：`energy_eh=`，或 ORCA 的 `E <数>`；读不到就只按几何判同。

命令：
- `confflow compare A.xyz[@k] B.xyz[@k]`：打印对称 RMSD、普通重原子 RMSD、是否同一结构。
- `confflow dedupe IN.xyz [IN2.xyz ...] -o OUT.xyz`：有能量时按能量升序处理，每组保留最低者；打印"N 帧 → M 个唯一"和每帧归属；`--json` 输出机器可读报告。输出帧保留原注释，追加 `members=<n>`。

接口：
- `confflow/science/confgen/compare.py`（纯计算，我写）：`structure_identity(elements, coords, *, bond_scale=1.15, metal=None, reaction_pairs=(), r_tol=R_TOL, mirror=True, e_tol=E_TOL) -> Identity`；`pair_rmsd(identity, x, y) -> tuple[float, float]`（对称、普通）；`dedupe(identity, frames, energies) -> list[DedupeRow]`，`DedupeRow(index, representative, rmsd, delta_kcal)`，每个输入帧一行，按输入顺序。`Identity` 增加 `e_tol` 属性（默认 `E_TOL`，行为不变）。
- `confflow/application/structure_compare.py`（I/O 与参数，子代理写）；`confflow/cli.py` 分派 `compare`/`dedupe`。

验证：TS1 试点 stage 4 的 37 个通过 TS（用户手工规则判为 15 个唯一）。

| 任务 | 内容 | 负责文件 |
|---|---|---|
| D1（我） | 纯计算模块与测试 | `science/confgen/compare.py`、`identity.py`（`e_tol`），新测试 `tests/v4/test_confgen_structure_compare.py` |
| D2 | CLI、xyz 读写、能量解析、文档 | `application/structure_compare.py`、`cli.py`、`docs/COMMAND_REFERENCE.md`、`docs/USAGE.md`，新测试 `tests/test_structure_compare_cli.py` |

验证结果（2026-10-26，本地，`compare.dedupe` 直接调用）：
- TS1 试点 stage 4 的 37 个通过 TS（UMA，能量 Eh）：默认阈值（0.2 Å / 0.5 kcal）→ 15 个唯一，22 个重复的归属与用户手工规则（|ΔE|<0.02 kcal 且排序重原子距离差 <0.01 Å）逐条相同；收紧到 0.1/0.1、0.05/0.02 结果不变。3 秒（122 原子，图自同构 5184 个）。
- fc_results 的 5 个力收敛 TS：笔记中的例子 new0126_b / new0126c 与 poolHm0301_b 普通重原子 RMSD 1.013 Å，对称 RMSD 0.000、ΔE 0.000 → 判为同一结构；5 → 3。
