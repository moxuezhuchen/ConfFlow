# ConfFlow Workflow V4 架构（V4-1 Greenfield Core + Compiler）

本文只记录已经落地在代码与测试中的事实。代码/测试是第一权威；本文反映当前实现。

里程碑状态：**V4-1 已完成**（greenfield domain + schema/parser + validator + typed binding graph + deterministic compiler + synthetic WorkItem assembly）。
V4-1 **不执行**任何 Gaussian/ORCA 原生程序，也不接入旧的 `CalcStepRunner` / `TaskRunner`。

## 1. 包边界与依赖规则

```
confflow/
  domain/                 # 无仓库内依赖的语义核心
    canonical.py          # RFC 8785 (JCS) 规范化 + typed_digest
    _immutable.py         # FrozenDict / freeze / thaw
    elements.py units.py  # 元素表、显式单位
    errors.py             # DomainError 体系
    structure.py          # StructureRecord / StructureSet
    artifact.py           # ArtifactLocator / ArtifactRef / ArtifactSet
    result.py             # Provenance / ScientificResult / ResultSet
    binding.py            # Binding / BindingSet / PortSelector / Pairing
    resources.py          # ResourceRequest / SchedulerPolicy
    completion.py         # CompletionPolicy / StepStatus / evaluate_step_status
    work_item.py          # WorkItem / WorkItemResult
    step_result.py        # StepResult（无 output_path）
    diagnostics.py        # Diagnostic / DiagnosticSeverity
    retention.py          # RetentionClass / may_garbage_collect
    publication.py        # 崩溃一致性发布协议
    stochastic.py         # SeedPolicy / seed 契约
  execution/              # 能力/契约注册表；不解析 YAML、不拥有拓扑
    contracts.py          # ExecutorContract / AdapterSpec / ProfileSpec / CheckSpec / RecoverySpec / ExecutionBinding / ExecutionEnvironment
    registry.py           # 单一权威能力词汇表 + 每契约版本号
  workflow/v4/            # V4 编译器
    document.py           # canonical definition（frozen dataclass）
    schema.py             # pydantic 形状模型 + 单一默认值来源 + JSON Schema 生成
    parser.py             # 严格 YAML（重复键拒绝）→ canonical definition
    validation.py         # 语义校验，解析 registry 契约与资源策略
    graph.py              # typed BindingGraph、disabled passthrough 重写、确定性拓扑序
    fingerprint.py        # 四个 digest 轴的 payload 与 digest
    scientific.py         # 科学参数 precedence 唯一权威 + electron parity
    plan.py               # ExecutionPlan（immutable）
    compiler.py           # 纯函数编译入口
    assembly.py           # run inputs + materialized outputs → synthetic WorkItems
    diagnostics.py        # 8 个 code family + 稳定 reason
  workflow/__init__.py    # PEP 562 懒导出；import confflow.workflow.v4 不加载 V3 runtime
```

依赖规则（`tests/v4/test_architecture_boundaries.py` 静态 + 运行时双重把关）：

- `confflow.domain` 不 import 任何其它 `confflow.*`（仅 stdlib、pydantic 之外的第三方：`rfc8785`）。
- `confflow.workflow.v4` / `confflow.execution` 只允许 import `confflow.domain`、`confflow.execution`、`confflow.workflow.v4`。
- 禁止 import 前缀：`confflow.config`、`confflow.calc`、`confflow.core`、`confflow.blocks`、`confflow.shared`、`confflow.application`、`confflow.worker_*`、`confflow.control*`、`confflow.cli/main/confts/contract/artifact_json`，以及全部 V2/V3 workflow 模块。
- `confflow.domain` 中的元素表是 V4 自有的最小副本；`tests/v4/test_elements_drift.py` 与 `confflow.core.data.PERIODIC_SYMBOLS` 交叉校验，防止静默漂移（生产代码不 import legacy）。
- `import confflow.workflow.v4` 在子进程中验证不会把 V3 runtime / 旧 config / calc 拉进 `sys.modules`。

## 2. 核心模型（事实）

### 2.1 StructureRecord / StructureSet

- `StructureRecord.id` = 实体身份；`geometry_digest` = 几何内容身份（atoms 顺序 + 坐标 + `angstrom` 单位，metadata/charge/multiplicity/id/provenance 不参与）。二者绝不混用；两个几何相同的记录可以有不同的 id。
- `scientific_payload()` = `geometry_digest` + `charge` + `multiplicity`，是 WorkItem 复用的输入内容身份。
- 坐标规范：`tuple[tuple[float, float, float], ...]`，单位 Ångström，拒绝 NaN/Inf/bool/元素标签（如 `C1`）。
- `StructureSet` 有序不可变；保留重复内容不同 id 的多个记录；按 id/role/group_key/lineage 查询。
- 配对键：`StructureRecord.group_key`（producer 显式提供，绝不推导）。

### 2.2 ArtifactRef / ArtifactSet

- locator 是 typed 的（`RUN_RELATIVE` / `EXTERNAL_URI`）；路径不是身份，绝对路径不是 durable identity。
- `checksum` 规范化为 `sha256:<hex>`；`digest_payload()` 有意排除 locator（移动文件不改变复用身份）。
- `subject_structure_id` 是 checkpoint 等 artifact 的匹配键。

### 2.3 ScientificResult / ResultSet

- 每个量显式携带 `unit`；`quantity` 可由 unit 推导，反之缺失 unit 直接报错（domain 层无隐式单位）。
- `value` 在构造时经 JCS 规范化并验证（拒绝非 JSON 值、非有限数字）。
- 不存在 chemistry-column 形状的结果表。

### 2.4 Binding / BindingSet

- 唯一边 vocabulary：`target step + target port ← source(run input | step output + port + selector)`。
- selector 只有 `ALL / ROLE / IDS`；禁止 label/basename/list index 作为 selector。
- pairing：`single / per_structure / by_group_key / by_subject`；artifact/result 端口禁止 `per_structure`（位置匹配被静态拒绝）。
- cardinality 只能收缩或加强端口契约（`OPTIONAL + one` 合法；`ONE + many` 报错）。
- 同一 `(step, port)` 只允许一个 binding（聚合必须显式建模为独立 step）。

### 2.5 WorkItem / WorkItemResult

- `id = make_work_item_id(logical_key) = "wi:<logical_key>"`（构造时强校验）。
- `logical_key = "<step_id>:<subject id | group key | all>"`：跨重编译稳定。
- `semantic_digest`：step digest + 解析后的输入内容 + 资源；**不含** logical_key（内容相同即可复用，即使实体 id 不同）。
- 三层身份互相独立并有测试覆盖。

### 2.6 StepResult / Completion / Publication

- `StepResult` 只有结构/结果/artifact/item_results/diagnostics/summary/provenance，**没有** `output_path`。
- §11 的两个正交概念：`CompletionPolicy(mode=require_all|allow_partial, minimum_success, partial_output)` 与 `SchedulerPolicy(on_failure=continue|fail_fast)`。
- `evaluate_step_status`：空 items → completed；出现 cancel → cancelled；require_all + 失败 → failed；allow_partial 达到 `minimum_success` → partial，否则 failed。
- `allow_partial` 生产者：`partial_output=deny` 时下游绑定编译报错；`allow` 时下游必须显式声明 `partial_consumption=accept_subset|require_complete`。
- `PublicationTracker` 编码 8 阶段 commit order；`verify_step_publication` 要求 item result durable 且 artifact checksum 已验证，否则 `PublicationError`。SQLite 实现属于 V4-2。

