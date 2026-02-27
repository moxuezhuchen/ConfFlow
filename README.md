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

## 工程化改进（2026-02）

- ✅ 统一构建与依赖管理：仅保留 `pyproject.toml`
- ✅ 引入 `Pydantic v2`：核心上下文模型集中在 `confflow/core/models.py`
- ✅ 清理重复 I/O：统一复用 `confflow/core/io.py`
- ✅ 进程终止增强：`cli` 使用 `psutil` 进行进程树回收
- ✅ 测试架构重构：28 个聚焦测试文件、465 个测试、~6s 运行
- ✅ 覆盖率基础设施：branch coverage + `fail_under = 70`
- ✅ 测试回归通过：全量 465 passed，零失败
- ✅ 构象去重精度提升：对称性感知 RMSD + 能量辅助阈值，解决大分子原子乱序/对称互换导致的去重漏判

## 目录清理

可使用以下命令清理缓存与构建产物：

```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
rm -rf confflow.egg-info .pytest_cache build dist .coverage
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
  charge: 0
  multiplicity: 1

steps:
  - name: opt_b3lyp
    type: calc
    params:
      iprog: g16
      itask: opt_freq
      keyword: "B3LYP/6-31G* opt freq"

  - name: refine
    type: refine
    params:
      rmsd_threshold: 0.25
      energy_window: 5.0
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
- `step_xx/results.db`：每个 `geom_XXXX` 的状态与 `error/error_details`。

此外也可查看 `confflow.log` 以及 `backups/` 中的 `.log/.out` 备份文件。

**Q: 断点续传如何工作？**  
A: 再次运行相同命令会自动跳过已成功的任务。如果 `results.db` 丢失但 `backups/` 存在，也会尝试从备份恢复。

**Q: TS 任务失败后如何救援？**  
A: 设置 `ts_rescue_scan: true`（默认开启），会自动执行 scan 寻找正确的 TS 结构。

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

