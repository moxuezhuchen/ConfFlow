# C1-a — refine 默认 `bond_scale` 改为 1.15，与 ConfGen 一致（ConfFlow，分支 refactor/c1a-bond-scale）

用户 2026-10-04 批准 C1-a。背景与证据：`docs/refactor/plan2/C1-evaluation.md`（规划者已独立复核，见下）。

- 工作树 `/opt/cf-worktrees/c1a-exec`，分支 `refactor/c1a-bond-scale`（基点 `afdf9df`），HEAD 应为 `afdf9df` 开头，工作树干净。**提交前再核对 `cd /opt/cf-worktrees/c1a-exec && git branch --show-current` 输出 `refactor/c1a-bond-scale`。**每条命令显式 `cd`。
- 类型：`feature`（默认值变化）。白名单（恰 3 个文件）：`confflow/execution/transform_executor.py`（+7/−3）、`docs/USAGE.md`（1/1）、`tests/v4/test_refine_default_bond_scale.py`（新增，+67）。应用：`cd /opt/cf-worktrees/c1a-exec && git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/C1a.patch && git apply …`；`git status --short` 恰 2 个 `M` 加 1 个 `??`。

## 内容
- `REFINE_DEFAULT_BOND_SCALE` 1.2 → 1.15（ConfGen 感知默认 `tolerances.bond_scale`），并同步模块 docstring 与 `docs/USAGE.md` 的一处措辞。
- **不变的行为**：显式 `bond_scale` 仍覆盖默认（`native.get("bond_scale", …)`）；记录自带 `working_topology` 仍逐字优先、与尺度无关（`resolve_working_adjacency`）；声明 `topology_bonds` 的路径不经过该默认值（其感知尺度来自 `topology_bonds.tolerances`，默认即 ConfGen 1.15）；ConfGen 默认值不动；`mapping_budget` 不动。
- 不新增字段、不改契约或边界（规划者预演 golden_check 5 项摘要全部 ok）。

## 测试（3 个，逐字节点名写进提交信息）
`tests/v4/test_refine_default_bond_scale.py`：判别几何为 C–C 距离 1.81 Å（1.15 阈值 1.771 之上、1.2 阈值 1.848 之下）。
1. `test_the_refine_default_matches_the_confgen_perception_scale`——常量等于 1.15 且等于 `ConfgenToleranceModel().bond_scale`；默认 refine 把"断键结构 + 拉伸结构"判为重复（保留 1 个）。
2. `test_an_explicit_bond_scale_still_overrides_the_default`——显式 1.2 时两者都保留。
3. `test_a_persisted_working_topology_wins_verbatim_at_any_scale`——两结构均带持久空图时，默认与显式 1.2 结果相同（持久图压过 1.2 几何感知，否则 1.2 会保留两个）。

## 数字（规划者预演 @afdf9df+c94bf38 实测）
- collect 4483 → 4486（+3）。
- 全量 `{"passed": 4476, "skipped": 10}`。
- golden_check `ok: true`，契约/边界摘要 5 项全部 ok，引擎报告 added/different/missing 为空。
- TS1 独立复核（8 个结构，阈值 0.25）：默认（1.15）与显式 1.2 在 budget 1000/200000 下保留集合完全相同（7/8，丢 `ts1_original_plus_F1-F6`）；O74–C79 距离 1.6853 Å，1.15 阈 1.6445（不成键）、1.2 阈 1.7160（成键）——只在尺度间有差异，不改 refine 判定。

## 自检（原始输出）
1. `cd /opt/cf-worktrees/c1a-exec && ruff check confflow tests scripts && mypy confflow && black --check confflow/execution/transform_executor.py tests/v4/test_refine_default_bond_scale.py`
2. collect 4486；全量 `{"passed": 4476, "skipped": 10}`（全量与 golden_check 命令同 handoff/A1.md 通用自检，目录换成 `/tmp/acc2/C1a-exec`，golden 基线与 removed-nodes 配置照抄）。
3. golden_check：期望 `ok: true`，契约/边界摘要 5 项全部 ok，引擎报告 added/different/missing 为空；任何摘要 DIFF → 停止报告。
4. 抽样破坏：把 `confflow/execution/transform_executor.py` 还原为固定基点 `afdf9dfe6c3da39211a6d67cab7fe5f8ed6f13f8` 版本后跑新测试文件：恰 `test_the_refine_default_matches_the_confgen_perception_scale` 1 个 FAILED、其余 2 个 PASSED（后两者断言的是尺度无关的既有行为，main 天然满足）；随后恢复，工作树保持干净。
- 提交：`fix(refine): default the perception scale to the ConfGen 1.15`，Removed-Tests 0、Added-Tests 3（逐字写出节点名），Behavior-Change：refine 默认感知尺度 1.2 → 1.15；显式 `bond_scale` 仍可覆盖，`working_topology` 继承不变，ConfGen 默认不动。不 push；不动 main/master；补丁无法应用、golden 摘要 DIFF 或数字不符，停止报告，不要自行修复。

## 提示词复核补充
- refine_default/refine_strict 预设的显式 1.2 不在本卡范围。
- 合并中的 main 不作为本卡执行或抽样破坏基点；仍钉 afdf9df。
- 验收方修正补丁中“所有几何感知使用同一尺度”的过宽注释，仅限表述，无行为改动；在 /tmp 独立检出 afdf9df 应用修订补丁，新测试实跑 3 passed。既有全量与 TS1 数字仍标注为规划者预演结果，本轮未据此宣布验收通过。
