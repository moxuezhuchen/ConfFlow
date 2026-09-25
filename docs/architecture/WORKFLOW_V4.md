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

- 不执行 Gaussian/ORCA（无进程、无 native 输入渲染落地）。
- 不实现 WorkItem SQLite 持久化、remote worker v2、JobDesk 集成、V2/V3 migration 命令。
- 不支持自由 GUI、streaming DAG、distributed scheduler、插件生态、arbitrary native template 执行。
- 不把 V4 接入 V2/V3 runtime dispatch / CLI 执行路径；V3 对这些 schema 仍然 fail-closed。
- 原子序数到 QST/NEB 的 atom mapping（`permutation|mapped`）推迟到 V4-2；V4-1 要求配对结构 atom 顺序一致。

## 9. 测试与门禁

- `tests/v4/`：domain/structure/artifact/identity、binding/resource/scientific、schema/parser、failure policy/publication、digest 四轴、compiler（determinism/disabled/policy/registry）、assembly（§23 四场景）、architecture debt gate。
- Architecture gate：AST 静态 import/symbol 扫描 + 子进程 runtime 导入隔离 + `setuptools.find_packages` 打包检查 + schema 无 hidden cleanup 词汇。
- 本地命令：

```bash
.venv/bin/python -m pytest tests/v4 -q
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/mypy confflow
```

V4-2 readiness：新的 `ProgramAdapter` 可以只实现 execution 层契约（native rendering / output parsing / environment measurement），通过 `assemble_work_items` 消费确定性 WorkItem，无需经过 `input_xyz → CalcStepRunner → output_path`。
