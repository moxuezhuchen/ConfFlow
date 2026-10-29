# PR #199 后遗留项处理（2026-10-29）

来源：`docs/plans/review-post198-fix-20261029.md` 中“结果与遗留”一节。基线为 main `e29e885e`。

## 第 3 项：解析之后文件被原地替换（本 PR 修复）

**问题：** 原生计算和 MLIP 启动时使用的是已固定的绝对路径，但启动前不再复核文件。如果该文件在解析、测量之后、启动之前被原地替换，替换不会被发现，实际运行的程序与测量的程序不一致。

**修复：**
- `locate_command` 解析时记录文件指纹，包括 `os.stat` 得到的设备号、inode、大小、`mtime_ns`、`ctime_ns`，以及 realpath。
- 原生计算（`work_item_executor._resolve_executable`）和 MLIP（`mlip_executor`）启动前重新取一次指纹并比对。不一致或文件无法 stat 时，按 fail closed 拒绝启动，该条目记为失败。
- 选指纹而不是每次启动都算 sha256，是因为一次运行会启动几千个 xtb 条目，每次对几十 MB 的二进制算哈希开销不小。原地写入必然改变 `ctime`，`os.replace` 和改符号链接指向会改变 inode 或 realpath，这些都能被指纹发现。
- 已知的保守误报：对该文件执行 chmod 或新建硬链接也会改变 `ctime`，这时会拒绝启动。续跑时会重新解析和测量，不影响正确性。
- 没有指纹的 `ResolvedCommand`（调用方自己构造的，例如脚本执行器）不做这项检查。脚本解释器已有启动前的 sha256 校验（`verify_interpreter_identity`）。

## 第 1 项：控制服务测量自身（按设计关闭）

`workflow_adapter._CurrentProcessIdentity` 测量的是控制服务自己的 Python 解释器，默认取 `sys.executable`，本身是绝对路径。只有在 `control.py` 的 `identity_executable` 传入裸名称时，才会按控制进程的 PATH 查找。被启动的就是控制进程本身，用控制进程的 PATH 是正确的，与步骤的启动环境无关，因此不需要修改。

## 第 2 项：脚本注册时的解释器身份（用户决定保持现状）

`script_registry._resolve_interpreter_identity` 在注册时按控制进程的 PATH 解析解释器。启动前已有 sha256 校验兜底：注册时如果解析到了别的文件，启动前会被拒绝，不会运行错误的程序。如果改为依赖步骤环境，已编译的摘要和 `script_item_key` 会改变，旧结果会全部失效，代价大于收益。另一种做法是强制要求绝对路径，但这会使现有写裸名称的配置报错。用户于 2026-10-29 决定保持现状。

## 验收

- `tests/v4/test_launch_fingerprint.py`：原地改写、`os.replace`、符号链接改指向都会被拒绝启动；文件未变则正常启动；原生和 MLIP 两条路径都覆盖。
- 现有契约测试 `test_executor_command_contract.py`、`test_pr199_resolution_pin.py`、`test_review_post198_v1_mlip_failure.py` 全部通过。
- 全量测试、ruff、black（CI 版本）、LOC 门禁全部通过。

## 第 4 项：ConfGen 接入指纹检查（用户 2026-10-29 同意，并入本 PR）

**问题：** 应用层对 xtb 做 sha256 测量，之后 ConfGen 搜索会持续数小时，调用 xtb 几千次。`XtbCalculator` 在开始时也算了一遍 sha，但只写进统计信息，从不比对。测量之后 xtb 被替换，结果仍会按旧身份登记。

**修复：**
- `v4_run.resolve_confgen_xtb_binding` 同时返回 `ResolvedCommand`（带解析时的指纹，指纹在 sha 测量之前取得），ConfGen 步骤也把它放进 `resolved_command`。
- `confgen_search_run`：有 `resolved_command` 时，在建目录、写任务之前先调用 `verify_launch_unchanged`，再把指纹写入 `search_job.json` 的 `xtb_fingerprint` 字段。
- worker / `XtbCalculator`：任务带指纹时，开始搜索前以及每次启动 xtb 子进程前，都和当前文件比对一次（只做 stat，开销可忽略）。不一致时抛出 `ExecutableChanged`。worker 把它当作任务失败处理（`input_baseline` 记为 failed，退出码非 0），不当作普通的单次优化失败，因此整个条目记为 FAILED，不发布结果。
- 没有指纹的直接调用（旧任务文件、测试）行为不变。
- 已核对异常传播路径：`_relax_one` / `_launch` / `hessian` → `explore._run_round`（`f.result()`，没有拦截）→ search → worker，worker 显式处理该异常。唯一会吞掉异常的地方是 `explore.py` 的 `_settle_planned`：到达时限后丢弃排队中的优化时，用了 `except Exception: pass`。这里保持不改，原因有二：
  1. 抛出 `ExecutableChanged` 时，校验发生在启动之前，被替换的程序从未运行；而这批结果本来就要被丢弃。之后所有 xtb 启动（包括 release）都会再校验一次，并让任务失败。因此被替换的程序产出的结果不会被发布。
  2. `science/` 层不能导入 `execution` 层。
