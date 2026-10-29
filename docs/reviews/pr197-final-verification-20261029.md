# PR #197 最终复核 · 2026-10-29

审查对象：[PR #197](https://github.com/moxuezhuchen/ConfFlow/pull/197)，最终提交 `967d1ff3193903f0894cefdebae6cce10067aecd`。基线为 `f5754b97e5b4d50535a9953f0da8387adedec661`。

结论：上一轮 F1、F2、F3 均已修复，原 N1–N4 的七个复现场景保持通过。本轮没有发现新的合并阻断项。以下判断基于代码复核、独立复现、451 个定向测试及该提交的 GitHub CI；不是对项目所有潜在缺陷不存在的保证。

## F1：环境与其他身份同时变化

`reuse.py` 现在先检查不可由环境更新覆盖的身份轴，只有其余身份一致时才返回环境失效；`batch.py` 在推进登记前再次检查条件。环境和输入、定义或 provenance 同时变化会拒绝发布，符合已选定的严格规则。

独立复现的 PENDING、死亡 owner RUNNING、INTERRUPTED × 输入、定义、provenance 共九种组合均被拒绝，没有启动计算，也没有改写登记身份。正常 `script → MLIP` 工作流中，上游受环境影响产生新输入时，下游拒绝，原环境和输入登记保持不变。终态及其他身份组合由相关回归测试覆盖。

## F2：旧 MLIP 结果失效

MLIP executor contract 已升到 `confflow.contract.executor.mlip.v2`。参与计划和复用身份的版本变化使升级前的 MLIP run root 在 run 入口明确拒绝，并提示 `use a fresh run root or explicit migration`。

使用实际旧提交生成结果，再切换最终提交续跑同一个 run root：拒绝符合预期，拒绝前后逐文件 SHA256 一致。新建 run 的拓扑为 `[[1],[0],[]]`，保留删除 1–3 键的声明，并有 fairchem provenance。版本选择通过独立进程的源码路径完成，未通过切换环境变量人为制造失效。

## F3：裸名称 allowlist

应用层保留配置时的 `configured_executable` 用于名称规则，同时用解析后的唯一绝对路径执行绝对路径规则、身份测量和启动。独立复现 `allowed_executables: [xtb]` 成功完成，记录 14 次假 xTB 调用；只允许其他绝对路径时拒绝且不启动，相关定向测试及原 N2 场景通过。

## 验证证据

- 独立源码快照 `git archive 967d1ff3`：相关 resume/reuse、MLIP、ConfGen、契约和新增回归测试 **416 passed**；额外 assembly/artifact 测试 **35 passed**，合计 **451 passed**。未在本轮本地重复运行全部 5058 个测试；该完整数量来自提交者报告。
- GitHub [CI 40798437156](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40798437156) 成功：Python 3.10、3.11、3.12、3.13 的 Pytest 均通过，3.11 的 black、ruff、mypy 通过，changed-coverage 通过。独立的完整 coverage job 按 PR 配置跳过。
- [JobDesk Consumer Contract 40798437134](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40798437134) 成功。
- [组合与跨版本原始结果](data/pr197-final-verification-20261029/results.json)、[验证驱动](data/pr197-final-verification-20261029/verify.py)、[416 项测试日志](data/pr197-final-verification-20261029/focused-tests.txt)、[35 项测试日志](data/pr197-final-verification-20261029/artifact-tests.txt)、[原七个场景结果](data/pr197-final-verification-20261029/original-scenarios.json)。驱动保留本轮快照的绝对路径，后续复用需重新创建对应源码快照并调整 TREE / BASE；场景脚本位于上一轮证据目录。

## 遗留项判断

独立 `artifact_checksums` 字段确实为空，但正常 assembler 把绑定 artifact 的 checksum 计入 work-item 输入摘要。实际编译和组装同一 checkpoint、改变 checksum 后，输入摘要改变；同时改变环境，在 PENDING、死亡 owner RUNNING、COMPLETED、FAILED、INTERRUPTED 五种状态均返回 `invalidate_input`。因此这一字段尚未接通不等于正常流程完全漏检绑定 artifact 身份，本轮未发现它导致 F1 绕过。[证据](data/pr197-final-verification-20261029/artifact-axis.json)。实际字节校验、直接 API 构造身份和独立诊断轴的完整覆盖仍可单独评估。

以下仍保留在后续清单，不阻断本次合并：直接执行器缺少环境快照时的 ambient 回退；xTB 显式接收环境快照的整理（需保留线程/栈参数覆盖）；独立 artifact 比较字段的后续评估；步骤级 provenance 拒绝缺少新建 run root 提示。原 review 脚本的 tuple 键序列化问题已解决。`/tmp/deferred-fixes.md` 已补充上述 artifact 判断，本报告提供长期副本。

## 使用影响与合并记录

含升级前 MLIP 步骤的旧 run root 需要换新目录重跑。环境变化导致上游重算、进而改变下游输入时，同一 run root 的下游会被拒绝，也需要新的 run root。

合并前核对：PR open、非 draft、mergeable，head 与上述审查 SHA 一致，base 仍为上述基线。合并结果：2026-10-29 01:16（UTC）由仓库所有者合并，合并提交 `c1ba73f2561955d1513c670e4f2cc808da180249`，合并时 head 为上述审查 SHA `967d1ff3`。复核材料与遗留清单（`docs/plans/deferred-fixes-20261029.md`）在合并后经后续 PR 入库；D1、D2、D4 在同一 PR 中修复，D3 评估后保持现状，新增 D5 待决定。
