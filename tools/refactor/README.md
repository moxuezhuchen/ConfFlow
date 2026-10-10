# 验收工具用法（L0.1 迁入）

> 2026-10-11：ConfGen 单引擎化后，引擎报告捕获（`capture_engine_reports.py`、`capture_provenance.py`）、TS1 引擎基线（`ts1_engine.py`、`golden_check.py`）已删除；其余工具保留。

> 本目录由 L0.1 从 `docs/refactor/tools/` 整体迁入（原 `docs/refactor/PLAN.md` B0.1 第 2–9 步的用法说明逐字搬运，见下）。
> Plan2 的分片 runner、`weights.json` 与 `noeditable/` 位于并列目录 `tools/refactor-acc/`（用法见文末补充）。

## 各工具用法（逐字自 B0.1 第 2–9 步）

  4. `contract_digests.py --cf DIR [--jd-src DIR] --out FILE`：在子进程中（cwd=DIR）计算 `v4cli.main(["contract","--json"])` 标准输出的 sha256、`v4cli.main(["boundary","--json"])` 标准输出的 sha256、`generate_contract_bytes(producer_version=confflow.__version__)` 的 sha256、contract 内的 `contract_digest` 字段；提供 `--jd-src` 时再用该 JD 源码的 `parse_v4_contract_bytes(...)` 计算 `contract_key`。同时把 contract 和 boundary 的完整 JSON 存为 `--out` 旁边的 `contract.full.json` 和 `boundary.full.json`（供 Phase 3 做路径 diff）。
  5. `test_inventory.py`：
     - `collect --repo cf|jd --out FILE`：运行 `pytest --collect-only -q -o addopts=""`，写排序后的 nodeid 列表。CF 设 `JOBDESK_V2_SRC=$JDPIN/src`；JD 一律通过 `run_jd_tests.sh` 运行（§2.3）。
     - `run --repo cf|jd --out FILE`：完整运行并读 junit xml，写 `{nodeid: passed|failed|error|skipped}`。
     - `diff --prev A --cur B --declared N [--allowed-files f1,f2,...]`：输出新增节点、删除节点，检查删除数 == N，删除节点全部落在 `--allowed-files` 中；不满足时以非零退出。
  7. `json_paths_diff.py OLD NEW`：输出 JSON pointer 级别的新增、删除、修改路径列表。
  8. `reachability.py --cf DIR`：从 `confflow.main`、`confflow.v4cli`、`confflow.cli`、`confflow.control_worker`、`confflow.worker_attempt` 出发，按 AST import 闭包计算可达模块（相对 import 要解析；`importlib.import_module("字面量")` 计入；各包 `_LAZY_EXPORTS` 表中的目标计入，前提是有模块以 `from <包> import <名字>` 使用它）。输出不可达模块列表。（R1.1/R1.2 已删除 `confflow.fixture_agent`、`confflow.remote.worker`，入口表同步移除。）
  9. `diff_guard.py --repo DIR --base SHA --head SHA --type TYPE --whitelist FILE`：供验收方使用，规则见 `ACCEPTANCE.md` §2。

## Plan2 分片 runner 补充（tools/refactor-acc/）

- `run_sharded.py --cf DIR --out FILE [--shards 12] [--weights FILE] [--jdpin DIR] [-- extra pytest args]`：分片并行运行并合并结果。
- 权重：把 `tools/refactor-acc/weights.json` **复制**到仓库外的本次运行目录，再把副本传给 `--weights`；运行器把实测秒数写回该副本，已提交的种子不被覆盖。
- `run_sharded.py` 自动注入 `PYTHONPATH=<tools/refactor-acc>/noeditable:<CF 根目录>` 并设置 `JOBDESK_V2_SRC` 与 `QT_QPA_PLATFORM=offscreen`，不要破坏或覆盖。
