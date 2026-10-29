# PR #196 第三次复核 — 2026-10-28

审查提交：`1a542b5052131f1717b93792cb72d6f00431a07d`。
对比提交：`75603dcae5cc8924926b16ff197d36470fc7d65f`。
PR：https://github.com/moxuezhuchen/ConfFlow/pull/196 （复核时未合并）。

结论：前两次复核提出的 P1、P2 已关闭，本轮未发现新的阻塞问题，建议合并当前提交。R6 保持测量后暂不优化的决定，R8 的修复保持有效。

## 修复位置与时序

- `confflow/application/v4_run.py:454`：先检查 root 的文件系统，再创建目录。
- `confflow/application/v4_run.py:502`：编译得到计划后，枚举所有步骤，在读取运行状态和 `begin_generation` 之前执行步骤数据库预检。
- `confflow/persistence/storage_policy.py:208`：`preflight_store` 只检查路径、符号链接和文件系统，不创建目录、不打开数据库。
- 步骤数据库本身及 `-wal`、`-shm`、`-journal` 侧文件都不能是符号链接；目录级符号链接仍按真实路径检测文件系统。
- 底层 `SqliteWorkItemStore.open` 保留原有检查，预检没有替代打开时的校验。
- 预检失败时 generation 尚未开始，异常处理不会发布失败清单，也不会修改旧运行状态。

## 独立复现

复跑第二次复核的脚本，只为“新目录不再存在”的正确结果增加了 `iterdir()` 存在性保护，未改变原有场景与结果采集。另跑一份将异常位置从第一步 `s_opt` 移至第二步 `s_sp` 的脚本，验证预检覆盖整张计划。

| 场景 | 最新提交的结果 |
| --- | --- |
| root 被检测为 NFS | 拒绝，原有文件内容不变，completed、2 个步骤保留 |
| 步骤目录通过符号链接指向 NFS | 拒绝，原有文件内容不变，completed、2 个步骤保留 |
| 步骤数据库文件是符号链接 | 拒绝，原有文件内容不变，completed、2 个步骤保留 |
| SQLite 侧文件是符号链接 | 拒绝，原有文件内容不变，completed、2 个步骤保留 |
| 异常在第二步数据库或其目录/侧文件 | 同样提前拒绝，第一步状态也不改写 |
| NFS 上全新且不存在的 run root | 拒绝，目录不创建 |
| 直接打开指向共享存储数据库的符号链接 | 拒绝，实际目标数据库内容不变 |

以上 NFS 场景使用注入的挂载表、真实 SQLite 文件和真实符号链接，执行器为受控 fake ORCA。另只读核对本机实际挂载：`/opt`、`/root`、`/tmp` 为 ext4，政策选择 WAL；`/mnt/c` 为 9p，政策拒绝。未操作 `/mnt/c` 上已有的 run。

## 回归有效性与 CI

- 当前提交的存储策略、入口预检、根包导入、V4 应用、状态库、续跑、控制库及架构检查：**496 passed**，113.43 秒。
- 将本提交新增的六个测试复制到上一提交的独立归档执行：**6 failed, 2 deselected**。失败均对应文件被改写或新目录被创建，证明测试能捕捉旧缺陷。
- GitHub CI 的 Python 3.10 / 3.11 / 3.12 / 3.13 矩阵、Black、Ruff、Mypy、changed-coverage 全部通过，JobDesk Consumer Contract 通过。全量 coverage 作业按 PR 配置跳过。
- 本次没有独立重跑全量 4971 项，不将作者的全量测试计数视为本次复跑计数。
- 预算增量核对一致：persistence +22、entry_cli +17、tests +98。

## 保留的行为边界

正常工作流仍只支持政策允许的本地文件系统；DELETE 批准文件仍只在底层 API 可用。共享存储和 `/mnt/c` 上的新建、续跑会被拒绝，既有状态由预检保护。R6 的耗时结论依照已写明的测量场景解读。

本次未修改生产代码或原有测试，未提交 GitHub 评论、批准或合并操作。审查在提交的独立归档 `/tmp/confflow-pr196-final.dEu35p` 中完成。

## 证据文件

- 第一处步骤异常复现：`/tmp/confflow_pr196_final_repro.py`。
- 第二处步骤异常复现：`/tmp/confflow_pr196_final_second_step_repro.py`。
- 结果：`/tmp/pr196-followup-repro-rv6mvnew/results.json`、`/tmp/pr196-followup-repro-xtfbvqnh/results.json`。
- 复现输出：`/tmp/confflow-pr196-final-repro.log`、`/tmp/confflow-pr196-final-second-step-repro.log`。
- 新测试在旧提交上的失败日志：`/tmp/confflow-pr196-final-baseline-regressions.log`。
- 当前提交的针对性回归及架构检查日志：`/tmp/confflow-pr196-final-tests.log`。