### 2.7 资源与三层分离

- `ResourceRequest = cores_per_item + memory_per_item_bytes`（科学身份，进入 step/work-item digest）。
- `SchedulerPolicy = max_parallel_items + on_failure`（运维身份，**不进入**任何科学 digest）。
- `ScientificDefinition`（program/native/adapter/profile/checks/recovery/seed/overrides）与 machine-specific `ExecutionBinding`（executable/env/sandbox/target/walltime）与 scheduler 三层分离；XML/executable path 不污染定义 digest。
- 内存文本（`16GB`/`16GiB`）按二进制后缀解析为 bytes，解析规则唯一来源 `domain/resources.py`。

### 2.8 科学参数 precedence（单一权威）

`confflow/workflow/v4/scientific.py`：

```
StructureRecord 固有属性 > step explicit override > run-level scientific default
```

- 结构显式值与 step override 冲突：step 生效 + `scientific_parameter_conflict(structure_value_overridden)` warning。
- charge/multiplicity 都已知时校验 electron parity（不可能的组合直接 error）。
- `freeze`（1-based 原子索引）同样由该权威解析：step override > run default；索引排序去重进入 digest；越界报 `freeze_index_out_of_range`。
- precedence 结果进入 work-item 输入内容（effective charge/multiplicity/freeze）。

## 3. 编译流水线

```
WorkflowDocument (YAML)
  → parse              # 重复 YAML key 拒绝；extra=forbid；单一默认值来源
  → Canonical Definition
  → semantic validation # registry 词汇表、端口、checks/profile、seed、资源、scheduler
  → typed BindingGraph   # bindings 是唯一边来源；disabled passthrough 在此重写；Kahn 波次拓扑
  → ExecutionPlan        # 不可变、deterministic、stable-ID keyed
  → assemble_work_items  # run inputs + materialized outputs → synthetic WorkItems
```

- 编译纯函数（不读时钟/文件系统/环境）；同一输入两次编译 payload 字节级一致。
- 拓扑序：`sNNN` 数字序优先，其余字典序；文档数组顺序不影响结果。
- 依赖环、自引用、未知 step/port/run input、kind 不匹配、selector 不合法、`role_required`、位置配对全部编译期拒绝。
- Disabled step：契约声明 `passthrough_ports`（纯透传）时，consumer 的 binding 递归重写到 effective upstream（记录 `via_disabled`）；需要 artifact/result 的 consumer 直接 `capability_error(disabled_capability_lost)`；不存在任何 runtime bypass 代码路径。
  - 内置 V4-1 registry **刻意不把任何生产 step 声明为 pure passthrough**；该机制由 contract 驱动，测试用注入 registry 覆盖（`tests/v4/_builders.py::passthrough_registry`）。

### 3.1 Synthetic WorkItem 组装（§23 四场景）

- A：`run.structures [A,B,C]` → `per_structure` 驱动 → 3 个 WorkItem，logical key 稳定。
- B：checkpoint 按 `subject_structure_id` 匹配（与列表顺序、文件名无关）。
- C：required checkpoint 缺失 → `cardinality_error(artifact_subject_missing)`，明确 step_id / logical_key / port / subject，且不产出任何 item。
- D：命名输入（reactant/product/guess）只在显式 `by_group_key` 且 producer 提供 group_key 时配对；否则 `binding_error(pairing_undefined)`。
- 未能 materialize 的 producer → `not_assemblable` warning 并跳过该 step（绝不猜测上游结果）。

## 4. Digest 四轴

| 轴 | 内容 | 排除 |
|---|---|---|
| `WorkflowDefinitionDigest` | schema/semantics 版本、scientific defaults、named run inputs、每步 step digest、bindings、completion | label、annotations、GUI、数组顺序、scheduler、execution binding |
| `StepSemanticDigest` | program/native/adapter/profile/checks/recovery/seed/overrides（科学）+ 各契约版本 + `cores_per_item`/`memory_per_item` + enabled | label/annotations、`max_parallel_items`、executable/env/sandbox/target/walltime |
| `WorkItemDigest` | step digest + 解析后输入内容（structure scientific payload / artifact checksum+role / result value digest）+ resources + source 描述 | logical_key、时间、locator、主机信息 |
| `ExecutionEnvironmentDigest` | program、program_version、executable content digest、target | 绝对 executable 路径（作为 provenance 保留） |

- 约定：JCS 规范化 + `typed_digest(kind, payload)` 域分离；digest 格式 `sha256:<64hex>`。
- secret/label/metadata/annotations 改名、`max_parallel_items`、executable 路径变化 **不移动**科学 digest；native/checks/seed/resources/科学 defaults 变化 **移动**。
- 金标准 digest 固定在 `tests/v4/test_digest_axes.py::TestGoldenDigests`。

## 5. Registry / 能力词汇表（单一来源）

`confflow/execution/registry.py` 是唯一权威；schema JSON 由 pydantic 模型生成后注入 registry 的 enum：

- executor capability：`calculation`、`confgen`、`analysis`、`structure_transform`（`structure_transform` 的 YAML block 名为 `transform`）。
- execution adapter：`standard`、`named_structures`、`native_template`。
- result profile：`standard`、`path_endpoints`、`ensemble`、`opaque`。
- scientific check：`normal_termination`、`geometry_required`、`frequencies_required`、`imaginary_frequency_count`、`max_rmsd_from_input`、`bond_drift`。
- recovery：`none`、`ts_rescue_scan`（必须显式声明；不存在 `if role == "ts"` 或 itask 驱动的救援）。
- 每个 descriptor 带独立 contract version，进入 step digest（registry 全局版本被禁止）。

端口契约事实：

- `calculation` 的输入端口由 adapter 决定：`standard` = `structure` + 可选 `checkpoint`（BY_SUBJECT）；`named_structures` = `reactant`/`product`（BY_GROUP_KEY）+ 可选 `guess`；`native_template` 同 standard。
- 输出端口：`structures`（MANY, per_structure）、`artifacts`（MANY, by_subject, roles=checkpoint/native_output/trajectory）、`results`（MANY, by_subject）。
- `confgen` 是 stochastic：缺 seed 直接编译错误；seed 进入所有科学 digest（resume 不重新随机）。
- `structure_transform` 只接受显式 `kind = refine|deduplicate|filter`；calculation **不存在** hidden auto_clean/refine 尾巴。
- `analysis` 无结构输出。

## 6. Defaults 单一来源

- `confflow/workflow/v4/schema.py`：`DEFAULT_CORES_PER_ITEM=1`、`DEFAULT_MEMORY_PER_ITEM="1GiB"`、`DEFAULT_MAX_PARALLEL_ITEMS=1`；pydantic 字段默认值即运行时默认值，JSON Schema 同时输出。
- `build_workflow_json_schema()` 注入 registry 词汇表；测试断言 schema 文本中不存在 `auto_clean` / `delete_work_dir` / `clean_opts` / `ibkout`。
- YAML 中显式写出默认值与省略默认值产生相同 digest。

## 7. 复用决策（来自低层审计，V4-2 执行前生效）

