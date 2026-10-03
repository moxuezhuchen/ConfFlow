# ConfFlow 项目架构

## Workflow V4（greenfield）

V4 是独立的新 workflow engine，代码位于 `confflow/domain`、`confflow/execution`、
`confflow/workflow/v4`。它不依赖本文档描述的 V2/V3 workflow runtime，也不经过
旧的 calc 调度链。V4 架构事实见
`docs/architecture/WORKFLOW_V4.md`。

## 项目概述

ConfFlow 是一个自动化计算化学工作流引擎，用于分子构象搜索、量子化学计算、构象筛选和结果可视化。核心设计遵循模块化、可扩展原则，支持多种量子化学程序（Gaussian 16、ORCA）。

## 当前重构主线

当前主执行路径是破兼容后的 V4-only 结构：

- 配置/工作流格式：唯一受支持的是 V4（`confflow.workflow.v4` /
  `confflow.configuration-contract.v4`）；`confflow.config.contract_schemas`
  是 schema id 的唯一权威。
- 正式执行入口：`confflow.application.v4_entry.formal_v4_runner`
  （`confflow` CLI、application service 与 control worker 全部经此进入）。
- V4 编译/执行：`confflow.workflow.v4.compile_workflow` ->
  `confflow.application.v4_run.V4RunApplication` -> `WorkItem` -> executor ->
  `StepResult` 发布 -> run result manifest。
- 发布契约：`confflow v4 contract --json`
  （`confflow.producer.contract.generate_contract_bytes`）。

已退役：旧的 calc/blocks 工具链及其独立 CLI、V2/V3 workflow 执行运行时（PR-4）、无发布的 V3 public wire（PR-7）、
已发布的 V1/V2 配置 wire（PR-9）。旧的 `ChemTaskManager`、INI settings、
legacy flat calc config、MD5 `.config_hash` 兼容路径已从主执行路径和公共导出中移除。

## 目录结构

