**架构修复复查 — 2026-10-28**

检查基线：`23fb3c27`（PR #194 合并后）；比较起点为上次评审的 `6aa24592`。本轮修复范围由 `docs/plans/arch-review-fixes-20261028.md` 明确限定为 R1–R5 和文档漂移，R6–R8 暂缓。

结论：**R1、R3、R5 的原始问题已修复；R2、R4 部分修复，尚不能认定全部完成。** 新增及相关测试 440 通过、1 跳过，但补充复现发现两个 P1 和一个 P2，详见下面。此次没有修改生产代码或现有测试。

| 原报告项 | 复查状态 | 依据 |
| --- | --- | --- |
| R1 默认 Script/MLIP 环境未进入复用身份 | 原始问题已修复 | 即使省略 execution 也建立 native_env；环境变化触发重算；Script/MLIP 的继承会话变量在启动与摘要两侧同时剔除 |
| R2 MLIP 模型/解释器/依赖身份 | 部分修复 | 模型原路径替换、runner/refs 内容变化进入摘要，不存在的解释器不会复用旧成功结果；依赖版本探测仍有缓存和启动环境不一致问题，且引入执行策略顺序回归 |
| R3 MLIP 电荷/多重度不一致 | 已修复 | 共享参数解析器逐结构解析；混合批次、缺失状态和不合法状态拒绝；job 与输出标签一致 |
| R4 脚本编译身份与执行身份分离 | 部分修复 | 编译身份传到了执行器，并能拦截检查前的脚本变更；最终 hash 检查之后仍可等待配额，在该等待期间的修改不会被拦住 |
| R5 自定义 registry 未参与编译 | 原始问题已修复 | compile_workflow 接收应用 registry；自定义合同版本的离线编译摘要与运行报告一致。registry 本身尚未冻结，这是原报告的进一步建议 |
| R6 重复组装与无背压提交 | 本轮未修 | 明确暂缓，相关生产代码未修改 |
| R7 两种 SQLite 的文件系统策略不同 | 本轮未修 | 明确暂缓，相关生产代码未修改 |
| R8 根包导入和全局日志副作用 | 本轮未修 | 明确暂缓，相关生产代码未修改 |

**F1 · P1：MLIP 版本探测先于执行策略校验，禁止的解释器已经运行**

位置：[v4_run.py:893](/opt/ConfFlow/confflow/application/v4_run.py:893)、[environment.py:358](/opt/ConfFlow/confflow/execution/environment.py:358)、[v4_run.py:912](/opt/ConfFlow/confflow/application/v4_run.py:912)。

新增 `_mlip_runtime_digest()` 会在复用判断前调用 `subprocess.run([interpreter, "-I", ...])`。此时尚未检查该解释器是否满足 `allowed_executables`，sandbox 检查也在后面；应用层的后续检查对 MLIP 使用 `check_program=False`，真正的解释器白名单检查要等到执行器阶段。

复现使用一个临时目录中的无害假解释器，只写入 marker 并返回包版本 JSON。把该解释器设为 MLIP executable，白名单只允许真实 Python。最终报告确实是 failed，诊断也说解释器不被允许，但 marker 已存在，说明被禁止的程序已经执行。

```text
report_status = failed
probe_executed = true
diagnostic = mlip interpreter is not allowed by allowed_executables
```

这是新引入的顺序回归，与“工作流输入可信”无关：用户显式配置的执行限制没有覆盖新增探测动作。相同顺序也使 sandbox 拒绝发生在探测之后。

建议在任何可能启动子进程的测量前完成解释器解析、白名单和目录策略检查；探测应使用明确的启动环境和可控的进程边界。新增应用级断言：被拒绝的解释器既不能执行计算，也不能执行版本探测，marker 必须不存在。

**F2 · P1：脚本最终 hash 在等待配额之前完成，R4 仍可复现**

位置：[script_executor.py:295](/opt/ConfFlow/confflow/execution/script_executor.py:295)、[work_item_executor.py:1278](/opt/ConfFlow/confflow/execution/work_item_executor.py:1278)、[work_item_executor.py:1289](/opt/ConfFlow/confflow/execution/work_item_executor.py:1289)。

