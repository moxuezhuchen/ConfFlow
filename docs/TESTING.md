# ConfFlow 测试指南

## 快速开始

```bash
# 全量测试
pytest tests/ -q

# 带覆盖率
pytest tests/ --cov=confflow --cov-report=term-missing

# 仅集成测试
pytest tests/ -m integration

# 跳过集成测试
pytest tests/ -m "not integration"
```

---

## 测试概览

| 指标 | 数值 |
|------|------|
| 总测试数 | 660 |
| 测试文件 | 41 |
| 通过率 | 100% |
| 分支覆盖率 | 90.72% |
| 运行时间 | `pytest -q` 约 6s；带覆盖率约 7s |

---

## 测试文件清单

### 核心层 (`core/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_core.py` | config/schema, package exports | 配置归一化、包导出、低能量溯源 |
| `test_io.py` | core/io | XYZ 文件读写、元数据解析、键长计算 |
| `test_data.py` | core/data | 共价半径、元素符号、原子序数 |
| `test_models.py` | core/models | TaskContext、GlobalConfigModel、CalcConfigModel |
| `test_console.py` | core/console | 控制台输出格式化 |
| `test_contracts.py` | core/contracts | 输入/输出契约验证 |
| `test_keyword_rewrite.py` | core/keyword_rewrite | TS→scan 关键字改写 |
| `test_logging_hotspots.py` | core/logging | 日志重定向、handler 切换热点路径 |

### 配置层 (`config/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_schema.py` | config/schema | Schema 验证、参数合并、遗留键检测 |
| `test_defaults.py` | config/defaults | 默认常量类型与值检查 |
| `test_loader.py` | config/loader | 配置文件加载边界条件 |
| `test_validation.py` | workflow/validation | 输入验证与兼容性校验 |

### 构象生成 (`blocks/confgen/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_confgen.py` | confgen/generator | 构象生成核心、链旋转、CLI 入口 |
| `test_confgen_validator.py` | confgen/validator | 构象验证器 |
| `test_collision.py` | confgen/collision | 碰撞检测核心与拓扑过滤 |
| `test_mapping.py` | confgen/mapping | 多输入拓扑映射与柔性链索引转移 |
| `test_confts_keyword.py` | confts | TS 关键字解析、confts CLI |

### 构象筛选 (`blocks/refine/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_refine.py` | refine/processor, rmsd_engine | RMSD 去重、能量筛选、虚频过滤 |
| `test_processor_hotspots.py` | refine/processor | 去重、能量窗口、失败路径热点 |
| `test_rmsd_engine_hotspots.py` | refine/rmsd_engine | 对称 RMSD 与拓扑分组热点路径 |

### 回退测试

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_confgen_refine_fallbacks.py` | confgen, refine | 回退路径、RMSD/collision 边界测试 |

### 量化计算 (`calc/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_calc.py` | calc 基础 + task_runner + input_helpers | 任务运行器、输入生成、资源计算 |
| `test_calc_full.py` | calc 完整集成 | 端到端计算流程、多步骤场景 |
| `test_policies.py` | policies/gaussian, orca | Gaussian/ORCA 输入生成与输出解析 |
| `test_rescue.py` | calc/rescue, scan_ops | TS 失败救援、约束扫描 |
| `test_utils_manager.py` | calc/manager, core/utils | 任务管理器、工具函数 |
| `test_geometry.py` | calc/geometry | 几何解析、正常终止检测 |
| `test_input_helpers_hotspots.py` | calc/components/input_helpers | 内存、约束、冻结参数热点路径 |
| `test_policies_hotspots.py` | calc/policies | Gaussian/ORCA 策略边缘路径 |
| `test_rescue_hotspots.py` | calc/rescue | TS 救援扫描与重优化热点路径 |

### 工作流 (`workflow/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_engine.py` | workflow/engine, helpers | 工作流引擎、断点恢复、步骤调度 |
| `test_step_handlers.py` | workflow/step_handlers | 步骤执行适配器（confgen/calc 步骤） |
| `test_runtime_context.py` | workflow/runtime_context | 运行时上下文初始化 |
| `test_presenter.py` | workflow/presenter | 步骤展示与报告输出 |
| `test_runtime_and_policy_base_hotspots.py` | workflow/runtime_context, policies/base | 运行时上下文与策略基类热点路径 |

### 可视化与报告

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_viz_report.py` | viz/report, core/types | Boltzmann 权重、报告生成、时间格式化 |

### 其他

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_cli.py` | cli, main | CLI 参数解析、主入口集成 |
| `test_input_snapshot.py` | core/io (快照) | Gaussian/ORCA 输入文件生成快照 |
| `test_collision_hotspots.py` | confgen/collision | Numba/Python 回退执行路径 |

---

## Fixtures 与 Helpers

### 共享 Fixtures (`conftest.py`)

| Fixture | 说明 |
|---------|------|
| `input_xyz` | 在 `tmp_path` 中创建一个最小 XYZ 文件 |
| `config_yaml` | 在 `tmp_path` 中创建一个最小 YAML 配置 |
| `cd_tmp` | 切换到 `tmp_path` 并在结束后恢复 |
| `sync_executor` | 同步执行器（替代 ProcessPoolExecutor） |

### 共享 Helpers (`_helpers.py`)

| Helper | 说明 |
|--------|------|
| `FakeRunner` | 计算任务的假执行器 |
| `FakeResultsDB` | 可配置结果的假数据库 |
| `FakeFuture` | 返回预设值的假 Future |
| `FakeExecutor` | 使用 FakeFuture 的假线程池 |
| `assert_raises_match` | 带正则匹配的异常断言 |
| `reload_with_import_block` | 模拟模块导入失败后重新加载 |

---

## 测试标记

| 标记 | 说明 | 用法 |
|------|------|------|
| `integration` | 端到端集成测试 | `pytest -m integration` |

---

## 覆盖率

已在 `pyproject.toml` 中配置：

```toml
[tool.coverage.run]
source = ["confflow"]
branch = true

[tool.coverage.report]
fail_under = 85
show_missing = true
```

运行带覆盖率检查的测试：

```bash
pytest tests/ --cov=confflow --cov-report=term-missing
```

最近一次本地验证基线（2026-04-03）：

- `pytest -q`：660 passed
- `pytest tests/ --cov=confflow --cov-report=term`：branch coverage 90.72%
- `ruff check .`：通过
- `mypy confflow`：通过

---

## 编写测试的约定

1. **使用 `tmp_path`**：所有文件操作使用 pytest 内置的 `tmp_path` fixture，不要用 `tempfile` + 手动清理
2. **try/finally 保护 `importlib.reload`**：回退测试中修改模块状态后必须在 `finally` 中恢复
3. **每个测试必须有断言**：不允许仅调用函数而不检查结果的"烟雾测试"
4. **参数化优于复制**：相同逻辑不同输入使用 `@pytest.mark.parametrize`
5. **Fake 对象集中维护**：放在 `_helpers.py`，不在各测试文件内重复定义
