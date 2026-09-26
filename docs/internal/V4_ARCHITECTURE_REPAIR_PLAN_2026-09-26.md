# ConfFlow V4 Architecture Repair Plan

日期：2026-09-26。审计基线：`bfdae1c`，分支：`feat/workflow-v4-greenfield`。

状态：待执行与验证；不是 Final Closure 声明。本文保存用户确认的完整修复目标、22 项审计发现、按依赖组织的修复阶段和独立复审门禁。执行记录必须区分已实现、已验证和仍阻断，不能提前标记完成。

## 1. 最终设计与不可违反的约束

ConfFlow V4 必须成为唯一正式 runtime，不是在 V2/V3 外再包一层。

```text
WorkflowDocument V4
  → Compiler
  → ExecutionPlan
  → typed Binding materialization
  → WorkItem
  → Calculation / ConfGen / Transform / Analysis Executor
  → WorkItemResult
  → durable item commit
  → StepResult assembly + validated publication
  → downstream Binding materialization
  → …
  → RunResultManifest durable publication
  → JobDesk
```

1. Scientific Definition、Execution Binding、Scheduler Policy 三层彻底分离。
2. Structure / Result / Artifact / Binding / WorkItem / StepResult 都是 typed semantic objects；路径、文件名、list index 不是科学身份。
3. WorkItem 是最小执行、调度、恢复单元；batch、IRC fan-out、GOAT ensemble 通过 WorkItem/StructureSet 表达，不动态膨胀 workflow DAG。
4. Gaussian/ORCA native syntax/parser 只属于 ProgramAdapter。ResultProfile、Checks、Recovery 独立，不能恢复 TaskName/itask 式 runtime dispatch。
5. Persistence 必须 per-WorkItem durable resume/reuse；已完成 item 不因整步中断重算。
6. 跨步骤 artifact 必须通过 ArtifactRef + checksum + subject identity + Binding；禁止 chk_from_step、文件名或目录位置配对。
7. local/remote 使用同一个 WorkItemExecutor 和 ProgramAdapter；remote 只是 transport/staging。
8. multi-output、IRC、QST2/QST3、NEB、GOAT、named structures、atom mapping 基于同一套 V4 contracts。
9. Analysis 是纯 executor，不启动 Gaussian/ORCA；TSPES/PES/Gibbs 根据 typed sets/group_key/subject 聚合，不按顺序猜。
10. forward/reverse IRC endpoint 不自动等同 reactant/product；只有显式 assignment 可以建立该映射。
11. Producer Contract 是 ConfFlow 对 JobDesk 的唯一科学语义接口；workflow schema、editor manifest、recipes、capabilities、validation、result schema 从真实 V4 权威生成。
12. JobDesk 仅编辑、提交、状态和展示，不复制科学语义。
13. 所有正式 CLI/application/control execution path 直接进入 V4；V2/V3 只能 migration/read-only。
14. TaskRunner、CalcStepRunner、ResultsDB、旧 WorkflowState、result.xyz/output_path/chk_from_step 不再是 production truth。
15. 最终完整链路：JobDesk → producer contract → V4 workflow → compile → local/remote → interruption/resume → IRC 20→40 → endpoint Opt/Freq/SP → Analysis/PES → RunResultManifest → JobDesk。

Analysis 是同一个 WorkItem/persistence 体系中的 executor，不能成为步骤级旁路。持久化顺序是 item 先 commit，再发布 StepResult。不得新增 V5、兼容 runtime、另一套 manager、架构 allowlist，或者用 bridge/shim 掩盖 contract 错误。不得修改测试去接受已知错误，不得手工构造中间结果冒充 E2E。

## 2. 审计发现：当前实现 → 应有设计 → 问题

审计执行了 application、recipes、architecture、atom mapping、remote resume 的 103 项现有测试，全部通过；以下最小复现仍失败。没有运行真实 Gaussian/ORCA 或真实 JobDesk，不把静态推断说成已验证 E2E。

### P0

