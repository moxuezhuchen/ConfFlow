# ConfFlow 测试指南

怎么跑测试、测试放在哪、有哪些固定的约定和已知的坑。工程规范见 [`DEVELOPMENT.md`](DEVELOPMENT.md)，
`tests/README.md` 是对测试编写准则的补充。

## 1. 运行

```bash
scripts/test.sh                      # 推荐：所有产物（缓存、覆盖率、basetemp）都放进系统临时目录，退出时清理
scripts/test.sh -m integration       # 只跑端到端集成测试
scripts/test.sh tests/v4/test_compiler.py -k binding    # 透传任意 pytest 参数
pytest -q                            # 直接跑（pyproject 的 addopts 是 -v --tb=short，testpaths 是 tests）
pytest --collect-only -q | tail -1   # 当前收集到的测试数（文档不写固定数字）
```

- 覆盖率门禁 `fail_under = 70`（`pyproject.toml`；CI 的 coverage 任务只在非 PR 时执行）。
- PR 门禁是新增/修改生产代码行覆盖率 ≥85%（CI 的 changed-coverage 任务：先跑受影响测试，再用
  `tools/changed_coverage.py --min 85` 检查）。
- 子系统下限：`python tools/subsystem_coverage.py check --xml coverage.xml --baseline tools/coverage_baseline.json`
 （execution / persistence / workflow / confgen_kernel，见 `tools/coverage_baseline.json`）。
- 本地并行需要先 `pip install -e ".[dev]"`（含 pytest-xdist），再 `pytest -q -n auto`；
  `scripts/test.sh` 透传任意 pytest 参数。
- 真实的 Gaussian / ORCA 不在测试范围内；所有计算类测试都用 `tests/v4/fakes/`（`fake_g16.py`、`fake_orca.py`、
  `fake_goat.py`、`fake_irc.py`、`fake_neb.py`）这类行为可控的假可执行文件。注意：`fake_goat` / `fake_irc` /
  `fake_neb` 只是测试替身，不代表产品能力——GOAT/IRC/NEB 已于 R2.2（`383a1f6`）退役，
  `contract --json` 无对应项。

## 2. 目录

| 位置 | 内容 |
| --- | --- |
| `tests/v4/` | V4 的主体：domain、编译器、digest、执行器、持久化、resume、producer 契约/边界/authoring/intent、ConfGen（DG 搜索、拓扑构图、coordination 几何）、refine 与拓扑、架构护栏、跨仓测试（`analysis`、`remote` 目录与多输出机制已退役，见 `architecture/WORKFLOW_V4.md` §§17–21、30） |
| `tests/science/` | `confflow.science` 的图同构映射与帧比较（含按节点数/剪枝数固定的工作量测试）和带氢分子数据 `data/molecules_h.json` |
| `tests/` 根目录 | 入口与基础设施：CLI、控制协议适配器与外部 worker、执行服务与 SQLite 仓库、安装/发布溯源与 wheel 安装器、路径策略、日志、I/O、数据表、键感知、示例工作流、退役 wire 的失败关闭 |
| `tests/fixtures/` | 静态夹具：ConfGen（含 TS1 基准）、控制协议、Gaussian 日志样例 |
| `tests/conftest.py`、`tests/v4/conftest.py`、`tests/v4/_builders.py` | 共享 fixtures、替身与构造器 |

## 3. 标记

- `integration`：端到端测试（`pytest -m integration`）。
- `cross_repo`：消费 JobDesk-v2 检出的测试；没有检出时跳过。设 `JOBDESK_V2_SRC=<JobDesk-v2/src>`；检出的提交必须等于
  `tests/v4/jobdesk_integration.py::EXPECTED_JOBDESK_SHA`，否则测试报错（开发时可设 `JOBDESK_V2_ALLOW_ANY_SHA=1`）。
- 未在 `pyproject.toml` 声明的标记会因 `strict_config = true` 报错。

## 4. 写测试的约定

- 断言可观察的行为和不变量，不要对私有实现细节 patch；需要替身时用小型 fake。
- **不要用耗时断言性能。** 有预算的算法（例如图映射搜索）把节点数、映射数、剪枝数和判定结果当作固定值来断言。
- **构象比较类夹具必须使用带氢的真实分子。** 不带氢的夹具会让末端键没有可测二面角，等价性结论没有意义。
- 比较构象集合时使用只含**真旋转**的 Kabsch RMSD（不含镜像），阈值写明；不要逐原子比较未叠合的坐标。
- 删除代码时同时删除只守护它的测试；"已删除的模块必须不存在"的护栏保留（清单 `REMOVED_LEGACY_MODULES` 位于 `tools/architecture_policy.py`）。
- 用 `tmp_path`，不要自己建临时目录；`importlib.reload` 放进 `try/finally`。
- 子进程类测试（导入闭包、worker、CLI）要给足超时，并避免依赖机器忙闲。

## 5. 已知的负载敏感测试

以下测试涉及真实子进程/并发，在多进程并行（例如把测试分片同时跑）且机器很忙时偶发超时失败，单独运行稳定通过：

- `tests/v4/test_v4_runtime_cutover.py::TestControlWorkerEntersV4::test_worker_runs_v4_end_to_end`
- `tests/v4/test_v4_runtime_cutover.py::TestPlainCliEntersV4::test_plain_cli_runs_v4_application`
- `tests/v4/test_v44_worker.py::TestCancellation::test_cancel_trap_yields_cancelled_without_rescue`
- `tests/v4/test_terminal_arbitration_recheck.py::TestRealControlCancel::test_concurrent_control_cancel_and_completion_stay_consistent`
- `tests/test_cli.py::test_kill_proc_tree_timeout`

如果只有它们失败，单独重跑；单独也失败才是真问题。

## 6. 契约基线（重构证据）

契约摘要检查仍可用：`tools/refactor/contract_digests.py` 计算 contract/boundary 的 sha256，
`tools/refactor/json_paths_diff.py` 给出两份 JSON 的路径级差异，用法见 `tools/refactor/README.md`。
ConfGen 引擎报告的逐字节基线（TS1 与 engine 报告）已随旧引擎删除（2026-10-11），不再有对应检查。

## 7. CI

`.github/workflows/ci.yml`：test-matrix（3.10–3.13，`pip install -e ".[dev]"` 后跑 `pytest -q -n auto`；
black / ruff / `mypy confflow` 门禁只在 3.11 执行）；coverage（非 PR 时跑全量覆盖率并检查子系统下限，
上传 `coverage.xml`）；changed-coverage（PR 时只跑受影响测试，要求新增/修改生产代码行覆盖率 ≥85%）。
`jobdesk-contract.yml` 用固定的 JobDesk 提交
检查契约互通。真实 Gaussian/ORCA 的手动验证不在 CI 内，记录在发布说明里。
