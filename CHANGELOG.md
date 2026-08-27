# ConfFlow 更新日志

> **Status update (2026-07-21): releases resumed.**
>
> The archived-snapshot notice below describes the earlier repository state. ConfFlow is active again
> as JobDesk's external chemistry workflow dependency beginning with v1.4.0.

## v2.1.3 (2026-08-27) - Full remediation release

### Changed

- Promoted the full remediation candidate to the 2.1.3 release line while
  preserving the v2.0.0 schema and release inputs as immutable historical
  records.
- Added exact candidate-pair and pre-switch JobDesk contract gates for the
  producer/consumer release boundary.

### Validation

- Release artifacts remain bound to the annotated tag's peeled commit,
  checksums, SBOM, provenance record, and build attestation.

## v2.0.0 (2026-08-11) - Phase F owner-exception release

### Changed

- Removed the optional `confflow-agent` service and its packaging/deployment
  surface. The supported production boundary is the direct control protocol
  worker handoff.
- Declared the companion JobDesk move to a control-only backend as a breaking
  compatibility change under the owner exception. Historical compatibility
  evidence remains archived; this release does not claim a measured
  compatibility-period closeout.

### Validation

- The release workflow derives and verifies the package version from the
  annotated release tag, and publishes provenance, attestation, SBOM, and
  checksums bound to that tag's peeled commit.

## v1.5.1 (2026-08-02) - fixture agent entry release

### Added

- Added the explicit `confflow-fixture-agent` console entrypoint for the
  synthetic, non-compute producer lifecycle fixture used by JobDesk
  launcher-path acceptance.
- Bound fixture lifecycle execution to the invoked console-script identity
  and retained the existing fail-closed control protocol behavior.

### Validation

- Merged the fixture agent entry PR through the normal ConfFlow mainline
  review and CI path before this release preparation.

## v1.4.6 (2026-07-31) — output-manifest path hotfix candidate

### Fixed

- Emit terminal artifact paths in `output_manifest.json` relative to the
  workflow root, using portable POSIX separators.
- Reject terminal artifact paths that escape the workflow root, including
  traversal and symbolic-link escapes.

## v1.4.5 (2026-07-30) — post-M2 acceptance remediation candidate

This candidate closes the post-M2 release/install and producer-consumer
acceptance gaps. It is not a production release until the independent
tag, artifact, and remote-verification gates are authorized and complete.

### Fixed

- Unified the current release and install-provenance test contract at 1.4.5.
- Bound capability executable/python identity to the invoked candidate venv.
- Rewrote staged console-script shebangs before the atomic venv rename.
- Consumed Gaussian/ORCA single-point `e_high` energy without weakening
  missing-energy fail-closed behavior.