- REUSE（计划）：`artifact_json.write_atomic_json`、`worker_staging._stage_file` 的 secure-copy 语义、`core/path_policy.py` 的配置无关校验、Gaussian/ORCA 纯渲染/解析函数、`calc/geometry.py` 的 `parse_last_geometry`/`check_termination`、`core/io.py` 的流式 XYZ 读取、`worker_supervision` 的 liveness 规则、`launch_lease` 的 flock 模式。
- DO_NOT_REUSE：`CalcStepRunner`/`CalcStepRequest`/`CalcStepResult`、`TaskRunner`、`CalculationPolicy` 注册表、`TaskContext`、`GlobalOptions`、V2/V3 canonical fingerprint、manifest/results.db 合同、itask/iprog 映射、`chk_from_step`/`backup_dir`/`ibkout` 语义、worker handoff 的 `input_xyz` envelope。
- 原则：复用正确低层能力，替换错误高层抽象；V4-1 不复制任何 legacy 代码。

## 8. 明确非目标（V4-1）

- V4-1 不执行 Gaussian/ORCA（无进程、无 native 输入渲染落地）。
- 不实现 WorkItem SQLite 持久化、remote worker v2、JobDesk 集成、V2/V3 migration 命令。
- 不支持自由 GUI、streaming DAG、distributed scheduler、插件生态、arbitrary native template 执行。
- 不把 V4 接入 V2/V3 runtime dispatch / CLI 执行路径；V3 对这些 schema 仍然 fail-closed。
- 原子序数到 QST/NEB 的 atom mapping（`permutation|mapped`）推迟；V4-1 要求配对结构 atom 顺序一致。

## 9. 测试与门禁

- `tests/v4/`：domain/structure/identity、binding/resource/scientific、schema/parser、failure policy/publication、digest 四轴、compiler（determinism/disabled/policy/registry）、assembly（§23 四场景）、architecture debt gate。
- Architecture gate：AST 静态 import/symbol 扫描 + 子进程 runtime 导入隔离 + `setuptools.find_packages` 打包检查 + schema 无 hidden cleanup 词汇。
- 本地命令：

```bash
.venv/bin/python -m pytest tests/v4 -q
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/mypy confflow
```

---

# V4-2：New Standard Calculation Engine（已完成）

里程碑状态：**V4-2 已完成**。建立了真正的 standard-calculation vertical slice，
全程不经过 `input_xyz → CalcStepRunner → output_path`。

```
StructureSet → Binding/WorkItem → BatchStepExecutor → WorkItemExecutor
  → ExecutionAdapter → ProgramAdapter → native process boundary
  → NativeResult → ResultProfile → ScientificChecks → RecoveryPolicy
  → WorkItemResult → StepResult
```

Opt / SP / Freq / Opt+Freq / TS 只作为 human role、native input 内容和
recipe/test fixture 存在；V4 runtime 没有 task dispatch enum。

## 10. V4-2 包与依赖规则

新增：

```
confflow/
  execution/
    native.py             # NativeExecutionRequest/Result、ProgramAdapter 协议、process 接口
    profiles.py           # ResultProfile 协议 + passthrough 规则
    checks.py             # ScientificCheck 协议 + CHECK_DEFAULTS 单一来源
    recovery.py           # RecoveryPolicy 协议 + RescueDriver
    process.py            # V4 自有进程边界（抽取的 launch/poll/cancel 机制，无 policy 知识）
    work_item_executor.py # 单 item 全流水线
    batch.py              # BatchStepExecutor + repository/reuse 端口（内存实现）
    environment.py        # 可执行文件测量 + environment digest
    profile_standard.py   # standard profile 实现
    checks_standard.py    # 6 个 check 实现
    recovery_standard.py  # none + ts_rescue_scan（含 scan 引擎）
  programs/
    registry.py           # program 名 → adapter（g16/gaussian/orca 等别名）
    gaussian/             # GaussianProgramAdapter（渲染/解析/artifact 发现）
    orca/                 # OrcaProgramAdapter（同上）
```

规则（debt gate 已扩展覆盖 `confflow.programs`）：

- `confflow.programs` 只允许 import `confflow.domain`、`confflow.execution`、
  `confflow.workflow.v4`（类型）、`confflow.programs` 自身。
- `confflow.execution` 不在模块加载期 import `confflow.workflow.v4`
 （只用 `TYPE_CHECKING` + 调用期延迟 import），因此不存在 import cycle；
  子进程导入隔离测试保持通过。
- 新 execution/program 代码零 legacy 语义 import（gate 列表见 §27 扩展）。

## 11. V4-2 核心语义（事实）

- **NativeResult 是 parser 事实层**：termination、final geometry（有/无）、
  Hartree 能量（electronic/gibbs/gibbs_correction）、cm⁻¹ 频率、文件清单、
  parser diagnostics。`geometry_output` 只有 `produced/none`；passthrough
  与否由 profile 按 declared checks 决定，不从 task 名推断。
- **standard profile**：Gibbs 优先的能量选择（`g ?? e+gc ?? e`，缺失 gc 时
  `gc = g - e` 显式推导）；无几何 + 未声明 `geometry_required` 时发射
  passthrough `StructureRecord`（新 id、parent=input、lineage 保持、
  geometry_digest 不变）；显式拒绝静默 metadata 继承（旧 SP 的
  `G_corr/Imag/LowestFreq/TSAtoms/TSBond` 继承在本轮被有意打破）。
- **Checks**（全部显式声明才执行）：`normal_termination`（executor 对未正常
  结束 fail-closed，与声明无关）、`geometry_required`、`frequencies_required`、
  `imaginary_frequency_count{expected}`（无频率数据时仅 `expected==0` 通过）、
  `max_rmsd_from_input{threshold_angstrom=1.0}`（Kabsch 对齐，fail-closed）、
  `bond_drift{atoms, threshold_angstrom=0.4}`（fail-closed；V4 相对旧 fail-open
  收紧）。阈值默认值与旧科学策略一致，唯一来源 `CHECK_DEFAULTS`。
- **Recovery**：`none` 直接拒绝；`ts_rescue_scan` 仅 Gaussian、仅首次、
  仅 cancellation 已确认、仅 bond atoms 可解析、仅 TS 类失败原因；
  scan 引擎做 coarse+fine 峰搜索后用原 keyword 重优化并复验
  drift/RMSD/imag；成功标记 `rescued_by_scan` provenance，失败记
  `recovery_failed`。未确认的 cancellation 永不进入 rescue。
- **WorkItemExecutor**：驱动结构必须是 `structure` 端口唯一结构（命名多结构
  执行留给后续里程碑，显式报错不猜测）；checkpoint artifact 按 locator 从
  run root 暂存并校验 checksum；charge/multiplicity/freeze 由 V4-1 唯一权威
  预先 resolve，adapter 缺值直接 `native_input_error`。
- **BatchStepExecutor**：`ThreadPoolExecutor(max_parallel_items)`；
  fail_fast 置位后未启动 item 直接记 cancelled（已修复预启动取消 KeyError）；
  结果按 logical key 规范序收集；`require_all/allow_partial` 复用 V4-1
  `evaluate_step_status`；StepResult 只聚合 completed subset。
- **资源渲染**：`%nprocshared=cores_per_item`、`%mem=⌊bytes/ GiB⌋（≥1GB）`；
  ORCA `PAL nprocs` + `%maxcore`（显式 override 原样通过，否则按核均分
  向下取整到百、保底 100）；渲染输入与 `max_parallel_items` 无关（已测试）。
- **Environment**：resolved path、dev/inode、流式 sha256、adapter/parser
  版本进 digest；绝对路径只做 provenance；同 binary 重定位 digest 不变，
  binary 内容变化 digest 变化（已测试）；version probe 无安全手段时为空。
- **SP passthrough 规则（§18 选型 B）**：输出新语义 StructureRecord，
  parent=input，geometry_digest 相同。所有 StepResult 内结构都有 producer
  provenance，内容身份不受影响。

