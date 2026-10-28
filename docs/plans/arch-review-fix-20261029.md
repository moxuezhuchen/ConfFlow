# 架构复审 N1–N4 修复计划 · 2026-10-29

来源：`docs/reviews/architecture-review-20261029.md`（GPT 复审，基线 f5754b97）。四项均已对照代码确认。
分支 / worktree：`refactor/arch-review-20261029` @ `/opt/cf-worktrees/arch-review-1029`。

执行：三个 Haiku 5.5 子代理并行，**文件互不重叠**，不提交、不推送；Claude 负责审 diff、跑全部门禁、提交和开 PR。

## 公共规则（所有任务）

- 只改自己"拥有文件"中列出的文件；其他文件只读。确实需要改别的文件时停下来报告，不要自行修改。
- 在 worktree 中运行：`cd /opt/cf-worktrees/arch-review-1029 && PYTHONPATH="$PWD" /opt/ConfFlow/.venv/bin/python -m pytest <files> -q -o addopts=''`（editable 安装指向 /opt/ConfFlow，必须设 PYTHONPATH）。
- 本地没有 pytest-xdist。`tests/v4` 的文件在显式列表中要连续放。
- 交付前必须通过：`/opt/ConfFlow/.venv/bin/ruff check <改动文件>`（含 D401：docstring 首句用祈使语气），`/opt/ConfFlow/.venv/bin/mypy confflow`，`/opt/ConfFlow/.venv/bin/black --check <改动文件>`。
- 每个修复都要有**回归测试**；测试要在修复前失败、修复后通过（交付报告里说明是怎样验证的）。changed-coverage 门禁要求改动的生产代码行覆盖率 ≥85%，新分支（含异常分支）都要写测试覆盖。
- 不放宽现有测试、契约文档（`docs/STYLE_CONTRACT.md` 等）或架构扫描来迁就代码。若 `tools/loc_budget.json` 超限，在报告中给出实测值，由 Claude 调整。
- 不运行真实 xtb/ORCA/UMA；使用仓库已有的假程序 / 假模型（参考 `docs/reviews/data/architecture-review-20261029/reproduce.py` 的写法）。
- 不开后台进程；结束时不得留有运行中的 shell。
- 交付报告：改动文件列表、设计要点、新测试名、门禁命令输出摘要、遗留问题。

## 任务 A — N1：恢复分支推进执行环境身份（P1）

拥有文件：`confflow/persistence/reuse.py`、`confflow/execution/batch.py`、`confflow/persistence/work_items.py`、新测试 `tests/v4/test_arch_review_1029_resume_identity.py`。

问题：`evaluate_reuse()`（reuse.py:297–315）对 PENDING 返回 EXECUTE_NEW、对 owner 已死的 RUNNING 返回 RECOVER_ABANDONED，二者都发生在环境摘要比较（reuse.py:355）之前；batch.py:852–870 中恢复后 claim → launch → `record_finished(environment_digest=self._commit_environment_digest(result))`，本地结果的覆盖值为 None，所以登记的仍是旧环境摘要。之后用旧环境运行时，会把新环境的结果当作命中复用。

修复要求：
1. 在重新 claim 之前，把登记的身份（environment_digest，以及现有的输入/定义/producer provenance 轴）与本次身份比较。环境变化而其他轴不变时，走与 COMPLETED 路径相同的正式规则（参考 batch.py:911 的 `refresh_for_new_environment`）推进登记的环境摘要；RUNNING-dead 必须先结束旧 attempt（mark_interrupted），再推进身份并开新 attempt。输入、定义或 provenance 变化按各自现有规则处理，不能一律放行。
2. 不能只在 `record_finished` 时补写环境摘要（这会让登记与 attempt 历史不一致）。旧 attempt 的身份要保留，以便审计。
3. 回归矩阵：{PENDING, RUNNING-dead, COMPLETED, FAILED, INTERRUPTED} × {环境相同, 环境变化}。断言：环境变化时执行新计算，登记的 environment_digest 等于新环境；之后切回旧环境再跑时触发重算，不复用另一个环境的结果。复现方法见 review 的 N1 一节（真实 `SqliteWorkItemStore` + `execute_step_resumable()`，只替换科学执行器）。
4. 回归：`tests/v4/test_v43_reuse.py` 以及 `tests/v4` 中所有涉及 resume / reuse / batch 的测试全部通过。

## 任务 B — N2：ConfGen 只解析一次 xtb，校验和启动用同一个文件（P1）