Pre-tag, Gate A candidate. Do not promote to production; the formal
`v1.4.4` tag will be cut from a clean tagged checkout as a separate
Gate B build (not back-filling this candidate's wheel digest).

### Added

- **Capability handshake schema v4**:
  - Six artifact fields, including `output_manifest: "output_manifest.json"`.
  - Four stable `content_schema` strings stamped into producer artifacts:
    `confflow.run_summary.v1`, `confflow.workflow_stats.v1`,
    `confflow.workflow_state.v1`, `confflow.output_manifest.v1`.
  - `producer` block reports package/version/build/wheel filename/wheel
    digest/`install_provenance.status`.
  - `executable` block reports the resolved on-disk `confflow` path,
    its own SHA-256, and the `python` interpreter that hosts the venv.
- **Three-layer release / install provenance**:
  1. Wheel-internal build provenance (`confflow.__build__.COMMIT` /
     `DIRTY`) describes only what source built the wheel.
  2. External `SHA256SUMS` next to the wheel in `dist/` is the
     authoritative wheel digest.
  3. The deployer writes `<sys.prefix>/share/confflow/install-provenance.json`
     after verifying against `SHA256SUMS` (and, in production, against
     the approved attestation). The capability probe reads this record
     and never reports a wheel digest derived from the wheel file
     itself. The literal string `"unbound"` is no longer a trusted
     provenance output.
- **Controlled runtime dependency closure**:
  - locks the verified 1.4.4 production venv runtime baseline for
    CPython 3.12/Linux x86_64 with exact hashes;
  - requires an offline binary-only wheelhouse and its SHA256 manifest;
  - records lock/manifest digests and runtime identity in install provenance.
- **Tested-isolation deployer** (`scripts/install_release_wheel.py`):
  - Accepts `--mode candidate` (Gate A, attestation_unverified) and
    `--mode production` (Gate B, requires approved attestation).
  - Refuses to overwrite an existing `--target-venv`.
  - Refuses on checksum mismatch, glob in `SHA256SUMS`, missing
    basename, or build-commit/version drift between `--expected-*` and
    the wheel's `__build__.py`.
  - Pip-installs into a brand-new staging venv under the same parent
    directory, runs `confflow --capabilities --json` once, atomically
    renames staging to target, and rolls back only its own staging
    directory on any failure.
- **DAG legacy migration**: `DAGStep`, `DAGGraph`, and `WorkflowDAG`
  moved from `confflow.workflow.dag` to `confflow.workflow.dag.legacy`.
  Importing the legacy classes through `confflow.workflow.dag` emits
  `DeprecationWarning` for one release cycle. The workflow engine and
  all new code use only `build_step_graph` / `topo_order` from the
  explicit API.

### Changed

- `setup.py` no longer injects `WHEEL_FILENAME` / `WHEEL_SHA256` into
  `confflow/__build__.py`. The wheel is not its own source of truth.
- `confflow.cli._build_capability_payload` now reads install
  provenance from disk; the wheel's `__build__` values appear only in
  `producer.build` (a build-source reference, not an identity claim).
- `confflow.contract` exposes the four content schemas and the six
  artifact filenames as the public wire contract.

### Documentation

- README §ConfFlow↔JobDesk handshake now describes the v4 contract
  (six artifacts, four content schemas, `producer`/`executable`
  blocks, three-layer provenance).
- `docs/RELEASE.md` describes the external `SHA256SUMS` and
  install-provenance workflow.

### Archived

The archived `2026-07-06` reference snapshot section is unchanged;
historical entries are not rewritten.



### Added

- Added `--version` flag: prints the ConfFlow version and exits immediately
  without loading any workflow, input files, or configuration.
- Added `--capabilities` flag: prints the JobDesk ↔ ConfFlow capability-contract
  as JSON (schema version 1) and exits immediately. Reports three capabilities:
  `workflow_state`, `resume`, and `dag` — all `true`.
- Added `_CAPABILITY_SCHEMA_VERSION` and `_CAPABILITY_PAYLOAD` constants to
  `confflow.cli` to formalise the handshake contract.

### Changed

- ConfFlow CLI now parses `--version` and `--capabilities` at the top of `main()`
  before any workflow, input, or configuration loading occurs.
- ConfFlow is now pinned to the JobDesk `>=1.4.1,<2.0` range.

## v1.4.0 (2026-07-21)

### Added

- Added the public `confflow.workflow.dag` module with deterministic
  `build_step_graph` and `topo_order` helpers.
- Added pure-Python DAG regression coverage for chains, fan-out, fan-in,
  duplicate names, unknown predecessors, cycles, disabled steps, and legacy
  linear workflows.

### Changed

- The workflow engine now honors explicit step `inputs` and routes actual
  predecessor outputs while preserving linear behavior when no step declares
  `inputs`.
- Workflow configuration round-tripping now preserves explicitly declared
  `inputs` fields.

## Unreleased (archived snapshot — locked at v1.1.0-archived)

### Changed

- Raised minimum runtime dependency versions:
  - `numpy >=2.2.6`
  - `scipy >=1.15.3`
  - `rdkit >=2026.3.2`
- Raised minimum build/development dependency versions:
  - `setuptools >=82.0.1`
  - `ruff >=0.15.12`

### Added

- Added persistent regression coverage for RDKit/XYZ handling of two-letter element symbols (`Cl`, `Br`, `Al`, `Si`, `Zn`).
- Added RDKit smoke coverage for `MolFromSmiles`, `AddHs`, `EmbedMolecule`, and `RemoveHs`.

### 架构审查收尾

- `pyproject.toml`
  - 移除未使用运行时依赖 `jinja2`
  - 移除未落地的 `viz` optional extra
- `core/types.py` / `shared/config_validation.py` / `config/schema.py` / `core/utils.py`
  - `TypedDict` 切换到标准库 `typing.TypedDict`
  - YAML 结构校验抽到 `shared/config_validation.py`
  - `core.utils` 不再反向依赖 `config.schema`
- `core/chem_validation.py` / `workflow/validation.py`
  - 柔性链校验走稳定的中立服务边界，避免 workflow 直接耦合 `blocks.confgen`
- `calc/step_contract.py` / `calc/run_services.py` / `calc/postprocess.py` / `core/path_policy.py`
  - 固化 calc step 工件合同、输入签名与恢复语义
  - 将 `ChemTaskManager` 的工作目录准备、任务来源、恢复过滤、结果装配拆为内部服务
  - 统一 calc 后处理适配与路径/可执行文件安全策略
- `blocks/confgen/generator.py` / `blocks/refine/rmsd_engine.py` / `calc/db/database.py`
  - `confgen` 改为边生成边写 `search.xyz`
  - `refine` 去重不再 `list(executor.map(...))` 全量物化
  - `results.db` 提供迭代读取并对损坏 `final_coords` 做降级处理
- 文档
  - 删除 `docs/ARCHITECTURE_REVIEW_2026-04-10.md`
  - README / ARCHITECTURE / USAGE 同步到当前实现基线

### 工作流与统计修复

- `workflow/helpers.py` / `workflow/engine.py` / `workflow/step_handlers.py`
  - 按步骤类型收紧标准产物解析：`confgen` 只识别 `search.xyz`，`calc` 只识别 `output.xyz` / `result.xyz`
  - resume 复用同一套工件判定逻辑，避免把 `search.xyz` 误当成 calc 已完成输出
- `calc/components/executor.py` / `workflow/engine.py` / `workflow/step_handlers.py`
  - calc step 的复用条件从“目录里有输出文件”升级为“输出文件存在且 `.config_hash` 与当前任务配置一致”
  - 配置不匹配或缺少哈希时，会丢弃旧的 step 局部工件并重新计算，避免 resume 误复用过期结果
- `workflow/stats.py`
  - `TaskStatsCollector` 优先按每个 `job_name` 的最新记录统计 `results.db` 状态
  - step footer / `workflow_stats.json` 与 `ResultsDB.get_all_results()` 的最新结果视图保持一致

### 输入解析与后处理修复

- `core/gaussian_input.py`
  - 解析 Gaussian 输入时，优先识别“后面紧跟坐标块”的电荷/多重度行，避免把纯数字标题行误判为 `charge multiplicity`
- `blocks/refine/processor.py`
  - `--imag` 过滤后若构象数已归零，会在能量窗口/RMSD 之前直接给出提示并安全返回
- `blocks/confgen/generator.py`
  - `search.xyz` 现保留每个输入构象自己的原子符号顺序，不再强制回写为参考分子的元素顺序
- `config/schema.py` / `workflow/validation.py`
  - `add_bond` / `del_bond` / `no_rotate` / `force_rotate` 支持字符串形式的键定义校验
  - 工作流多输入一致性校验同时接受 `chain` 与 `chains` 两种 YAML 写法

### 文档同步

- README、USAGE、ARCHITECTURE、TESTING、DEVELOPMENT、ASSESSMENT 已更新到当前基线
- 当前本地验证结果：`pytest -q` 全绿（测试文件数与用例数以当前 CI 输出为准）

## Archived (2026-07-06) — final reference snapshot

ConfFlow 已被合并进 [JobDesk](https://github.com/moxuezhuchen/jobdesk)
monorepo（路径 `jobdesk_app/workflow/` + `jobdesk_app/agent/`）。本仓库
保留为只读 archive，不再发版本。

最后版本号：`v1.1.0-archived`（指向 HEAD）。

### 已知吸纳路径

| ConfFlow 模块 | JobDesk 落点 |
| --- | --- |
| `confflow/workflow/`、`confflow/blocks/`、`confflow/core/`、`confflow/cli.py` | `jobdesk_app/workflow/` |
| `confflow/agent/`、`confflow/calc/agent_*` | `jobdesk_app/agent/` |
| `confflow/calc/`（量子化学计算步骤） | 已被 JobDesk 通过 process abstraction 重写 |
| ConfFlow 文档 `docs/USAGE.md` 等 | JobDesk 内 `docs/CONFFLOW_WSL_SINGLE_RUN.md` |

### 关联迁移计划

`.cursor/plans/merge_confflow_into_jobdesk_9fff6a34.plan.md` 是合并
过  程的 32 KB 实施计划文档；在 archive 时被删除，避免日后访问者误
以为尚未实施。整合事实以 JobDesk 仓的 `docs/` 与 `tests/` 为准。

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
