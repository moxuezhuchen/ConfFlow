# #198 合并后复审 V1–V3 修复计划 · 2026-10-29

来源：`docs/reviews/review-post198-20261029.md`（基线 71cc92ee），复现脚本和结果在 `docs/reviews/data/review-post198-20261029/`（`runtime_probes.py`、`runtime-results.json`）。
分支 / worktree：`refactor/review-post198-20261029` @ `/opt/cf-worktrees/review-post198`。

用户决定（2026-10-29）：
- 旧的"空完成" MLIP 结果：**窄失效**。复用时识别出"所有输入结构都因错误被丢弃、输出为空"的已存 COMPLETED 结果，不复用、改为重算。不升 MLIP 契约版本。
- 所有结构都单独报错（不是后端加载失败）：**算失败**（FAILED）。只有合法筛选导致的空集合（例如 stretch 筛除、能量窗口筛除）仍算 COMPLETED。

## 公共规则

- 只改自己拥有的文件，其他文件只读。确实需要改别的文件时，先停下来报告。
- 先写回归测试，确认它在修复前失败。测试必须通过公共入口（`V4RunApplication.run()` 或 `execute_step_resumable()`）断言对外可见的结果，不能只用 spy。
- 门禁：`/opt/ConfFlow/.venv/bin/ruff check <改动文件>`（含 D401），`/opt/ConfFlow/.venv/bin/black --check <改动文件>`，`/opt/ConfFlow/.venv/bin/mypy confflow`。
- 运行测试：`cd /opt/cf-worktrees/review-post198 && PYTHONPATH="$PWD" /opt/ConfFlow/.venv/bin/python -m pytest <files> -q -o addopts=''`。本地没有 pytest-xdist；单条命令不要超过约 10 分钟，大集合分批跑。
- 不运行真实 ORCA / Gaussian / xTB / fairchem，使用仓库已有的假程序 / 假后端。
- 不提交、不推送、不开后台进程。临时文件只写到 `/tmp/claude-0/-opt-ConfFlow/bfc5cc9d-eb0c-41d7-a82d-612d2df713ea/scratchpad`，不写到 `/`，不生成大文件。
- 不修改 `tools/loc_budget.json`（由 Claude 调整）。不放宽现有断言；如果已有测试依赖旧行为，先报告。
- 交付报告：改动文件、设计要点、新测试名、修复前失败 / 修复后通过的证据、门禁输出摘要、遗留问题。

## 任务 A — V1（P1）：MLIP 后端失败与"全部报错"算失败；旧空结果不复用

拥有文件：`confflow/execution/mlip_runner.py`；`confflow/execution/mlip_executor.py` 中**结果判定 / 组装部分**（`_assemble` 及其以下，约 622 行之后；不要改 `_resolve_executable` 和约 495–520 行的解释器解析与策略调用，那部分在任务 C 里处理）；`confflow/execution/batch.py`（只为第 3 点的复用钩子）；新测试 `tests/v4/test_review_post198_v1_mlip_failure.py`。

问题：`mlip_runner._load_backend()`（约 236 行）把后端构造异常吞掉，换成 `_BrokenBackend`，于是每个结构都得到一条 error row，`run_job()` 仍返回 0 并写出完成文件；`mlip_executor` 约 910 行无条件返回 COMPLETED。结果：后端临时故障被持久化成一个可复用的空结果，后端恢复后，同一 run root 续跑仍是 reuse_hit。

