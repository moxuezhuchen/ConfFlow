# ConfFlow 项目架构

## 项目概述

ConfFlow 是一个自动化计算化学工作流引擎，用于分子构象搜索、量子化学计算、构象筛选和结果可视化。核心设计遵循模块化、可扩展原则，支持多种量子化学程序（Gaussian 16、ORCA）。

## 目录结构

```
confflow/
├── core/                      # 基础设施层（共享工具、I/O、日志）
│   ├── __init__.py
│   ├── utils.py              # 统一的工具函数、异常类、日志系统
│   ├── io.py                 # XYZ I/O 门面与读写入口
│   ├── xyz_metadata.py       # XYZ 注释元数据与 CID 处理
│   ├── gaussian_input.py     # Gaussian 输入与坐标解析
│   ├── data.py               # 共价半径、元素符号等化学数据
│   ├── models.py             # Pydantic 数据模型定义
│   ├── types.py              # 类型定义与常量
│   ├── constants.py          # 核心常量
│   ├── contracts.py          # 输入/输出契约验证
│   ├── console.py            # 控制台输出格式化
│   ├── exceptions.py         # 异常类定义
│   ├── keyword_rewrite.py    # TS→scan 关键字改写
│   ├── logging.py            # 日志配置
│   ├── parsers.py            # 通用解析工具
│   ├── pairs.py              # 原子对操作
│   └── validation.py         # 核心验证逻辑
│
├── config/                    # 配置层（配置加载、解析、验证）
│   ├── __init__.py
│   ├── loader.py             # YAML/INI 配置文件加载器
│   ├── schema.py             # 配置架构定义与验证
│   └── defaults.py           # 默认配置值
│
├── blocks/                    # 业务逻辑层（具体功能模块）
│   ├── confgen/              # 构象生成模块
│   │   ├── __init__.py
│   │   └── generator.py      # 构象生成核心（链旋转模式）
│   │
│   ├── refine/               # 构象筛选模块
│   │   ├── __init__.py
│   │   ├── processor.py      # RMSD 去重、能量筛选、虚频过滤
│   │   └── rmsd_engine.py    # RMSD/PMI 计算引擎（Numba JIT 加速、对称性感知 RMSD）
│   │
│   └── viz/                  # 可视化模块
│       ├── __init__.py
│       └── report.py         # 美化纯文本报告生成（Boltzmann 权重、工作流统计）
│
├── calc/                      # 量子化学计算子系统
│   ├── __init__.py           # 兼容层导出
│   ├── manager.py            # 任务管理器（ChemTaskManager）
│   ├── setup.py              # 计算模块初始化
│   ├── analysis.py           # 计算分析工具函数
│   ├── constants.py          # 程序常量（路径、参数等）
│   ├── geometry.py           # 几何解析（parse_last_geometry, check_termination）
│   ├── resources.py          # 资源监控（CPU、内存）
│   ├── rescue.py             # TS 失败救援逻辑
│   ├── scan_ops.py           # 扫描操作
│   │
│   ├── policies/             # 策略模式实现（按程序区分）
│   │   ├── __init__.py
│   │   ├── base.py           # 基类 CalculationPolicy
│   │   ├── gaussian.py       # Gaussian 16 专用实现
│   │   └── orca.py           # ORCA 专用实现
│   │
│   ├── components/           # 内部组件（低层操作）
│   │   ├── __init__.py
│   │   ├── input_helpers.py     # 共享的内存/关键字/约束助手
│   │   ├── parser.py            # 解析计算输出文件
│   │   ├── executor.py          # 执行计算程序
│   │   └── task_runner.py       # 单个任务运行器
│   │
│   └── db/                   # 结果数据库
│       ├── __init__.py
│       └── database.py       # SQLite 结果库管理
│
├── workflow/                  # 工作流编排层
│   ├── __init__.py           # 公共 API 导出
│   ├── engine.py             # 工作流执行引擎（核心调度逻辑，~360 行）
│   ├── step_handlers.py      # 步骤执行适配层（confgen/calc）
│   ├── presenter.py          # 步骤展示与报告输出
│   ├── runtime_context.py    # 运行时状态初始化与恢复
│   ├── helpers.py            # 辅助工具（pushd、构象计数、列表转换）
│   ├── validation.py         # 输入验证与标签标准化
│   ├── config_builder.py     # 任务配置字典构建（YAML→dict）
│   └── stats.py              # 检查点、统计追踪、构象溯源
│
├── cli.py                     # CLI 参数解析
├── main.py                    # 工作流主程序入口
├── confts.py                  # TS 专用执行器与 keyword 改写工具
└── __init__.py               # 包导出与依赖管理

docs/                          # 文档
├── ARCHITECTURE.md           # 本文档（项目架构说明）
├── USAGE.md                  # 使用说明（精简版）
├── COMMAND_REFERENCE.md      # 所有命令的参考手册
├── KEYWORD_REFERENCE.md      # YAML/INI 关键字参考
├── TESTING.md                # 测试说明
├── STYLE_CONTRACT.md         # 代码/输入/输出一致性标准
└── DEVELOPMENT.md            # 开发指南

tests/                         # 测试套件（41 个文件，660 个测试）
├── conftest.py               # 共享 fixtures
├── _helpers.py               # 共享 fake 对象与工具函数
├── test_core.py              # 配置、包导出、低能量溯源
├── test_io.py                # XYZ 读写、元数据解析
├── test_data.py              # 共价半径、元素符号
├── test_models.py            # Pydantic 数据模型
├── test_defaults.py          # 默认常量验证
├── test_loader.py            # 配置文件加载
├── test_schema.py            # Schema 校验
├── test_confgen.py           # 构象生成
├── test_refine.py            # 构象筛选
├── test_calc.py              # 计算任务基础
├── test_calc_full.py         # 计算完整集成
├── test_policies.py          # Gaussian/ORCA 策略
├── test_rescue.py            # TS 救援
├── test_engine.py            # 工作流引擎
├── test_cli.py               # CLI 入口
├── test_validation.py        # 输入验证
└── ...                       # 完整清单见 docs/TESTING.md

confflow.yaml                  # 工作流示例配置
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
  - 输入验证（XYZ、YAML）
  - 工具函数（内存解析、iprog/itask 解析、freeze 索引范围解析）

- **`io.py`**：
  - 统一的 XYZ 文件读写入口
  - 对外保持兼容 API，内部转发到更小的解析子模块

- **`xyz_metadata.py` / `gaussian_input.py`**：
  - 分离 XYZ 注释/CID 规则与 Gaussian 坐标解析
  - 降低 `io.py` 的职责耦合和维护成本

- **`types.py`**：
  - 枚举类型（`TaskType`, `ProgType` 等）
  - 常量定义

### 2. `config/` - 配置层

**职责**：处理工作流的所有配置（YAML 工作流配置和 INI 计算配置）。

- **`loader.py`**：加载并解析 YAML/INI 文件
- **`schema.py`**：配置架构定义、验证与合并
- **`defaults.py`**：全局默认值

### 3. `blocks/` - 业务逻辑层

**职责**：提供工作流的具体功能步骤。

- **`confgen/`**：
  - 使用 RDKit 生成分子构象
  - 支持链旋转模式（需显式指定旋转链）
  - MMFF94s 预优化

- **`refine/`**：
  - RMSD 去重（支持 Numba JIT 加速）
  - 对称性感知 RMSD（主轴对齐 + 同元素贪心匹配，解决大分子原子乱序/对称互换问题）
  - 能量辅助去重（ΔE ≤ tolerance 时放宽 RMSD 阈值）
  - 双重校验：快路径（Kabsch）+ 慢路径（对称性感知），兼顾速度与准确度
  - 能量窗口筛选
  - 虚频校验
  - 拓扑分类

- **`viz/`**：
  - 生成美化的纯文本总结报告（.txt）
  - Boltzmann 权重计算
  - 工作流统计信息
  - CID 后向追踪

### 4. `calc/` - 量子化学计算子系统

**职责**：管理所有量子化学计算。

**设计**：使用**策略模式** (Policy Pattern) 区分不同程序（Gaussian/ORCA）的实现细节。

- **`manager.py`** - `ChemTaskManager`：
  - 任务队列管理
  - 并行执行与资源调度
  - 结果数据库管理
  - 断点恢复

- **`policies/`** - 程序专用实现：
  - `base.py`：抽象基类 `CalculationPolicy`
  - `gaussian.py`：Gaussian 16 的输入/输出格式、参数处理
  - `orca.py`：ORCA 的输入/输出格式、参数处理

- **`components/`** - 内部组件：
-  - `input_helpers.py`：内存/关键字/约束/冻结的共享工具，供 policy 与 TaskRunner 复用
-  - `parser.py`：解析计算输出（兼容层；当前委托给对应 Policy）
-  - `executor.py`：执行计算程序、监控进程
-  - `task_runner.py`：单任务执行主入口（推荐：`TaskRunner().run(...)`，不再暴露旧兼容 helper）

- **`db/`** - 结果数据库：
  - `database.py`：SQLite 数据库管理
  - 存储：任务 ID、状态、能量、虚频、错误信息等

- **其他**：
  - `core.py`：任务类型和程序类型的解析函数
  - `analysis.py`：TS 键长分析、频率分析等
  - `constants.py`：程序路径、任务常量等
  - `resources.py`：CPU/内存监控
  - `rescue.py`：TS 失败后的 scan 救援逻辑

  ### TS 失败后的 scan 救援（calc/rescue.py）

  实现要点（只覆盖当前实现）：

  - **起点结构**：优先读取失败 TS 的输入文件 `<work_dir>/<job>.gjf|.com`；若 TS 失败后工作目录已被备份/清理，则回退到 `backup_dir/<job>.gjf|.com`。
  - **约束与扫描**：对 `ts_bond_atoms` 对应键长做多点扫描，每个点为一次 `opt`。
    - 约束方式复用 confflow 原生 `freeze`（Gaussian 坐标第二列 `-1`），不依赖 ModRedundant。
  - **输出组织**：scan 点输出集中写入 `<work_dir>/scan/`（平铺文件），避免产生大量子目录。
  - **TS 重跑**：选取能量局部极大值点作为初猜后，用原 TS 的 `keyword` 重新跑 TS（保证方法一致）。
  - **备份**：若配置了 `backup_dir`，TS 任务结束时会把 `scan/` 一并备份到 `backup_dir/<basename(work_dir)>_scan`。

  ### Gaussian checkpoint（`.chk`）作为跨步骤工件

  - ConfFlow 将每个构象映射为稳定的 `job_name`（优先使用 `CID`，例如 `CID=A000001 -> A000001`）。
  - 当启用 `gaussian_write_chk`（默认开启）时，Gaussian 输入会写出 `%Chk={job_name}.chk`，并随常规备份规则进入对应步骤的 `backups/`。
  - 当某一步声明 `chk_from_step` 时，会从**指定步骤**（不限定“上一步”）的 `backups/{job_name}.chk` 回填到当前 job 工作目录，并通过 `%OldChk=...` 注入。
### 5. `workflow/` - 工作流编排层

**职责**：协调各模块执行，管理工作流逻辑。当前版本已将原单体 `engine.py` 拆分为“编排 + 执行适配 + 展示 + 运行时上下文 + 统计”的多模块结构。

- **`engine.py`**：
  - 入口 `run_workflow()` 负责 prepare / execute / finalize 三段主流程
  - resume 时复用 `resolve_step_output()` 按 step type 校验标准工件，避免把 `search.xyz` 误当成 calc 完成输出
  - 明确 step type 合同（`confgen/gen/calc/task`）并做早期校验

- **`runtime_context.py`**：
  - 初始化 `root_dir/failed/.checkpoint/workflow_stats` 等运行时状态
  - 封装 resume 场景下的恢复信息（`resume_from_step/current_input`）

- **`step_handlers.py`**：
  - `run_confgen_step` / `run_calc_step` 的执行适配层
  - 对接 `confgen`、`ChemTaskManager` 与失败聚合逻辑

- **`presenter.py`**：
  - 统一 step header/footer 输出
  - 统一最终报告与最低能构象落盘逻辑

- **`helpers.py`**：
  - 工具函数：`pushd`、`as_list`、`resolve_step_output`
  - `resolve_step_output` 按 step type 区分 `search.xyz` 与 `output.xyz` / `result.xyz`
  - 构象计数：`count_conformers_any`、`count_conformers_in_xyz`

- **`validation.py`**：
  - `validate_inputs_compatible`：多输入兼容性校验
  - 支持 `force_consistency=true` 的“警告并继续”分支

- **`config_builder.py`**：
  - `build_task_config`：YAML 参数构建为 calc 可消费 dict
  - `create_runtask_config`：兼容 INI 写入接口（外部工具可用）

- **`stats.py`**：
  - `CheckpointManager`：断点序列化/反序列化
  - `WorkflowStatsTracker`：流程统计追踪
  - `TaskStatsCollector`：results.db 状态聚合（优先按每个 `job_name` 的最新记录统计）
  - `FailureTracker`：跨步骤失败构象汇总
  - `Tracer`：低能构象溯源

### 6. CLI 层

- **`cli.py`**：参数解析（`confflow` 命令）
- **`main.py`**：工作流主程序入口
- **`confts.py`**：TS 专用执行器与 keyword 改写工具

## 设计模式与架构原则

### 1. 策略模式 (Strategy Pattern)

在 `calc/policies/` 中实现，用于处理不同量子化学程序的差异：

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

- **分层**：core → config → blocks/calc → workflow
- **单一职责**：每个模块只处理一个功能域
- **依赖明确**：高层依赖低层，不存在循环依赖

### 3. 兼容性设计

- **`confflow/calc/__init__.py`** 提供导出兼容层，确保旧代码仍可用：
  ```python
  from .db.database import ResultsDB
  from .manager import ChemTaskManager
  # ...
  __all__ = [...]
  ```

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

### YAML 工作流配置 (`confflow.yaml`)

```yaml
global:
  gaussian_path: "/opt/g16/g16"
  orca_path: "/opt/orca/orca"
  cores_per_task: 4
  total_memory: "16GB"
  max_parallel_jobs: 2
  charge: 0
  multiplicity: 1
  freeze: [1, 5]  # 冻结原子坐标（也支持 "1,5" / "1-5" / "1,2,5-7"）

