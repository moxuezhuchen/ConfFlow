# 架构评审修复计划 — 2026-10-28

来源：`docs/reviews/architecture-review-20261028.md`（GPT 评审，基线 6aa24592）。
分支 / worktree：`refactor/arch-review-20261028` @ `/opt/cf-worktrees/arch-review-1028`。
执行：4 个 Haiku 5.5 subagent 并行，同一 worktree，文件互不重叠，不提交；Claude 负责胶水代码、LOC 预算、全量门禁和验收。

## 范围

本轮修：R1–R5（结果正确性 / 可追溯性）+ 文档漂移。
暂缓：R6（按步骤组装、背压）、R7（WorkItemStore 文件系统检查）、R8（根包导入副作用）。单机个人使用下这三项不影响结果，留到下一轮。

## 设计决定

1. **R1 启动环境 = 摘要环境。** Script 和 MLIP 两类启动进程的 pure 执行器，不论 `execution` 写没写，都要建立 `native_env` 快照。快照同时用于摘要计算和进程启动（`ItemExecutionContext.native_env`）。
   - 启动环境和摘要环境都去掉一组会话性变量：`TERM*`、`COLORTERM`、`SSH_*`、`DISPLAY`、`WAYLAND_DISPLAY`、`XDG_SESSION_*`、`PWD`、`OLDPWD`、`SHLVL`、`_`、`TMUX*`、`STY`、`WINDOWID`、`COLUMNS`、`LINES`、`PS1`、`PROMPT_COMMAND`、`LS_COLORS`、`WSL_INTEROP`。两边去掉的是同一组，所以"启动的就是摘要的"仍然成立。
   - 只作用于 Script/MLIP 分支。原生计算（Gaussian/ORCA）的摘要**不动**，避免 cfr 已有 DFT 结果在续跑时全部重算。原生侧的 TERM 敏感问题记为已知、暂缓。
2. **R2 MLIP 运行前身份。** 应用层在复用判断之前测量以下内容，合成一个 `sha256:` 摘要，作为 `executable_digest` 进入 `build_pure_environment`：
   - 解释器内容（`measure_executable`）；
   - `mlip_runner.py` 内容；
   - 模型文件内容（`CONFFLOW_MLIP_MODEL` 一类变量，以 executor 里的 `MODEL_ENV` 为准）；
   - refs 文件内容（如有）；
   - 解释器里 `fairchem-core`、`torch`、`ase`、`numpy` 的版本，用 `importlib.metadata` 子进程查询，不 import torch。

   大文件的 hash 按 `(realpath, size, mtime_ns, ino)` 做进程内缓存。缺模型或解释器不可解析时身份为 `None`，由执行器照常失败，不能静默复用。
3. **R3 MLIP 逐结构科学参数。** 每个结构都调用 `resolve_scientific_parameters(structure, overrides=context.scientific.overrides, defaults=context.scientific_defaults)`，规则与 `work_item_executor.py:483` 的计算执行器一致：
   - 有 ERROR 诊断就失败；
   - charge 或 multiplicity 缺失就失败，**不再默认 0/1**；
   - 批次内有效 (charge, mult) 不一致就失败，因为 runner 只收一组参数；
   - 输出 `StructureRecord` 的 charge/multiplicity 取实际使用的有效值。

   行为变化：以前靠 MLIP 默认 0/1 跑的工作流，现在需要声明 charge/multiplicity。
4. **R4 脚本身份锁定。**
   - 编译时的 `script_identity_payload` 保存到 `ValidatedStep` → `PlannedStep.script_identity`，再到 `ItemExecutionContext.expected_script_identity`。
   - `ScriptExecutor` 启动前重新计算 `script_identity(entry)`，与期望值不一致就 `DomainError`（脚本在编译后被改动，需要重新提交）。
   - 启动前再 hash 一次脚本文件，与 `entry.sha256` 比对，缩小 TOCTOU 窗口。不做 staging 拷贝，因为脚本可能依赖自己所在目录。
   - 应用层（Task A）在编译前载入一次脚本注册表快照，传给 `compile_workflow(script_registry=...)`，让应用路径的编译核心不再读服务器配置。
