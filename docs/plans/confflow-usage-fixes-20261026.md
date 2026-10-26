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
