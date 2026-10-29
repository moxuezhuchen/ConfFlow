# PR #196 修复复核 — 2026-10-28

审查对象：[PR #196](https://github.com/moxuezhuchen/ConfFlow/pull/196)，提交 `5d90a89c03edcc066c4ef5bdbbdbbe0134765ff6`，基线 `943aae29f2f8d35a831b55e5449c2c938b36b4cf`。

结论：建议修复下列两个 R7 问题后再合并。R8 本轮未发现阻塞问题；R6 暂缓优化的决定可以保留，但报告需要区分取消后步骤返回和整个 run 返回。

本次仅审查。生产代码和已有测试未修改，PR 未评论、未合并。测试使用提交的独立归档 `/tmp/confflow-pr196-review.WJkWLs`，没有切换或污染主工作区，也没有操作 `/mnt/c` 上的已有 run。

## F1 / P1：存储拒绝发生过晚，已完成 run 的状态和结果清单被改写

位置：新增检查在 `confflow/persistence/work_items.py:640`；其调用位置是 `confflow/application/v4_run.py:992`，晚于 `:481` 的 `arbitration.begin_generation` 和 `:496` 的状态保存。

存储政策确实在 `sqlite3.connect` 前执行，但正常工作流已经在此之前取得 generation 所有权并更新运行状态。续跑一个在新政策下不受支持的旧 run，会先修改原有状态，然后才抛出 `StorageUnsupportedError`；异常处理随后发布失败清单。

复现使用真实 V4 两步运行、受控 fake ORCA、真实持久化，仅将挂载表注入为 `nfs4`：

1. 本地创建并完成两步 run，状态为 `completed`。
2. 保持工作流、输入和 run root 不变，将该 root 的检测结果切为 `nfs4`。
3. 再次运行，收到预期的存储拒绝错误。
4. `run_result.json` 同时变成 `status: failed`、`steps: []`、`results: []`；`run_state.json` 中第一步变成 failed，清除了该步的发布摘要。
5. `run_generation.json` 和 `generation_ownership.json` 也发生变化。已有步骤产物仍在，不能将其描述成所有计算文件被删除，但原有完成清单已被覆盖。

这对升级后误在 `/mnt/c` 续跑已有任务尤其直接。拒绝不支持的存储应作为执行前置条件，在占用 generation、改写状态和发布清单之前完成；不能先依赖该存储上的锁和原子写入，再判定它不受支持。

建议：在 `begin_generation` 之前校验 run root 和计划涉及的状态库位置；保留底层打开时的检查。补上“已完成 run 在不支持的存储上被拒绝后，状态、所有权和结果清单均不变”的应用层回归测试。

## F2 / P2：数据库文件符号链接绕过文件系统政策

位置：`confflow/persistence/work_items.py:630–647`，尤其 `select_journal_mode(os.path.dirname(resolved), ...)`。

`_validate_store_path` 只执行 `abspath`，没有拒绝最终数据库文件的符号链接。新检查只解析父目录的文件系统，随后 SQLite 打开实际目标文件。

复现使用真实 SQLite 文件和真实符号链接，注入一张包含本地 ext4 与目标 nfs4 的挂载表：

```text
local/work_items.sqlite -> network/work_items.sqlite
```

- 直接打开 `network/work_items.sqlite`：按预期拒绝，报错包含 `nfs4`。
- 打开本地目录里的符号链接：成功，SQLite 报告 `journal_mode = wal`。

因此，“本地目录 + 指向共享存储的数据库文件”仍能在没有批准文件时打开 WAL。测试没有使用真实 NFS，证明的是政策绕过路径，不是模拟数据库损坏。

建议：与控制库的数据库路径约束保持一致，拒绝数据库及相关侧文件的符号链接，或针对实际目标实施同等可靠的路径和存储校验。至少补齐最终数据库文件为符号链接的回归测试；只检测父目录符号链接不够。

## 功能边界：DELETE 批准文件尚未接入正常工作流入口

`shared_fs_approval_path` 目前只新增于 `SqliteWorkItemStore.open`。正式调用点 `V4RunApplication._run_step` 未传入该参数，`V4RunRequest` 和 CLI 也没有相应配置入口。

因此，直接调用底层 API 可以使用批准后的 DELETE，但正常工作流依然会拒绝共享存储。该事实不另计为阻塞缺陷；若本 PR 只交付底层能力，应在说明中限定范围。如果承诺用户可通过批准文件在共享存储运行，还需补齐参数传递和端到端验证。

## R8 验证

- 根包和控制模型的独立子进程导入不再加载 RDKit、NumPy、numba、psutil，也不修改全局日志禁用级别。
- 惰性标志真正尝试导入；损坏的可选包能按测试降级，原有 `njit` 回退得到保留。
- 对基线与 PR 的七组调用独立比较退出码、stdout 和 stderr，全部逐字节相同：`--version`、`--capabilities`、`--capabilities --json`、`control capabilities --json`、`config contract --json`、`control --json` 错误路径，以及缺失 handoff 的 worker JSON 路径。
- 本轮未发现需要阻塞合并的 R8 问题。上述入口比较不等于覆盖所有真实 worker 作业场景。

## R6 测量的适用范围与文档修正

`8 × 300` 的原始数据是组装累计 `1.6168 s`、总计 `43.3770 s`，约 `3.73%`，与报告约 4% 一致。该组装计时包含必要组装与重复组装，不能直接当成全部可消除的成本。测试是水分子输入、假计算执行器、真实持久化路径；支持本轮暂缓优化，不代表所有 cfr 工作流的固定比例。

取消时间需要修正文档：

| 指标 | PR 原始数据中位数 | 本次在 PR 提交上单次复跑 |
| --- | ---: | ---: |
| 取消请求到步骤返回 | 3.317 s | 1.987 s |
| 取消请求到整个 run 返回 | 8.996 s | 5.085 s |
| 已提交 Future | 3000 | 3000 |
| 提交期间内存增量 | 5,369,858 B | 5,368,890 B |

本次单次复跑使用原测量脚本，不作为新的性能基线。它确认步骤返回后还有运行终态发布开销；计划和 PR 描述中的“取消后约 3.3 秒返回”应明确是步骤返回，并同时给出整个 run 的时间。

原始 JSON 已如实标注 `N=3000` 的多步完整计时未做、`20 × 300` 只有一次；摘要也应保留这一适用范围。测量脚本目前只引用 `/tmp/claude-0/...`，建议将脚本或可重建它的完整方法随数据留存。

## 验证记录

- 新增存储策略、根包副作用及 core 测试：**59 passed**，10.83 s。
- 控制库、worker、执行服务、CLI、协议、持久化、续跑、崩溃矩阵、应用与架构检查：**630 passed, 1 skipped**，139.27 s。
- 合计：**689 passed, 1 skipped**。没有重跑全量 4956 项，不把作者报告的全量结果当成本次独立复跑结果。
- `mypy confflow`：199 个源文件通过。
- `ruff check confflow tests/test_root_import_side_effects.py tests/test_storage_policy.py`：通过。
- GitHub CI 最终复查：Python 3.10 / 3.11 / 3.12 / 3.13 测试矩阵和 changed-coverage 全部通过；Python 3.11 的 Black、Ruff、Mypy 门禁通过；JobDesk Consumer Contract 通过。非 PR 门禁的全量 coverage 作业按配置跳过。
- 行数预算增加与实际提交匹配：persistence 净增 237 行；新增两个测试文件共 658 行。独立归档中的架构预算检查通过。

复现与日志：

- 复现脚本：`/tmp/confflow_pr196_repro.py`。
- 复现结果：`/tmp/pr196-repro-gs5hwe_l/results.json`。
- 实际 run 文件：`/tmp/pr196-repro-gs5hwe_l/resume/run/`。
- `/tmp/confflow-pr196-targeted.log`。
- `/tmp/confflow-pr196-regression.log`。
- `/tmp/confflow-pr196-mypy.log`。
- `/tmp/confflow-pr196-r6-cancel.log`。

复现命令：

```bash
PYTHONPATH=/tmp/confflow-pr196-review.WJkWLs \
  /opt/ConfFlow/.venv/bin/python /tmp/confflow_pr196_repro.py
```
