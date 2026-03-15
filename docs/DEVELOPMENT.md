# ConfFlow 开发指南

## 项目结构

```
confflow/
├── confflow/              # 核心包
│   ├── main.py            # 工作流主程序
│   ├── cli.py             # 命令行入口
│   ├── confts.py          # TS 专用执行器
│   ├── blocks/            # 工作流步骤块
│   │   ├── confgen/       # 构象生成
│   │   ├── refine/        # 结果筛选与精炼
│   │   └── viz/           # 可视化与报告
│   ├── calc/              # 量子计算核心
│   │   ├── policies/      # 程序特定策略 (Gaussian/Orca)
│   │   ├── components/    # 执行器与任务管理
│   │   └── db/            # 结果数据库
│   ├── config/            # 配置加载与校验
│   ├── core/              # 基础 IO、数据、模型与工具函数
│   └── workflow/          # 工作流引擎
├── tests/                 # 单元测试（41 个文件，630 个用例）
├── docs/                  # 文档
├── confflow.yaml          # 配置模板
├── README.md              # 主文档
└── pyproject.toml         # 项目元数据与打包配置
```

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/moxuezhuchen/ConfFlow.git
cd ConfFlow
```

### 2. 创建虚拟环境

```bash
conda create -n confflow-dev python=3.9 -y
conda activate confflow-dev
```

### 3. 安装开发依赖

```bash
pip install -e ".[dev]"
```

## 代码规范

### 格式化

使用 Black 进行代码格式化：

```bash
black confflow/ tests/
```

### 类型检查

```bash
mypy confflow
```

### 代码风格检查

```bash
ruff check .
```

统一风格与输入/输出契约见：`docs/STYLE_CONTRACT.md`

## 运行测试

### 所有测试

```bash
pytest tests/ -v
```

### 指定测试文件

```bash
pytest tests/test_confgen.py -v
```

### 仅集成测试

```bash
pytest tests/ -m integration
```

### 代码覆盖率

```bash
pytest tests/ --cov=confflow --cov-report=term-missing
```

覆盖率阈值已配置在 `pyproject.toml` 中（`fail_under = 70`），并启用了分支覆盖率。

### 常用质量门禁（推荐）

```bash
ruff check .
mypy confflow
pytest -q
```

### 测试产物目录规范

- 统一测试临时目录：`.pytest_basetemp`
- 统一 pytest 缓存目录：`.pytest_cache`
- 覆盖率与报告目录：`htmlcov/`、`coverage.xml`、`reports/`

以上目录均已在 `.gitignore` 中忽略，避免污染仓库根目录。

测试架构详见：`docs/TESTING.md`

### 目录清理（缓存/临时文件）

```bash
find . -type d -name "__pycache__" -exec rm -rf {} +
rm -rf .pytest_cache .pytest_basetemp .mypy_cache .ruff_cache confflow.egg-info build dist htmlcov coverage.xml reports .coverage
```

## 核心模块说明

### blocks/confgen - 构象生成

**主要类与函数：**
- `ConformerGenerator` - 构象生成核心类
- `gen_confs()` - 生成初始构象集（CLI 入口）

**扩展点：**
- 在 `generator.py` 中添加新的构象生成策略。

### calc - 量子计算

**架构：**
- `policies/`：定义不同程序的输入生成与输出解析逻辑（如 `GaussianPolicy`, `OrcaPolicy`）。
- `components/task_runner.py`：管理单个任务的生命周期（生成、执行、解析、救援）。
- `components/executor.py`：底层 shell 命令执行。
- `manager.py`：多任务并行管理。

**支持的程序：**
- Gaussian 16
- ORCA 6.0+

**扩展新程序：**
1. 在 `calc/policies/` 下创建新的 Policy 类，继承自 `CalculationPolicy`。
2. 实现 `generate_input` 和 `parse_output` 方法。
3. 在 `calc/policies/__init__.py` 中注册新程序。

### blocks/refine - 结果筛选

**主要功能：**
- 能量窗口筛选
- RMSD 去重
- 虚频过滤
- 结构有效性检查

### core/utils.py - 工具函数

**核心工具：**
- `ConfFlowLogger` - 日志系统
- `fast_rmsd()` - 快速 RMSD 计算

### blocks/viz - 可视化

**主要功能：**
- 生成文本报告（可合并到 .txt 输出）。
- 能量分布与收敛轨迹可视化。

## 添加新功能的步骤

### 1. 新的量子化学程序支持

**文件修改：**
- `confflow/calc/policies/`：添加新的 Policy 实现。
- `confflow/config/schema.py`：如果需要新的程序特定配置项，更新 Schema。

**示例：**

```python
# 在 calc/policies/myprog.py 中
class MyProgPolicy(CalculationPolicy):
    def generate_input(self, ...):
        pass
    def parse_output(self, ...):
        pass
```

### 2. 新的构象生成策略

**文件修改：**
- `confflow/blocks/confgen/generator.py`：添加新的生成逻辑。
- `confflow/config/schema.py`：添加新参数。

### 3. 新的筛选条件

**文件修改：**
- `confflow/blocks/refine/processor.py`：添加新的筛选逻辑。

## 性能优化

### 1. 使用 Numba JIT

对计算密集型函数使用 `@jit` 装饰器：

```python
from numba import jit

@jit(nopython=True)
def fast_calculation(arr):
    # 计算密集的代码
    pass
```

### 2. 并行处理

使用 `multiprocessing` 处理多个构象：

```python
from multiprocessing import Pool

def process_batch(conformers):
    with Pool(max_workers) as pool:
        results = pool.map(process_one, conformers)
    return results
```

### 3. 内存管理

- 及时释放大数组
- 使用流式处理处理大量构象

## 文档编写

### Python 文档字符串

使用 Google 风格的文档字符串：

```python
def calculate_energy(conformer):
    """计算构象能量。
    
    Args:
        conformer: Nx3 numpy 数组，分子坐标
        
    Returns:
        float: 能量值，单位 Ha
        
    Raises:
        ValueError: 如果构象无效
        
    Example:
        >>> energy = calculate_energy(conf)
        >>> print(f"{energy:.6f}")
    """
    pass
```

### Markdown 文档

- 使用清晰的标题层级
- 提供代码示例
- 包含常见问题解答

## 版本管理

### 版本号格式

采用语义版本化 (Semantic Versioning)：
- MAJOR: 不兼容的 API 改变
- MINOR: 向后兼容的功能添加
- PATCH: 向后兼容的 bug 修复

**示例：** 1.0.0 (主版本.次版本.修订版本)

### 发布流程

1. 更新 CHANGELOG.md
2. 更新 `__version__` 在 `__init__.py`
3. 标记 git tag
4. 发布到 PyPI

## 常见问题

### Q: 如何调试工作流？

A: 使用 `--verbose` 启用调试日志：
```bash
confflow input.xyz -c confflow.yaml --verbose
```

### Q: 如何添加新的量子化学程序？

A: 参考"添加新功能的步骤"中的量子化学程序部分，实现新的 `CalculationPolicy`。

### Q: 如何优化性能？

A: 查看"性能优化"部分，或调整 `confflow.yaml` 中的 `max_jobs` 并行数。

## 联系与反馈

- 提交 Issue 报告 bug
- 提交 Pull Request 贡献代码
- 邮件反馈：feedback@confflow.org

---

感谢为 ConfFlow 做贡献！
