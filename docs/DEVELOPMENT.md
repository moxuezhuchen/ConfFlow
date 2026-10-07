# ConfFlow 开发指南

面向要改 ConfFlow 代码的人：怎么搭环境、质量门禁是什么、代码放哪、新增能力怎么接、契约和跨仓测试怎么处理。
整体结构见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，测试见 [`TESTING.md`](TESTING.md)。

## 1. 环境

- Python ≥ 3.10（CI 矩阵 3.10–3.13）。
- 运行依赖：numpy、scipy、pyyaml、psutil、rich、pydantic、rdkit、jsonschema、referencing、rfc8785。

```bash
git clone https://github.com/moxuezhuchen/ConfFlow.git && cd ConfFlow
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

- `dev` 额外装 black、ruff、mypy、pytest、pytest-cov、build 与类型桩。
- `speed` / `all` extra 装 numba；目前没有任何代码路径依赖 numba 加速（`confflow/__init__.py` 只探测它是否可用）。
- 真实的 Gaussian / ORCA 需要自己安装并取得许可；仓库内测试只用 `tests/v4/fakes/` 里的假可执行文件。

## 2. 质量门禁（与 CI 一致）

```bash
black --check .            # 行宽 100，target py310
ruff check .               # E F I B UP D；numpy 风格 docstring；模块/包 docstring 必须有
mypy confflow
pytest -q                  # 见 TESTING.md；scripts/test.sh 会把所有产物放进系统临时目录
```

- 格式化只用 black；ruff 只做 lint（不要用 `ruff format` 整体重排）。
- 公开类/函数 docstring（D101–D107）暂缓强制；模块与包 docstring 必须有，且应说明该模块**拥有**什么、**不做**什么。
- `pyproject.toml` 里 `strict_config = true`：未声明的 pytest 标记会报错。

## 3. 代码放哪

| 要做的事 | 放在 | 注意 |
| --- | --- | --- |
| 新的领域概念（不可变记录、digest 规范化） | `domain/` | 不得 import 其他 `confflow.*`，不做 I/O |
| 新的纯科学计算 | `science/` | 纯函数；不读环境、不写文件；有预算/上限的算法要把预算做成显式参数并用"节点数/结果"测试固定，而不是用耗时 |
| 新的执行器能力或 step 类型 | `execution/` + `execution/registry.py` | 能力、adapter、profile、check、recovery 都带独立的 contract version，进入 step digest |
| 新的量化程序 | `programs/<name>/` + `programs/registry.py` | 适配器只负责输入渲染与输出解析 |
| 新的科学检查 / 恢复策略 | `execution/checks_standard.py`、`recovery_standard.py` | 必须在文档里显式声明才会生效，没有按 role 隐式触发 |
| 工作流 schema 字段 | `workflow/v4/schema.py` | 默认值只在这里定义；JSON schema 由模型生成 |
| 对客户端发布的内容 | `producer/` | 改动会移动契约/边界 digest，见 §5 |
| 持久化格式 | `persistence/` | 通过 `persistence/contracts.py` 的冻结契约 |

原则：复用正确的低层能力，替换错误的高层抽象；不要因为"方便"绕过 registry 或在执行层按 program/role 名分支。

## 4. 新增能力的最小步骤

**新的 calculation program**：写适配器（渲染 + 解析 + artifact 发现 + 环境探测）→ 在 `programs/registry.py` 注册 →
在 `tests/v4/fakes/` 加一个行为可控的假可执行文件 → 在 `tests/v4/` 里加渲染/解析/一致性测试 →
`confflow v4 contract --json` 里 program 列表随之变化，确认契约变更是预期的（见 §5）。

**新的科学检查 / 结果 profile**：在 `execution/contracts.py` 描述符中声明（带 contract version）→ 实现 → 注册 →
测试覆盖"通过/失败/缺数据"三种情形。

**新的 ConfGen 声明（torsion / ring / coordination / path）**：改 `workflow/v4/confgen_schema.py` 与
`science/confgen/` 中对应的 lane；任何改变构象集合的改动都要有等价性或回归证据，并且要重新捕获引擎报告基线
（外部基线 `$BASE` 与 `tools/refactor/`；本轮实际路径示例 `/tmp/l0-baseline-run-v2/baseline`，配套 `MANIFEST.json`，不是通用设计）。

## 5. 契约与指纹

`producer/` 生成的内容（配置契约、边界协议、authoring schema、editor manifest、recipes）是对 JobDesk 的**公共接口**：

- `confflow v4 contract --json`、`confflow v4 boundary --json` 的字节是被钉住的；改动前后用
  `tools/refactor/contract_digests.py` 对比，用 `tools/refactor/json_paths_diff.py` 看具体差了哪些路径。
- 边界协议的 fixture 由 `scripts/generate_p0_boundary_fixtures.py` 生成（生产者拥有），JobDesk 通过同步脚本原样拷贝，
  不得手改；`tests/v4/test_p0_boundary.py` 会检查入库的 fixture 与现在生成的一致。
- 删除或重命名 wire 成员属于不兼容变更：JobDesk 必须同步升级，并在提交信息里写明。

## 6. 跨仓库测试

`tests/v4/jobdesk_integration.py` 把 JobDesk-v2 的源码当作消费者来读取真实的 producer 字节：

- 用环境变量 `JOBDESK_V2_SRC=<JobDesk-v2 的 src 目录>` 指向检出；缺失时这些测试跳过（标记 `cross_repo`）。
- 检出的提交必须等于 `EXPECTED_JOBDESK_SHA`，且与 `.github/workflows/jobdesk-contract.yml` 的 `JOBDESK_COMPAT_SHA` 一致
  （`tests/test_release_workflow.py` 校验两处相同）。需要对着别的提交开发时设 `JOBDESK_V2_ALLOW_ANY_SHA=1`。
- 升级 JobDesk 侧提交后，按顺序更新两处 SHA 并重新跑跨仓测试。

## 7. 架构护栏

- `tests/v4/test_architecture_boundaries.py`：包依赖规则、禁止导入前缀、"已删除的模块必须不存在"、导入闭包检查。
- `scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py`：静态扫描与度量，CI 外也可手动运行。
- 删除代码时要同时删掉只守护它的测试（以及它们在护栏清单中的条目），并把已删除的模块名加入 `REMOVED_LEGACY_MODULES`。

## 8. 发布

离线发布流水线（`release.yml`、离线 wheelhouse、`release/` 锁文件、
`scripts/install_release_wheel.py`、`scripts/generate_dependency_locks.py`）
已退役。服务器安装与更新见 [`USAGE.md`](USAGE.md)（`git clone` /
`git pull && pip install .`）；构建溯源（`setup.py` 写入
`confflow/__build__.py` 的提交号）与安装溯源读取
（`confflow/install_provenance.py`）保留，详见 [`RELEASE.md`](RELEASE.md)。

## 9. 重构记录

2026 年的架构瘦身（删除 calc/blocks/legacy CLI、边界瘦身、输入简化、refine 对称映射）的计划、逐卡验收和检查点在
历史归档：`docs/refactor/`（`PLAN.md`、`LOG.md`、`baseline/` 等）已随 architecture-diet-1 归档（见 `docs/ARCHITECTURE_DIET_1.md` 与 `docs/archive_manifests/architecture_diet_1.json`，按归档提交 SHA/blob 定位）。
等价性证据 `paths_equivalence/` 已迁至 `tests/fixtures/paths_equivalence/`。验收协议在 `docs/process/ACCEPTANCE.md`，通用规则在 `docs/process/RULES.md`，验收工具在 `tools/refactor/` 与并列的 `tools/refactor-acc/`。这些文件是历史记录与证据，普通开发不需要改它们。
