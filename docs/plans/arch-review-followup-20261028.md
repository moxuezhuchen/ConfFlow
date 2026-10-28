# 架构修复复查跟进计划 — 2026-10-28

来源：`docs/reviews/architecture-fix-verification-20261028.md`（GPT 复查，基线 23fb3c27）。三项我都已对照代码确认。
分支 / worktree：`refactor/arch-review-followup-20261028` @ `/opt/cf-worktrees/arch-followup-1028`。
执行：2 个 Haiku 5.5 subagent 并行，文件互不重叠，不提交；Claude 负责验收、LOC 预算、全量门禁、PR。

范围：F1、F2、F3。R6–R8 仍暂缓。

## 设计决定

1. **F1 先校验，再探测。**
   - 应用层对 MLIP 步骤，在 `_mlip_runtime_digest()` 启动任何子进程之前，先解析解释器（`binding.executable` 或 `python3`，用 `shutil.which` / `measure_executable` 解析成真实路径）。
   - 接着对该解释器执行完整的 `enforce_execution_policy`（`allowed_executables` 白名单 + sandbox/写路径，`check_program=True`）。校验不通过就以现有方式失败，并且不执行任何探测。
   - Script 步骤不变：它不做探测，解释器仍在执行器里校验。
2. **F2 最终复核放在拿到配额之后、submit 之前。**
   - `WorkItemExecutor.launch_and_wait` / `_launch_and_wait` 新增可选参数 `before_submit: Callable[[], None] | None = None`。拿到配额租约之后、`supervisor.submit()` 之前调用它；抛异常时先释放租约，再把异常原样抛出（不吞掉、不变成 `None`）。
   - `ScriptExecutor` 把现有的"脚本身份复核 + 文件重新 hash"移进这个回调；启动前不再单独检查一次（若保留早检查也可以，但最后一次检查必须在回调里）。
   - 其他调用方（MLIP、计算、ConfGen）不传，行为不变。
3. **F3 版本探测与正式启动一致。**
   - 探测去掉 `-I`，`env` 用冻结后的 `native_env`（与 runner 启动相同），`cwd` 用一个临时目录。
   - 删除 `_VERSION_CACHE`，每次测量都重新探测（每个 MLIP 步骤一次，约 0.1 秒，可接受）。
   - 文件内容 hash 的 stat 缓存保留。

## 任务

| Task | 内容 | 拥有的文件 | 新测试文件 |
| --- | --- | --- | --- |
| A | F1、F3 | `confflow/application/v4_run.py`、`confflow/execution/environment.py`、`tests/v4/test_arch_review_launch_identity.py` | `tests/v4/test_arch_followup_mlip_probe.py` |
| B | F2 | `confflow/execution/work_item_executor.py`（只改 `launch_and_wait`/`_launch_and_wait`）、`confflow/execution/script_executor.py`、`tests/test_arch_review_script_snapshot.py` | `tests/test_arch_followup_script_quota.py` |

## 硬性规则

与 `docs/plans/arch-review-fixes-20261028.md` 相同：
- 只改自己拥有的文件，不提交、不 push；
- 不放宽测试或预算，不删已有断言；
- 回归测试必须在修复前失败；
- 改动的生产代码行覆盖率 ≥ 85%；
- 执行层不 import `confflow.core`；
- 结束前清理自己启动的后台进程。

## 验收场景

1. 被白名单拒绝的 MLIP 假解释器：报告 failed，并且 marker 文件**不存在**，即连版本探测都没执行。
2. 先占满真实 `ServerQuota`，脚本工作流进入 acquire 后修改脚本，再释放配额：步骤失败，不发布新内容，租约被释放。
3. 同进程内升级 venv 里的包元数据（numpy 1.0 → 2.0）：MLIP 身份改变。
4. 正式启动的 `PYTHONPATH` 指向另一份元数据：探测到的版本与实际启动看到的版本相同。

## 验收命令

```bash
PY=/opt/ConfFlow/.venv/bin/python
# A
$PY -m pytest -q tests/v4/test_arch_followup_mlip_probe.py tests/v4/test_arch_review_launch_identity.py tests/v4/test_mlip_executor.py tests/v4/test_v4_runtime_cutover.py
# B
$PY -m pytest -q tests/test_arch_followup_script_quota.py tests/test_arch_review_script_snapshot.py tests/test_script_steps.py tests/test_review_fix_script.py tests/test_script_retry_outputs.py tests/test_server_quota.py tests/test_review_e_branches.py
# 两个 Task 都跑
$PY -m mypy confflow
$PY -m black --check <改动的文件>
```

Claude 合并后另跑：全量 pytest（12 个分片并行跑）、架构扫描、changed-coverage、CI 版 black、GPT 的复现脚本 `/tmp/confflow_fix_followup_repro.py`。