```
confflow/
├── contract.py                # JobDesk capability/artifact wire contract
├── core/                      # 基础设施层（共享工具、I/O、日志）
│   ├── __init__.py
│   ├── utils.py              # 统一的工具函数、异常类、日志系统
│   ├── io.py                 # XYZ I/O 门面与读写入口
│   ├── chem_validation.py    # 中立化学结构/柔性链校验服务
│   ├── path_policy.py        # 路径/可执行文件安全策略
│   ├── xyz_metadata.py       # XYZ 注释元数据与 CID 处理
│   ├── gaussian_input.py     # Gaussian 输入与坐标解析
│   ├── data.py               # 共价半径、元素符号等化学数据
│   ├── models.py             # canonical Pydantic 模型的 compatibility facade
│   ├── types.py              # 类型定义与常量
│   ├── constants.py          # 核心常量
│   ├── contracts.py          # 输入/输出契约验证
│   ├── console.py            # 控制台输出格式化
│   ├── exceptions.py         # 异常类定义
│   ├── keyword_rewrite.py    # TS→scan 关键字改写
│   ├── logging.py            # 日志配置
│   ├── parsers.py            # 通用解析工具
│   ├── pairs.py              # 原子对操作
│   ├── validation.py         # 核心验证逻辑
│   └── cli_base.py           # CLI 基础工具
│
├── config/                    # 配置层（配置加载、解析、验证）
│   ├── canonical/            # producer-owned parser/types/schema/serialization
│   └── models.py             # compatibility facade, no independent rules
│
├── shared/                    # 轻量共享层（稳定常量/格式化/结构校验）
│   ├── __init__.py
│   ├── defaults.py           # 与 config 解耦的默认常量
│   ├── orca_blocks.py        # ORCA blocks 格式化
│   └── config_validation.py  # YAML 结构校验
│
├── workflow/                  # 工作流编排层
│   ├── __init__.py           # 公共 API 导出
│   ├── dag/                   # 显式 inputs DAG 构建、校验与拓扑排序
│   ├── engine.py             # 工作流执行引擎（核心调度逻辑）
│   ├── state.py              # .workflow_state.json 原子状态模型与存储
│   ├── step_handlers.py      # 步骤执行适配层（薄壳，默认调用 calc 官方入口）
│   ├── presenter.py          # 步骤展示与报告输出
│   ├── runtime_context.py    # 运行时状态初始化与恢复
│   ├── helpers.py            # 辅助工具（pushd、构象计数、列表转换）
│   ├── validation.py         # 输入验证与标签标准化
│   ├── stats.py              # 检查点、统计追踪、构象溯源
│   ├── rerun_failed.py       # 失败重跑
│   ├── supervisor.py         # 子进程监督与停止处理
│   └── step_naming.py        # 步骤命名
│
├── cli.py                     # CLI 参数解析
├── main.py                    # 工作流主程序入口
└── __init__.py               # 轻量包入口

docs/                          # 文档
├── ARCHITECTURE.md           # 本文档（项目架构说明）
├── USAGE.md                  # 使用说明（精简版）
├── COMMAND_REFERENCE.md      # 所有命令的参考手册
├── TESTING.md                # 测试说明
├── STYLE_CONTRACT.md         # 代码/输入/输出一致性标准
└── DEVELOPMENT.md            # 开发指南

tests/                         # 测试套件（以 pytest --collect-only -q 和 CI 输出为准）
├── conftest.py               # 共享 fixtures
├── _helpers.py               # 共享 fake 对象与工具函数
├── test_core.py              # 包导出与核心公共入口
├── test_io.py                # XYZ 读写、元数据解析
├── test_data.py              # 共价半径、元素符号
├── test_retired_wire_versions.py  # V1/V2/V3 配置 wire 失败关闭闸门
├── test_cli.py               # CLI 入口
└── ...                       # 完整清单见 docs/TESTING.md

confflow.example.yaml          # 工作流示例配置
pyproject.toml                 # 项目配置（PEP 621 + 构建系统）
README.md                      # 项目简介
LICENSE                        # MIT 许可证
```

## 核心模块说明

### 1. `core/` - 基础设施层

**职责**：提供所有模块都需要的共享功能。

- **`utils.py`**：
  - 基础异常与输入校验（`ConfFlowError`, `InputFileError`, `XYZFormatError` 等）
  - 日志系统（`ConfFlowLogger`, `get_logger()`）
  - 输入验证（XYZ）
  - 工具函数（内存解析、iprog/itask 解析、freeze 索引范围解析）

- **`io.py`**：
  - 统一的 XYZ 文件读写入口
  - 提供 `iter_xyz_frames()` 流式读取接口，避免大轨迹全量装载

- **`xyz_metadata.py` / `gaussian_input.py`**：
  - 分离 XYZ 注释/CID 规则与 Gaussian 坐标解析
  - 降低 `io.py` 的职责耦合和维护成本

- **`types.py`**：
  - 枚举类型（`TaskType`, `ProgType` 等）
  - 常量定义

### 2. `config/` 与 `shared/` - 配置层与轻量共享层

**职责**：处理工作流配置，并把稳定常量/格式化/结构校验从 `config` 中抽离出去，避免 `core -> config` 反向依赖。

- **`loader.py`**：加载并解析 YAML/INI 文件
- **`schema.py`**：配置架构定义、验证与合并
- **`defaults.py`**：兼容层默认值导出
- **`shared/defaults.py`**：真实默认常量来源
- **`shared/config_validation.py`**：YAML 结构校验
- **`shared/orca_blocks.py`**：ORCA blocks 渲染

### 5. `workflow/` - 工作流编排层

**职责**：协调各模块执行，管理工作流逻辑。当前版本已将原单体 `engine.py` 拆分为“编排 + 执行适配 + 展示 + 运行时上下文 + 统计”的多模块结构。

