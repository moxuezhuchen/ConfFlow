# #200 后复审修复计划（W1–W3，2026-10-29）

来源：`docs/reviews/review-post200-20261029.md`（复现脚本与结果在 `docs/reviews/data/review-post200-20261029/`）。基线 main `31392be3`。三项均为 P1：等待配额期间被替换的程序或模型，其结果按旧身份登记，恢复原文件后又被错误复用。

原则：
- 保留现有分层。
- 最终检查统一放在取得配额之后、`supervisor.submit()` 之前，使用公共启动入口的 `before_submit` 回调。
- 检查失败时释放配额，条目记为 FAILED，不启动、不发布；续跑会重新解析、重新测量，然后重算。
- 排队前已有的检查保留，用于尽早失败。

共享字段（Claude 已先加好）：`StepExecutionRequest.expected_mlip_runtime`、`ItemExecutionContext.expected_mlip_runtime`（`FrozenDict | None`），batch 原样传递。

## 任务 A：W1 原生计算 + W2 脚本

拥有文件：`confflow/execution/work_item_executor.py`、`confflow/execution/script_executor.py`、新测试 `tests/v4/test_review_post200_w1_native_w2_script.py`。

- **W1 原生计算：** 在 `_launch_and_wait` 调用处（约 676 行）传入 `before_submit=lambda: verify_launch_unchanged(command)`，`command` 用的是该条目解析出的同一个 `ResolvedCommand`，不再搜索 PATH。检查失败时返回明确的失败结果（沿用 executor 现有的错误收尾方式，消息含 "changed after it was resolved and measured"），配额由公共入口释放。
- **W2 脚本：** `_verify_at_launch` 增加参数，接收实际启动的解释器路径，在取得配额后调用 `verify_interpreter_identity(entry, interpreter)`。单元素命令（脚本自身作为解释器）已有脚本 sha 校验，不重复处理。注册身份规则与摘要不变。
- **测试：** 使用真实 `ServerQuota` 等待（参考 `docs/reviews/data/review-post200-20261029/quota_launch_probe.py` 和 `assets_quota_probe.py`），在等待期间分别做原地改写、`os.replace`、改符号链接指向。释放配额后不得启动替换后的程序，不得发布结果，配额不得泄漏；恢复原文件后续跑能重算成功。需覆盖：控制 PATH 和步骤 PATH 指向不同文件、内容相同时仍可运行；只替换步骤解释器时会被拒绝。

## 任务 B：W1 MLIP + W3 模型 / refs

拥有文件：`confflow/execution/mlip_executor.py`、`confflow/execution/mlip_runner.py`、`confflow/execution/environment.py`（只动 `mlip_runtime_identity` 一带）、`confflow/application/v4_run.py`（只动 `_mlip_runtime_digest` 和 MLIP 分支，以及把字段放进 request）、新测试 `tests/v4/test_review_post200_w1_mlip_w3_assets.py`。

- **environment：** 新增 `mlip_runtime_components(...) -> dict | None`，返回现有 payload（含 interpreter / runner / model / refs 的 sha256、packages，以及模型和 refs 的绝对路径）。`mlip_runtime_identity` 改为对同一 payload 求摘要。**摘要必须逐字节不变**，路径不进入摘要，否则旧的 MLIP 结果会全部重算。需加测试锁定这一点。
- **v4_run：** 测量 MLIP 时取得 components，将 `FrozenDict` 放进 `expected_mlip_runtime`，摘要从同一份 components 计算，只测量一次。
- **W1 MLIP：** `launch_and_wait` 传入 `before_submit`，在取得配额后执行两项检查：`verify_launch_unchanged(resolved)`；以及复制到 work_dir 的 `mlip_runner.py`，其 sha256 必须等于 expected 中的 `runner_sha256`（存在时）。
- **W3：** 任务文件增加 `expected_model_sha256` / `expected_refs_sha256`（存在时）。runner 在 `main` 中加载前先取 stat 指纹，计算 sha256 并与 expected 比对，再取一次指纹，确认两次相同。之后每次 `_load_backend`（单进程和每个 pool worker）在调用 factory 前后都确认指纹与这次一致。不一致时抛出 `BackendLoadError`，消息含 "model file changed after it was measured"（refs 同理），走现有的 `backend_failed` 路径，结果为 FAILED，续跑重算。runner 只用标准库。没有 expected 的旧任务行为不变。
- **测试：** 真实配额等待期间替换模型、替换 refs，结果必须被拒绝；恢复后续跑结果正确（-5），不得 reuse_hit 到 -10。多 worker 下也被拒绝。等待期间替换解释器、替换 runner 副本，均被拒绝。摘要不变的测试。

## 验收

- `tests/v4/test_review_post200_*.py` 全部通过，并做变异检查：关掉对应检查后，拒绝用例应失败。
- 用修复后的代码重跑两个复现脚本：替换场景应为 failed 或被拒绝，不得出现 -10 或 z=2 的复用。
- 回归测试：`test_launch_fingerprint.py`、`test_confgen_launch_fingerprint.py`、`test_executor_command_contract.py`、`test_pr199_resolution_pin.py`、`test_review_post198_v1_mlip_failure.py`、MLIP 组、脚本组。
- 全量测试、ruff、mypy、black 26.10.1、LOC 门禁全部通过。

## 结果（2026-10-29）

W1–W3 已修复。修复后用当前代码重跑了两个复现脚本，结果保存在 `docs/reviews/data/review-post200-20261029/postfix-*.json`：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 原生计算：等待配额期间替换程序 | 启动替换后的程序，completed | failed："changed after it was resolved and measured"；没有启动任何程序 |
| MLIP：等待配额期间替换解释器 | completed，-10；续跑 reuse_hit -10 | failed；续跑重算得到 -5 |
| 脚本：等待配额期间只替换步骤解释器 | z=2；续跑 reuse_hit z=2 | failed（解释器与注册内容不一致）；续跑 z=1 |
| MLIP：等待配额期间替换模型 | -10；续跑 reuse_hit -10 | 见下 |

所有场景下配额租约都已释放。

**复现脚本的说明：**
- `quota_launch_probe.py` 默认原生程序一定会启动，修复后它会因为没有 `native-launches.log` 文件而报错退出。重跑时临时把"没有日志"当作"没有启动"处理，仓库里的归档副本没有修改逻辑。
- `assets_quota_probe.py` 的模型场景把整个 `mlip_runner.py` 换成了不做任何检查的替身（来自 `tests/v4/test_arch_review_launch_identity._install_mlip_runner`），所以修复后它仍显示 -10。这不代表生产路径有问题：生产环境始终使用真实的 runner，它的 sha 也计入身份摘要。W3 的验收改由 `tests/v4/test_review_post200_w1_mlip_w3_assets.py` 承担，该测试使用真实的 runner 源码，只替换 UMA 后端。覆盖的场景：模型替换、refs 替换、多 worker、解释器替换、runner 副本被篡改，全部被拒绝，续跑得到 -5，新 run 对照得到 -5。

**身份摘要：** 逐字节不变（测试 `test_identity_digest_is_byte_identical_to_the_pre_change_formula`），旧的 MLIP 结果不会因此重算。

**已知的小问题（不阻断）：** MLIP 在取得配额后被拒绝时，条目代码是 `native_input_error`，拒绝原因写在 diagnostics 里，`errors` 列表为空。
