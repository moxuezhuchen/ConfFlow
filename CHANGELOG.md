# ConfFlow 更新日志

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

#### 4. HTML 报告删除
- **移除函数**: `generate_html_report()`, `generate_workflow_section()`, CLI main()
- **代码精简**: 减少约 250 行无用代码
- **纯文本**: 所有报告输出为美化的纯文本格式

### 📝 文档更新
- **USAGE.md**: CID 命名系统文档 (A/B/C 前缀说明)
- **ARCHITECTURE.md**: 更新纯文本报告生成描述
- **DEVELOPMENT.md**: 覆盖率报告格式更新
- **示例**: 新增 TS 救援输出示例

### 🔧 技术细节

#### 代码变更
- `confflow/core/console.py`: +100 行 (新增格式化函数)
- `confflow/blocks/viz/report.py`: -250 行 (HTML 代码删除)
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
- HTML 报告生成功能已移除
- CID 格式从数字改为源感知前缀 (A000001 替代 c000001)
- 使用 `generate_text_report()` 替代已删除的 `generate_html_report()`

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
2. 在发行说明中强调 HTML 报告移除
3. 更新 CI/CD 配置避免 HTML 覆盖率输出
4. 考虑添加导出为 JSON 格式的报告选项