- **`engine.py`**：
  - 入口 `run_workflow()` 负责 prepare / execute / finalize 三段主流程
  - resume 时复用 `resolve_step_output()` 按 step type 校验标准工件，避免把 `search.xyz` 误当成 calc 完成输出
  - calc step 的配置/input digest、stale 判断和复用语义由 `calc.artifacts` 的 `manifest.json` 合同负责
  - 只要任一步声明 `inputs` 就进入显式 DAG 模式；无 `inputs` 的旧配置继续按声明顺序线性执行
  - 显式 DAG 在初始化运行目录或调用 step handler 前完成未知依赖、环和终端数校验
  - 当前显式 DAG 必须恰好有一个终端 step；不支持多输出聚合

- **`dag/explicit.py`**：
  - 规范化 step 名称与 `inputs`，构建 predecessor map
  - 使用确定性拓扑 wave 校验未知依赖与环

- **`state.py`**：
  - 通过临时文件 + 原子替换维护 `.workflow_state.json`

- **`runtime_context.py`**：
  - 初始化 `root_dir/failed/.checkpoint/workflow_stats` 等运行时状态
  - 封装 resume 场景下的恢复信息（`resume_from_step/current_input`）

- **`step_handlers.py`**：
  - `run_confgen_step` / `run_calc_step` 的执行适配层
  - 不再作为 calc artifact/stale/resume 的主语义中心

- **`presenter.py`**：
  - 统一 step header/footer 输出
  - 统一最终报告与最低能构象落盘逻辑

- **`helpers.py`**：
  - 工具函数：`pushd`、`as_list`、`resolve_step_output`
  - `resolve_step_output` 按 step type 区分 `search.xyz` 与 `output.xyz` / `result.xyz`
  - 构象计数：`count_conformers_any`、`count_conformers_in_xyz`

- **`validation.py`**：
  - `validate_inputs_compatible`：多输入兼容性校验
  - `chain` / `chains` 两种配置键都会触发柔性链映射模式
  - 支持 `force_consistency=true` 的“警告并继续”分支

- **`stats.py`**：
  - `CheckpointManager`：断点序列化/反序列化
  - `WorkflowStatsTracker`：流程统计追踪
  - `TaskStatsCollector`：results.db 状态聚合（优先按每个 `job_name` 的最新记录统计）
  - `FailureTracker`：跨步骤失败构象汇总
  - `Tracer`：低能构象溯源

### 6. CLI 层

- **`cli.py`**：参数解析（`confflow` 命令）
- **`main.py`**：工作流主程序入口
- **`contract.py`**：版本、schema、能力、产物名以及构建身份的 wire contract；`cli.py` 负责发出 capability JSON

## 设计模式与架构原则

### 1. 策略模式 (Strategy Pattern)


```python
# 基类定义
class CalculationPolicy:
    def generate_input(self, ...): ...
    def parse_output(self, ...): ...

# 具体实现
class GaussianPolicy(CalculationPolicy):
    def generate_input(self, ...):
        # Gaussian 特定的输入格式

class OrcaPolicy(CalculationPolicy):
    def parse_output(self, ...):
        # ORCA 特定的输出解析
```

**好处**：
- 代码复用：共同的执行流程放在 `Manager`
- 易维护：新增程序时只需新增 Policy 类
- 易测试：可独立测试每个 Policy

### 2. 模块化架构

- **单一职责**：每个模块只处理一个功能域
- **依赖明确**：`shared` 承担轻量公共边界，`core` 不再反向依赖 `config.schema`

### 3. 公共入口设计

- 顶层 `confflow.__init__` 暴露轻量官方入口（如 `run_workflow`）
- 旧 INI / legacy flat config / `.config_hash` / manager facade 已从主路径移除
- 仓库内部代码应直接从真实子模块导入，避免重新扩大包初始化耦合

## 工作流执行流程