拥有文件：`confflow/application/v4_run.py`、`confflow/execution/confgen_search_run.py`、`confflow/execution/confgen_search_worker.py`、新测试 `tests/v4/test_arch_review_1029_confgen_xtb_identity.py`。若 binding 环境的解析 helper 位于别的模块，只读引用；需要修改时先报告。

问题：应用层（v4_run.py:191、978）按控制进程的 PATH 解析 `binding.executable`，做 allowlist 校验和身份测量；job 文件仍写裸名称 `xtb`（confgen_search_run.py:834）；worker（confgen_search_worker.py:667 `resolve_xtb(job["xtb"])`）在 binding 环境里重新 `which`，并且重新读取 `os.environ`，而不是使用 `context.native_env` 快照。

修复要求：
1. 先确定唯一的有效环境（binding 环境 + 快照），在该环境下把 xtb 解析成绝对路径；allowlist 校验、内容/身份测量都针对这个绝对路径进行，并把绝对路径写入 job。
2. worker 使用 job 中的绝对路径和同一个环境快照，不再用裸名称做 PATH 查找，也不再读取当前 ambient 环境。job 中的路径不是绝对路径时直接失败（fail closed）。
3. 端到端测试（正常的 `V4RunApplication.run()`，两个假 xtb 脚本 a/ 和 b/，控制进程 PATH 优先 a，binding PATH 优先 b）：(i) allowlist 只允许 a 时，运行被拒绝或报错，且 b 没有被执行；(ii) allowlist 允许 b 时，修改 b 的内容后再次运行会触发重算（身份变化）。
4. 回归：所有 confgen_search_* 和 v4_run 相关测试通过。

## 任务 C — N3 + N4：MLIP 输出保留拓扑和结果来源（P1 + P2）

拥有文件：`confflow/execution/mlip_executor.py`、`confflow/science/mlip_analysis.py`（只在 stretch_check 需要时修改）、`docs/ARCHITECTURE.md`、新测试 `tests/v4/test_arch_review_1029_mlip_contract.py`。

N3 问题：mlip_executor.py:738–753 构造输出 `StructureRecord` 时漏掉了 `topology_patch` / `working_topology`，下游 `resolve_working_adjacency()` 会按距离重新感知键。
修复：复用现有的拓扑传播入口，不另写一套。参考 `confflow/execution/profile_standard.py:184–200`（`science.topology.resolve_and_persist_kwargs`）和 `confgen_search_run.py:931–935`（`should_persist_working_graph`）。预期的工作图从输入继承；松弛后按坐标观察到的键变化只作为诊断 / metadata 记录，不覆盖权威图。检查 `mlip_analysis.stretch_check()` 是否基于权威输入图做比较（不是基于距离重新感知的图）。
测试：mode 取 sp、relax、sp_then_relax，输入分别为只有 patch、只有 working graph、两者都有；断言输出的两个字段与输入的权威语义一致，下游 `resolve_working_adjacency()` 的结果与输入相同（例如：水结构删除 1–3 键后，邻接表应保持 `[[1],[0],[]]`）。再加一个正常工作流用例：在 `inputs.structures.topology.delete: [[1,3]]` 声明，跑完 MLIP 后声明不能丢失。

N4 问题：mlip_executor.py:755 的 energy `ScientificResult` 没有 `provenance`，`producer/run_result.py:106` 因此跳过内联能量。
修复：为 MLIP 构造完整的 `ResultProvenance`（program、模型/任务、执行器版本等实际可得的信息），参考 `profile_standard.py:297` 的 `_build_provenance`。不修改 run_result 的来源要求。注意：provenance 变化可能影响 result/identity 摘要，若有已有 golden/摘要测试因此需要更新，要在报告中说明原因。
测试：完整工作流（假模型）跑完后，`run_result.json` 中对应条目包含 `energies.electronic_hartree` 和真实的 `source`；resume 之后再次投影，结果相同。

文档：在 `docs/ARCHITECTURE.md` 的能力列表和数据流中补上 MLIP。
回归：`tests/v4/test_mlip_*.py`、`test_arch_review_mlip_scientific.py`、`test_arch_followup_mlip_probe.py` 全部通过。

## Claude 验收

1. 逐个审 diff，并与本计划对照。
2. 门禁：`ruff check .`、CI 版本 black、`mypy confflow`、`scripts/v4_arch_scan.py`、loc_budget；全量 `pytest tests`（分片并行，需要设置 JOBDESK_V2_SRC 为 pin 的 JD sha）。
3. 重跑 review 的 `reproduce.py`，七个场景都不应再复现缺口。
4. 一次提交（或按 N 拆分提交），推送 `refactor/arch-review-20261029`，开 PR。
