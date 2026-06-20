# ConfFlow 测试指南

## 快速开始

### 推荐方式（零产物）

```bash
# 全量测试
./scripts/test.sh

# 带覆盖率
./scripts/test.sh --cov=confflow --cov-report=term-missing

# 仅集成测试
./scripts/test.sh -m integration

# 跳过集成测试
./scripts/test.sh -m "not integration"

# 指定测试文件
./scripts/test.sh tests/test_core.py -v

# 透传任意 pytest 参数
./scripts/test.sh -k test_manager --maxfail=1
```

`./scripts/test.sh` 将所有 pytest 和 coverage 产物重定向到系统临时目录，测试结束后自动清理。

### 干净环境安装验证

发布前或调整依赖边界后，应在全新虚拟环境中验证声明依赖和打包配置：

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest
```

CI 也会执行 `pip check`，用于尽早发现声明依赖与解析结果不一致的问题。

### 直接运行 pytest

```bash
.venv/bin/python -m pytest -q
```

本项目当前开发环境使用仓库内 `.venv`。优先使用 `.venv/bin/python -m pytest`，避免误用系统 Python 中的 pytest 或依赖版本。直接运行 `pytest` 会在项目根目录产生 `.pytest_cache_temp` 和 `.coverage_temp`（已在 .gitignore 中，不会提交）。由于 pytest 和 pytest-cov 的初始化机制限制，无法在不传参数的情况下完全避免这些文件。

---

## 测试概览

| 指标 | 数值 |
|------|------|
| 总测试数 | 以 `pytest --collect-only -q` 和 CI 输出为准 |
| 测试文件 | 以当前 `tests/` 目录为准 |
| 通过率 | 以当前 CI 和本地测试输出为准 |
| 覆盖率门禁 | `fail_under = 85`（见 `pyproject.toml`） |
| 运行时间 | 取决于机器、依赖版本和测试范围 |

---

## 测试文件清单

### 核心层 (`core/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_core.py` | package exports, shared entrypoints | 包导出、核心公共入口 |
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
| `test_config_models.py` | config/models | typed YAML 加载、全局参数合并、calc step runtime dict |
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
| `test_calc_artifacts.py` | calc/artifacts | manifest 复用、stale cleanup、sandbox 边界 |
| `test_calc_runner.py` | calc/runner | typed runner 执行、manifest 写入与复用 |
| `test_calc_full.py` | calc policies + typed config | policy 解析/输入生成与 runtime dict 集成 |
| `test_policies.py` | policies/gaussian, orca | Gaussian/ORCA 输入生成与输出解析 |
| `test_rescue.py` | calc/rescue, scan_ops | TS 失败救援、约束扫描 |
| `test_rescue_ts_scan_paths.py` | calc/rescue, scan_ops | TS 救援扫描路径与目录策略 |
| `test_geometry.py` | calc/geometry | 几何解析、正常终止检测 |
| `test_input_helpers_hotspots.py` | calc/components/input_helpers | 内存、约束、冻结参数热点路径 |
| `test_policies_hotspots.py` | calc/policies | Gaussian/ORCA 策略边缘路径 |
| `test_rescue_hotspots.py` | calc/rescue | TS 救援扫描与重优化热点路径 |

### 工作流 (`workflow/`)

| 文件 | 覆盖模块 | 说明 |
|------|----------|------|
| `test_engine.py` | workflow/engine, helpers | 工作流引擎、断点恢复、步骤调度 |
| `test_export.py` | workflow/export | 导出功能测试 |
| `test_rerun_failed.py` | workflow/rerun_failed | 失败重跑功能测试 |
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
data_file = ".coverage_temp"

[tool.coverage.report]
fail_under = 85
show_missing = true
```

运行带覆盖率检查的测试：

```bash
# 推荐方式（零产物）
./scripts/test.sh --cov=confflow --cov-report=term-missing

# 或直接运行 pytest
pytest tests/ --cov=confflow --cov-report=term-missing
```

**注意**：
- `./scripts/test.sh` 将 coverage 数据文件重定向到系统临时目录
- 直接运行 `pytest` 会在项目根目录生成 `.coverage_temp`（已在 .gitignore 中）