修复：
1. runner：后端初始化失败（单 worker 和多 worker 的 `_init_worker` 都要覆盖）成为**任务级失败**：在结果文件 / done 文件中写一个明确的任务级错误标记（例如 `backend_error`），不再把它伪装成逐结构的 error row；runner 退出码或 done 文件状态要能让 executor 无歧义地识别出来。不能让 done 文件、退出码和最终状态互相矛盾。
2. executor：识别到任务级后端失败时，返回 `WorkItemStatus.FAILED`，`retryable=True`，诊断写明后端错误原因。已存在的 FAILED 行在续跑时会经 `RETRY_FAILED` 自动重算（reuse.py 约 507 行）——确认这一点，并用测试证明。另外，所有输入结构都因计算错误被丢弃、且没有任何结构是被合法筛选掉的 → 也返回 FAILED（诊断说明全部报错）。至少有一个结构被合法筛除、或至少有一个输出时，保持 COMPLETED。可重试与否：后端失败为可重试；全部单结构报错也设为可重试（续跑会重算）——如果发现 RETRY_FAILED 对两者的处理不同，在报告中说明。
3. 旧空结果窄失效：已存的 MLIP COMPLETED 结果如果"没有输出结构，且诊断显示所有输入结构都因错误被丢弃"（以 `mlip_executor` 已有的 `mlip_completed` 诊断 / dropped_error 信息为准，先读懂已存结果里实际有哪些字段），续跑时不复用、改为重新执行。实现方式：在 batch.py 复用判定之后、命中复用之前加一个通用钩子（例如执行器可选实现 `stored_result_reusable(stored_result) -> bool`，默认 True），MLIP 执行器实现它。不可复用时走正式的状态迁移开新 attempt（参考已有的 interrupted → next-attempt 路径），保留旧 attempt 历史，并给出一条诊断说明原因。不改 reuse.py 的轴比较规则。
4. 测试：
   - 公共入口：后端初始化失败 → run 不是 completed（该 item FAILED，并带有后端错误诊断）；恢复后端后同一 run root 续跑 → 真正重新计算，得到 1 个结构 / 1 条能量；新 run root 对照组正常。单 worker 和多 worker 各一例。参考 `docs/reviews/data/review-post198-20261029/runtime_probes.py` 中的 `backend_outage`。
   - 全部单结构报错 → FAILED；部分报错 → COMPLETED，保留剩余输出；合法筛选为空（例如 stretch 全部筛除）→ 仍为 COMPLETED。
   - 旧空结果：手工写入（或用旧行为生成）一个"全部报错、空输出"的 COMPLETED 结果，续跑时不出现 reuse_hit，而是重新执行，并且旧 attempt 仍在历史中。合法筛选为空的 COMPLETED 结果仍正常复用。
回归：`tests/v4/test_mlip_*.py`、`test_arch_review_1029_mlip_contract.py`、`test_arch_review_mlip_scientific.py`、`test_arch_followup_mlip_probe.py`、`test_pr197_mlip_upgrade_reuse.py`、`test_v43_*.py`、`test_arch_review_1029_resume_identity.py`、`test_deferred_d5_input_artifact_bytes.py`、`test_pr198_refusal_diagnostics.py`。

## 任务 B — V2 + V3：统一的命令解析入口（native / ConfGen / script）

拥有文件：新模块 `confflow/execution/command_resolution.py`；`confflow/execution/work_item_executor.py`；`confflow/execution/environment.py`；`confflow/application/v4_run.py`；`confflow/execution/script_executor.py`；新测试 `tests/v4/test_executor_command_contract.py`；以及因此需要给直接调用补上快照的测试文件（先列出，再改；不放宽断言）。
不要碰：`mlip_executor.py`、`mlip_runner.py`、`batch.py`（任务 A）。

问题：
- V2：`script_executor.py` 约 266 行在策略检查前已把解释器解析成绝对路径，两个参数都传绝对路径，丢失原始名称，`allowed_executables: [python3]` 被拒绝。（MLIP 的同一问题放在任务 C。）
- V3：原生计算在 `v4_run.py` 约 944 行预检、`environment.py` 约 193 行身份测量、`work_item_executor.py` 约 1142 行启动，都用不带 path 的 `shutil.which()`，按控制进程 PATH 解析，忽略步骤 `execution.env.PATH`。脚本解释器（`script_executor.py` 约 457 行）同样如此。