## 12. V4-2 复用结论

- EXTRACT（算法自有化，零 legacy import）：进程 session/pgid/identity/cancel
  证明；Gaussian/ORCA 渲染与解析（含 freq commit 规则、noise floor 10.0、
  archive 回退、termination tail）；`format_orca_blocks`、`gaussian_apply_freeze`、
  keyword 改写；Kabsch RMSD 与键长数学；scan 峰搜索与重优化验收。
- REWRITE：全部 config-dict 管线、itask/iprog 分发、policy 单例、
  `cleanup_lingering_processes`（按名杀进程，不复用）、backup/`ibkout` 语义、
  checkpoint 文件名约定（改为 binding + staged artifact）、`G_corr` 静默继承、
  bond-drift fail-open。
- 复用安全性：新包 import 边界 + 扩展 debt gate 保证无旧抽象渗入。

## 13. V4-2 非目标状态

IRC/path_endpoints、QST2/QST3、NEB、GOAT、ensemble、PES、SQLite
WorkItemStore、per-item resume、worker-handoff.v2、JobDesk、migration、
streaming DAG 均未做。Batch/Executor 通过 `WorkItemRepository` /
`ReuseStore` 协议与内存实现为 V4-3 留好 seam，无需改动核心 contracts
即可接入持久化与按 digest 复用。

V4-3 readiness：YES — WorkItem / ProgramAdapter / StepResult 契约无需改动，
即可接入 WorkItemStore、per-item durable resume、resource persistence、
fingerprint reuse（reuse 端口已存在并有内存实现与测试）。

---

# V4-3：Durable Persistence + Per-WorkItem Resume + Reuse（已完成）

里程碑状态：**V4-3 已完成**。V4 具备 durable execution semantics：

```
ExecutionPlan → WorkItems → WorkItemStore
  ├─ valid completed  → REUSE (native invocation 0)
  ├─ pending/new      → EXECUTE
  ├─ retryable failed → RETRY (new attempt, history preserved)
  ├─ abandoned running → RECONCILE (proof-gated, never blind duplicate)
  └─ stale/incompatible → INVALIDATE (fail closed, history preserved)
  → WorkItemExecutor → durable WorkItemResult → atomic StepResult
  → RunState transition → downstream eligibility
```

100-item 验收：95 COMPLETED + 3 FAILED + 2 PENDING 经进程重启 resume 后，
reused=95、native invocation=5、duplicate=0（在 submit 边界计数证明，
非结果数量推断），最终 100 COMPLETED。

## 14. V4-3 包与依赖规则

```
confflow/persistence/       # 只 import confflow.domain + stdlib (sqlite3)
  contracts.py              # 冻结共享权威（main owned）：schema v1、状态机、
                            # ReuseDecision、OwnerIdentity/Verdict、RunState、
                            # GCPlan、run layout
  work_items.py             # SqliteWorkItemStore + StoredAttempt
  reuse.py                  # ReuseInputs + evaluate_reuse（纯函数）
  publication.py            # 原子发布 + rebuild + durability gate
  run_state.py              # RunState load/save/transition/repair
  recovery.py               # reconcile_owner + owner_identity_current（只读）
  artifacts.py              # verify_artifact + plan_gc/apply_gc
confflow/execution/batch.py # BatchStepExecutor.execute_step_resumable（编排）
```

- `confflow.persistence` 只允许 import `confflow.domain` / 自身（debt gate
  新增 `test_persistence_imports_only_domain_and_self` 锁定；相对 import
  按层级解析后判定）。
- `confflow.execution` 允许 import `confflow.persistence`（单向；persistence
  永不反向依赖，无 import cycle；子进程隔离测试保持通过）。
- `ProgramAdapter` / `ResultProfile` 零 SQLite：orchestration 只存在于
  `BatchStepExecutor`；scientific execution 保持纯净（gate 覆盖）。
- Debt gate 新增：`ResultsDB/task_results/WorkflowStateV1-V3/
  CheckpointManager/WorkflowStatsTracker/FailureTracker/TaskStatsCollector/
  output_xyz/delete_work_dir/binding_v2` symbols，以及
  `result.xyz/failed.xyz/output_xyz/delete_work_dir/input_xyz/output_path`
  的 code-text 扫描（带点文件名 AST 扫不到）；`confflow.programs` 与
  `confflow.persistence` 纳入全部扫描根；packaging 要求两个新包可发现。

## 15. V4-3 核心语义（事实）

- **Truth hierarchy**：WorkItemStore（per-item 真值）> StepResult（语义输出
  真值）> RunState（生命周期真值）> Artifacts（文件对象）。`result.xyz` /
  `failed.xyz` / `output.xyz` / `workflow_stats` / log 存在性 / 目录名 /
  文件名永不作为语义真值。
- **Run layout**（§4 微调：item 目录直接位于 step 目录下，不再套 `work/`
  层；authority 不变）：
  `run/run_state.json` + `run/steps/<step>/work_items.sqlite` +
  `step_result.json` + `steps/<step>/<job>/`（native 工作目录，artifact
  locator 经 relpath 与之严格一致；V4-2 默认布局下 relpath 恰好还原旧
  `items/...` 前缀，行为零变化）。
- **State machine**：`PENDING→RUNNING→COMPLETED|FAILED|CANCELLED|
  INTERRUPTED`；`FAILED|INTERRUPTED→RUNNING`（新 attempt）；`COMPLETED|
  CANCELLED` 无出边，历史永不覆盖。非法 transition 报
  `StateTransitionError`。
- **Claim**：`BEGIN IMMEDIATE` 单事务 `PENDING→RUNNING + attempt 行`；
  8 线程抢同一 item 恰好 1 胜；不同 item 可并行（WAL + 5s busy_timeout；
  事务永不横跨 native execution）。
- **ReuseDecision**：`REUSE/EXECUTE_NEW/RETRY_FAILED/RECOVER_ABANDONED/
  INVALIDATE_DEFINITION|INPUT|ENVIRONMENT|PROVENANCE|ARTIFACT/
  BLOCKED_UNCERTAIN_OWNER`，带 reason + mismatch 轴 + work_item 身份。
  规则顺序冻结：无记录→PENDING→RUNNING（dead 才 recover，alive/uncertain
  一律 block）→CANCELLED（`requires_explicit_retry`，永不 auto-retry）→
  provenance→environment→definition→artifact→input→verification→REUSE/
  RETRY。label/GUI/`max_parallel_items`/binary 路径不是字段，构造上无法
  invalidate；memory/cores 进 `work_item_digest`→`INVALIDATE_INPUT`；
  keyword/checks/recovery/seed 进 step digest→`INVALIDATE_DEFINITION`。
- **INVALIDATE  fail-closed**：`execute_step_resumable` 对 INVALIDATE_* 不
  执行、不碰历史，合成 FAILED 报告参与 completion（`require_all`→failed，
  `allow_partial`→partial 且失败项保留）；同 store 上新 definition 代重跑
  是显式未来工作（run generation），本轮拒绝静默覆盖。`CANCELLED` 行只
  携带（stored result durable），永不重跑。
- **Provenance**：`build_producer_provenance` 单一形状（adapter/profile/
  check versions/recovery/canonicalization id）；无 git-SHA 粗粒度门槛，
  无法证明兼容即 invalidate。
