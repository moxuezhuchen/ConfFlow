# 2026-09-22 全项目 Review 修复计划

状态：已完成。五个 Luna/max 子代理均已交付，主代理已完成集成、复核及全量验证；未部署。

审查基线：`77bcbb9`。目标是修复本次 review 的 9 个已复现问题，并补充能阻止同类回归的测试。

## 执行方式

- 主代理负责设计约束、任务分配、跨模块集成、复核和最终验证。
- 所有修复子代理统一使用 `gpt-5.6-luna`，`reasoning_effort=max`。
- 同时最多运行 3 个子代理，加主代理共 4 个执行槽位。
- 通过明确文件归属避免并发修改冲突；子代理如需越界修改，先向主代理说明，由主代理协调。
- 子代理先补充失败回归用例，再修复并运行相关测试，交付修改说明、测试结果和剩余风险。
- 本计划不包含发布、部署或更新外部系统。

## 第一阶段：建立回归基线

主代理将 review 的最小复现整理为各任务的输入与验收条件，并检查现有测试对以下语义的约束：

1. CLI 新运行、附着已有任务、失败恢复与外部 control 协议终态的区别。
2. 禁用步骤的输入透传、终端输出归属及输出 manifest 的工作目录边界。
3. ConfGen 对多帧文件的既有跳过/生成语义，以及 DAG 汇合的预期行为。
4. 历史 workflow state、calc manifest 和 confgen signature 的兼容性要求。
5. 本地计算进程组与 control worker 崩溃恢复、取消监督的关系。

保留现有测试失败记录：全量测试曾为 1554 passed、3 skipped、10 failed。10 项安装器测试在构建夹具时受继承 PYTHONPATH 权限和网络代理问题阻塞，不能视为业务缺陷，也不能视为已经验证通过。

执行补充：已从本机 pip 缓存提取原始构建依赖 wheel 至临时目录，设置 `PYTHONPATH=/opt/ConfFlow`、`PIP_NO_INDEX=1` 和 `PIP_FIND_LINKS` 后，安装器测试 11 项全部通过；没有修改安装器或放宽测试断言。

恢复兼容性决定：支持现有 v1 ConfGen signature 和 calc manifest；缺少 sidecar 的旧 checkpoint-only 目录无法绑定输入和配置，严格恢复明确拒绝并保留现场。XYZ 校验保证文件非空、帧格式有效；既有协议未提供输出内容摘要，因此不宣称可检出所有合法格式的坐标篡改。

进程取消边界：POSIX 私有 session/process group 加上已观测到的后代身份，覆盖普通计算子进程、父进程提前退出和忽略 TERM 的进程。若后代在首次观测前自行脱离 session 且失去父子关系，当前模型不能可靠识别它；不宣称具备 cgroup/job-object 级别的任意 daemon 收容。缺少 psutil 或无法确认已识别进程停止时失败保留现场，不继续 rescue 或删除目录。

## 第二阶段：第一批并行修复

### 子代理 A：CLI 执行身份和失败恢复（问题 1、2）

主要文件：

- `confflow/cli.py`
- `confflow/application/execution/workflow_adapter.py`
- 对应 CLI 和 workflow adapter 测试

修复设计：

- 将输入与配置的内容绑定纳入执行请求身份；输入列表的顺序和文件边界必须明确。
- 区分“新执行”和“附着既有执行”，避免未指定恢复的新调用直接返回历史成功结果。
- 让失败后的 `--resume` 能到达引擎恢复路径，通过新的受控执行记录/尝试保留旧失败记录。
- 保留外部 control 协议对终态的限制，不简单放开所有 FAILED/CANCELLED 到 RUNNING 的转换。
- 保留活动任务防重复启动约束，避免两个请求在同一工作目录同时执行。
- 完成状态的附着不能在必需产物已经丢失时仍报告成功。

验收：

- 同一路径修改配置、修改 XYZ 后，不再返回旧成功结果；根据新运行或严格恢复模式执行或明确拒绝。
- 未修改输入的重复控制请求保持幂等。
- 首次执行失败，修复失败原因后 `--resume` 能继续；恢复时仍拒绝输入/配置不兼容。
- 已运行任务不会因为身份策略变化而重复启动。
- 覆盖 completed、failed、paused、running、cancelled 各状态的 CLI 行为及既有控制协议测试。

### 子代理 B：DAG 输出、步骤目录和恢复产物检查（问题 3、5、8）

主要文件：

- `confflow/workflow/engine.py`
- `confflow/workflow/step_naming.py`
- 必要时修改 `confflow/workflow/plan.py`、`confflow/workflow/export.py`
- 必要时新增独立的恢复验证模块
- 对应 workflow/DAG/resume/export 测试