1. **正式入口仍并存。** `cli.py:573` 仅显式 v4 子命令进入 V4；普通 CLI (`:785`)、`application/execution/workflow_adapter.py:35`、`control_worker.py:49` 仍运行旧 engine，后续进入 CalcStepRunner/旧 WorkflowState。应所有正式入口统一 V4，旧路径只能迁移/只读。问题是正式 runtime 未替换；并非已发现 V4 WorkItemExecutor 内部再调用旧 runner。
2. **StepResult shortcut 绕过 reuse。** `application/v4_run.py:210` 只要文件存在就 continue，不比较当前 definition/input/environment/provenance/artifact，也不要求 completed。应由当前 items 驱动逐项 reuse。已复现改方法和结构仍返回旧 subject A、新 definition digest；failed step 重跑直接返回旧失败。
3. **装配错误变成成功。** application 忽略 assembly.ok/errors/skipped；assembly 错误返回空 items；completion 对空集合判 completed。应区分合法空集和绑定失败，并保留诊断。已复现无必需输入的 Opt→SP 均 completed、零 item、无错误。全 plan 装配错误也可能清掉无关 items。
4. **producer 状态和 partial 语义丢失。** StepOutputs/_extend_materialized 只保留数据，不保留状态；下游不按实际 failed/partial/cancelled 执行消费策略。应在 Binding materialization 检查实际状态；编译检查不能代替运行检查。失败 require_all producer 的成功子集可能被静默消费。
5. **production native parser 是测试方言。** Gaussian IRC (`programs/gaussian/path.py`)、ORCA IRC/NEB (`programs/orca/path.py`, `neb.py`) 解析 handcrafted markers；GOAT (`ensemble_parse.py`) 明确不是 ORCA native output。应支持真实版本输出，fake 模拟真实格式。recipes 的 IRC/NEB/GOAT keyword 仍是 Opt，而 helper 未完整激活相应 native 模式；不能用 fake E2E 证明真实链闭合。
6. **Structure/Result subject 链断裂。** `execution/profile_standard.py:195` 创建新输出结构但结果绑定输入 ID，并清掉 role；TSPES analysis 绑定 IRC endpoints 却输入后续 Opt/Freq/SP results。应结果绑定实际 geometry，以 typed lineage/association 关联 reaction node。优化能量被标成初始 geometry；SP subject 不匹配 IRC endpoint。无 group_key TS 与 endpoint fallback group 不匹配。recipe 还漏 endpoint freq/TS SP results；手工 Gibbs 测试绕过了问题。
7. **Analysis first-match。** `analysis/thermochemistry.py:159` 返回同 subject/kind 的首个结果，端口合并无理论层级/producer/ResultRef 唯一选择。应明确 selector，歧义失败。已复现交换 -1/-2 energy 的顺序改变所选结果。
8. **manifest 与 contract 不符且未持久发布。** `application/v4_run.py:386` 输出 step_result_digest/diagnostics_summary，而 schema 要 digest/diagnostics；digest 填 completed 字符串；counts 有非法字段；analysis shape 不一致；artifacts 恒空；publish 仅返回内存对象。应生成真实 digest/ref、schema 验证、原子持久发布。实际 Opt→SP manifest 无法通过公布 schema。

### P1