- **Abandoned RUNNING**：`reconcile_owner` 只读判定（pid+create_time
  1e-6s 容差 + pgid/sid OR 组扫描；zombie 不算存活；self 排除；不可读
  成员按存活计）。`DEFINITELY_DEAD→mark_interrupted→re-claim→执行`；
  `ALIVE/UNCERTAIN→BLOCKED`（合成 FAILED `blocked_uncertain_owner`，
  retryable=True，不持久化，store 保持 RUNNING 供下次 reconcile）。
  未确认的 cancellation 永不 rescue（V4-2 规则延续）。
- **Crash order**：native 结束→artifact 落盘→checksum 验证→DB commit→
  assemble→temp+fsync+`os.replace` 发布→RunState→downstream。`COMPLETED`
  无结果行、`COMPLETED` 损坏结果行一律 fail-closed 合成失败，不 invent。
- **Rebuild**：全部 COMPLETED 但未 publish 时，从 store 结果重建（不跑
  native），幂等同 digest；`detect_published` 区分 absent（None）与
  corrupt（raise）。
- **Artifacts**：`verify_artifact`（containment + realpath + sha256 流式 +
  可选 size；`EXTERNAL_URI` 只验形）；retention 映射（TEMPORARY→
  `TEMPORARY`，NATIVE_SUPPORTING→`INTERMEDIATE`，RESUME_REQUIRED/
  PUBLISHED/DOWNSTREAM_REQUIRED 为保护 id 集而非新 enum）；`plan_gc` 纯
  dry-run + `apply_gc`（TOCTOU 重验、只删文件、幂等、失败进 failed_ids）；
  无 `delete_work_dir` 语义。
- **Determinism**：全部 DB/发布 JSON 经 JCS canonical；`list_items` 按
  logical_key；assemble 按 work_item_id；duration 只用 monotonic 上报，
  wall 只做 provenance（含 V4-2 wall 回退 clamp）。
- **Security**：run-root containment（store path/locator/GC 三处独立校验），
  symlink-outside 拒绝，atomic writes，owner token 绑定 claim（token
  mismatch 属调用方比对，reconcile 只判 triple 存活）。

## 16. V4-3 复用结论与遗留

- REUSE：JCS canonical 层、`typed_digest`、`may_garbage_collect`、
  process session/pgid/identity 证明机制（只读移植）。
- REWRITE：ResultsDB 化学列、`WorkflowState` V1/V2/V3、`ibkout`/`backup`/
  `delete_work_dir`、`chk_from_step` 文件名约定、静默 metadata 继承
  （V4-2 已破）、`G_corr` 跨步继承。
- 遗留风险：`claim()` 内 sqlite I/O 错误返回 False（按 contention 处理；
  安全方向——永不 duplicate，但可能掩盖磁盘故障为 block；需要磁盘故障
  注入测试，V4-4 补）；Windows 分支未覆盖；`resolve_multiplicity(0)`
  已修复（Gaussian 与 ORCA 一致 `>=1`）。
- V4-3 非目标（未做）：IRC/path_endpoints/QST2/QST3/NEB/GOAT/ensemble/
  PES/worker-handoff.v2/remote/JobDesk/migration/streaming DAG/分布式调度/
  cloud artifact/cancelled 显式重跑原语/跨 definition 代重跑。

V4-4 readiness：YES — 无需改动 WorkItem / ProgramAdapter /
WorkItemResult / StepResult / WorkItemStore / reuse-resume 核心契约，
可直接增加 typed cross-step artifact flow、checkpoint binding、
worker-handoff.v2、remote staging（store 的 artifact_rows + 保护 id 集
+ locator 体系即为接入口）。

---

# V4-4：Typed Cross-Step Artifact Flow + Worker Handoff V2（已完成）

里程碑状态：**V4-4 已完成**。跨步骤数据流统一为 typed ArtifactRef 流，
remote worker 消费编译后的 V4 语义并运行同一执行核心。

```
StepResult {StructureSet, ResultSet, ArtifactSet}
  → Binding (role/subject/checksum, never filename)
  → WorkItem assembly (BY_SUBJECT + cardinality fail-closed)
  → LocalTransport | RemoteTransport (handoff V2)
  → WorkItemExecutor (同一套) → ProgramAdapter → ResultProfile → Checks
  → WorkItemResult → ResultBundle → producer import → StepResult
```

## 17. Artifact flow（事实）

- **Identity 轴**：`artifact.id` 身份；`checksum` 内容身份；`role` +
  `subject_structure_id` + producer ids 语义关系；`locator` 位置。
  locator/filename 永远不是身份（digest 已排除 locator）。
- **Subject 规则（§5）**：adapter 按 input structure 发现 artifact；
  executor 经 `resolve_restart_subject` 重定向 restart-role artifact 到
  profile 输出结构 id（produced 与 passthrough 统一：两者都 mint 新
  StructureRecord）。多输出时不猜测、原样透传。
- **Restart role 唯一**：`checkpoint`。ORCA `.gbw` 归一化为
  `role=checkpoint + metadata program_format=orca_gbw`（role 是语义，
  不是扩展名别名）；`checkpoint_wavefunction` 不再作为 ArtifactRef
  role 出现。
- **Cardinality**：ONE 要求恰好 1（0 → `artifact_subject_missing`，
  >1 → `artifact_subject_ambiguous`）；MANY/OPTIONAL 透传；未知名
  fail-closed。绝不 pick-first/newest/alpha。
- **Executor preflight（§9）**：staging 校验 containment + checksum +
  subject==driving（错 subject 直接 `artifact_error`，native 调用 0 次；
  测试钉死）。
- **Gaussian 防御收紧**：`resolve_multiplicity` 拒绝 `<1`（ORCA 早已拒绝）。

## 18. Worker handoff V2（事实）

- **Schema**：`confflow.control.worker-handoff.v2`（envelope）/
  `confflow.control.worker-result.v2`（result），Pydantic strict +
  `extra=forbid` + frozen 单一来源，JSON Schema 由模型生成。
  V1（`worker_handoff.py`，`input_xyz` envelope）frozen 不动。
- **Envelope 内容**：run/step/work-item/logical-key/attempt/launch-token
  身份 + digests + provenance + environment request（transport 提示，
  不进 digest）+ `ExecutionDefinition`（program/native/contracts/
  resources/resolved charge-mult-freeze，**无 graph/YAML/bindings/
  scheduler**）+ `InputBundleManifest`（structure canonical payload /
  artifact id+role+checksum+subject+bundle_locator / typed result）。
- **Digest**：`confflow.remote.bundle.v1` 域；transport 路径、暂存名、
  时间戳、hostname、并发度不进 digest。tuple 字段经 before-validator
  接受 JSON 数组（frozen tuple 类型不变）；`.new()` 在 default 补齐后
  的 dump 上计算 digest，读写一致。
- **Launch token**：`{prefix}+{work_item_id(:→+)}+attempt-{N}`，
  单路径段安全；同 token 重复投递返回已记录结果（内存 + worker
  `results/<token>/result.json` 双层：后者覆盖 producer crash 场景）。
- **Result bundle**：identity 回声 + environment 身份 + result payload +
  produced artifact（checksum/size/subject）+ transport metadata（不进
  digest）。Producer import 验证 identity/attempt/digest → 校验字节 →
  安全拷贝到 run-relative → 重建 ArtifactRef → 按 attempt 匹配 store
  后返回（**不直接 commit**，batch 照常 commit）。

## 19. Remote 执行架构（事实）

