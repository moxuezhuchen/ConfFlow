# A2-b — refine 报告同一组内键连接不同的结构（只报告，不过滤；ConfFlow，分支 refactor/a2-connectivity-report）

用户 2026-10-04 选 A2-b：只报告、不过滤；若新增字段影响契约或边界摘要，走检查点。调查见 `docs/refactor/plan2/A2-report.md`。

- 工作树 `/opt/cf-worktrees/a2`，分支 `refactor/a2-connectivity-report`（基点 `afdf9df`），HEAD 应为 `afdf9df` 开头，工作树干净。**提交前再核对 `cd /opt/cf-worktrees/a2 && git branch --show-current` 输出 `refactor/a2-connectivity-report`。**每条命令显式 `cd`。
- 类型：`feature`（只增加诊断信息）。白名单（恰 2 个文件）：`confflow/execution/transform_executor.py`（+58）、`tests/v4/test_refine_connectivity_report.py`（新增，+100）。应用：`cd /opt/cf-worktrees/a2 && git apply --check …/handoff/A2b.patch && git apply …`；`git status --short` 恰 1 个修改、1 个新增。

## 内容
- `refine` 在每个科学分组内，用已有的比较框架（`_frame` 的键图）取各结构的共价键集合，**最常见的键集合为参照**（并列时取持有该集合的最小 id）；每个键集合不同的结构追加一条 note，写进步骤已有的 `notes`（与 `dropped … as duplicate` 同一通道）。**不丢弃、不合并、不改变顺序，输出结构集合与此前逐项相同。**
- note 措辞（验收核对）：`connectivity of {id} differs from the majority bonding graph of its group ({n} of {N} structures): gained [{bonds}], lost [{bonds}]`；键写成 1 基序号加元素符号，如 `C1-H5`，超过 8 条后以 `... (+k more)` 收尾；无差异时不产生任何 note。
- **不新增字段、不改契约或边界**：只往已有的 `notes` 字符串列表里加条目；golden_check 的契约/边界摘要必须全部 ok（预演已验证）。若执行时摘要有变化，停止并报告，走检查点，不要自行处理。
- 有 `topology_bonds` 声明时所有结构共用声明的图，不会产生差异 note；没有声明时按记录的 `working_topology`，否则几何感知（bond_scale 1.2）——与此前 refine 看到的图一致。

## 测试（4 个，逐字节点名写进提交信息）
`test_a_structure_with_a_lost_bond_is_named_and_kept`、`test_the_majority_graph_is_the_reference`、`test_a_gained_bond_is_reported_as_gained`、`test_equal_connectivity_adds_no_note`（均在 `tests/v4/test_refine_connectivity_report.py`，用带氢的丁烷，把 H5 拉出成键距离造出"断键"结构）。

## 自检（原始输出）
1. `cd /opt/cf-worktrees/a2 && ruff check confflow tests scripts && mypy confflow && black --check confflow/execution/transform_executor.py tests/v4/test_refine_connectivity_report.py`
2. collect 4483 → 4487；全量 `{"passed": 4477, "skipped": 10}`（全量命令与 golden_check 命令同 A1.md，目录换成 `/tmp/acc2/A2-exec`）。
3. **golden_check（不得省略）**：期望 `ok: true`，契约/边界摘要 5 项全部 ok，引擎报告 added/different/missing 为空。
4. 抽样破坏：把 `transform_executor.py` 还原为 main 版本，4 个新测试里 3 个（断言 note 的那三个）应失败，第 4 个（无差异无 note）通过。
- 提交：`feat(refine): report structures whose bonding differs from their group's majority`，Removed-Tests 0、Added-Tests 4，Behavior-Change：refine 步骤 notes 可能多出 `connectivity of …` 条目；结构集合不变。不 push；不动 main/master；补丁无法应用或数字不符，停止报告，不要自行修复。