9. **ConfGen/Transform 无真实路由。** application 仅 analysis/其余 calculation 分支并 fallback ORCA；合法 confgen/transform 编译但运行失败。应四类 executor 真实注册、统一 WorkItem 合同。
10. **Analysis durable 旁路与聚合 bridge。** 合并全部 analysis items 执行一次，再给每个 item 复制相同全量输出；无 item commit/step publication。应明确一个全局聚合 item 或按 group 的 items，输出归属唯一且可恢复。application 任意 error 全丢结果还覆盖 accept_subset。
11. **assignment/policy bridge 失效。** `_analysis_definition` 将整个 native 交 policy_from_native，后者拒绝 endpoint_assignment/partial_policy；ReactionEnergyModel 丢 assignment；payload 固定 None；analysis_step_id None。应分别解析并传递。两选项均编译成功、运行 ValueError。默认方向不等于化学身份是正确的，但显式指定也必须生效。
12. **位置身份与 digest 缺失。** import_xyz 使用 xyz:index，多文件重号；structure input digest 缺 entity/group/role/lineage。应持久 entity ID 与顺序分离，影响语义的信息参与 reuse。已复现 group_key 变化而 item digest 不变。
13. **Result identity/selector 不完整。** ScientificResult 无独立引用身份；source_result_ids 用不含 subject/provenance 的 value digest；remote 用 kind:subject；result selector.ids 实际不筛选。应区分 value equality 与 entity identity，并执行唯一选择。不同来源同值被折叠、多级别无法唯一引用。
14. **Execution Binding 被覆盖。** application 硬编码 v4-run、空 env、按 program executable map，environment=None，planned target/walltime 丢失。应绑定解析唯一且真实环境可审计/reuse。声明和实际执行脱节，底层 environment 测试不代表 application 接通。
15. **remote 非保真。** handoff 强制单 structure，named QST/NEB 失败；artifact/result ports 丢失；step digest 填 item digest；provenance 空；walltime 不传；一个 transport 固定 store 却 application 多 step；直接进程内调用 worker，没有正式跨机器接线。应完整 typed transport，同 executor，正确 per-step attempts。named parity 仅 rehome 人工结果不是 handoff 验证。
16. **mapping 被装配层否决。** assembly 在 explicit permutation 前要求原 element order 相同；一个 permutation 应用所有非 reference slots。应先 mapping 再验证，QST3 各 slot 独立。合法 O/H 异序输入已复现被拒绝。
17. **artifact 没有完整消费。** staging 接受缺 checksum/subject，无 path 跳过；ORCA 不消费 inputs.checkpoints。应消费前完整校验，adapter 使用或明确拒绝。staged 不等于 native used；缺 checksum 无可靠内容身份。
18. **publication/attempt 缺口。** batch 捕获 PublicationError 后特定 non-durable gap 继续 publish；application 未接 V4 RunState；remote 只找当前 attempt token，但 abandoned claim 增加 attempt。应严格 durable backing、生命周期和 attempt reconciliation 顺序一致。跨重启完成未 commit 场景须进程级验证，不能只靠静态推断关闭。
19. **工作目录碰撞。** sanitize logical_key 非单射且无 attempt 隔离，s:A:B 与 s:A_B 同为 s_A_B。应无歧义 item/attempt locator。并行可能覆盖并解析彼此输出，重试可能读旧 companion 文件。
20. **capability 双重权威。** spec registry 与 PROFILES/CHECKS/RECOVERIES 独立；opaque 可公布/编译但无 runtime；未知 program 验证成功还被测试固定。应实际实现及 descriptor 派生 compile/dispatch/contract。
21. **Recovery 穿透 native/binding。** recovery 自己改 Gaussian route/ModRedundant；executor 混入 executable/env/workdir/walltime，scientific recovery params 还能覆盖。应 policy 仅策略，adapter 独占 syntax，binding 独占环境。
22. **GOAT seed 双重权威。** calculation 非 stochastic，recipe 无 scientific seed；executor 不传 seed，native 又有独立 Seed。应能力声明 stochastic，唯一有效 seed 贯通 digest/native/remote。当前 GOAT recipe seed=None 仍编译。

### 测试盲区

不能推断开发者动机，但现有测试固定或绕过了错误：debt gate allowlist 保留旧入口且漏 application；importer 断言 xyz:index；resume 只测同输入；recipe 只编译；cross-repo E2E 使用 consumer double/fake subjects/手工 manifest；named parity rehome 本地结果；mapping helper 测试未穿过 assembly。合成测试可保留为单元测试，不能承担 production closure 证明。

## 3. 权威与 contracts 决策

| 对象 | 保持不变的职责 | 必须补全/调整 |
|---|---|---|
| WorkflowDocument V4 | 顶层模型、四 executor、三层分离 | 真实能力校验，必要 result selector/mapping/seed 表达 |
| Compiler/ExecutionPlan | 纯编译、冻结执行合同，不计算 | 从真实注册解析可执行能力 |
| StructureRecord/Set | entity、typed geometry、lineage/group | 导入身份、变换/测量身份、冲突检测 |
| Binding | typed ports/cardinality/pairing | 实际 producer 状态、ResultRef、named port 保真 |
| WorkItem/Result | 最小执行/恢复、统一输入输出 | digest 语义覆盖，四 executor 全使用 |
| ScientificResult/ResultSet | kind/value/unit/subject/provenance | 独立可引用 ResultRef，区别 value digest |
| ArtifactRef | role/locator/checksum/subject | 消费边界完整性和明确不支持错误 |
| Execution Binding | 唯一 executable/env/target/walltime | application/remote 完整解析执行 |
| WorkItemStore | item/attempt/terminal durable truth | 各 executor 同协议 |
| StepResult | 经验证发布的语义输出 | 严格发布门禁，不充当未验证缓存 |
| RunState | 生命周期，不复制 scientific truth | application 接入 |
| Completion/partial | 接受性与调度分离 | 编译和运行一致 |
| Remote envelope | transport-only | ports/identity/digests/attempt/provenance/environment 保真 |
| Producer Contract/Manifest | 唯一外部语义接口/结果投影 | 真实实现派生、schema 一致、持久发布 |