最近一次本地验证基线（2026-04-12）：

- `pytest -q`：当前测试数量会随仓库演进变化；以当前 CI 输出为准
- `ruff check confflow tests`：通过
- `mypy confflow`：通过
- 本轮未重跑 `pytest tests/ --cov=confflow --cov-report=term`，因此不在此处重复历史覆盖率数值

最近一次重构验证（2026-06-20）：

- `python3 -m compileall confflow tests`：通过
- `.venv/bin/python -m pytest --collect-only -q`：653 tests collected
- `.venv/bin/python -m pytest -q`：653 passed
- `.venv/bin/ruff check confflow tests`：通过
- `.venv/bin/mypy confflow`：通过（`mypy.ini` 仍提示存在未使用的 `[mypy-tests.*]` section）
- `.venv/bin/python -m pip check`：通过
- `.venv/bin/python -m pip wheel . -w /tmp/confflow-wheel`：通过，生成 `confflow-1.0.10-py3-none-any.whl`

---

## 公共 CI 覆盖边界

公共 GitHub Actions CI 当前覆盖：

- Python 3.10、3.11、3.12、3.13 上运行 `pytest -q`。
- Python 3.11 上运行 Black gate、`ruff check .`、`mypy confflow`。
- 独立 coverage job 在 Python 3.11 上运行 `pytest tests/ --cov=confflow --cov-report=term-missing --cov-report=xml`，并使用 `pyproject.toml` 中的 coverage 门禁。
- Gaussian/ORCA policy、输入生成、输出解析、错误处理、TS rescue、workflow resume 等逻辑通过单元测试、fake runner、mock、fixture 和样例日志覆盖。

公共 CI 当前不完整覆盖：

- 真实 Gaussian 16 或 ORCA 安装环境中的端到端计算。
- 商业软件许可证、环境模块、集群调度器、scratch 目录和站点特定 wrapper。
- 大规模分子体系、长时间运行任务、真实 checkpoint 文件生命周期。
- 每个操作系统和 RDKit 安装组合；公共 CI 主要在 Ubuntu runner 上验证。

### Fake/Mock 与真实 E2E 的区别

仓库测试会使用 fake ORCA/Gaussian 输出、mock policy、fake executor 或预制日志来验证 ConfFlow 的解析、调度和错误处理逻辑。这些测试可以证明 ConfFlow 的内部控制流和数据契约，但不能证明真实外部程序在目标机器上已正确安装、授权、收敛或生成完全一致的输出格式。

真实 Gaussian/ORCA 环境仍需要手动或站点内 CI 验证。

### 真实环境手动验证建议

在公开或部署到新的计算环境前，建议至少手动验证：

1. `confflow --help`、`confgen --help`、`confcalc --help` 能正常运行。
2. RDKit 能导入，并能完成一个最小 XYZ 的构象生成。
3. `allowed_executables` 指向的 Gaussian/ORCA 可执行文件存在且不带额外 shell 参数。
4. 一个最小 Gaussian 或 ORCA `sp` 任务能生成 `results.db`、`output.xyz` / `result.xyz` 和日志。
5. 一个失败任务能写入 `failed.xyz` 和可诊断的 `error_details`。
6. 如果使用 `sandbox_root`，确认 `work_dir`、`backup_dir`、`input_chk_dir` 均被限制在预期目录下。
7. 对包含敏感结构或私有路径的日志进行脱敏后再分享。

### 本地真实 Gaussian/ORCA E2E 记录

2026-06-20 在 `/opt/ConfFlow` 当前环境中，使用 `/opt/g16/g16` 和 `/opt/orca611/orca` 完成以下真实端到端验证。测试产物保留在未跟踪目录 `e2e_real/`，用于本机复查，不属于发布包内容。

已通过：