修复：
1. 新建 `command_resolution.py`，提供唯一的解析入口，例如 `resolve_command(configured: str, env: Mapping[str, str]) -> ResolvedCommand(configured, absolute)`：在给定的有效环境的 PATH 上解析一次（带目录部分的路径做 abspath），找不到或不可执行时抛 DomainError；`absolute` 为最终要测量、要启动的文件。另提供一个策略检查的辅助函数：`executable=configured` 用于名称规则，`resolved_executable=realpath(absolute)` 用于绝对路径规则。ConfGen 现有的 `resolve_confgen_xtb_binding`（v4_run.py）改为基于这个入口实现，行为保持不变（N2 / F3 的测试必须继续通过）。
2. 原生计算：在构造出步骤的有效环境快照（binding env 叠加在 ambient 快照之上，与 ConfGen / MLIP 相同的规则）之后才做预检，用快照解析一次；预检策略、身份测量（environment.py）和实际启动（work_item_executor.py）都使用这一次解析得到的绝对路径。不能出现"测量 A、启动 B"。
3. 脚本执行器：解释器按同一入口、在该步骤的有效环境里解析；策略检查传原始名称和解析后的路径。
4. 契约测试 `test_executor_command_contract.py`（公共入口，假程序）：对 native、ConfGen、script（MLIP 在任务 C 中补充），各覆盖：(a) 裸名允许 → 完成，并且启动的是步骤 PATH 上解析到的那个文件；(b) 对应的绝对路径允许 → 完成；(c) 只允许另一个绝对路径 → 拒绝且不启动；(d) 控制进程 PATH 指向 A、步骤 PATH 指向 B → 测量和启动都是 B，允许 A 时拒绝。参考 `runtime_probes.py` 中的 `native_binding_path`、`script_allow_*`。
回归：`test_review_fix_execution_policy.py`、`test_arch_review_1029_confgen_xtb_identity.py`、`test_confgen_search*.py`、`test_v4_runtime_cutover.py`、`test_script_*.py` / 含 `script_executor` 的测试、`test_execution_identity_*.py`、`tests/test_execution_identity_resume.py`、`test_repair_environment*.py`、`test_v42_executors.py`，以及 grep `shutil.which`、`_resolve_executable`、`enforce_execution_policy` 命中的测试。

## 任务 C — V2 / V3 的 MLIP 部分（任务 A、B 完成后再做）

拥有文件：`confflow/execution/mlip_executor.py` 中约 495–520 行的解释器解析与策略调用，以及 `_resolve_executable`（约 612 行）；`tests/v4/test_executor_command_contract.py` 中补充 MLIP 用例。
修复：MLIP 解释器改用任务 B 的 `resolve_command`，在 `_launch_env(context)` 得到的有效环境中解析；策略检查传原始名称和解析后的路径；启动使用同一个绝对路径。契约测试补上 MLIP 的 (a)–(d)。

## Claude 验收

审 diff；全量 pytest（8 片）、ruff、black 26.10.1、mypy、v4_arch_scan、architecture policy / loc_budget；重跑 `runtime_probes.py`，确认 V1–V3 的所有场景都已修好；提交、推送、开 PR。

### 任务 C 扩充（2026-10-29，任务 A、B 完成后）

任务 B 的报告指出，MLIP 之外还有同类残留。任务 C 的拥有文件扩大为：`confflow/execution/mlip_executor.py` 中的解释器解析 / 策略调用 / `_resolve_executable`；`confflow/application/v4_run.py` 中的 `_enforce_mlip_interpreter_policy`；`confflow/execution/environment.py` 中的 `_resolve_interpreter`（MLIP 身份）；`confflow/execution/script_registry.py`（约 139 行 `_resolve_interpreter_identity`）；`tests/v4/test_executor_command_contract.py`。
要求：
1. MLIP：`env = launch_environment(context.native_env, binding)`，`cmd = resolve_and_enforce_command(binding, interpreter, env, label=...)`；启动和身份测量都使用 `cmd.absolute`。应用层的 `_enforce_mlip_interpreter_policy` 和 `environment._resolve_interpreter` 在同一个有效环境中解析，不再用不带 path 的 `shutil.which`。
2. 注册脚本：解释器身份必须与实际启动的解释器是同一个文件。如果注册阶段拿不到步骤环境，就把身份解析推迟到有步骤快照的地方，或在启动前校验两者一致、不一致时 fail closed——选哪种方案都要在报告中说明理由。
3. 契约测试补充：MLIP 的 (a)–(d)；注册脚本"控制进程 PATH 指向 A、步骤 PATH 指向 B"时，身份与启动是同一个文件（或明确拒绝）。
4. `confgen_xtb.py` 的 `shutil.which` 保持不动（此时 binding 已是绝对路径），在报告中确认。
回归：任务 A、B 列出的 MLIP 组和执行身份组，以及 `test_script_*`、`script_registry` 相关测试。

## 结果与遗留（2026-10-29）