serialized/digest 意义变化必须升级相应 contract/semantic version；旧数据显式迁移、只读或拒绝复用，不能重新解释或恢复旧 execution runtime。不受影响的文档字段保持；必要变化提供 migration diagnostic。不引入 V5/新 manager。

## 4. 阶段 0：基线与共享 contract 决策

**目标**：将 22 项转成可追踪失败场景；先冻结必要共享合同，不边修边发明架构。

**模块**：application、workflow/v4、domain、execution、persistence、remote、analysis、producer、CLI/control/service 及测试。

**替换对象**：列出仍可执行 legacy 的入口，标记 allowlist、合成 E2E、人工 manifest/parity；不立即删除所有旧代码。

**测试**：缺输入成功、改变输入/definition 错复用、failed 快照、manifest schema、能量顺序、assignment/policy、mapping assembly、named handoff、group digest、directory collision、公布未实现能力。对跨重启 remote 风险补进程级实验。

**完成判据**：每项有位置/触发/预期/验收；ResultRef、structure identity、mapping、seed、publication 决策明确；contract 变化有必要性解释；无新 runtime/豁免。

## 5. 阶段 1：真实能力注册与执行合同

**问题**：9、20；支持 1、5、14、21、22。

**目标**：compiler/runtime/Producer Contract 使用同一真实注册，实现与 descriptor 同源。

**模块**：execution/registry.py、contracts.py、profile/check/recovery registries、programs/registry.py、workflow/v4/validation.py、plan.py、producer/contract.py。

**删除/替换**：spec/instance 双表、默认 ORCA、非 analysis 全 calculation、未实现却标可执行的能力。

**要求**：四 executor 明确注册；合法 program/adapter/profile/check/recovery 组合校验；错误 compile/preflight 结构化；role 不能成为 dispatch。临时未支持明确拒绝，但最终要求能力不能通过永久隐藏关闭。

**测试**：公布能力均解析真实实现；未知 program/profile/executor 拒绝；组合不兼容拒绝；opaque 实现或不再可执行发布；ConfGen/Transform 不调用 Calculation。

**判据**：验证、计划、运行、capability 不需人工同步第二权威。

## 6. 阶段 2：身份、ResultRef、Binding/mapping

**问题**：3、4、6、12、13、16、19；支持 7、15、17。

**目标**：身份与路径/顺序分离；Binding 得到完整无歧义 WorkItem。

**模块**：domain/structure.py、result.py、binding.py、work_item.py；workflow/v4/assembly.py、fingerprint.py；execution/output_identity.py、named_structures.py、atom_mapping.py；XYZ importer。

**删除/替换**：xyz:index、value digest/kind:subject 充当结果身份、忽略 selector、mapping 前元素顺序检查、非单射目录、丢 producer 状态、错误变空成功。

**要求**：

- 导入 entity ID 持久保存，多 named inputs 不碰撞；已导入实体重排身份不变；同 geometry 不自动合并。原始 XYZ 没有 ID 时不承诺凭重排猜身份；resume 使用已保存导入映射或显式 typed IDs。
- 新 geometry 创建新 entity；测量/明确 passthrough 不无理由创建对象。result 绑定实际 geometry；reaction node 后续关系使用 typed lineage/association，不盲拷 role/猜祖先。
- ResultRef 唯一关联来源；选择零/多个候选明确失败。
- Binding 区分等待、缺失、合法空、失败；检查 producer 实际状态/partial；最终 pairing 后 cardinality；错误不抹无关 items/诊断。
- 先显式 atom mapping 再验证；QST3 各 slot 独立，不自动推断。
- digest 纳入影响语义的 identity/group/role/lineage；排除 scheduler/locator。目录无歧义 item/attempt 派生。

**测试**：重排/多文件/重复实体/冲突；group/role/lineage/ref 变更；同值不同来源；selector 零一多；producer 状态矩阵；QST2/3 异序映射；sanitize collision 并行；合法空与缺输入区别。

**判据**：每个 item 的输入/归属/来源/配对/消费理由均可解释；错误保留且不变零 item 成功。