- CLI 入口：`confflow --help`、`confcalc --help`、`confts --rewrite-scan-keyword`、`confrefine --help`。
- ORCA：`sp`、`opt`、`freq`、`opt_freq` 最小水分子真实任务，manifest 均为 `completed`。
- Gaussian 16：`sp`、`opt`、`freq`、`opt_freq` 最小水分子真实任务，使用独立可写 `GAUSS_SCRDIR`，manifest 均为 `completed`。
- Gaussian checkpoint 继承：两步 `g16_seed_sp -> g16_readchk_sp`，第二步生成 `%OldChk=WATER001.old.chk`，并从上一阶段 `backups/WATER001.chk` 复制 checkpoint。
- 混合 workflow：`confgen -> ORCA opt + auto_clean refine -> Gaussian sp`，8 个 butane conformer 经 ORCA opt 后 refine 为 2 个，再由 Gaussian SP 完成。
- sandbox 正向：`sandbox_root` 下默认 work dir 解析到 `e2e_real/sandbox_root/water_work` 并完成 ORCA SP。
- sandbox 负向：显式 `-w` 指向 `sandbox_root` 外部时 CLI 返回非零，未创建逃逸 work dir。
- `allowed_executables` 负向：`orca_path: /bin/echo` 被拒绝，`failed.xyz` 记录 `ErrorKind=worker_exception` 和 allowlist 错误。
- `max_wall_time_seconds`：真实 ORCA 进程被超时杀掉，`failed.xyz` 记录 `ErrorKind=exec_error` 和 `exceeded max_wall_time_seconds`。
- STOP beacon：对 decane ORCA freq 运行期间连续写入 `STOP`，CLI 返回非零，`failed.xyz` 记录 `ErrorKind=stop_requested`。
- `confcalc`：ORCA SP 和 Gaussian SP 均完成。
- `confgen`：butane 旋转搜索生成 27 个 conformer；workflow 方式也完成。
- `confrefine`：butane conformer 去重筛选完成。
- export/report：ORCA/Gaussian 工作目录可导出 JSON/CSV，文本报告生成路径可调用。

发现的环境和使用注意事项：

- Gaussian 默认 scratch `/opt/g16/scratch` 在本容器中不可写；真实 Gaussian 任务需要设置可写 `GAUSS_SCRDIR`。
- Gaussian 多个 workflow 并行运行时不要共用同一个 `GAUSS_SCRDIR` 和相同 job name，否则可能产生 scratch/checkpoint 竞争。
- `itask` 用于 ConfFlow 的解析、分类和后处理语义；不会自动把 `Opt`、`Freq`、`TS` 注入 Gaussian/ORCA route keyword。真实 `opt/freq/opt_freq/ts` 任务必须在 `keyword` 中显式写外部程序关键字，例如 `HF/STO-3G Opt Freq` 或 `HF STO-3G Freq`。
- workflow schema 当前允许 `refine` / `viz` step type，但 engine 实际只执行 `confgen` 和 `calc`；真实 refine workflow 目前通过 calc step 的 `auto_clean` 后处理路径覆盖。

尚未完整覆盖，建议按需追加站点内长测：

- Gaussian/ORCA 真实 `ts` 全组合和 TS rescue scan；这类任务需要合适 TS 初猜和更长运行时间。
- 多输入 topology consistency / chain transfer 的真实 workflow；单元测试已覆盖映射逻辑，但尚未在真实外部程序链路中跑完整批量。
- 大分子、多 conformer、大并发资源调度压力测试；本轮只覆盖 decane STOP 和 butane 小批量混合 workflow。
- 真实外部程序失败类型的完整分类矩阵，例如 SCF 不收敛、内存不足、异常终止等；本轮覆盖了非法 executable、timeout、STOP、Gaussian scratch 不可写和本机异常终止样例。
- clean venv 安装测试已通过 editable reinstall 和 wheel 构建，但未在全新空 venv 中安装 wheel 后跑全量 CLI。

---

## 编写测试的约定

1. **使用 `tmp_path`**：所有文件操作使用 pytest 内置的 `tmp_path` fixture，不要用 `tempfile` + 手动清理
2. **try/finally 保护 `importlib.reload`**：回退测试中修改模块状态后必须在 `finally` 中恢复
3. **每个测试必须有断言**：不允许仅调用函数而不检查结果的"烟雾测试"
4. **参数化优于复制**：相同逻辑不同输入使用 `@pytest.mark.parametrize`
5. **Fake 对象集中维护**：放在 `_helpers.py`，不在各测试文件内重复定义
