# PR #196 第二次复核 — 2026-10-28

审查提交：`75603dcae5cc8924926b16ff197d36470fc7d65f`。
前次审查提交：`5d90a89c03edcc066c4ef5bdbbdbbe0134765ff6`。
PR：https://github.com/moxuezhuchen/ConfFlow/pull/196 （复核时 open、未合并）。

结论：P2 的底层符号链接绕过已修复；P1 的整根目录位于不支持的文件系统的场景已修复，但步骤存储位置的预检仍缺失，P1 尚未完整关闭。建议补齐后再合并。

## 剩余 P1：预检只覆盖 run root，步骤状态库的拒绝仍会覆盖完成清单

位置：`confflow/application/v4_run.py:395–402`，`_storage_preflight` 仅调用 `select_journal_mode(run_root)`。运行入口在 `:442` 调用它，但它没有枚举步骤状态库，也没有调用 `reject_database_symlinks`。

根目录位于本地文件系统，并不意味着每个步骤数据库都位于同一个文件系统。PR 明确允许目录符号链接；步骤目录可能通过符号链接或子挂载点落在其他文件系统。数据库及侧文件的符号链接拒绝也只发生在后续的 `SqliteWorkItemStore.open`。

真实 V4 两步运行、真实 SQLite 文件、受控 fake ORCA 的独立复现结果如下。NFS 情况仅注入挂载表，未使用真实共享存储；数据库及目录符号链接均为真实文件系统对象。

| 续跑前的存储状态 | 预期拒绝 | 拒绝后的结果 |
| --- | --- | --- |
| 整个 run root 被检测为 nfs4 | 是 | 文件内容全部保持不变；completed、2 个步骤保留（已修复） |
| run root 本地，`steps/s_opt` 符号链接到 nfs4 目录 | 是 | completed 变 failed，清单步骤数从 2 变 0 |
| run root 本地，`work_items.sqlite` 是指向本地数据库的符号链接 | 是 | completed 变 failed，清单步骤数从 2 变 0 |
| run root 本地，`work_items.sqlite-journal` 是符号链接 | 是 | completed 变 failed，清单步骤数从 2 变 0 |

后三种情况都修改了以下四个原有文件：

```text
generation_ownership.json
run_generation.json
run_result.json
run_state.json
```

这不是底层 WAL 仍能绕过的新发现：底层拒绝正常工作。问题仍是上轮 P1 的检查时序——拒绝发生时，应用已经开始新 generation，并在异常处理中覆盖旧清单。原有计算产物并未全部删除，不应描述为所有结果文件丢失。

建议将应用预检补齐为：

1. 保留 root 的文件系统检查。
2. 编译得到 `plan.steps` 后，在 `arbitration.begin_generation` 和任何运行状态写入之前，对每个 `store_path(run_root, step_id)` 检查数据库及侧文件符号链接，并校验实际父目录的文件系统。
3. 该阶段只读取路径和挂载信息，不创建或打开数据库；底层打开时继续保留检查。
4. 增加上述后三种情况的应用层回归，断言拒绝后整个 run 的文件内容、完成清单和 generation 所有权不变。

这里的“编译后、begin_generation 前”是对已有预检补全，不需要重构执行、续跑或发布机制。

## 已确认通过的修复

- 直接打开本地目录中的数据库符号链接现在抛出 `StorageUnsupportedError`，实际目标数据库内容不变，旧的 WAL 绕过已封闭。
- `-wal`、`-shm`、`-journal` 的符号链接检查发生在 SQLite 打开数据库之前。
- 整个 run root 位于不支持的文件系统时，原复现的续跑拒绝不再修改任何原有文件。
- R6 文档已区分步骤返回 3.3 秒与整个 run 返回 9.0 秒，并说明输入、执行器、计时口径及未测规模。
- DELETE 只暴露于底层 API、工作流和 CLI 尚未接入的范围已写明。
- `/opt/confflow-archive/experiments-20261028/r6-measurement/` 中脚本及原始数据存在；归档 `r6_driver.py` 的 SHA-256 与上轮复跑使用的脚本一致。

## 非阻塞说明：空目录副作用仍可能发生于正常入口

`V4RunApplication.run` 的 `os.makedirs(run_root, exist_ok=True)` 在 `:439`，早于 `:442` 的预检。因此不只是底层 API：正常工作流在新建一个不支持的 run root 时，也会先创建空目录，再拒绝。本次独立复现确认目录从不存在变为存在且为空。

这没有修改已有运行状态，不另计为合并阻塞项；但“正常工作流不会留下空目录”的说明不准确。

## 验证与边界

- 在提交的独立归档 `/tmp/confflow-pr196-followup.BoPalz` 上执行存储策略、入口预检、根包副作用、V4 应用、状态库、续跑、控制库测试：**140 passed**，32.25 秒。
- 远端 CI 对应本提交：Python 3.10 / 3.11 / 3.12 / 3.13 矩阵、Black、Ruff、Mypy、changed-coverage 全部通过；JobDesk Consumer Contract 通过。全量 coverage 按 PR 配置跳过。
- 本次没有独立重跑全量 4965 项，也没有用当前 140 项测试通过来代替对上述缺口的复现。
- 预算增量核对一致：persistence +22、entry_cli +14、tests +168。
- 未修改生产代码、原有测试或 `/mnt/c` 上的 run，未提交评论、批准或合并 PR。

复现与日志：

- 脚本：`/tmp/confflow_pr196_followup_repro.py`。
- 结果：`/tmp/pr196-followup-repro-w3gv925y/results.json`。
- 输出：`/tmp/confflow-pr196-followup-repro.log`。
- 回归日志：`/tmp/confflow-pr196-followup-tests.log`。

```bash
PYTHONPATH=/tmp/confflow-pr196-followup.BoPalz \
  /opt/ConfFlow/.venv/bin/python /tmp/confflow_pr196_followup_repro.py
```