## 7. 阶段 3：四 executor、native 与 ExecutionBinding 边界

**问题**：5、9、14、17、21、22；落实 6 输出规则。

**目标**：四 executor 共用 WorkItem；Calculation 保持 Adapter→Profile→Checks→Recovery 分层；本阶段完成 Analysis 接口，阶段 6 完成聚合。

**模块**：work_item_executor、各 executor、programs/gaussian/orca、profiles、recovery、artifact staging、binding/environment resolution。

**删除/替换**：production handcrafted dialect、recovery route rewrite、scientific params 环境注入、checkpoint 未使用即成功、双 seed、所有 executor 强制 native 单结构接口。

**要求**：真实支持版本 rendering/parsing，fake 使用真实格式；syntax 只由 adapter 解释。Recovery 给意图/参数，由 adapter 转 native；内部 attempt/substage 不是第二 runtime。ConfGen/Transform 不 legacy。artifact 消费前 checksum/subject/role/locator 校验，使用或拒绝。binding 独占机器环境。stochastic 唯一 seed 进入 digest/native/remote。

**测试**：四 executor 最小运行；Analysis/Transform 不 native；真实 IRC/NEB/GOAT 正常/截断/缺失/歧义/失败 fixtures；QST input/mapping；checkpoint 使用证据及损坏；recovery 不改 binding；seed 缺失/冲突/复现；environment/binary 记录。

**判据**：真实 native 数据产生正确 typed outputs；无 legacy dependency，无 synthetic native support 声明。

## 8. 阶段 4：durable execution/publication/resume

**问题**：2、10、18；承接 3、4、14、19。

**目标**：所有 executor 同 claim→execute→commit→publish 协议；StepResult 不越过 reuse。

**模块**：execution/batch、persistence/work_items/reuse/publication/run_state、application。

**删除/替换**：文件存在即 continue、non-durable gap 放行、analysis 内存旁路/结果复制、目录存在即完成、重试旧目录。

**要求**：恢复确认 run/definition/input generation，装配当前 items，再逐项 reuse；明确 failed/cancelled/abandoned retry；完成 item 不重算。environment/provenance 失效可审计。StepResult 必须 durable+artifact verified。blocked/interrupted lifecycle 不冒充科学发布。analysis 明确 aggregation item 范围。RunState 不复制结果。并发 ownership 与 publication 一致。新 generation 显式拒绝或创建，不能覆盖历史。

**测试**：item commit/step publish/run state 前后 crash matrix；部分完成 kill/restart；定义/输入/group/environment/provenance 失效；artifact 删除损坏；failed/partial/cancelled；analysis reuse；gap 拒绝；并发 run root ownership。

**判据**：跨进程 fault injection 证明完成 item 不重算，所有发布结果可追溯 durable backing，输入变化不被快照掩盖。

## 9. 阶段 5：remote 保真与跨机器恢复

**问题**：15、18；承接 14、17。

**目标**：transport 仅改变 delivery/staging/environment，不改变科学语义。

**模块**：remote/envelope/transport/worker/staging/result_bundle、binding resolution、application transport wiring。

**删除/替换**：强制 single driving、重建错误 port、假 step digest/空 provenance、错误固定 store、进程内调用充当跨机器完成证明、只查当前 token。

**要求**：完整 ports/typed identities/真实 digests/provenance。目标端解析 binding，不机械复制本地绝对 executable。共享 executor/adapter/profile/check/recovery。导入正确 run/step/item/attempt。producer 重启先 reconcile 旧 delivery，不盲增 attempt 重发。持久去重/存活边界。不支持组合提前拒绝，不静默降级另一 runtime。

**测试**：跨进程/隔离环境 local/remote parity；named QST/NEB、多输出 IRC/GOAT；多个 result/artifact ports；多 step store/attempt；response loss、双端 crash、重复投递、迟到结果；remote 完成但 producer 未 commit；target/walltime/env 实效。禁止 rehome 结果代替 parity。

**判据**：正式 application 提交/恢复多步骤 remote，科学语义一致，无重复 native launch/attempt 错配。

## 10. 阶段 6：Analysis/TSPES/PES/assignment

**问题**：6、7、10、11、13。

**目标**：纯、确定性 analysis WorkItem，typed identity+明确 selector，统一持久化。

**模块**：analysis/models/grouping/executor/compute/thermochemistry/reaction/pes、producer/recipes。