- **同一核心**：`RemoteTransport → handoff → staging →
  run_worker_envelope → 同一 WorkItemExecutor/ProgramAdapter/Profile/
  Checks/Recovery → package → import`。无 RemoteWorkItemExecutor；
  worker 禁止 import compiler/YAML/DAG/V3/calc（AST + 运行时双重门；
  executor 传递性拉起的 V4 包 `__init__` 在文档中明确声明为例外，
  worker 自身闭包经 stub 隔离探针验证）。
- **Executable 解析**：handoff 永不携带 producer 绝对路径；worker 侧
  按 program 经 PATH/default 解析（测试用 PATH symlink 注入 fake）。
- **Transport 契约**：stage 失败一律转为 failure *result*（`remote_
  {handoff,staging,worker,result_bundle}_error`，retryable），只有编程
  错误才抛；同 token 并发投递经 per-token 锁串行化；`forget()` 仅测试。
- **Batch 集成**：`execute_step_resumable(..., transport=None)`；
  transport 只接收 claim 后的执行（attempt 对齐 store 新开 attempt）；
  reuse/blocked/invalidate 永不到达 transport。
- **Producer 真值不变**：store 仍是唯一 durable truth；worker 无 DB；
  远端结果经 import 验证后走与本地完全相同的 commit/assemble/publish。
- **Claim 缺陷修复（V4-3 遗留）**：`SQLITE_BUSY/locked` → `False`
 （bounded busy_timeout 内 contention）；其余 OperationalError →
  `PersistenceError`；IntegrityError → `CorruptStateError`；各有故障
  注入回归测试。

## 20. Lifecycle / 安全（事实）

- **AttemptLease**：`(run, step, item, attempt, token)` 绑定 flock；
  同 attempt 不同 token 也互斥（per-attempt mutex 补 marker 路径差）；
  crash 释放由内核保证，audit 文件保留。
- **Supervision**：`reconcile_owner` 主判定 + workdir cwd/killpg 扫描组合
  （DEAD+dir-live → UNCERTAIN）；`cancel_attempt` 仅在 ALIVE 且非自身
  进程组时发信号，事后重证死亡，否则 unconfirmed（调用方映射 BLOCKED，
  永不误标 CANCELLED）。
- **Staging 安全**：O_NOFOLLOW + fstat owner/regular + 0o700 dir-fd pin +
  temp + 流式 sha256 + fsync + dev/ino 重验 + 发布前重验 + dir fsync；
  traversal/absolute/symlink/world-writable/checksum/TOCTOU 全 fail-closed。
- **Handoff 文件安全**：owner regular + 无组写 + 有界读取 + 严格 UTF-8 +
  digest 重验 + schema/run-id 匹配。

## 21. Parity 与复用（事实）

- 同一 synthetic item：local vs remote 的 structure 内容、energy 值/单位、
  check 结论、artifact 语义 role/checksum 一致；允许差 timestamps/
  locator/env-digest/transport diagnostics。
- Checkpoint 双向：local→remote 与 remote→local 使用同一 ArtifactRef/
  subject/checksum/binding 语义（E2E 钉死）。
- Remote resume：95/5-lite（16/2/2 → 16 reuse + 4 native，submit 边界计数，
  0 duplicate）；endpoint/width 变更全 reuse；binary 变更
  `INVALIDATE_ENVIRONMENT`。
- V4-3 语义延续：CANCELLED 永不 auto-retry（transport 不绕过）；
  unconfirmed cancel 永不 rescue；run generation 仍 deferred。

## 22. V4-4 非目标与遗留风险

- 未做：IRC/path_endpoints/QST2/QST3/NEB/GOAT/ensemble/PES/JobDesk/
  migration/streaming DAG/分布式调度/cloud backend/cancelled 显式重跑。
- 遗留：① packaging-crash-after-native-completion 的 resume 可能重跑一次
  （原子写下仅磁盘故障可达，已文档化）；② 跨 definition 代重跑仍
  fail-closed（run generation deferred）；③ Windows 分支未覆盖；
  ④ pydantic `schema` 字段名遮蔽警告（benign，已知）。

V4-5 readiness：YES — 无需改动 StructureRecord / ArtifactRef / Binding /
WorkItem / WorkItemExecutor / ProgramAdapter / WorkItemStore / remote
handoff-result 契约，可直接实现 IRC/path_endpoints、multi-output、
QST2/QST3、NEB、GOAT、ensemble、named structures（artifact 端口与
`named_structures` adapter 契约已就位；多输出结构时的 restart subject
规则需随 multi-output 明确，当前单输出路径已封闭）。

---

# V4-5：Multi-Output + Reaction Path + Named Structures + Ensemble（已完成）

里程碑状态：**V4-5 已完成**。一个 WorkItem 可输出 0/1/N 个 StructureRecord；
IRC/QST/NEB/GOAT 是 adapter + profile + native-definition 的科学能力，
不是核心 runtime dispatch 类型（无 IRC/QST/NEB/GOAT executor，无 task 枚举）。

```
20 TS → 20 IRC WorkItems → 20 WorkItemResults → 40 endpoints (1 StepResult)
  → 40 Opt WorkItems → 40 optimized → 40 SP WorkItems → 40 results
```

Topology 只表示科学步骤；batch/fan-out/multi-output 由 WorkItem /
StructureSet 表达（1/20/100 输入下 ExecutionPlan step 数不变，已钉死）。

## 23. Multi-output 模型（事实）

- **Entity id**：`<logical_key>:structure:<role>:<ordinal>`
  （`output_identity.py` 冻结）。单 slot role ordinal 恒 0；ensemble/image
  用 native member/image ordinal（never parser 顺序）。Retry/resume/
  local/remote 同一 slot 同一 id；geometry_digest 独立表示内容。
- **Roles**：`path_endpoint_forward` / `path_endpoint_reverse`（仅 native
  path direction，绝不声称 reactant/product）、`neb_image`、
  `neb_ts_candidate`（仅 native 明确报告优化 TS 时）、`conformer`。
- **Lineage**：endpoint 双端共享 `(TS.id, TS.root, TS.group or TS.id)`；
  多亲输出按 slot 语义顺序（reactant, product, guess）取 parent_ids，
  lineage_root 仅全体一致才 propagate 否则 None（never 任意挑），
  group_key 仅全体一致才 propagate。
- **Ordering**：item 内 forward→reverse→ts-candidate→images→conformers；
  跨 item 按 WorkItem 确定顺序。Correctness 永不依赖顺序（pairing 按
  identity/group/subject）。
- **Profiles**：`path_endpoints`（恰好 {forward, reverse} 否则
  `incomplete_path` ERROR；executor 转为 WorkItem scientific failure，
  native 已跑一次，不伪造第二端）、`ensemble`（member_index 定 id，
  重复 index/空集 fail-closed，永不 dedup 相同几何）。
- **Trajectory**：默认 ArtifactRef（role=`trajectory`）+ native_metadata
  facts + 可选 Result payload；不拆成 workflow Structures。

## 24. Multi-output restart subject（V4-4 遗留扩展点，已关闭）

- 已绑定某输出 subject 的 artifact 保持；input-bound checkpoint 在多输出
  时保持 input/TS 归属，**绝不复制到所有 endpoint**；单输出时沿用 V4-4
  重定向规则；其余透传。下游 ONE binding 缺失时 fail-closed（由 binding
  层报错，不猜测）。
- Executor staging 同步收紧：artifact subject 必须属于本 item 输入结构
  id 集（standard 仅 driving，named 允许多 slot）。

## 25. Named structures + atom mapping（事实）