执行顺序是：脚本身份校验 → 最终文件 hash → `launch_and_wait()` → 等待资源配额 → `supervisor.submit()`。配额等待没有固定的短时上限，因此这里不只是 hash 与启动之间的极小竞态窗口。

复现使用真实 ServerQuota：先占满一个核心，再提交脚本工作流；确认工作流进入 acquire 后修改脚本，释放配额。新脚本输出 z=9，运行成功，但 report.definition_digest 仍对应旧脚本。同步包装仅观察 acquire 的调用，没有替代配额获取或进程启动行为。

```text
status = completed
output_z = 9
report_matches_old_script = true
report_matches_new_script = false
```

现有新测试覆盖了编译后到执行器检查前、以及加载注册表后到 hash 前的修改，没有覆盖配额等待区间。

建议至少把最终的完整身份复核放到拿到配额之后、紧邻真实 submit 的位置，并保证复核失败时释放租约。若需要更强的字节一致性保证，应设计保留脚本相对依赖语义的冻结执行内容。验收需要加入“排队时修改脚本”的应用级测试。

**F3 · P2：MLIP 包版本身份没有准确反映实际依赖环境**

位置：[environment.py:348](/opt/ConfFlow/confflow/execution/environment.py:348)。有两个独立缺口：

1. `_VERSION_CACHE` 只以解释器内容 hash 和路径为键。向同一 venv 安装或升级 numpy/torch/fairchem 不会改变 Python 二进制，缓存也没有失效机制；同一长生命周期进程内再次测量仍返回旧版本。
2. 探测用 `python -I`，而正式 runner 启动没有 `-I`；探测也未使用已冻结的 native_env。因此 PYTHONPATH、用户包位置以及 Python 启动环境可能与实际计算不同，进程重启后仍可能测错环境。

复现全部使用临时 venv 和最小 distribution metadata，没有安装或运行真实 numpy/torch：

```text
numpy metadata 1.0 → 2.0:
    identity_before == identity_after_upgrade_cached
    identity_before != identity_after_cache_clear

同一解释器，正式启动的 PYTHONPATH 指向 numpy 9.0 metadata:
    version_probe_numpy = 2.0
    actual_launch_numpy = 9.0
```

这直接验证了身份测量代码对包元数据变更的行为，不声称用这些元数据替身进行了科学计算。真实部署中相同机制可能让依赖变化后的续跑错误复用旧结果。

建议让探测和正式计算使用相同 Python 启动模式与环境；删除仅按解释器文件缓存包版本的策略，或者采用具有可验证失效条件的环境/依赖清单。补充“同进程包升级”和“显式 Python 环境覆盖”的回归测试。

**验证结果与边界**

| 检查 | 结果 |
| --- | --- |
| 新增修复测试及 MLIP、脚本、执行策略、环境/持久化回归 | 273 passed、1 skipped，49.76 秒 |
| 应用执行、resume/reuse、digest、CLI 切换、科学参数/可靠性 | 167 passed，28.48 秒 |
| `scripts/v4_arch_scan.py` | 42 个入口文件通过 |
| 完整架构策略扫描 | 只有 AP-107：测试总行数 86,866，预算 86,433，仍超出 433 行 |
| `git diff --check 6aa24592..HEAD` | 通过 |
| 补充最小复现 | F1、F2、F3 均复现 |

AP-107 的 433 行差额仍来自评审前已有的 5 个未跟踪实验测试；本轮没有调整它们或预算。该项与上述运行时问题分别记录，不计为新增功能回归。

测试日志：`/tmp/confflow-fix-verification-tests.log`、`/tmp/confflow-fix-verification-integration.log`。主要补充复现脚本：`/tmp/confflow_fix_followup_repro.py`；结果：`/tmp/confflow-fix-followup-0xac1j6i/results.json`。PYTHONPATH 对照用同一临时 venv 另行验证。临时材料不是长期项目接口。

此次没有执行真实 Gaussian、ORCA、xTB 或 MLIP 模型，没有跑全量测试或跨仓消费者测试。现有测试通过说明原始场景和相关回归较稳定，不能代替新增启动边界场景的验证。

建议先修 F1、F2，再补齐 F3；之后可将本轮 R1–R5 标为关闭。R6–R8 应继续保留为明确未完成项，不应随此次修复一起销项。