```
confflow <input.xyz> -c <config.yaml>
        ↓
    cli.py (参数解析)
        ↓
    main.py → workflow.engine.run_workflow()
        ↓
    +------- confgen -------+
    | 链旋转 → 生成构象 |
    +-----────────────────+
            ↓
    +------- calc (step 1-N) -------+
    | 并行执行量子计算               |
    | - 调用 Policy 生成输入        |
    | - 执行 Gaussian/ORCA         |
    | - 调用 Policy 解析输出        |
    | - 保存结果到 DB              |
    +------────────────────────────+
            ↓
    +------- refine -------+
    | RMSD 去重            |
    | 能量筛选             |
    | 虚频过滤             |
    +-----────────────────+
            ↓
    +------- viz -------+
    | 生成文本报告     |
    +──────────────────+
```

## 配置系统

### V4 工作流配置 (`confflow.example.yaml`)

当前唯一受支持的 workflow/config 格式是 V4（`schema: confflow.workflow.v4`）。
随包分发的示例文件是一个可编译的 V4 文档：

```yaml
schema: confflow.workflow.v4

inputs:
  structures: {kind: structure, cardinality: many, grouping: each_entity}

global:
  scientific_defaults: {charge: 0, multiplicity: 1}
  resources: {cores_per_item: 8, memory_per_item: 32GiB}
  scheduler: {max_parallel_items: 4}

steps:
  - id: confgen
    executor: confgen
    bindings: {structure: {source: {run: structures}}}
    confgen: {native: {chains: ["1-2-3-4"], angle_step: 120}, seed: 20260928}

  - id: opt
    executor: calculation
    bindings: {structure: {source: {step: confgen, port: structures}}}
    calculation:
      program: orca
      role: opt
      native: {keyword: "B3LYP D3BJ def2-SVP Opt"}
      checks: ["normal_termination", "geometry_required"]
      overrides: {freeze: [1, 2]}
```

### 配置 wire 的当前状态

唯一受支持的配置 wire 是 V4（`confflow.configuration-contract.v4` /
`confflow.workflow.v4`）。V4 生产者契约由
`confflow.producer.contract.generate_contract_bytes` 生成，并由
`confflow v4 contract --json` 发布；`confflow.config.contract_schemas` 是
schema id 的唯一权威。

已发布的 V1/V2 配置 wire（`configuration-contract.v1/.v2`、
`confflow.workflow.v2` schema、公开 parser/validator、V2 editor manifest 与
recipe catalog、V2 -> canonical migration、`--dry-run`/`--config-show`
planner、`confflow config validate`）由 Architecture Diet PR-9 退役；V1/V2/V3
文档在入口处以 `unsupported_workflow_version` / `legacy_workflow_not_executable`
失败关闭，不做 fallback、不自动 upgrade、不执行。

## 测试组织

测试按被测模块分层组织，完整清单见 `docs/TESTING.md`。

```
tests/
├── conftest.py               # 共享 fixtures（input_xyz, cd_tmp, sync_executor）
├── _helpers.py               # 共享 fake 对象（FakeResultsDB, FakeExecutor 等）
│
├── test_core.py              # 包导出与核心公共入口
├── test_io.py                # XYZ 文件读写、元数据解析
├── test_data.py              # 共价半径、元素符号、原子序数
├── test_retired_wire_versions.py  # V1/V2/V3 配置 wire 失败关闭闸门
│
│
│
│
├── test_cli.py               # CLI 参数解析
├── test_console.py           # 控制台输出
├── test_contracts.py         # 输入/输出契约
```

当前测试套件的文件数与用例数以 `pytest --collect-only -q` 和 CI 输出为准；完整清单见 `docs/TESTING.md`。除主测试文件外，还包含一组 `*_hotspots.py` 用例，专门覆盖回退逻辑、异常路径和历史回归点。

## 依赖关系图

```
confflow/__init__.py (包入口)
  ├── main.py (工作流主程序)
  │   └── workflow.engine.run_workflow()
  │
  └── core/
      ├── utils.py (日志、异常、验证)
      ├── io.py (XYZ 文件 I/O 门面)
      ├── xyz_metadata.py (XYZ 注释/CID)
      ├── gaussian_input.py (Gaussian/坐标解析)
      └── types.py (类型定义)
```