修复设计：

- 禁用终端沿依赖关系透传上游结果，区分生成产物和外部原始输入；处理多分支、连续禁用节点和全禁用工作流。
- 步骤目录分配检查所有最终名称，保持不发生碰撞的既有名称稳定。
- 导出和 checkpoint 引用使用一致的名称映射，避免只修复执行端。
- 在 completed 状态和旧 checkpoint 两条恢复路径统一验证产物：必须是有效的非空 XYZ，相关 manifest/signature 必须符合既有兼容性规则。
- 验证失败前不得清理、重写或采用不兼容产物。若既有协议没有输出内容摘要，明确区分格式验证和内容完整性保证，不虚称已验证任意内容篡改。
- 不直接修改子代理 D 负责的 `step_handlers.py`；需要共享签名验证时先约定 helper 接口，由主代理协调。

验收：

- `source → disabled last` 的最终结果仍是 source 产物。
- 外部输入不会被伪装成工作目录内的输出 manifest 产物。
- `A!、A?、A_2` 及其顺序变体生成不同目录，状态记录不覆盖；恢复和导出映射一致。
- 输出为空、被截断、路径为目录、签名/manifest 未知或不匹配时严格恢复失败，已有文件保持不变。
- 合法的 completed、skipped 和受支持历史状态仍可恢复。

### 子代理 C：本地计算进程树取消（问题 7）

主要文件：

- `confflow/calc/executor.py`
- 必要时新增进程生命周期辅助模块
- 对应 executor/cancel/timeout 测试

修复设计：

- 为本地计算建立可安全识别的进程边界。
- 取消和超时终止整个计算进程树，采用有界等待及必要的强制终止；不能误杀主代理、工作流或无关任务进程。
- 处理父进程已退出但后代仍存活、后代忽略终止信号和重复取消。
- 保留日志流关闭和资源回收行为；无法确认停止时明确报告失败，不允许假装清理成功。
- 核查 POSIX 与现有非 POSIX 行为，避免引入不可导入的跨平台代码。
- 主代理复核新进程组是否影响 worker supervision 和 crash recovery；相关文件变更由主代理协调。

验收：

- 使用真实轻量父子进程复现，取消完成后无运行中的后代。
- 忽略终止信号的后代会在超时内被强制结束。
- 无关进程不受影响；测试所有退出路径均清理自己创建的进程。
- STOP、wall-time timeout、worker supervision 和 executor 协议测试通过。

## 第三阶段：第二批修复

在第一批释放槽位后启动。子代理 B 的恢复检查与 D 的产物签名接口须先协调完毕。

### 子代理 D：ConfGen 多帧汇合（问题 4）

主要文件：

- `confflow/workflow/step_handlers.py`
- 必要时修改 `confflow/blocks/confgen/generator.py`
- 必要时修改 `confflow/core/chem_validation.py`，避免破坏单分子读取调用方
- 对应 ConfGen、step handler 和 DAG 汇合测试

修复设计：

- 明确“合并已生成多帧构象”和“对种子逐帧生成”的路径，遵循既有多帧跳过语义。
- 文件列表不得隐式退化为每文件仅取首帧。
- 完整保留坐标、元素顺序和适用元数据；需要重分配 CID 时保持确定性及来源可追溯。
- 保留输出原子发布、输入签名和生成失败时不采用部分结果的行为。
- 避免一次读入全部大型轨迹造成不必要内存增长。

验收：

- 两个双帧文件在无旋转的汇合用例中保留全部 4 帧。
- 覆盖单帧与多帧混合、CID 冲突、原子重排、元数据保留和损坏的后续帧。
- 覆盖真正的 `left/right → confgen → calc` 数据传递链路，不仅测试 helper。
- 单输入已有多帧行为与普通种子生成行为不发生意外变化。
- 与子代理 B 的恢复签名检查联测通过。

### 子代理 E：构象任务名称和频率验收（问题 6、9）

主要文件：

- `confflow/calc/run_services.py`
- `confflow/calc/components/task_runner.py`
- 对应 task source、数据库结果和 task acceptance 测试

修复设计：

- 对最终分配的任务名检查全局唯一性，覆盖清洗、截断和用户自带 `_dupN` 后缀。
- 保持原 CID 与任务内部标识的关系清晰，确保 metadata、目录和数据库键一致。
- `opt_freq` 必须有有效频率结果；缺失结果返回 `parse_error`，实际零虚频仍有效。
- 保持 opt、SP、TS 及其元数据继承规则。

验收：

- `A、A、A_dup1` 及顺序变体生成唯一任务名和目录。
- 三个构象各自保存元数据和数据库结果，互不覆盖；清洗和长 CID 截断碰撞同样受测。
- `opt_freq` 缺失频率失败，零虚频通过，正虚频失败。
- SP 无频率及现有 TS 规则不被误伤。

