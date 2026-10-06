# 验收工具用法（L0.1 迁入）

> 本目录由 L0.1 从 `docs/refactor/tools/` 整体迁入（原 `docs/refactor/PLAN.md` B0.1 第 2–9 步的用法说明逐字搬运，见下）。
> Plan2 的分片 runner、`weights.json` 与 `noeditable/` 位于并列目录 `tools/refactor-acc/`（用法见文末补充）。

## 各工具用法（逐字自 B0.1 第 2–9 步）

  2. `ts1_engine.py --backend {default|rigid|flexible} --out FILE`：
     - 按 `tests/v4/test_confgen_v3_integration.py::TestCoordinationBoundary::test_ts1_sigma_workflow_boundary`（L1156-1236@d5a40ae）构造 TS1 输入：从 `tests/fixtures/confgen/coordination/ts1/` 读取 `typed_topology.json`、`ts1_original.xyz`、`expected_coordination_benchmark.json`、`coordination_constraints.json`；拓扑键按该测试 L1174-1189 的去重规则构造（共 131 条，脚本中断言）；`metal_center: 47`、`index_base: 1`、`shapes: ["octahedral"]`、`treatment: "enumerate"`、`donor_configuration`、`constraints`（FORBIDDEN_TRANS）、`site_group.generators = [sigma_site_permutation_0based]`。
     - 与该测试的差异只有两处：`site_group.provenance` 写固定字符串 `"expected_sigma_witness.json"`（避免绝对路径进入报告）；不写 `budgets`（使用默认值）。`--backend default` 时**不写** `backend` 键（engine 默认值，`coordination/stage.py:352`@d5a40ae），`rigid` / `flexible` 时写入对应值。
     - 执行：`ConfgenModelV3.model_validate(block).scientific_native()` → `model.build_context(structure, native)` → `ConfgenEngine(allow_preserve_input=True).run(ctx)`（与 `execution/confgen_executor.py:216-223`@d5a40ae 相同）。
     - 输出 JSON（`sort_keys=True`、`indent=1`）：`backend`、`status_counts`（按 `TerminalStatus.value` 计数）、`targets`（每条 `target_id/axis/ordinal/status/reason/parent_target_id` + `evidence_sha256` = evidence 规范 JSON 的 sha256）、`leaves`（每个 `state_key`、`atoms`、`coords_sha256` = `repr()` 坐标的 sha256）、`report_sha256`、`report`（`run.report_json()` 全文）。
     - 不在脚本或基线中写入任何预期计数（不预设 TS1 结果）。
  3. `capture_engine_reports.py`：pytest 插件。包装 `confflow.science.confgen.engine.ConfgenEngine.run`，每次调用写一个文件 `<清洗后的 nodeid>__<调用序号>.json`，内容为 `report`、`targets`（同上字段）、`leaves`（同上）。在 `pytest_runtest_setup` 中记录 nodeid 并把序号重置为 0。输出目录由环境变量 `CAP_OUT` 指定。运行方式：
     `cd $CF && CAP_OUT=$BASE/engine_reports PYTHONPATH=$TOOLS python3 -m pytest -q -o addopts="" -p capture_engine_reports -p no:cacheprovider tests/v4/test_confgen_*.py`
  4. `contract_digests.py --cf DIR [--jd-src DIR] --out FILE`：在子进程中（cwd=DIR）计算 `v4cli.main(["contract","--json"])` 标准输出的 sha256、`v4cli.main(["boundary","--json"])` 标准输出的 sha256、`generate_contract_bytes(producer_version=confflow.__version__)` 的 sha256、contract 内的 `contract_digest` 字段；提供 `--jd-src` 时再用该 JD 源码的 `parse_v4_contract_bytes(...)` 计算 `contract_key`。同时把 contract 和 boundary 的完整 JSON 存为 `--out` 旁边的 `contract.full.json` 和 `boundary.full.json`（供 Phase 3 做路径 diff）。
  5. `test_inventory.py`：
     - `collect --repo cf|jd --out FILE`：运行 `pytest --collect-only -q -o addopts=""`，写排序后的 nodeid 列表。CF 设 `JOBDESK_V2_SRC=$JDPIN/src`；JD 一律通过 `run_jd_tests.sh` 运行（§2.3）。
     - `run --repo cf|jd --out FILE`：完整运行并读 junit xml，写 `{nodeid: passed|failed|error|skipped}`。
     - `diff --prev A --cur B --declared N [--allowed-files f1,f2,...]`：输出新增节点、删除节点，检查删除数 == N，删除节点全部落在 `--allowed-files` 中；不满足时以非零退出。
  6. `golden_check.py --base DIR --cf DIR [--jd-src DIR] [--checkpoint FILE] [--removed-nodes FILE]`：重新生成 TS1 三份、engine 报告和 contract 摘要，与基线（或 `--checkpoint` 指定的 contract 检查点）逐字节比较。engine 报告缺失时，只有对应 nodeid 出现在 `--removed-nodes` 中才允许。新增报告一律报告为差异。
  7. `json_paths_diff.py OLD NEW`：输出 JSON pointer 级别的新增、删除、修改路径列表。
  8. `reachability.py --cf DIR`：从 `confflow.main`、`confflow.v4cli`、`confflow.cli`、`confflow.control_worker`、`confflow.worker_attempt` 出发，按 AST import 闭包计算可达模块（相对 import 要解析；`importlib.import_module("字面量")` 计入；各包 `_LAZY_EXPORTS` 表中的目标计入，前提是有模块以 `from <包> import <名字>` 使用它）。输出不可达模块列表。（R1.1/R1.2 已删除 `confflow.fixture_agent`、`confflow.remote.worker`，入口表同步移除。）
  9. `diff_guard.py --repo DIR --base SHA --head SHA --type TYPE --whitelist FILE`：供验收方使用，规则见 `ACCEPTANCE.md` §2。

## Plan2 分片 runner 补充（tools/refactor-acc/）

- `run_sharded.py --cf DIR --out FILE [--shards 12] [--weights FILE] [--jdpin DIR] [--capture-engine-reports DIR --run-id ID] [-- extra pytest args]`：分片并行运行并合并结果；捕获模式在全部校验通过后最后写 manifest。
- 权重：把 `tools/refactor-acc/weights.json` **复制**到仓库外的本次运行目录，再把副本传给 `--weights`；运行器把实测秒数写回该副本，已提交的种子不被覆盖。
- `run_sharded.py` 自动注入 `PYTHONPATH=<tools/refactor-acc>/noeditable:<CF 根目录>` 并设置 `JOBDESK_V2_SRC` 与 `QT_QPA_PLATFORM=offscreen`，不要破坏或覆盖。
- checker 与 runner 的调用位置：`tools/refactor/golden_check.py`（`--engine-capture DIR --run-id ID` 复用捕获）与 `tools/refactor-acc/run_sharded.py`；详细协议（原 `docs/refactor/plan2/CAPTURE_PIPELINE.md` 与 `docs/refactor/plan2/ACCEPTANCE_FAST.md`，已随 architecture-diet-1 归档）通过 `docs/ARCHITECTURE_DIET_1.md` 与 `docs/archive_manifests/architecture_diet_1.json` 定位，用 `git show 671c3fb14663e9a6f4ccf9880228c59d28fbb762:docs/refactor/plan2/CAPTURE_PIPELINE.md`（及 `…:docs/refactor/plan2/ACCEPTANCE_FAST.md`）自归档提交读取；这些是 git 对象定位，不再是工作树链接。