steps:
  - name: "confgen_step"
    type: "confgen"
    params:
      chains: ["1-2-3-4-5"]
      ...

  - name: "calc_step"
    type: "calc"
    params:
      iprog: "g16"
      itask: "opt"
      keyword: "B3LYP/6-31G* opt freq"
      ...
```

### 计算配置传递

v1.0.5 起，`engine.py` 通过 `build_task_config()` 将 YAML 步骤参数直接构建为 Python dict，传给 `ChemTaskManager(config_dict)`。**不再生成中间 INI 文件**。兼容性函数 `create_runtask_config()` 仍保留，供外部工具使用。

## 测试组织

测试按被测模块分层组织，完整清单见 `docs/TESTING.md`。

```
tests/
├── conftest.py               # 共享 fixtures（input_xyz, cd_tmp, sync_executor）
├── _helpers.py               # 共享 fake 对象（FakeResultsDB, FakeExecutor 等）
│
├── test_core.py              # 配置归一化、包导出、低能量溯源
├── test_io.py                # XYZ 文件读写、元数据解析
├── test_data.py              # 共价半径、元素符号、原子序数
├── test_models.py            # TaskContext Pydantic 模型
├── test_defaults.py          # 默认常量验证
├── test_loader.py            # 配置加载边界条件
├── test_keyword_rewrite.py   # TS→scan 关键字改写
│
├── test_confgen.py           # confgen 构象生成
├── test_confgen_validator.py  # 构象验证器
├── test_confts_keyword.py    # confts 关键字解析
├── test_confgen_refine_fallbacks.py  # numba 回退路径
│
├── test_refine.py            # refine 筛选与去重
├── test_calc.py              # calc 基础 + task_runner
├── test_calc_full.py         # calc 完整集成
├── test_policies.py          # Gaussian/ORCA Policy
├── test_rescue.py            # TS 救援逻辑
├── test_geometry.py          # 几何解析与终止检测
├── test_utils_manager.py     # manager 与工具函数
│
├── test_engine.py            # workflow engine
├── test_runtime_context.py   # 运行时上下文
├── test_presenter.py         # 步骤展示与报告
├── test_schema.py            # Schema 校验
├── test_validation.py        # 输入验证
│
├── test_cli.py               # CLI 参数解析
├── test_console.py           # 控制台输出
├── test_contracts.py         # 输入/输出契约
├── test_viz_report.py        # 可视化报告
└── test_input_snapshot.py    # 输入文件快照
```

当前测试套件共 41 个测试文件、660 个测试用例；完整清单见 `docs/TESTING.md`。除主测试文件外，还包含一组 `*_hotspots.py` 用例，专门覆盖回退逻辑、异常路径和历史回归点。

## 依赖关系图

```
confflow/__init__.py (包入口)
  ├── main.py (工作流主程序)
  │   └── workflow.engine.run_workflow()
  │       ├── config/loader.py (配置加载)
  │       ├── blocks/confgen (构象生成)
  │       ├── calc/manager.py (量子计算)
  │       │   ├── calc/policies/* (Gaussian/ORCA)
  │       │   ├── calc/db/database.py (结果库)
  │       │   └── calc/components/* (I/O 与执行)
  │       ├── blocks/refine (构象筛选)
  │       └── blocks/viz (可视化)
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
- 若工作目录缺少对应工件，会直接报错而不是沿用错误输入继续运行

### 2. 并行执行

- 使用 `ProcessPoolExecutor` 并行运行多个计算任务
- 资源限制：`max_parallel_jobs`, `cores_per_task`, `total_memory`
- 自动队列管理与负载均衡

### 3. TS 失败救援

- `itask=ts` 失败时自动改为 `itask=scan`（受 `ts_rescue_scan` 参数控制）
- 扫描键长空间以找到正确的 TS 结构
- 若 TS keyword 不含 `freq`，仅使用关键键长漂移作为几何判据
- 实现在 `calc/rescue.py`

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

> **v1.0.5 变更**：计算任务直接在 `step_xx/` 目录运行，不再创建 `step_xx/work/` 子目录。

## 失败聚合产物（工作目录）

- `_work/failed/failed.xyz`：合并后的失败构象（注释行包含 `Step=...`）
- `_work/failed/failed_summary.txt`：失败清单（结构名 + 错误原因 + 建议救援方案）
- `_work/failed/<config>.yaml`：运行时配置副本（便于在 failed 目录重跑）

## 扩展指南

### 添加新的计算程序

1. 在 `calc/policies/` 中创建新文件，例如 `mopac.py`
2. 实现 `CalculationPolicy` 基类
3. 在 `calc/core.py` 中注册新程序
4. 在 `calc/constants.py` 中添加程序常量

### 添加新的分析工具

1. 在 `blocks/` 中创建新目录
2. 实现核心处理函数
3. 提供 `main()` console script 入口
4. 在 `workflow/engine.py` 中集成

### 添加新的计算任务类型

1. 在 `calc/core.py` 中扩展 `TaskType` 枚举
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