- `named_structures` adapter：reactant/product（ONE + BY_GROUP_KEY）/
  guess（OPTIONAL）。逻辑 key = `step_id:group_key`（与 assembly
  BY_GROUP_KEY 规则一致）。QST2 要求恰好 1+1；QST3 再加恰好 1 guess；
  缺少 → `named_structure_missing`，重复 → `ambiguous`，group 不一致
  （含缺失）→ `named_group_mismatch`。全部 native launch 前 fail-closed。
- **Atom mapping**：`identity`（元素序列严格一致，不 reorder）/
  `explicit_permutation`（长度/bijective/范围/元素保持全验；内部
  deterministic reorder，原始 StructureRecord immutable）。自动猜映射
  禁止。Mapping 写入 step native `atom_mapping` 参数 → 自动进入
  step/work-item semantic digest（不同 mapping 不同 digest，等价拼写
  同 digest，已钉死）；geometry_digest 不受 execution-local reorder 影响。
- **Executor**：named adapter 时以 reactant 为 reference 解 charge/mult
 （单一 precedence 权威不变），slot 一致性值必须与 effective 值相等；
  mapping 在 launch 前验证（失败 0 native 调用）。标准路径零行为变化。
- QST2/QST3 渲染：Gaussian 多 molecule-spec section（title + charge/mult
  + 坐标，`%.8f`），slot 间 charge/mult 必须一致；route QST2/QST3 与
  guess 有无必须一致，否则 `native_input_error`。
- NEB 输入：ORCA `%neb`（allowlist：`n_images>=3` 必需，
  `neb_ts` bool，`NEB_End_XYZFile` 仅由 product XYZ 文件名决定）；
  未知键 fail-closed。Product 端点 XYZ 作为第二输入文件由 adapter 渲染。

## 26. Program capabilities（事实）

- **审计结论**：repo 内零 legacy IRC/QST/NEB/GOAT native 语法（仅
  registry 端口描述），故按公开程序语义实现 + fail-closed 边界，
  解析器定义严格方言（crafted fixtures 钉死），未知语法拒绝而非猜测。
- **Gaussian**：IRC route 校验（RCFC/both、Forward、Reverse；未知 bare
  token 拒绝）+ 双向 endpoint 解析（banner 定方向，缺失/重复 banner
  拒绝，乱序不变）；QST2/QST3 渲染 + 标准 TS 解析（NImag==1 仍由显式
  check 判定，renderer 内无检查）。
- **ORCA**：IRC（`%geom` 修饰块 allowlist + 双向解析）；NEB（path
  images 为 `neb_image` members + `neb_ts_candidate` 仅 banner 存在时，
  无 banner 的 path 极大值永不冒充，`neb_ts` 请求而无 banner → parse
  error）；GOAT（`%goat` allowlist：MaxIter/MaxConformers/EnergyWindow/
  Seed；conformer 按 banner number 定 id，能量缺失为 None，重复几何保留）。
- **分层**：program adapter 管语法/parser；execution adapter 管输入形状；
  profile 管输出语义；checks 管 acceptance。Parser 越层检查（如 Gaussian
  内写 NImag 判定）禁止。

## 27. Remote（事实，无 handoff v3）

- V4-4 Handoff V2 足够：仅做向后兼容扩展 —
  `StructureBundleEntry.port`（默认 `structure`，旧 envelope 仍合法；
  嵌套 FrozenDict 深度 thaw + `_jsonable` Mapping 分支，保证嵌套
  native 参数可序列化）。
- Worker 按 `port` 重建 named slots（standard 单 driving 行为不变）；
  执行端 profile 解析自动继承（同一 PROFILES 注册表）。
- Parity：IRC / named-QST / ensemble 三组 local-vs-remote E2E，
  ids/digests/roles/parents/lineage/group/results/units/artifact
  role+checksum 一致；允许差 locator/host/env-digest/timestamps/
  transport diagnostics。注意：worker 按 program 经 PATH 解析可执行
  文件（handoff 永不带 producer 绝对路径），parity 测试据此注入 fake。
- Resume：IRC complete → 0 native；GOAT complete → 0 native；
  19/20 → 恰好 1 native；remote 重启 → full reuse；mapping 变更 →
  digest 移动 + `invalidate_input`（0 native）；endpoint ids 跨 resume
  bit-identical。

## 28. TSPES mini gate（事实）

- 20 TS（group rxn-00..19）→ 20 IRC items → 40 endpoints →
  40 opt items → 40 optimized → 40 SP items → 40 results，全经 production
  adapter/profile/executor（IRC 解析 shim 已被真实布线取代，F 的 shim
  测试继续通过）。
- 按 `(group_key, role)` dict 重组回答每 TS 双向结果，无 list 索引。
- 19/20：require_all 下 FAILED，38 endpoints durable，retry 恰好 1
  native → 最终 40；allow_partial 发布 38-subset 且失败组明确缺席。
- Partial consumption 规则延续 V4-1（multi-output 不绕过）。

## 29. V4-5 非目标与遗留风险

- 未做：PES/Gibbs 最终 analysis、reactant/product 自动指派、Producer
  合约定稿、JobDesk 集成、V2/V3 退役、migration CLI、streaming DAG、
  分布式调度、cloud backend。
- 遗留：① Gaussian IRC 方言 markers 为 crafted（真实 Gaussian log 的
  endpoint 几何提取需对接真实输出时扩展方言，当前全部测试基于严格方言）；② ORCA `%geom` IRC 触发键仅验证 MaxIter
  子集（job-type keyword 走正常 keyword 路径）；③ `%goat` 键名拼写
  （如 NConformers）以 allowlist 为准，未知键 loud-fail；
  ④ Windows/psutil-absent 分支未覆盖；⑤ 跨 definition 代重跑仍 deferred。

V4-6 readiness：YES — 无需改动 StructureRecord / ArtifactRef / Binding /
WorkItem / WorkItemResult / StepResult / WorkItemExecutor / ProgramAdapter /
WorkItemStore / remote handoff-result / multi-output / named-input 契约，
可直接实现 Analysis executor、Gibbs/PES aggregation、Producer contract、
JobDesk 集成、legacy V2/V3 retirement。


---

# V4-6：Analysis + Producer Contract + JobDesk Integration（已完成）

里程碑状态：**V4-6 已完成**。V4-1~V4-5 核心契约冻结（本轮仅因明确
correctness/integration 需要做加法扩展，未重设计任何 frozen contract）。
ConfFlow 成为正式 production runtime，
JobDesk 成为由 V4 Producer Contract 驱动的 GUI。
之后不再有主体 architecture milestone，只剩 Final Closure。

```
XYZ / typed input
  → V4 importer → WorkflowDocument V4 → Compiler → ExecutionPlan
  → V4RunApplication → WorkItem assembly
  → Calculation | Analysis → StepResult → Persistence
  → RunResultManifest → JobDesk
```

## 30. Analysis（事实）

- **AnalysisExecutor**（`confflow/analysis/`）：正式 step executor，
  capability `analysis`。纯函数：typed ports + explicit definition →
  ResultSet/diagnostics；绝不调用 ProgramAdapter、不启动 subprocess、
  不读文件名（AST 门钉死）。
- **ReactionGroup**：typed 内部模型（group_key/ts/forward/reverse/source
  ids/assignment/diagnostics），非 domain 图节点。分组按 group_key +
  subject id + role，TS = 同时 parent 双 endpoint 的非端成员；重复
  TS/端点 → ambiguous fail-closed（never lowest-energy-wins）；
  缺失 → typed missing 诊断；result 按 subject 挂接，未知 subject →
  mismatch 诊断。
