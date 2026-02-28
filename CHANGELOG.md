# ConfFlow 更新日志

## v1.0.10 (2026-02-28)

### 🔧 工程质量全面提升

#### 测试增强（+49 tests → 529 passed）
- **step_handlers 测试**：新增 `test_step_handlers.py`（12 个测试），覆盖 `run_confgen_step` / `run_calc_step` 的正常、跳过、失败、默认参数等路径，消除 0% 覆盖盲区
- **Pydantic 配置模型测试**：新增 `TestGlobalConfigModel`（12 tests）+ `TestCalcConfigModel`（8 tests），验证字段默认值、强制转换、类型校验、序列化
- **RMSD/collision 边界测试**：新增 14 个测试覆盖 `greedy_permutation_rmsd`、`get_principal_axes`、`check_one_against_many`、`get_topology_hash_worker`、`collision` 边界路径

#### 异常处理精确化
- **scan_ops.py**：4 处 `except Exception` → 具体类型 `(OSError, ValueError, IndexError, KeyError)` + `logger.debug()` 替代静默 `return`
- **executor.py**：3 处 `except Exception` → `(ValueError, TypeError)` / `(OSError, shutil.SameFileError)`
- **generator.py**：MMFF 优化 `except Exception` → `(RuntimeError, ValueError)`

#### 类型安全
- **mypy 错误清零**：修复 `engine.py` 中 `str|list[str]` 类型处理（1→0 errors）
- **type: ignore 精确化**：27 处裸 `# type: ignore` → 全部使用具体错误码 `[assignment]`/`[no-redef]`/`[import-untyped]`/`[return-value]`，或通过 `isinstance` 运行时检查消除
- **新增 Pydantic 配置模型**：`GlobalConfigModel`（15 字段 + 6 个 validator）+ `CalcConfigModel`（3 字段 + 3 个 validator）

#### 代码重构
- **`_auto_clean` 重构**（manager.py）：`str.split("-t")` 字符串分割 → `shlex.split()` + token 解析，支持 `-t 0.25` 和 `-t=0.25` 两种格式
- **`StepContext` 数据类**（step_handlers.py）：封装 `run_calc_step` 的 8 个参数为结构化类型

#### Lint 清零
- **ruff 0 warnings**：修复 F401（未使用导入）、I001（导入排序）、UP037（引号注解）、D205（文档字符串格式）
- **D100/D104 规则启用**：补充 12 个模块/包级 docstring，从 ignore 列表中移除 D100/D104

#### 覆盖率提升
- 分支覆盖率：83.61% → **84.92%**
- `step_handlers.py` 从 0% 提升到有效覆盖

### 🧪 验证结果

| 指标 | v1.0.9 | v1.0.10 |
|------|--------|---------|
| 测试数 | 480 | **529** |
| 覆盖率 | 83.61% | **84.92%** |
| mypy 错误 | 1 | **0** |
| ruff 警告 | 6 | **0** |
| 裸 type: ignore | 14 | **0** |

---

## v1.0.9 (2026-02-28)

### 🎯 构象去重精度提升

- **对称性感知 RMSD**：新增 `greedy_permutation_rmsd()` — 基于主惯性轴对齐 + 同元素贪心最近匹配的 RMSD 计算，解决原子乱序/对称互换导致的 RMSD 虚高问题
- **主惯性轴提取**：新增 `get_principal_axes()` — 返回惯性张量的特征值（PMI）与特征向量（主轴基），用于对齐参考坐标系
- **双重 RMSD 校验**：`check_one_against_many()` 采用快慢两级判定：
  - 快路径：现有 Kabsch `fast_rmsd`（保持原有速度）
  - 慢路径：PMI 通过但 `fast_rmsd` 超阈值时，自动调用对称性感知 RMSD 进行复核
- **能量辅助阈值**：新增 `energy_tolerance` 参数（默认 0.05 kcal/mol）；当两个构象的能量差 ΔE ≤ tolerance 时，RMSD 判定阈值自动放宽 1.5 倍
- **元素信息传递**：`process_topology_group()` 在坐标/PMI 之外额外打包元素 ID 和能量，供贪心匹配使用
- **CLI 新参数**：`confrefine --energy-tolerance <kcal/mol>`
- **性能影响**：典型场景 <20% 下降（慢路径仅在 PMI 通过 + fast_rmsd 未过阈值时触发）

### 🔤 CID 命名统一

- **统一字母前缀格式**：所有 Conformer ID 统一为 `A000001` 格式（字母前缀 + 6 位数字），覆盖单帧、多帧、多文件三种输入场景
- **公共工具函数**：提取 `index_to_letter_prefix()` 到 `confflow/core/utils.py`，按 A→Z→AA→AZ… 生成字母前缀
- **消除旧格式**：移除 `c0001`（manager 无 CID 回退）、`s01_000001`（engine 步骤前缀）、`cf_000001`（confgen 回退）三种不一致格式