## 第四阶段：主代理集成与最终验证

1. 逐项复核子代理实现，检查测试是否验证用户可见行为，而非只复刻实现。
2. 完成跨模块测试：CLI 失败后恢复、变更输入后的新执行、禁用 DAG 终端、多帧汇合、名称碰撞后恢复、取消后的状态与产物。
3. 检查没有不兼容状态的静默清理、重复启动或残留计算进程。
4. 运行修改文件格式检查、全项目 Ruff、Mypy、全量 pytest；安装器测试使用不含不可读目录的 PYTHONPATH。
5. 如安装器夹具仍因网络无法构建，单独记录阻塞原因和受影响测试；不改业务实现或放宽断言来制造通过。
6. 核查 git diff 范围、更新本计划的完成状态，交付每个问题的修复位置、验证结果和未验证范围。

## 完成标准

- 9 项问题都有对应修复和回归证据。
- 无关代码与已有协议语义不被顺带重构。
- Ruff、Mypy 和适用测试通过；环境阻塞项目明确单列。
- 未经真实 Gaussian/ORCA、Windows 或远程 JobDesk 验证的行为如实标注，不将替身测试描述为真实程序联调。

## 实施结果

| 问题 | 修复位置 | 回归证据 |
| --- | --- | --- |
| 1、2：执行身份与 FAILED 恢复 | `cli.py`、`application/execution/workflow_adapter.py` | 实际 CLI 同路径内容变更、A→B→A、外部失败原因修复后恢复；重复控制请求与工作目录跨进程锁 |
| 3：禁用终端丢失上游输出 | `workflow/engine.py` | DAG 分支/禁用终端，以及 CLI 输出 manifest 指向生成产物 |
| 4：多文件多帧丢帧 | `workflow/step_handlers.py` | 严格逐帧验证、原子发布、CID 来源记录；实际 CLI 四帧汇合后计算仍为四帧，随后严格恢复成功 |
| 5：步骤目录重名 | `workflow/step_naming.py`、`workflow/export.py` | 预留全部自然名称，恢复后从 state 精确映射导出目录 |
| 6：构象任务重名 | `calc/run_services.py` | CID 清洗/截断/自带后缀冲突，metadata 与数据库结果分别保存 |
| 7：取消遗留后代进程 | `calc/executor.py`、`calc/components/task_runner.py`、`calc/rescue.py`、`calc/scan_ops.py` | 真实父子进程、TERM 忽略、父进程提前退出、同目录独立进程、worker 监督；未确认取消不 rescue、不备份、不清理 |
| 8：恢复接受损坏产物 | `workflow/resume_validation.py`、`workflow/engine.py` | completed 和 checkpoint 均验证 XYZ/signature/manifest；拒绝时保留已有产物 |
| 9：缺失频率被当成零虚频 | `calc/components/task_runner.py` | opt_freq 缺失数据失败，真实零虚频通过，SP/opt/无频率 TS 保持原语义 |

主代理额外集成修复：锁拒绝时不再追加其他运行的文本报告；进程枚举中成员退出不会中断取消；底层 killpg 异常统一转换为未确认取消，任务层保留现场。

验证环境：Python 3.12.3，Linux/POSIX；构建测试使用本机缓存的原始 wheel 离线执行。真实 CLI 中的外部计算程序为受控 ORCA 输出替身，没有调用真实 Gaussian/ORCA，也没有验证 Windows 或远程 JobDesk。

最终验证结果：

- 全量 pytest（包含安装器）：**1611 passed，3 skipped**，耗时 89.94 秒。
- 分支覆盖率：**85.05%**，满足项目 85% 门槛。
- 全项目 Ruff：通过。
- 全项目 Mypy：通过（141 个源码文件）。
- 全部修改/新增 Python 文件 Black 检查：通过（串行 Python API，项目 100 列/Python 3.10 配置）。
- `git diff --check`：通过。
- 真实 CLI 六项集成回归：全部通过。

完整测试日志：`/tmp/confflow-review-final.log`；覆盖率 XML：`/tmp/confflow-review-coverage.xml`。这些是本地验证产物，不随代码发布。

后续审查修复：FAILED 基础记录的成功 retry 在再次显式恢复时也必须重新校验，现统一传入 `revalidate_completed=True`。新增真实引擎回归验证重复恢复及删除 signature 后拒绝恢复，关闭该选项时测试按预期失败。此补充修复后相关测试 **77 passed**，Ruff、Mypy、格式与 diff 检查通过；上述全量和覆盖率数值对应补充修复之前的验证。