- **方向铁律**：forward/reverse 只是 path direction；无显式
  `endpoint_assignment` 时输出 forward/reverse endpoint，绝不输出
  reactant/product；显式指派冲突 → `analysis_assignment_conflict`。
- **能量选择**：只用权威 kinds（`energy`/`gibbs_energy`/
  `gibbs_correction`），无别名。复合公式唯一显式形式
  `G_high = E_high + (G_low − E_low)`，policy
  （direct/composite/fallback）进 digest + provenance；无 fallback
  声明时缺 high-level 不得退化到 low-level（fail-closed）。
- **单位安全**：所有四则运算前验证 QuantityKind.ENERGY + 兼容单位，
  内部 hartree，mismatch → typed error，绝无裸 float。
- **输出 kinds**：`barrier_forward_endpoint`/`barrier_reverse_endpoint`
  （`G_TS − G_endpoint`，未指派时绝不叫 reaction barrier）、
  `endpoint_energy_delta`/`endpoint_gibbs_delta`（显式 reference
  `reverse_minus_forward`/`min_zero`）、`reaction_profile`（结构化
  PES payload：nodes/energies/relative/barriers/source ids/
  assignment/model）。
- **失败/部分语义**：TS/端点/能量缺失、unit mismatch、group 歧义、
  subject 错配、指派冲突全部 typed fail-closed；partial 策略显式
  （require_complete 默认整步失败零结果 / accept_subset 缺组省略），
  永不用别组数据填充。
- **Digest/resume**：policy/指派/公式进 analysis digest（变更则
  invalidate）；上游不变则 reuse。输入乱序 → 同一语义 digest（已钉死）。
- **Analysis 端口**：registry analysis 合约新增 `structures` /
  `ts_structures` / `ts_results`（MANY/SINGLE）——端点与 TS 记录、
  端点与 TS 结果分属不同生产步骤，单端口无法诚实表达（V4-1 单源
  规则不妥协）。TSPES recipe 同步绑定四端口。

## 31. Producer Contract V4（事实）

- Schema：`confflow.configuration-contract.v4`（v1/v2 被 JobDesk 解析
  中，v3 = V3 workflow，v4 是自然下一版）；validation response 沿用
  `confflow.configuration-validation.v1`（V4 诊断字段天然兼容）。
- 内容全部由真实源生成（零复制）：V4 workflow schema bytes、
  editor manifest（pointer 逐个验证存在于 schema，否则 build 失败）、
  recipe catalog（11 个全部真实编译，TSPES = 8 步真链）、registry
  capabilities、ports/pairing/cardinality、resources、
  completion/scheduler、native escape-hatch、analysis capabilities、
  result schema、remote（handoff/result v2）。
- 每个 artifact 独立 digest + envelope `contract_digest`；JobDesk 重算
  验证，不匹配 reject。
- Legacy truth（result.xyz/failed.xyz/workflow_stats/output_path/
  min_xyz）零发布。
- CLI：`confflow v4 contract --json`（machine JSON）、
  `confflow v4 validate --workflow/--stdin --json`（结构化诊断）、
  `confflow v4 run ...`（见 §32）。
- Drift 门：schema 变而 manifest 不变 → fail；registry 变而 contract
  不变 → fail；recipe 不可编译 → fail。

## 32. Application Runtime（事实）

- **V4RunApplication**（`confflow/application/v4_run.py`）：compile →
  拓扑步序 → 单步 `BatchStepExecutor`（calculation/confgen）或
  `AnalysisExecutor`（analysis，跨 item 合并 ports 后单次执行）→
  StepResult → publication → `RunResultManifest`（analyses 条目从
  `reaction_profile` 结果提取）。Resume：published 整步直接 load，
  未完成计算步按 work item resume，已完成 analysis 复用，从未启动
  的步继续——永不全重跑。
- **XYZ importer**：`import_xyz`（多 block，`xyz:<index>` 稳定 id，
  路径仅 provenance）。
- **CLI 切面**：`confflow/v4cli.py`（`cli.py` 仅加 3 行 dispatch）。
  Legacy V2/V3 文档 → `legacy_workflow_not_executable` + migration
  提示，无自动迁移、无 fallback。
- **Legacy 退役**（E 审计结论执行）：仅删除被证无 production 引用的
  `calc/retry_runner.py` 及其形状锁定测试；其余（engine/TaskRunner/
  ResultsDB/state/stats 等）全部 MIGRATION-ONLY——仍被现行 CLI 入口
  可达，删除将破坏生产 CLI 与 JobDesk 兼容，留待 Final Closure 的
  migration CLI 之后。

## 33. JobDesk 集成（事实，V4-A 基线 `32f50a2`）

- V4-A 冻结架构（CalculationCard/CardSnapshot/CardLibrary/composer/
  WorkflowDraftStore/dual contexts/resources）零改动；V4-6 只做
  `ProducerAuthoringProvider` 真实实现 + contract/result adapters。
- JobDesk 解析 v4 envelope 并验证全部 5 个 digest；v1/v2 仅 legacy
  路径；正式 V4 run 必须 v4-capable contract（结构化拒绝，无静默
  回退）。Epoch-safe refresh 保留（兼容刷新不碰 draft bytes）。
- Recipe → 原生 `confflow.workflow.v4` document（new-run 路径无
  V2→V4 converter；legacy 加载 → migration-needed）；提交门：
  validated-bytes == submitted-bytes（digest 相等，否则硬错）。
- ResultManifest → 展示 view models（状态/诊断/每组 TS/双端能量与
  barrier/指派状态/可下载 artifacts）；JobDesk 零科学计算（AST 断言）。
- 跨仓库只走 wire（contract bytes/validation response/submission/
  manifest），JobDesk 永不 `import confflow`（AST 门）。

## 34. Cross-repo E2E（事实）

- 至少一个门使用真实 producer bytes（`generate_contract_bytes` →
  JobDesk 解析 → recipe 建 workflow → 同字节 ConfFlow 验证 →
  fake 执行 → 20→40 管线 → 真实 `assemble_reaction_result`×20 →
  manifest → JobDesk 解析/展示）。
- TSPES fake 全链：20 TS → 20 IRC items → 40 endpoints → opt/freq/
  SP → 20 ReactionGroups（每组 5 结果）→ manifest 自洽（引用可解、
  checksum 对字节、provenance 一致）。
- 失败矩阵 13 项全结构化失败、无静默回退。

## 35. V4-6 遗留与风险

- Recipe TSPES 的 analysis 在端点缺 freq 级 Gibbs 时 fail-closed
  （`analysis_energy_missing`）——接线诚实，数值通过需每节点 Gibbs
  源（recipe 内容精修，非新 milestone）。
- `test_library_modules_import_without_qt`（JobDesk）：V4-A 基线前置
  环境失败（子进程 import 未安装的 src 布局包），与 V4-6 无关。
- Windows/psutil-absent、跨 definition 代重跑、cancelled 显式重跑：
  与 V4-5 一致，deferred 到 Final Closure。
- V1 handoff：ARCHIVE-NOT-IMPORTED（fixtures 保留，无 live consumer，
  本轮未动——迁移 oracle 保留）。

FINAL CLOSURE READINESS：YES — 在不再改变核心架构的前提下，可直接
执行最终验收（JobDesk → 真实 producer contract → 真实 V4 workflow →
真实 Gaussian/ORCA → 中断/resume → local/remote → IRC fan-out →
端点 Opt/Freq/SP → Analysis/PES → manifest → JobDesk）。
