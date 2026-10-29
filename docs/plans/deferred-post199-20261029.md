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
