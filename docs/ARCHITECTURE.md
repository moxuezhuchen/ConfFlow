# ConfFlow 项目架构

本文是当前代码结构的总览：有哪些包、各自负责什么、数据怎样从一份工作流文档流到结果、依赖规则由什么测试把关。
工作流 V4 的逐项语义（digest 四轴、端口契约、持久化、remote、多输出……）见
[`architecture/WORKFLOW_V4.md`](architecture/WORKFLOW_V4.md)；输入简化与 ConfGen paths 见
[`PRODUCER_INTENT.md`](PRODUCER_INTENT.md) 与 [`CONFGEN_PATHS.md`](CONFGEN_PATHS.md)。代码和测试是第一权威。

## 1. 它是什么

ConfFlow 是计算化学工作流的**生产者（producer）**：给定一份 V4 工作流文档（`schema: confflow.workflow.v4`）和若干 XYZ 输入，
它编译出确定性的执行计划，按 work item 运行构象生成、Gaussian / ORCA 量化计算、结构变换（精修/去重/过滤）和反应分析，
并发布带类型的结果（`StepResult`、整次运行的 `run_result.json` manifest）。

同时它向 GUI 客户端（JobDesk-v2）**发布契约**：配置契约、边界协议、authoring 接口和输入简化（intent）编译器都由 producer 生成，
客户端只读取、不重新实现这些语义。

唯一受支持的工作流格式是 V4。V1/V2/V3 文档在入口处以 `unsupported_workflow_version` / `legacy_workflow_not_executable` 失败关闭。

## 2. 包与职责

```
confflow/
  domain/        不可变语义核心：结构、绑定、work item、step result、完成策略、digest 规范化（无仓库内依赖）
  workflow/v4/   V4 编译器：严格解析 → 语义校验 → 带类型的绑定图 → 不可变 ExecutionPlan → 合成 WorkItem
  execution/     能力注册表与各类执行器：calculation / confgen / transform / analysis、
                 检查（checks）、恢复（recovery）、结果 profile、原生进程边界、批执行
  science/       纯科学计算：ConfGen v3 引擎（扭转/环/配位）、工作拓扑权威、图同构映射、帧比较、键感知
  programs/      量化程序适配器（Gaussian、ORCA）：输入渲染与输出解析
  persistence/   持久化：逐 work item 的 SQLite 存储、发布协议、复用判定、孤儿回收、run state
  application/   应用层：正式 V4 入口 `v4_entry`、整次运行 `v4_run`、执行服务与仓库（SQLite）
  remote/        远程执行边界：worker-handoff v2、安全 staging、传输、结果包
  analysis/      反应/PES 聚合：反应组发现、Gibbs 能、势垒
  producer/      对客户端发布的契约与工具：contract / boundary / authoring / intent、cards、presets、
                 editor manifest、recipes、字节级校验、run result 投影、种子与机器检查点辅助
  config/        契约 schema id 的唯一权威（无依赖）
  core/          基础设施：XYZ I/O、元素与数据表、键感知、路径策略、日志、控制台、异常
  shared/        跨层默认值与 ORCA block 格式化
  cli.py main.py v4cli.py                命令行入口
  control.py control_worker.py worker_*  控制协议 v1 适配器与外部 worker（排队的启动意图）
  contract.py artifact_json.py install_provenance.py   契约常量、原子 JSON、安装溯源
```

`science/` 与 `domain/` 不做 I/O；`execution/` 不解析 YAML；`workflow/v4` 不执行任何东西。执行层只通过
`execution.registry` 的描述符认识能力，不按 role 或 program 名做分支。

## 3. 数据流

```
 intent（可选）            工作流文档 YAML/JSON
 confflow.intent.v1  ──►  schema: confflow.workflow.v4
   producer.intent          │
   (cards/presets)          ▼
                      parse（重复键拒绝、extra=forbid）
                            ▼
                      semantic validation（registry 词汇表、端口、checks、seed、资源）
                            ▼
                      typed BindingGraph（bindings 是唯一的边来源）
                            ▼
                      ExecutionPlan（不可变、确定性、稳定 ID）
                            ▼
                      assemble_work_items（run 输入 + 已发布输出 → WorkItem）
                            ▼
   application.v4_run  ──► executors（calculation / confgen / transform / analysis）
                            ▼  每个 work item 的结果先落盘（persistence）
                      StepResult 发布（崩溃一致性协议）
                            ▼
                      run_result.json（producer.run_result 投影，客户端只读）
```

- **同一份科学语义在本地和远程相同**：同一个 `WorkItem` 可以直接执行，也可以经 `worker-handoff.v2` 边界交给远程 worker。
- **resume 靠持久化而不是重跑**：已发布的 step 从磁盘加载；未完成的 step 按 work item 续跑，已完成的 work item 复用。
- **没有隐式收尾**：清理、去重、精修都是显式的 `transform` / `analysis` 步骤；不存在 calculation 的隐藏尾巴。

