# PR #197 修复复核 · 2026-10-29

> 后续状态：以下为提交 `f0351b1c` 的历史复核；追加提交 `967d1ff3` 已解决 F1–F3，见[最终复核](pr197-final-verification-20261029.md)。

基线：`f5754b97e5b4d50535a9953f0da8387adedec661`。审查对象：[PR #197](https://github.com/moxuezhuchen/ConfFlow/pull/197)，提交 `f0351b1caaee76ac2377002e1e01ead4eb048846`，当前未合并。

结论：原来的七个复现场景均已通过，N1–N4 的主要修改有效；但补查确认 **2 项 P1、1 项 P2**，建议处理后再合并。新计算的 MLIP 拓扑与能量来源正常，不能据此推断旧 run 的续跑也已得到修复。

**F1 / P1：环境和输入同时改变仍会绕过 N1 的失效规则**

位置：[reuse.py:270](/opt/cf-worktrees/arch-review-1029/confflow/persistence/reuse.py:270)、[batch.py:923](/opt/cf-worktrees/arch-review-1029/confflow/execution/batch.py:923)。

新增 `_first_mismatch()` 仍先返回 `INVALIDATE_ENVIRONMENT`，之后才比较定义、绑定 artifact 和 work-item 输入。批执行器收到该返回值后直接调用 `refresh_for_new_environment()`，同时覆盖环境、输入和定义的登记。因此它没有确认此次变化确实“只有环境”，输入/定义同时变化会被环境变化掩盖。

复现结果：

- PENDING、RUNNING-dead、INTERRUPTED 三种状态，分别叠加“环境 + 输入”或“环境 + 定义”变化，共六种情况，全部执行了 B、返回 completed，并改写原登记身份。
- “环境 + provenance”作为对照正确拒绝，原因是 provenance 检查在环境之前。
- 正常应用入口也可触发：固定工作流 `script → MLIP`，script 根据环境变量输出 He 的 z 坐标；先运行 z=1，再用 store API 构造下游旧 attempt 的死亡 owner，最后把环境变量改成 2 续跑。上下游仍是同一份工作流、相同导入字节；新运行返回 completed，下游环境摘要和输入摘要均被替换。没有绕开 `V4RunApplication.run()`。

这与本 PR 所述“输入或定义变化拒绝发布”不一致。终态旧路径也有相同的多轴问题；本次扩展的恢复分支沿用了它，不能将 `INVALIDATE_ENVIRONMENT` 自动理解为“环境是唯一的差异”。

建议：在允许推进环境登记之前完整验证所有不能被环境更新覆盖的轴；即使诊断保留“首个差异”的展示规则，也要单独验证环境更新的前提。补充“环境 + 输入 / 定义 / artifact”的组合测试，断言拒绝后没有启动计算、登记身份未改变。已经存在的输入变更规则应保持一致，不能通过同时改环境来绕过。

证据：[组合结果](data/pr197-verification-20261029/combined-results.json)、[完整工作流结果](data/pr197-verification-20261029/workflow-results.json)。脚本：[combined-cases.py](data/pr197-verification-20261029/combined-cases.py)、[workflow-combined-change.py](data/pr197-verification-20261029/workflow-combined-change.py)。

**F2 / P1：旧 MLIP 的错误拓扑仍被视为可复用结果**

位置：[registry.py:719](/opt/cf-worktrees/arch-review-1029/confflow/execution/registry.py:719)、[v4_run.py:991](/opt/cf-worktrees/arch-review-1029/confflow/application/v4_run.py:991)、[mlip_executor.py:763](/opt/cf-worktrees/arch-review-1029/confflow/execution/mlip_executor.py:763)。

MLIP 的输出科学语义已经修正，但参与执行环境和 producer provenance 的契约版本仍为 `confflow.contract.executor.mlip.v1`。runner 文件、模型、解释器、环境和原始输入不变时，旧结果仍满足复用条件，不会调用修正后的输出构造逻辑。

实际跨提交验证使用两个独立 Python 进程，在同一份环境、模型和 run root 下切换源码目录；通过 `sys.path` 选版本，没有用不同的 PYTHONPATH 制造环境失效：

| 执行 | 状态 | 拓扑字段 | 诊断 |
| --- | --- | --- | --- |
| 旧提交生成水结构结果，输入声明删除 1–3 键 | completed | patch、working_topology 均为空 | mlip_completed |
| PR #197 续跑同一 run | completed | 仍为空 | mlip_completed、reuse_hit |
| PR #197 新建 run 作对照 | completed | 删除声明保留，图为 `[[1],[0],[]]` | mlip_completed |

提交说明已披露旧结果没有能量来源，这一行为确实存在；但影响还包括 N3 的科学拓扑。下游仍可能重新感知回已明确删除的键，不能把它仅视为能量显示的兼容性限制。

建议：对旧 MLIP 输出增加明确的失效/迁移处理，或推进确实参与复用判定的 executor/producer 版本，要求用户重新计算或使用新的 run root。不要静默覆盖旧历史，但也不应将已知缺少权威拓扑的旧结果作为有效命中继续供下游使用。需要补“旧提交生成 → 新提交续跑”的测试，而不只是修复后生成再 resume。

证据：[旧版本](data/pr197-verification-20261029/upgrade-baseline.json)、[升级续跑](data/pr197-verification-20261029/upgrade-resume.json)、[新建 run](data/pr197-verification-20261029/upgrade-fresh.json)。脚本：[upgrade-reuse.py](data/pr197-verification-20261029/upgrade-reuse.py)，参数依次为源码目录、共享测试根目录、`baseline` / `resume` / `fresh`。前两次必须使用同一环境和共享根目录。

**F3 / P2：裸名称 allowlist 出现兼容性回归**

位置：[v4_run.py:976](/opt/cf-worktrees/arch-review-1029/confflow/application/v4_run.py:976)、[v4_run.py:1015](/opt/cf-worktrees/arch-review-1029/confflow/application/v4_run.py:1015)。

`resolve_confgen_xtb_binding()` 把 `binding.executable` 从 `xtb` 替换成绝对路径。后续策略检查的两个候选都成了绝对路径，丢失了配置中的原始名称。现有 `enforce_execution_policy()` 明确支持裸名称条目，但它对绝对候选只匹配绝对 allowlist 条目，因此合法配置 `executable: xtb`、`allowed_executables: [xtb]` 被拒绝。

同一工作流、同一假程序验证：旧提交 completed，14 次假 xTB 调用；PR #197 抛出 `DomainError: ... not allowed by allowed_executables`，没有调用。它发生在正常 V4 应用入口。

建议：保留配置时的名称用于名称规则匹配，同时用唯一解析出的绝对路径做绝对路径规则检查、身份测量及启动。应继续满足“只允许 A 的绝对路径不能运行 B”，并补上允许裸名称、允许解析后绝对路径、拒绝其他绝对路径三类用例。

证据：[PR 结果中的 named_allowlist](data/pr197-verification-20261029/combined-results.json)、[旧提交结果](data/pr197-verification-20261029/named-allowlist-baseline.txt)。独立脚本：[named-allowlist.py](data/pr197-verification-20261029/named-allowlist.py)，传入 PR 新增 `test_arch_review_1029_confgen_xtb_identity.py` 的文件路径作为 fixture 来源，并分别使用旧/新代码的 PYTHONPATH。

**已经确认有效的修复**

- N1：单独改变环境时，PENDING 和 RUNNING-dead 会正确推进环境身份；模型 `-5 → -10 → -5` 返回正确的三次能量。输入/定义/provenance 单独改变的拒绝用例也通过。
- N2：绝对路径 allowlist 只允许 A 时，binding PATH 指向的 B 不再启动；允许 B 后，B 内容改变会触发重算。worker 使用绝对路径和冻结环境。
- N3：新计算的 sp / relax / sp_then_relax 输出保留 patch / working graph；stretch 检查使用输入权威图，观察到的几何图变化作为诊断。
- N4：新计算有 fairchem 来源，公开清单含 `energies.electronic_hartree`，修复后产生结果再续跑时投影稳定。
- `test_v43_coverage.py` 的调整合理：旧 fixture 用空 producer provenance 和未知环境登记；改成实际请求的身份后仍断言死亡 owner 的恢复行为，未放松原断言。

**验证范围**

GitHub 已核实本提交的 [CI](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40739490377) 与 [JobDesk Consumer Contract](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40739490411) 成功。CI 中 Python 3.10 / 3.11 / 3.12 / 3.13 的 Pytest、3.11 的 black / ruff / mypy、changed-coverage 均成功。

本机在 `git archive f0351b1c` 的独立快照运行相关测试：**272 passed，74.53 秒**，包括三份新增回归测试、既有 reuse/resume、MLIP、ConfGen、环境身份和探测测试。[测试日志](data/pr197-verification-20261029/focused-tests.txt)。本轮没有另跑完整 5013 个测试；该具体数量来自提交者的全量验证报告，本轮独立确认的是上述 CI 状态与相关测试。

原审查的七个场景用明确的“修复后应成立”断言重跑，全部通过。[验证脚本](data/pr197-verification-20261029/original-scenarios.py)、[原始结果](data/pr197-verification-20261029/original-scenarios.json)。N2 的正确拒绝按预期结果处理，没有把拒绝异常误记成失败。

原审查 `reproduce.py` 的 tuple 字典键确实不能被 `json.dumps(default=str)` 处理；已将该投影改为包含 step / subject 字段的记录数组，未修改生产行为，也未覆盖上轮原始证据。

后续已收到并核对 `/tmp/deferred-fixes.md` 和 PR 描述的 Deferred minors：`confgen_search_run.py` 的无快照回退仅在未提供快照的直接调用中触发，正常 V4 路径提供快照；`confgen_xtb.py` 继承快照启动的 worker 环境，再覆盖 OMP / OpenBLAS / MKL 线程参数及 `OMP_STACKSIZE`，目前没有确认新增的环境漂移问题。这两项可以继续作为后续整理，不加入合并阻断项。清单中的第三项是已经修正的 review 脚本序列化问题，已在本地清单标记解决。GitHub PR 描述仍保留旧待修状态，本轮未修改外部描述。上面 F1、F2、F3 均已有正常应用入口的证据，和这三项清单条目是不同问题，复核结论不变。

按用户要求检查并删除了误生成的 `/cov.txt`：删除前为 root 所有的普通文件，47,230 字节，内容是 pytest / coverage 输出；删除后确认路径不存在。未修改两个工作树的已跟踪生产源码，也未发布 GitHub 评论或执行合并。