**删除/替换**：first-match、顺序选理论层级、assignment 丢失、整个 native policy 透传、空 provenance、手工 Gibbs 代替链路、application 再解释 partial。

**要求**：definition/energy policy/assignment 分别解析一次；reaction node 明确 geometry/result 关联；direct/composite Gibbs、low correction/high SP 来源显式，多候选拒绝。forward/reverse 默认方向，assignment 进入结果/provenance/manifest。partial 与 item/step completion 一致。同 ID 不同 payload 不静默取第一份。TSPES 绑定完整 TS/endpoint Opt/Freq/SP；PES 从真实 analysis results 聚合。

**测试**：结构/结果/端口乱序不变；多级别选择和歧义；同值不同来源；未/显式/非法 assignment；group partial policies；无预置 group_key TS；完整计算链；analysis 无 native 且 durable reuse。

**判据**：无需手工科学中间结果，实际输出可产生正确 profile/PES，每个数值追溯明确 ResultRef。

## 11. 阶段 7：唯一 application/入口与 Producer Contract

**问题**：1、8、20；集成所有前置阶段。

**目标**：CLI/service/control/JobDesk submission 都调用同一 V4 application。

**模块**：main/cli/v4cli、application/v4_run/execution、control_worker、producer/contract/manifest/validation/recipes。

**删除/替换**：旧正式 execution 路由、仅子命令 V4、错误手写 manifest、状态冒充 digest、空 artifact/内存 publish、旧 configuration scientific authority、application 再解析 science。

**要求**：多用户入口允许，但一个 pipeline。V2/V3 明确迁移/拒绝，不 fallback。service 可保留通用 lifecycle，不调用旧 engine。producer 从实际 registry/schema/validator/recipes 生成。实际 manifest 有真实 digests、refs、artifact identity/checksum/locator、analysis/status，验证后原子发布。partial/cancelled 不全压 failed。JSON failures 结构化。JobDesk 不复制 science。

**测试**：所有正式入口到同一 runtime；legacy 全入口不可执行；actual manifest 对 actual schema；artifact 可读可校验/追溯；全状态矩阵；recipes 完整执行而非仅 compile；validated/submitted identity；真实 JobDesk consumer，不只 double。

**判据**：正式路径无法到 legacy execution；JobDesk 可凭唯一 contract 提交并消费实际持久结果。

## 12. 阶段 8：去豁免与独立复审

删除保留旧入口的架构 allowlist、固定 index identity/未知 program 合法的断言、人工中间结果冒充 E2E、遗漏 application/CLI/control 的扫描门禁。合成测试保留为范围明确的单元测试。

必须完整运行：ruff/format、mypy、tests/v4、full tests、coverage、crash/resume tests、production-path E2E。不能降低 coverage 门槛、增加 architecture allowlist 或修改测试接受已知错误。

最终场景：真实 JobDesk consumer → 真实 contract → validate/submit → 正式 application → local/remote → interruption/resume → 20 IRC items → 40 endpoints → Opt/Freq/SP → Analysis/PES → durable manifest → JobDesk artifact 展示。另覆盖 QST2/QST3/NEB/GOAT/ConfGen/Transform、歧义/partial/cancel/artifact damage/environment change。

独立 reviewer 从正式入口追踪全链，不只 diff。新发现 drift 同样阻断。仅当全部 architecture drift 清零才允许进入 Final Closure；本执行轮只可报告 READY_FOR_FRESH_ASTRA_REVIEW = YES / NO，不自行宣称 Final Closure ready。

## 13. 根因与覆盖矩阵

“自动消失”不免除回归验收。