5. **R5** `_run_generation` 编译时传 `registry=self._active_registry`。

## 任务

| Task | 内容 | 拥有的文件（只能改这些） | 新测试文件 |
| --- | --- | --- | --- |
| A | R1、R2、R5，以及 R4 的应用层快照 | `confflow/application/v4_run.py`、`confflow/execution/environment.py` | `tests/v4/test_arch_review_launch_identity.py` |
| B | R3 | `confflow/execution/mlip_executor.py`、`tests/v4/test_mlip_executor.py` | `tests/v4/test_arch_review_mlip_scientific.py` |
| C | R4 的身份传递和启动校验 | `confflow/workflow/v4/validation.py`、`confflow/workflow/v4/plan.py`（以及生成 PlannedStep 的模块，若不是 plan.py 则先报告）、`confflow/execution/batch.py`、`confflow/execution/work_item_executor.py`（仅 context 字段）、`confflow/execution/script_executor.py` | `tests/test_arch_review_script_snapshot.py` |
| D | 文档漂移 | `tests/README.md`（覆盖率 85→70）、`docs/` 下引用 `science/frame_compare.py` 和旧 `input_xyz` envelope 的架构文档 | 无 |

跨任务胶水（Claude 合并时做）：
- 在 `v4_run.py` 把 `planned.script_identity` 传进 batch request，或者 C 报告的等价位置；
- `tools/loc_budget.json` 的增量。

## 每个 Task 的硬性规则

- 只改自己拥有的文件；改别的文件之前先报告。不提交、不 push。
- 不放宽测试、预算或规范文档来迁就代码；不删除已有断言。
- 每个修复都要有应用级或执行器级回归测试，复现评审中描述的场景，并且在修复前会失败。
- 改动的生产代码行覆盖率 ≥ 85%（PR 的 changed-coverage 门禁）；收窄 except 一类的分支也要有测试覆盖。
- 执行层模块不得 import `confflow.core`（RT-065）。
- 结束前清理自己启动的后台进程。

## 验收命令（在 worktree 根目录，`/opt/ConfFlow/.venv/bin/python`）

```bash
PY=/opt/ConfFlow/.venv/bin/python
# A
$PY -m pytest -q tests/v4/test_arch_review_launch_identity.py tests/v4/test_v4_runtime_cutover.py tests/test_v4cli_run_text.py tests/v4/test_mlip_executor.py
# B
$PY -m pytest -q tests/v4/test_arch_review_mlip_scientific.py tests/v4/test_mlip_executor.py tests/v4/test_mlip_runner.py tests/v4/test_mlip_step_schema.py
# C
$PY -m pytest -q tests/test_arch_review_script_snapshot.py tests/test_script_steps.py tests/test_review_fix_script.py tests/test_script_retry_outputs.py tests/test_server_quota.py tests/test_review_e_branches.py
# 所有 Task
$PY -m mypy confflow
$PY -m black --check <改动的文件>   # 合并时 Claude 用 CI 版本 black 26.10.1 复查
```

Claude 合并后另跑：全量 pytest（需设置 JOBDESK_V2_SRC 为 pin 的 JD sha）、`tools.architecture_policy.scan()`、`scripts/v4_arch_scan.py`、changed-coverage、CI 版 black、mypy。

## 验收场景（评审要求的五类应用级对照）

1. 同目录续跑、环境变量变化（不写 `execution`）→ 新 attempt，输出新值。
2. 只改 `TERM` → 复用，不新开 attempt。
3. MLIP 模型同路径替换 / 解释器改成不存在的路径 → 不复用（重算或失败），不报告旧结果为 completed。
4. 编译后改脚本 → 拒绝运行，且不发布旧摘要下的新内容结果。
5. 带电结构 (1,2) + 全局默认 (0,1) → job.json 为 (1,2)，输出标签为 (1,2)；异构批次和缺参数都失败。
6. 自定义 registry 合同版本 → 应用报告的 definition digest 等于使用该 registry 离线编译的结果。