### ⚙️ 配置增强

- **`energy_tolerance` YAML 可配**：新增 YAML 参数 `energy_tolerance`，可在全局或步骤级覆盖；`config_builder` 自动转为 `--energy-tolerance` CLI 标志传递给 refine 流程
- **YAML 完整参数文档**：在参考 YAML 中以注释形式列出所有支持的参数（全局、confgen、calc、TS、Gaussian、ORCA 等），方便用户查阅

### 🧪 验证结果

- 全量测试：**480 passed**，零失败
- 实测效果：11 帧大分子构象（131 原子）去重 11→7，视觉重复的 Rank 3&4、6&7 均成功合并

---

## v1.0.8 (2026-02-27)

### 🏗️ 测试架构重构

- **拆分 `test_core.py`**：从 611 行的"杂物箱"拆分为 5 个聚焦测试文件
  - `test_io.py`：XYZ 文件读写与元数据解析
  - `test_data.py`：共价半径、元素符号、原子序数
  - `test_viz_report.py`：Boltzmann 权重、报告生成
  - `test_input_snapshot.py`：Gaussian/ORCA 输入快照
  - `test_confts_keyword.py`：TS 关键字解析与 confts CLI
- **合并 6 个碎片化 `*_paths.py`**：coverage push 阶段产生的路径补测文件合并回对应主测试文件，去重后删除
- **统一 Fake 对象**：`FakeResultsDB`、`FakeFuture`、`FakeExecutor` 集中到 `_helpers.py`，消除 3+ 处重复定义
- **清理 conftest.py**：移除 4 个未使用的 fixtures（`fake_runner`、`sample_config`、`write_text_file`、`assert_raises`）

### 🔧 测试质量改进

- **修复隔离问题**：`importlib.reload` 调用包裹 `try/finally`，确保模块状态恢复；`test_io.py` 改用 `tmp_path` 替代 `tempfile` + 手动清理
- **增强断言**：`test_run_generation_advanced` 和 `test_run_generation_multi_input` 补充返回值断言
- **新增测试标记**：`@pytest.mark.integration` 标记端到端测试

### 📦 新增模块测试

- `test_models.py`：TaskContext Pydantic 模型（序列化、extra fields、必填字段）
- `test_defaults.py`：默认常量完整性与类型验证
- `test_geometry.py`：`parse_last_geometry`（Gaussian/ORCA）、`check_termination`
- `test_keyword_rewrite.py`：`make_scan_keyword_from_ts_keyword` 直接测试
- `test_loader.py`：`load_workflow_config_file` 边界条件（空路径、非文件、无效 YAML、遗留键）

### ⚙️ 基础设施