| 问题 | 根因阶段 | 随根因消失 | 单独下游验收 |
|---|---|---|---|
| 1 | 7 唯一 application | legacy production truth | 所有正式入口 |
| 2 | 4 恢复协议 | 错复用/失败快照 | definition/input/artifact matrix |
| 3 | 2 装配状态 | 缺输入空成功 | application 诊断 |
| 4 | 2 状态化 Binding | 无条件消费失败子集 | partial matrix |
| 5 | 3 真实 adapter | fake native support | 各程序真实格式/运行 |
| 6 | 2 identity | 错 subject/新 identity | 6 recipe/node association |
| 7 | 2 ResultRef | 结果不可唯一引用 | 6 删除 first-match |
| 8 | 7 唯一结果合同 | shape drift | digest/artifact/durable publish |
| 9 | 1 注册 | 默认 ORCA dispatch | 3 ConfGen/Transform 实现 |
| 10 | 4 item lifecycle | analysis 重算/复制 | 6 aggregation/partial |
| 11 | 6 解析 | policy 冲突 | assignment/provenance |
| 12 | 2 identity/digest | 重排换身份/错 reuse | 导入映射/旧状态版本 |
| 13 | 2 ResultRef | value equality 冒充 identity | remote/analysis selector |
| 14 | 3 binding | 覆盖声明 | 4 reuse/5 remote |
| 15 | 5 transport | 单结构特例 | named/multistep/cross-host |
| 16 | 2 mapping | 原序检查误拒 | QST3 各 slot |
| 17 | 3 artifact | staged 冒充 used | adapter 使用/损坏 |
| 18 | 4 持久协议 | 非持久正式快照 | 5 attempt reconciliation |
| 19 | 2 locator | sanitize collision | 4 attempt isolation |
| 20 | 1 注册 | 声明未实现/未知 program | 7 contract 一致 |
| 21 | 3 adapter/binding | 第二 syntax authority | recovery native 回归 |
| 22 | 3 stochastic | 双 seed | GOAT/ConfGen/local/remote |

## 14. 再次独立 review 的硬门禁

1. 所有正式入口 V4，旧 TaskRunner/CalcStepRunner/ResultsDB/WorkflowState execution 不可达；V2/V3 只 migration/read-only。
2. science 不改 executable/env/target/workdir；scheduler 不改科学身份；environment reuse 有明确规则。
3. compiler/runtime/producer 能力一致；无公布未实现能力；无 TaskName/role 隐式 dispatch。
4. 路径/文件名/index 非身份；geometry/ResultRef/artifact/lineage 可追溯；值与实体严格区分。
5. Binding 缺失/歧义/cardinality/status 受控，无顺序猜配，无错误空成功。
6. 四 executor 统一 WorkItem→Result；Analysis 不 native；ConfGen/Transform 不 legacy。
7. adapter 独占 native；无 production fake dialect；Recovery 不越界。
8. 完成 item 不因中断重算；严格 durable publish；变更不错误复用；analysis 同样恢复。
9. remote 同 executor/adapter，ports/identity 保真，跨重启/重复/迟到/response loss 有证据，无第二科学 runtime。
10. IRC20→40/QST2/QST3/NEB/GOAT/mapping 正式路径；Opt/Freq/SP subject/selector 正确；显式 assignment 生效。
11. actual manifest 通过 actual schema；refs/digests/artifacts 真实可读；真实 JobDesk 无科学复制；double 非唯一证据。
12. 22 项均有 ROOT CAUSE / FIX / TEST / STATUS；review 全调用链/故障/产物；任何新 drift 阻断。未通过不得进入 Final Closure。

## 15. 执行组织与交付

主 agent 先冻结共享 contract 决策。第一波并行：capability/registry/compiler；identity/ResultRef/Binding/mapping；executors/native boundary/ExecutionBinding；persistence/resume/publication。明确文件 ownership，不同时修改同一模块。第一波完成后必须 cross-module integration。

第二波并行：remote transport；Analysis/TSPES/PES；真实 Gaussian/ORCA IRC/NEB/GOAT native semantics；adversarial/crash/architecture tests。完成后再次 cross-module integration。最后统一正式 CLI/application/control、Producer Contract、manifest。

完整目标和本文必须传给所有 OpenCode MS 1.3 agent，不能用摘要替代。共享 contracts 和最终 integration 由主 agent 负责；agents 不自行 commit/push，不扩大文件 ownership。发现冲突先记录接口建议，由主 agent 统一裁决。

每阶段交付：实现变更、回归测试、删除的旧权威、验证证据、剩余问题。单个 agent 单测通过不等于集成完成。执行结束逐项 ROOT CAUSE/FIX/TEST/STATUS，新增 drift 纳入。最终运行全部门禁，commit/push 当前 repair branch，不 merge/tag/release。

可延后清理：正式切换后物理删除隔离的旧代码；过时 agent/frozen 注释；不影响语义的 serializer/Any/命名整理；内部版本名整理。WorkerHandoffV2 名称本身不是 legacy runtime；bridge 作为纯适配器可保留，但丢语义/覆盖 authority 的行为必须修复。