## 关键特性

### 1. 断点续传

- 工作目录中保存 `.checkpoint` 文件
- 记录已完成的步骤和构象处理状态
- 使用 `--resume` 标志从中断点恢复
- resume 会按步骤类型验证标准产物：`confgen` 只接受 `search.xyz`，`calc` 只接受 `output.xyz` / `result.xyz`
- calc step 的 manifest 记录配置/input digest；digest 不匹配时由 `calc.artifacts` 清理 stale 工件并重跑
- 若工作目录缺少对应工件，会直接报错而不是沿用错误输入继续运行

### 2. 并行执行

- 使用 `ProcessPoolExecutor` 并行运行多个计算任务
- 资源限制：`max_parallel_jobs`, `cores_per_task`, `total_memory`
- 自动队列管理与负载均衡
- 上述并行发生在单个 calc step 内部的构象任务层
- DAG step 当前按确定性拓扑顺序串行执行；本里程碑不承诺 wave 级并发

### 3. TS 失败救援

- `itask=ts` 失败时自动改为 `itask=scan`（受 `ts_rescue_scan` 参数控制）
- 扫描键长空间以找到正确的 TS 结构
- 若 TS keyword 不含 `freq`，仅使用关键键长漂移作为几何判据

### 4. 多程序支持

- Gaussian 16（使用 `.gjf` 输入格式）
- ORCA（使用 `.inp` 输入格式）
- 易于扩展新程序（创建新 Policy 类）

### 5. 资源监控

- 实时监控 CPU 和内存使用
- 支持动态资源限制
- 异常监控与进程清理

## 标准产物（calc/task step）

- `results.db`：SQLite 结果库（持久化每个 `job_name` 的运行结果；读取/统计时默认使用最新记录视图）
- `result.xyz` / `output.xyz`：成功构象输出（是否 cleaned 取决于 auto_clean/refine）
- `failed.xyz`：失败构象集合（输入结构坐标，注释行包含失败原因），便于重算与排障
- `manifest.json`：calc step 状态、typed config digest、input digest、输出路径和任务统计

> **v1.0.5 变更**：计算任务直接在 `step_xx/` 目录运行，不再创建 `step_xx/work/` 子目录。

## 失败聚合产物（工作目录）

- `_work/failed/failed.xyz`：合并后的失败构象（注释行包含 `Step=...`）
- `_work/failed/failed_summary.txt`：失败清单（结构名 + 错误原因 + 建议救援方案）
- `_work/failed/<config>.yaml`：运行时配置副本（便于在 failed 目录重跑）

## 扩展指南

### 添加新的计算程序

2. 实现 `CalculationPolicy` 基类

### 添加新的分析工具

2. 实现核心处理函数
3. 提供 `main()` console script 入口
4. 在 `workflow/engine.py` 中集成

### 添加新的计算任务类型

1. 在 `core/types.py` 中扩展 `TaskType` 枚举
2. 在各 Policy 中实现新任务的输入/输出处理
3. 添加相应的测试

## 常见问题

**Q: 为什么要用策略模式？**

A: 不同程序（Gaussian/ORCA）的输入输出格式完全不同，策略模式可以将这些差异隐藏起来，让上层代码不需要关心具体使用哪个程序。

**Q: 如何添加对新程序的支持？**

A: 创建新的 Policy 类，实现 `generate_input()` 和 `parse_output()` 方法，其他代码无需修改。

**Q: 断点续传如何工作？**

A: 每次运行记录已完成的任务到 `results.db`，再次运行时自动跳过已完成的任务。如果 DB 丢失但备份存在，会从备份恢复。

**Q: 可以自定义资源限制吗？**

A: 可以，在步骤级别 (`params`) 中覆盖全局配置：

```yaml
steps:
  - name: heavy_calc
    type: calc
    params:
      cores_per_task: 16
      total_memory: "64GB"
```
