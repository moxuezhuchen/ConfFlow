# R6–R8 计划 — 2026-10-28

来源：`docs/reviews/architecture-review-20261028.md` 的 R6–R8，以及 GPT 对上一版方案的意见：R7 不做自动回退，R8 补兼容验证，R6 先测量。
分支 / worktree：`refactor/arch-review-r7r8-20261028` @ `/opt/cf-worktrees/arch-r7r8-1028`。
执行：3 个 Haiku 5.5 subagent 并行，文件互不重叠，不提交；Claude 负责验收、预算、全量门禁（含 `ruff check .`）、PR。

## 决定

1. **R7 两类状态库用同一条存储规则。**
   - 只在本地文件系统上开 WAL（`ext4/xfs/btrfs/tmpfs/overlay/apfs`，挂载类型不是 `9p/nfs/nfs4/cifs/smbfs/fuse.sshfs`），规则与控制库现在的 `_assert_wal_supported` 相同。
   - 非本地存储默认拒绝，报错写清文件系统类型和解决办法：把 run root 放到本地文件系统。
   - 只有显式提供经过验证的批准文件（`ApprovalVerifier`，声明锁、原子重命名、fsync 三项保证）时，才允许改用 DELETE 模式。
   - 设置日志模式后检查 SQLite 实际返回的模式，不一致就报错。
   - 检测函数和批准文件校验下沉到 `confflow/persistence/storage_policy.py`；application 层改为从这里导入，并在原位置保留兼容导出。persistence 不得 import application。
   - 已知影响：cfr 现有 run root 在 `/mnt/c`（9p）上。新代码会拒绝在那里打开 WorkItemStore，以后 run root 要放在 WSL 的 ext4 盘上。已有的 run 不动。
2. **R8 根包无副作用。**
   - `confflow/__init__.py` 不再在导入时加载 RDKit/NumPy/numba/psutil。`RDKIT_AVAILABLE`、`PSUTIL_AVAILABLE`、`NUMBA_AVAILABLE`、`Chem`、`AllChem`、`njit` 改为第一次访问时才检测（模块级 `__getattr__`）。
   - 检测方式是真的尝试导入，并缓存结果；能找到包不等于能导入。
   - `njit` 的回退行为不变。
   - `logging.disable` 从根包移到 CLI、worker 入口，在任何重依赖导入之前执行。版本探测、`--capabilities`、`control --json`、`config --json`、worker JSON 的输出必须与现在完全一致。
3. **R6 先测量，不改代码。** 用假执行器在本机测（已获批准，几分钟），结果写成报告，再决定组装和背压是否要改。

## 任务

| Task | 内容 | 拥有的文件 | 新测试 / 产出 |
| --- | --- | --- | --- |
| A | R7 | `confflow/persistence/work_items.py`、新文件 `confflow/persistence/storage_policy.py`、`confflow/application/execution/sqlite.py`、`confflow/application/execution/shared_fs_approval.py` | `tests/test_storage_policy.py` |
| B | R8 | `confflow/__init__.py`、`confflow/cli.py`、`confflow/v4cli.py`、`confflow/control_worker.py`（只改入口处的日志设置）、`tests/test_core.py` | `tests/test_root_import_side_effects.py` |
| C | R6 测量 | 只在 scratchpad 下工作，不改仓库 | `docs/plans/data/r6-measurement-20261028.json` 和报告正文（交给 Claude） |

## 硬性规则

与之前两轮相同：
- 只改自己拥有的文件，不提交；
- 不放宽测试或预算；
- 回归测试必须在修复前失败；
- 改动的生产代码行覆盖率 ≥ 85%；
- 执行层不 import `confflow.core`；
- 本地先跑 `ruff check .` 和 `black --check`；
- 结束前清理后台进程。

## 验收

- **R7：**
  - `9p`/`nfs`/未知文件系统（用注入的检测函数模拟）→ 拒绝打开，报错信息里有文件系统类型；
  - 本地文件系统 → WAL，并核对 SQLite 实际返回的模式；
  - 有效批准文件 → DELETE；
  - 实际模式与请求不符 → 报错；
  - 架构检查确认 persistence 不 import application。
- **R8：**
  - 子进程里 `import confflow.application.execution.models` 之后，`rdkit`/`numpy`/`numba` 不在 `sys.modules` 中，`logging.root.manager.disable == 0`；
  - 访问 `confflow.RDKIT_AVAILABLE` 的结果与现在一致；
  - 包存在但导入失败时，标志为 False；
  - `njit` 回退可用；
  - 上面列出的 CLI JSON 输出逐字节不变（新旧代码对比）。
- **R6：** 报告给出以下数据，并说明测量方法：
  - 步骤数 S∈{3,8,20} × 每步 item 数 N∈{10,300,3000} 时的组装调用次数、累计耗时；
  - 峰值内存；
  - width=4 时，N=3000 的队列里发出取消后到步骤返回的时间。

## R6 测量结果（2026-10-28）

- **数据：** `docs/plans/data/r6-measurement-20261028.json`。
- **方法：** 基线 943aae29；真实的 `V4RunApplication` 持久化路径，配假计算执行器；width=4；每组取 3 次的中位数。

| S × N | 总耗时 | 重复组装耗时 | 占比 |
| --- | --- | --- | --- |
| 3 × 300 | 16.6 s | 0.26 s | 2% |
| 8 × 300 | 43.4 s | 1.6 s | 4% |
| 20 × 300 | 132 s | 9.5 s | 7%（单次） |

- **组装：** 开销按 S² 增长，在常用规模下只占百分之几。
- **排队：** N=3000 时，排队 Future 约 5 MB（约 1.8 KB/项）。
- **取消耗时：** 从发出取消到**步骤**返回约 3.3 s；到**整个 run** 返回约 9.0 s，多出的是终态发布。两者都是 3 次的中位数。
- **组装计时口径：** 组装耗时同时包含必要的组装和重复的组装，不全是能消除的成本。
- **适用范围：** 输入是水分子，执行器是假计算执行器，持久化走真实路径；20×300 只跑了 1 次，N=3000 的多步整链没有跑。这些数字支持本轮不改，不代表所有 cfr 工作流的固定比例。
- **脚本和原始数据：** `/opt/confflow-archive/experiments-20261028/r6-measurement/`（`scripts/r6_driver.py`、`scripts/r6_grid.py`、`raw_runs.jsonl`、`cancel_valid_runs.jsonl`）。
- **更大的开销：** 每个 item 走持久化路径约 16 ms，大 N 时比组装更显著。
- **结论：** R6 本轮不改。等常规工作流达到几千 item × 十几步时，先做按步骤组装。

## R7 范围说明

- DELETE 批准文件目前只在底层 API（`SqliteWorkItemStore.open(shared_fs_approval_path=...)`）可用。`V4RunRequest`、CLI 和正常工作流入口都没有接入，所以正常工作流在共享存储上一律拒绝。要支持共享存储，需要另外补参数透传和端到端验证。
- PR #196 复核（`docs/reviews/pr196-verification-20261028.md`）发现两个问题，已在同一分支修复：
  - 存储预检提前到取得 generation 之前，被拒绝时不写任何运行状态；
  - 拒绝数据库文件及其侧文件是符号链接的情况。