V1–V3 已修复，验收结果见 PR 描述。不阻断合并的遗留项：
- `application/execution/workflow_adapter.py` 的 `_resolve_executable` 仍按控制进程 PATH 解析。它用于控制服务测量自己启动的程序，不在步骤 binding / 启动路径上。
- `script_registry._resolve_interpreter_identity` 在注册时仍按控制进程 PATH 解析解释器身份；现在启动前会按内容（sha256）校验步骤 PATH 解析到的解释器，不一致时 fail closed。需要用别的解释器时，应在注册时使用绝对路径。

## 任务 D — A2（PR #199 验收 P1）：解析结果贯穿到启动

来源：`docs/reviews/pr199-acceptance-20261029.md`，复现脚本 `docs/reviews/data/pr199-acceptance-20261029/resolution_pin_probe.py`。
拥有文件：`confflow/execution/batch.py`（StepExecutionRequest → ItemExecutionContext 的传递）、`confflow/execution/work_item_executor.py`、`confflow/execution/mlip_executor.py`（只限解释器解析 / 启动部分，不动 V1 的结果判定和 `stored_result_reusable`）、`confflow/application/v4_run.py`、`confflow/execution/command_resolution.py`；新测试 `tests/v4/test_pr199_resolution_pin.py`；以及构造这些 request / context 时需要补字段的测试（列出来，不放宽断言）。

问题：应用层解析出 A 并测量（v4_run.py 约 966 行原生、约 1034 行 MLIP），但交给 batch 的仍是原始 binding（约 1099 行）；`work_item_executor.py` 约 1151 行（以及 `_enforce_policy`）和 `mlip_executor.py` 约 533 / 537 行在启动前重新按 PATH 查找。测量之后，如果 PATH 中更靠前的目录出现了同名程序 B，就会启动 B，结果却按 A 的身份登记；之后恢复 A 续跑，会 reuse_hit，复用 B 的结果。原生计算中，同一步的两个工作项可以分别启动 A 和 B，却登记在同一个环境摘要下。

修复：
1. `StepExecutionRequest` 和 `ItemExecutionContext` 增加可选字段 `resolved_command: ResolvedCommand | None`（同时保留原始配置名和绝对路径）。batch 把它从 request 原样传进每个 item 的 context。
2. v4_run：原生和 MLIP 步骤把应用层那一次解析得到的 `ResolvedCommand` 放进 request；测量也使用这一个对象。ConfGen 已经是绝对路径，保持不变（可以同样放入，但不要改变行为）。
3. 执行器：context 中有 `resolved_command` 时，策略检查（名称规则用 `configured`，绝对路径规则用 `real_path`）和启动都只使用它，不再搜索 PATH。没有时（直接调用的底层入口），在自己的入口解析一次，之后的策略检查和启动都复用这次结果，同一入口内不再查第二次。
4. 不得重新引入 V2：裸名称白名单必须继续生效（策略检查仍要看到原始名称）。
5. 测试（先写，并确认修复前失败；通过公共入口，不 mock 解析、测量、batch 或持久化）：(i) MLIP：测量 A 之后，更靠前的 PATH 目录出现 B → 启动的仍是 A（或明确拒绝），绝不能以 A 的身份保存 B 的结果；移除 B 后同 root 续跑，结果与 A 一致。(ii) 原生：`max_parallel_items: 1`，第一个工作项启动后出现 B → 两个工作项都启动 A。(iii) 无 resolved_command 的直接调用：同一入口内只解析一次（例如对 `locate_command` 计数）。参考 `resolution_pin_probe.py`。
回归：`test_executor_command_contract.py`、`test_review_post198_v1_mlip_failure.py`、MLIP 组、执行身份组、`test_v42_executors.py`、`test_v46_application.py`、`test_review_fix_execution_policy.py`、`test_arch_review_1029_confgen_xtb_identity.py`，以及所有构造 `StepExecutionRequest` / `ItemExecutionContext` 的测试。
- A2 之后：原生 / MLIP 启动使用应用层固定的绝对路径，但启动前不再复核该文件的内容。如果 A 在测量和启动之间被原地替换，不会被发现（不在 A2 范围内）。脚本解释器每次启动前都按内容校验，不受此影响。

**后续处理（2026-10-29，见 `docs/plans/deferred-post199-20261029.md`）：** 原地替换由启动前的文件指纹比对修复（fail closed）。控制服务测量自身按设计关闭。脚本注册时的身份由用户决定保持现状，理由是启动前已有 sha256 校验兜底，而且修改会改变已编译的摘要。
