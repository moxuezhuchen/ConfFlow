## ConfFlow

ConfFlow 是一个自动化工作流工具：从 XYZ 输入出发，按 YAML 配置完成构象生成、量化计算、去重与报告输出（合并到 .txt）。

[![CI](https://github.com/user/confflow/actions/workflows/ci.yml/badge.svg)](https://github.com/user/confflow/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 特性

- 🔄 **完整工作流**：构象生成 → 量化计算 → 去重筛选 → 文本报告（合并到 .txt）
- 🧩 **最低能量构象导出**：输出最低能量构象的单帧 XYZ 到输入目录
- 🧪 **多程序支持**：Gaussian 16、ORCA
- ⚡ **并行计算**：多任务并发执行
- 🔁 **断点续传**：任务中断后可恢复
- 📊 **TS 特性**：TS 失败后自动 scan 救援、虚频校验
- 🧭 **柔性链自动映射**：多输入原子序号不同也可基于拓扑自动对齐柔性链

## 安装

```bash
# 开发安装（可编辑）
pip install -e .

# 或标准安装
pip install .

# 可选依赖（开发/类型检查）
pip install -e ".[dev]"
```

项目已统一为 `pyproject.toml` 构建（PEP 621），不再使用 `setup.py`。

## 工程化改进（2026-04）

- ✅ 统一构建与依赖管理：仅保留 `pyproject.toml`
- ✅ 依赖清单收敛：移除未使用的 `jinja2` 与空置 `viz` extra
- ✅ 引入 `Pydantic v2`：核心上下文模型集中在 `confflow/core/models.py`（含 `GlobalConfigModel`、`CalcConfigModel`）
- ✅ 清理重复 I/O：统一复用 `confflow/core/io.py`
- ✅ XYZ 流式处理：新增 `iter_xyz_frames()`，`confgen` 改为边生成边写 `search.xyz`
- ✅ 进程终止增强：`cli` 使用 `psutil` 进行进程树回收
- ✅ 架构边界收口：新增 calc step contract、路径策略、后处理适配器、内部 run services
- ✅ 测试基线（2026-04-12）：41 个 `test_*.py` 测试文件、**682 个测试**、`pytest -q` 本地约 6.4s
- ✅ 覆盖率门禁：`pyproject.toml` 中配置 `fail_under = 85`
- ✅ 类型安全：`core/types.py` 改为标准库 `typing.TypedDict`
- ✅ 类型/风格基线（2026-04-12）：`mypy confflow`、`ruff check confflow tests`、`pytest -q` 均通过
- ✅ 支持矩阵明确：CI 现验证 Python **3.9-3.13**
- ✅ 异常精确化：`scan_ops`/`executor`/`generator` 中 8 处 `except Exception` 收窄为具体异常
- ✅ 构象去重精度提升：对称性感知 RMSD + 能量辅助阈值，解决大分子原子乱序/对称互换导致的去重漏判
- ✅ 工作流工件契约收紧：`calc`/`resume` 仅接受 `output.xyz` / `result.xyz` 作为已完成输出，避免误把 `search.xyz` 当成计算结果
- ✅ calc 断点复用更安全：仅当 step 目录中的 `.config_hash` 与当前任务配置一致时才复用旧结果，配置变化会自动重算
- ✅ 状态统计与结果视图一致：`results.db` 按每个 `job_name` 的最新记录聚合 step 失败数与汇总统计

## 目录清理

可使用以下命令清理缓存与构建产物：

```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
rm -rf confflow.egg-info .pytest_cache .pytest_basetemp .mypy_cache .ruff_cache build dist htmlcov coverage.xml reports .coverage
```

## 快速开始

```bash
# 基础用法
confflow mol.xyz -c confflow.yaml

# 从断点恢复
confflow mol.xyz -c confflow.yaml --resume

# 详细日志
confflow mol.xyz -c confflow.yaml --verbose
```

运行时默认不会在终端打印日志；所有 CLI 运行日志会写入输入目录下同名输出文件：`<input_basename>.txt`。

常用排查方式：

```bash
tail -f mol.txt
```

## 命令行工具

| 命令 | 说明 |
|------|------|
| `confflow` | 按 YAML 工作流调度 |
| `confgen` | 构象生成（链模式） |
| `confcalc` | 量化计算执行器 |
| `confrefine` | 构象去重/筛选 |
| `confts` | TS 专用（scan 救援） |

## 配置示例

```yaml
global:
  gaussian_path: "/opt/g16/g16"
  cores_per_task: 4
  total_memory: "16GB"
  sandbox_root: "/scratch/confjobs"
  allowed_executables: ["g16", "/opt/orca/orca"]
  charge: 0
  multiplicity: 1

steps:
  - name: confgen
    type: confgen
    params:
      chains: ["1-2-3-4"]

  - name: opt_b3lyp
    type: calc
    params:
      iprog: g16
      itask: opt_freq
      keyword: "B3LYP/6-31G* opt freq"
```

## 文档

- [项目架构](docs/ARCHITECTURE.md) - 完整的架构设计与模块说明
- [使用说明](docs/USAGE.md) - 快速开始指南
- [命令参考](docs/COMMAND_REFERENCE.md) - 所有命令的完整参考
- [关键字参考](docs/KEYWORD_REFERENCE.md) - YAML 配置关键字
- [开发指南](docs/DEVELOPMENT.md) - 扩展与开发说明
- [测试说明](docs/TESTING.md) - 测试套件文档
- [风格契约](docs/STYLE_CONTRACT.md) - 代码/输入/输出一致性标准

## FAQ

**Q: RDKit/numba 是必须的吗？**

A: RDKit 是必须的（用于 MMFF 预优化与分子操作）。Numba 是可选的（用于 RMSD 加速），缺失时会自动降级使用纯 Python 实现，但速度会变慢。

**Q: 如何查看任务失败原因？**

A: 优先看对应 step 的两类信息：

- `step_xx/failed.xyz`：失败构象（输入结构）集合，注释行包含 `Job/CID/Error`，方便定位与重算。
- `step_xx/results.db`：每个 `job_name` / `CID` 的最新状态与 `error/error_details`。

此外也可查看 `confflow.log` 以及 `backups/` 中的 `.log/.out` 备份文件。

工作流根目录还会额外写出两份 JSON：

- `workflow_stats.json`：完整运行统计与低能构象追踪
- `run_summary.json`：面向脚本消费的精简摘要

**Q: 断点续传如何工作？**

A: 再次运行相同命令会自动跳过已成功的任务。如果 `results.db` 丢失但 `backups/` 存在，也会尝试从备份恢复。恢复时会按 step 类型检查标准产物：`confgen` 只接受 `search.xyz`，`calc` 只接受 `output.xyz` / `result.xyz`；对 `calc` 还会校验 step 目录中的 `.config_hash` 是否与当前任务配置一致，不一致时会清理该 step 的旧工件并重新计算。工作目录不完整会直接报错，避免误用错误工件继续运行。

**Q: 如何限制工作目录和外部程序路径？**

A: 在 YAML `global` 中设置 `sandbox_root` 和 `allowed_executables`。前者限制 `work_dir`、`backup_dir`、`input_chk_dir` 必须位于指定根目录下，后者限制 `gaussian_path` / `orca_path` 只能命中白名单中的单个可执行目标。未配置时仍会执行基础安全校验，拒绝明显危险的删除目标和带参数的可执行字符串。

**Q: TS 任务失败后如何救援？**

A: 设置 `ts_rescue_scan: true`（默认关闭），会自动执行 scan 寻找正确的 TS 结构。

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 类型检查
mypy confflow

# 代码风格
ruff check .
```

## 许可证

MIT License

---

**ConfFlow** - 让计算化学更简单 🧪