- **pyproject.toml**：新增 `[tool.coverage.run]`（branch coverage）、`[tool.coverage.report]`（`fail_under = 70`）、`markers` 定义
- **目录清理**：移除 `verify_output.py`、`tests/coverage_push/`、`tests/artifacts/`、空工作目录（`input_work/`、`tests/pentane_work/`、`tests/test_work/`）
- **缓存清理**：清除所有 `__pycache__`、`.pytest_basetemp`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`

### 📖 文档全面更新

- **TESTING.md**：重写为结构化测试指南，包含 28 个文件清单、fixtures/helpers 表、编写约定
- **ARCHITECTURE.md**：更新目录树（补充 15+ 遗漏模块）、测试组织章节与实际文件对齐
- **DEVELOPMENT.md**：移除 `coverage_push` 引用、更新项目结构与测试运行说明
- **tests/README.md**：更新准入规则，移除已废弃的 coverage_push 流程
- **README.md**：更新工程化改进章节

### 🧪 验证结果

- 测试文件：28 个（净减 6 个碎片文件，净增 5 个聚焦文件）
- 全量测试：**465 passed**，零失败
- 运行时间：~6s（较重构前 ~13s 提速 54%）

---

## v1.0.7 (2026-02-27)

### 🏗️ 构建与依赖

- **移除幽灵依赖**：从 `dependencies` 中移除未使用的 `tqdm`
- **可选加速**：`numba` 移至 `[project.optional-dependencies.speed]`，安装变为 `pip install confflow[speed]`；代码已有纯 Python 回退
- **精简开发依赖**：移除 `isort`、`flake8`（已被 `ruff` 覆盖）
- **修复占位 URL**：`project.urls` 从 `user/confflow` 更正为 `confflow/confflow`

### 🔒 异常处理加固

- **42 处 `except Exception:` 缩窄为具体类型**：涵盖 `generator.py`、`rescue.py`、`manager.py`、`io.py`、`contracts.py`、`geometry.py`、`processor.py`、`database.py`、`executor.py`、`task_runner.py` 等 20+ 个模块
  - `ImportError`（回退导入）、`OSError`（文件 I/O）、`ValueError/TypeError`（解析转换）、`AttributeError`（流重定向）等
  - 保留 28 处有正当理由的 broad catch（CLI 入口兜底、RDKit 任意异常、safe-wrapper 模式、进程管理竞态等）

### ⚡ 性能优化

- **BFS 算法**：`_bfs_distances` / `_bfs_distances_multi` 从 `list + head pointer` 改为 `collections.deque`
- **分子成键检测**：O(N²) 全距离矩阵改为 `scipy.spatial.cKDTree.query_pairs()`，大分子下显著加速
- **拓扑哈希**：SHA-1 `hexdigest()[:10]` → 完整 40 字符摘要，消除 >10K 构象时的哈希碰撞风险

### 🛠️ 代码质量

- **方法内导入提升至模块顶层**：`import copy`（orca.py）、`import json`（engine.py）、`import traceback`（cli.py、generator.py）、`from collections import deque`（generator.py）
- **移除重复导入**：`io.py` 中冗余的 `import logging / os / re`、`generator.py` 中重复的 `from ...core.console import console`
- **`utils.py`**：添加模块文档说明双重职责（re-export 层 + 验证工具），新增 `__all__` 导出列表
- **`rescue.py` 去 Gaussian 硬编码**：新增 `_get_policy(cfg)` 辅助函数，`_ConstrainedScanner.run()` 与 `_run_ts_reoptimization()` 现根据配置自动选择 ORCA/Gaussian 策略

### 📝 类型检查 (mypy)

- `check_untyped_defs = True`（原 False）
- 从 `disable_error_code` 中移除 `var-annotated`、`method-assign`、`type-var`
- 启用 `warn_return_any = True`

### 📖 文档修正

- **DEVELOPMENT.md**：移除不存在的 `setup.py` 条目、修正 git URL
- **TESTING.md**：测试统计从 "21 个功能要素" 更新为 "420+ 个自动化测试"
- **USAGE.md**：移除重复的 `ts_bond_atoms` 键（YAML 静默覆盖问题）

### 🧪 验证结果

- 全量测试：**420 passed**，零失败
- 无阻塞回归

---

## v1.0.6 (2026-02-12)

### ✅ 本轮改进收口

- **终端静默输出**：`confflow` 运行默认不向终端打印日志，stdout/stderr 统一写入输入目录下同名 `.txt`。
- **calc 目录与备份策略**：`ChemTaskManager` 默认备份目录改为 step-local（`<step_dir>/backups`），并在运行前自动创建。
- **跨步骤 checkpoint 继承增强**：`chk_from_step` 支持通过安全 step 目录映射解析，避免特殊字符 step 名导致路径失配。
- **任务资源生命周期修复**：`ChemTaskManager.run()` 增加 `finally` 收口，确保 `results.db` 在异常路径下也能关闭。
- **工件备份补齐**：计算备份扩展新增 `.gbw`，提升 ORCA 中间产物可追溯性。

### 🧪 验证结果

- 全量测试：`405 passed`
- 无阻塞回归

---
## v1.0.5 (2026-02-08)

### 🏗️ 架构重构

#### 1. Workflow 模块拆分
- **拆分单体 `engine.py`**：原始 ~1177 行的 `engine.py` 拆分为 5 个模块
  - `engine.py`（~360 行）：纯调度逻辑
  - `helpers.py`：辅助工具（pushd、构象计数）
  - `validation.py`：输入验证与标签标准化
  - `config_builder.py`：配置字典构建（YAML→dict）
  - `stats.py`：CheckpointManager、WorkflowStatsTracker、FailureTracker、Tracer
- **导出统一**：`workflow/__init__.py` 现导出所有公共 API

#### 2. INI 配置消除
- 工作流内部不再生成中间 `.ini` 文件
- `ChemTaskManager` 现直接接受 Python dict 配置
- 兼容性函数 `create_runtask_config()` 仍保留

#### 3. 目录结构精简
- 移除了 `step_xx/work/` 中间层级
- 计算任务直接在 `step_xx/` 目录运行
- 路径更短：`step_xx/results.db` 而非 `step_xx/work/results.db`

#### 4. 核心层统一
- 统一共价半径数据源至 `core/data.py`
- 统一 XYZ 文件 I/O 至 `core/io.py`（含 CID 维护、元数据解析）
- `ChemTaskManager._read_xyz()` 内置异常安全的 fallback 解析

### ✅ 测试
- 295/295 测试全部通过
- 无功能回归

---
## v1.0.4 (2026-02-04)

### ✨ 主要功能

#### 1. TS 救援扫描优化
- **命名简化**: 扫描作业不再使用 `{job}_scan_p1` 等复杂前缀，统一使用三位小数的 **键长数值** (如 `1.746.gjf`) 命名，方便数据追溯。
- **输出精炼**: 移除了不够完美的 ASCII 能量曲线，仅保留更直观且精确的表格数据。
- **标记增强**: 在扫描表中区分 **`PEAK`** (逻辑选中的救援点) 与 **`MAX`** (势能面全局最高点)，高亮显示实际选用的起始结构。

### 🔧 技术细节
- **代码重构**: `confflow/calc/rescue.py` 中的 `run_constrained_opt` 移除了 `point_id` 参数。
- **回归测试**: 更新了 `tests/test_rescue.py` 以兼容新的扫描命名规则，确保自动化测试通过。

## v1.0.3 (2026-02-01)

### ✨ 主要功能

#### 1. 输出格式美化
- **统一布局**: 所有输出限制在 80 字符宽度
- **层次分隔符**: 使用 `═` (主要部分) 和 `─` (步骤部分) 分隔
- **对齐显示**: 所有表格数据右对齐，标题左对齐
- **彩色禁用**: 纯文本格式，适合日志保存和归档

#### 2. 构象 ID 系统升级
- **来源感知前缀**: A/B/C... (基于输入文件索引)
- **稳定格式**: `{prefix}{count:06d}` (例: A000001, B000001)
- **CID 列**: 最终报告中追踪每个构象的来源
- **多输入支持**: 自动区分不同输入源的构象

#### 3. TS 救援输出统一
- **救援启动信息**: 显示 Job、键、初始键长和失败原因
- **Scan 表格**: 统一格式显示扫描点、能量和阶段
- **ASCII 曲线**: 能量随步数变化和键长-能量关系曲线
- **成功消息**: 显示峰值键长和最终键长

#### 4. 网页报告功能删除
- **移除函数**: 历史网页报告相关函数、CLI main() 中对应调用
- **代码精简**: 减少约 250 行无用代码
- **纯文本**: 所有报告输出统一为美化的纯文本格式

### 📝 文档更新
- **USAGE.md**: CID 命名系统文档 (A/B/C 前缀说明)
- **ARCHITECTURE.md**: 更新纯文本报告生成描述
- **DEVELOPMENT.md**: 覆盖率报告格式更新
- **示例**: 新增 TS 救援输出示例

### 🔧 技术细节

#### 代码变更
- `confflow/core/console.py`: +100 行 (新增格式化函数)
- `confflow/blocks/viz/report.py`: -250 行 (网页报告代码删除)
- `confflow/calc/rescue.py`: +75 行 (统一输出)
- `confflow/workflow/engine.py`: +10 行 (统一头部)
- 15+ 测试文件更新

#### 表格格式优化
```
CONFORMER ANALYSIS 表格 (10 列):
Rank | Energy (Ha) | ΔG (kcal) | Pop (%) | Imag | TSBond | CID
─────┼─────────────┼───────────┼─────────┼──────┼────────┼─────
   1 | -384.019307 |      0.00 |    38.9 |    - |      - | A000001
```

#### CID 命名示例
```
# 单输入文件
input.xyz (3 构象) → A000001, A000002, A000003

# 多输入文件
input1.xyz (2 构象) → A000001, A000002
input2.xyz (3 构象) → B000001, B000002, B000003
input3.xyz (1 构象) → C000001
```

### ✅ 测试覆盖
- 295/295 测试通过
- TS 救援输出格式验证
- 报告生成列对齐验证
- 无功能回归

### ⚠️ 破坏性变更
- 网页报告生成功能已移除
- CID 格式从数字改为源感知前缀 (A000001 替代 c000001)
- 使用 `generate_text_report()` 替代已删除的历史网页报告接口

### 📦 清理
- 删除临时文件: output.txt, output_ascii.txt
- 删除缓存: __pycache__, *.pyc
- 规范文件: 重命名 traj.xyz → search.xyz

### 🔗 GitHub 提交
```
commit: 23e7822
message: feat: beautify output format and implement source-based CID naming
```

---

## 后续建议
1. 用户文档中补充 CID 系统使用说明
2. 在发行说明中强调网页报告移除
3. 更新 CI/CD 配置避免冗余覆盖率产物输出
4. 考虑添加导出为 JSON 格式的报告选项