## 4. 入口与对外接口

| 入口 | 作用 |
| --- | --- |
| `confflow v4 run` | 运行整份 V4 工作流（`--workflow`、`--inputs NAME=FILE`、`--run-root`、`--executable PROG=PATH`，`--json` 输出机器可读报告） |
| `confflow v4 validate` | 对精确的工作流字节做 producer 校验 |
| `confflow v4 contract --json` | 发布配置契约（`confflow.configuration-contract.v4`） |
| `confflow v4 boundary --json` | 发布边界协议文档（`confflow.boundary.v4`） |
| `confflow v4 authoring --json --stdin` | authoring 接口：`describe_step`、`binding_candidates`、`instantiate_card`、`validate_document`、`check_compatibility`、`compile_intent`、`preview_paths` |
| `confflow v4 canonical --json --stdin` | RFC 8785（JCS）规范化 |
| `confflow --capabilities --json` | 能力握手 JSON |
| `confflow-control-worker` | 控制协议 v1 的外部 worker |

`confflow <input.xyz> -c <工作流>` 这一调用形式保留，作为同一个 V4 应用的薄入口（供 JobDesk 与控制 worker 沿用既有调用方式）；
它只接受 V4 文档，V1/V2/V3 文档以 `legacy_workflow_not_executable` 失败关闭且没有任何副作用。

## 5. 契约与摘要

- **配置契约**由真实注册表生成（工作流 JSON schema、editor manifest、recipe 目录、能力、端口、资源、分析与结果 schema），
  全部用 digest 钉住；客户端据此编辑、校验、提交。
- **边界协议**（`confflow.boundary.v4`）声明规范化算法（JCS）、authoring 请求/响应 schema 与兼容性词汇；已发布的契约 digest
  由 `tests/` 与外部检查点（`$BASE`，本轮实际路径示例 `/tmp/l0-baseline-run-v2/baseline`）固定；
  仓内检查点记录位于 `docs/confgen-fix/checkpoints/`。
- **四个 digest 轴**（WorkflowDefinition / StepSemantic / WorkItem / ExecutionEnvironment）区分"科学内容"和"调度/展示内容"：
  改 label、`max_parallel_items`、executable 路径不会移动科学 digest；改 native、checks、seed、资源、科学默认值会。

## 6. 科学包要点

- **ConfGen v3**（`science/confgen/`）：条件式的 配位 → 环 → 扭转 树；每个目标以唯一终态结束；报告里区分
  "带标号的状态数"和"最终发布的叶子数"。种子是唯一的随机性权威，缺失即编译错误（全枚举时不需要种子）。
- **工作拓扑权威**（`science/topology.py`）：结构记录自带 `working_topology` / `topology_patch`；
  ConfGen、refine、计算输出 profile 都经 `resolve_working_adjacency` 取同一份"预期共价图"，
  不再各自做感知加修正。spec 级拓扑声明与记录级权威同时出现会失败关闭。
- **精修去重**（`execution/transform_executor.py` + `science/frame_compare.py`、`topology_mapping.py`）：
  只在合法的元素/边保持映射下比较 Kabsch RMSD，所以仅标号对称置换的构象会被合并；搜索有节点预算
  （默认 1000/每对），预算耗尽的一对保留两个并在 notes 中说明。可选的 `topology_bonds` 声明带类型的边
  （COVALENT / COORDINATION / FORMING / BREAKING），映射必须保持这些类型。

## 7. 依赖规则与把关

- `domain` 不 import 任何其他 `confflow.*`；`workflow/v4` 与 `execution` 只依赖 `domain`、`execution`、`workflow/v4`
  以及各自文档化的 `science` 入口。
- 已退役的模块必须从磁盘上消失：`tests/v4/test_architecture_boundaries.py` 的 `REMOVED_LEGACY_MODULES`
  与"模块必须不存在"测试保证它们不会被悄悄恢复；禁止导入前缀与静态扫描见 `scripts/v4_arch_scan.py`。
- worker 的导入闭包不得加载编译器或重依赖包（`test_worker_run_import_closure_is_compiler_free`）。

## 8. 已退役

旧的 calc 调度链与 `confts` / `confgen` / `confrefine` 独立 CLI、`blocks/`、V1/V2/V3 工作流执行与配置 wire、
`confflow export`（`results.db` 读取器）都已删除。迁移史与逐卡验收记录随 architecture-diet-1 归档：
见 `docs/ARCHITECTURE_DIET_1.md`、`docs/archive_manifests/architecture_diet_1.json` 与归档提交 `671c3fb14663e9a6f4ccf9880228c59d28fbb762`（可用仓库外 bundle 恢复，按 git 对象定位，不打 tag）。
