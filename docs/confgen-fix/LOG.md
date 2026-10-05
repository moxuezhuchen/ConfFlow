# LOG（L0 集成）

## L0.0 — 冻结 Plan2 集成基线（baseline）

- 状态：执行器完成两轮采集与冻结；**根独立验收未完成**。
- 基点：CF `ce0dd996af057e63904370e0da294caf3ae3ca2e`（/tmp/l0-baseline-cf detached）；
  JD 当前产品 `5c90732da78f096de7c77924eaaa64e105dbd221`（/tmp/l0-baseline-jd）；
  CF 跨仓测试专用 JD pin `9de35d66f8047e0e7f71ab004e584bbdf0b6c8db`
  （/opt/cf-worktrees/jd-pin，`tests/v4/jobdesk_integration.py EXPECTED_JOBDESK_SHA` 要求）。
- 两轮全量（run_sharded --shards 12 --capture-engine-reports，run-id
  `l0-baseline-1`/`l0-baseline-2`，--jdpin 与 JOBDESK_V2_SRC 均绑旧 pin）：
  collect 4509（两轮逐字节相同、无重复）；**4499 passed / 10 skipped / 0 failed / 0 error**
  （两轮 out 节点结果逐字一致，含 10-skip 集合）；engine reports 93 份（两轮文件名与内容
  逐字节相同）；capture manifest status=complete 且 verify_capture 零 problems。
- 契约与 TS1：contract/boundary 摘要（jd-pin 绑定）与 TS1 default/rigid/flexible 两轮逐字节
  相同；另记录当前 JD（5c90732d）契约摘要于 baseline/current-jd-contract/，仅作当前产品
  契约基准，不参与 golden_check 主流程（将来 golden 复用 capture 时 --jd-src 必须绑旧 pin）。
- 权重：种子 SHA256 `7995630aa5967a3e65cc3595a46f6eacbb6f99bf6de4e194585e2dce3dacba33`
  （154 项），每轮独立副本 weights-1/2.json（复制前后一致），runner 更新仅写副本。
- 测试失败集合：**空**。
- 过程记录（含纠错）：
  1. 首轮停止（/tmp/l0-baseline-run/STOP-REPORT.md）：原提示词 --jdpin 误绑当前 JD master，
     shard1 两个跨仓节点因 `EXPECTED_JOBDESK_SHA=9de35d66…` 在 setup 阶段 ERROR。根裁决：
     属根规划错误（混淆 JD 主线与 CF 测试专用 pin），引入三角色 jd-pin；该报告中
     "4502/5/2" 为**推算值**（非成功聚合的全量实测），特此注明。
  2. 第二次停止（DEVIATION-REPORT.md）：结果计数 4499/10 与提示词预期 4504/5 不符。
     根裁决：本环境正确预期为 4499/10——Plan2 原始证据确有 4504/5，差异恰为 5 个
     `tests/test_fixture_agent.py` installed_fixture 节点（本环境 `/usr/bin/confflow-fixture-agent`
     缺失，`_installed_command` 按设计 skip；sys.executable=/usr/bin/python3，命令实际位于
     /usr/local/bin）。未安装依赖、未设绕过、未改环境凑数。
  3. 未启用 `JOBDESK_V2_ALLOW_ANY_SHA`；未修改 `EXPECTED_JOBDESK_SHA`；未改源码/测试/容差。
- 覆盖限制：10 个 skip（5 fixture-agent 部署 + wheel 构建/权限类/cross-repo 条件项）不在本
  基线覆盖内，其证据由后续集成验收单独提供。
- 证据根：`/tmp/l0-baseline-run-v2/`（MANIFEST.json、baseline/、attempt-1/、attempt-2/、
  logs/、RULING-appendix.md、DEVIATION-REPORT.md 原件）。
- 基线根独立哈希验证（本集成执行，不虚称基线全量独立重跑）：
  已按 `MANIFEST.json:baseline_files` 逐项重算 `/tmp/l0-baseline-run-v2/baseline` 104 文件
  sha256，一致；`MANIFEST.json` 本体取自
  `/tmp/l0-baseline-run-v2/proposed-docs/checkpoints/L0.0/MANIFEST.json` 原样采纳，
  其 `commands` 中旧工具路径（`docs/refactor/tools…`）保持历史事实，不改已冻结记录。
  未在本树独立重跑基线全量（全量统一留根）。

## L0 组合集成（refactor/l0-integration）

- 开工 HEAD `061af56ad7ae5707a1fe9ff5652805351aefe481` 且干净；分支 `refactor/l0-integration`；
  main/master 未改；不 push/tag/amend。
- 来源卡真实 SHA（原卡）：
  L0.1 `e323e595e7234a6ed80d170ff483f040c0c9fb26`、
  L0.2 `671c3fb14663e9a6f4ccf9880228c59d28fbb762`、
  L0.3 `db022bab71fbbbcd00b4e23998dfe131ed6599c6`、
  L0.4a `5c05c8756fff4be348aa1470f6e536a82a31e88a`、
  L0.4b `b881eb4ff5a95d79707c61863f563901686c5977`、
  L0.4c `b99305ca6048f8eb7ac6d16618080b2cc40b38c9`、
  L0.4d1 `2d6b54f2888630a72dfb2beda48237be91a3b3fa`、
  L0.4d2 `d79c3dc17375889323839cb5ea8fa1cd69e6ba33`、
  L0.4d3 `f9ed1f33af1c71b2ddadcafcdf349091accb65d5`、
  L0.5 `235d21387a7bdd9e25b0d8a6c01c804367a4c51f`（集成链 `18bde51363b34c9d0c2555d0d17053f4bd9cf32f`）、
  L0.6 `2ce8fb9fc24439bc948fd7ee8c6e0e474dc4d426`（集成链 `681fc78885af9281806ce3593b3dfca8db529c57`）、
  L0.7 `3be8caf768dc56549a7ec0c1edaa7550a270e734`（本集成 `ce62d5c4a17bc3401adaa62b6f100a822a6a0d5e`，冲突解决后 33 文件）、
  PLANv3.3 `a990e51d3d01553cb93758825c9590848d1e3929`（集成链 `061af56ad7ae5707a1fe9ff5652805351aefe481`）。
- 根验收证据：
  collect 精确门 `4509 + 309 - 151 - 3 = 4664`（`node_gate.py ok=true`，集合精确非只计数；
  新增来自 `collect-base-policy.txt` 309 policy 节点，删除为两源清单合并 154）；
  全 tests AST 零跨测试 import、动态零跨测试模块引用；七 KEEP 测试 AST 与 `d79c3dc` 基点一致；
  必要测试 `test_architecture_policy` 309 passed、剩余 7 guard passed、
  `test_v46_production_gates` + `test_elements_drift` 139 passed、
  `test_v4_runtime_cutover` 真实 runner 2 passed、四 paths_equivalence 消费者 88 passed；
  ruff/black 只改动文件通过；mypy 生产四小卡改动文件通过；源码/工具既已通过者不重复无关检查。
  全量/golden 统一留根，本集成不跑。
- 已知错误更正：`test_confgen_paths_typed.py` 自动合并保留 L0.2 新 `tests/fixtures/paths_equivalence`
  路径与 L0.7 `tests/v4/_helpers/repair` 导入；`test_architecture_boundaries.py`/`test_v46_debt.py`
  删除 L0.7 引入的已退休 guard 导入/定义（见 `CONFLICTS.md` 逐 hunk 记录），不恢复旧测试；
  helper 文件 10 个保持 L0.7 源卡字节。
- 输出独占 `/tmp/l0-integration-output`（REPORT/SOURCE-COMMITS/CONFLICTS/added/removed/collect/gate/实际日志）。

## L0-runtime-fixture-fix — 旧 runtime fixture namespace 回退补修

- 基点 `b1167274e30ace45d7f80f1fbb734bba6fe16220`，分支 `refactor/l0-runtime-fixture-fix`，工作树
  `/tmp/l0-runtime-fixture-fix`，输出独占 `/tmp/l0-runtime-fixture-fix-output`；不碰 main/master，
  不 push/tag/amend，不正式提交。
- 证据定性：`/tmp/l0-final-root-run/shard7-diagnose.xml` 与 `.log` 的 `16 failed/806 passed/6 skipped`
  是分片诊断，不是全量汇总；原全量 `run_sharded` 退出 4 且无 manifest，不可复用。
- 真实失败：首个失败 `RT-016 clean` 的 `source_binding` 全部指向 `/tmp/l0-integration` 宿主树；
  共 16 个旧 `test_runtime_rule_clean_and_mutant`（RT-016/017/018/019/020/038/039/040/041/042/043/044/045/046/047/065）
  以同样方式失败。
- 根因：`_materialise` 只写叶文件，16 个旧 `RUNTIME_SCENARIOS` 缺 `confflow` 包根 `__init__.py`，
  fixture `confflow` 退化为 namespace；`scan_runtime` 继承绝对宿主 `PYTHONPATH`，
  宿主 regular `confflow` 遮蔽 fixture，子进程 `source_binding` 指向宿主。原阶段相对
  `PYTHONPATH:.` 在子进程（cwd=fixture）恰指 fixture，所以假环境完整性通过。
- 根疏漏：根验收此前没有覆盖绝对宿主路径场景；policy 来源校验本身未放宽，本补修亦不放宽。
- 补修范围（仅 `tests/v4/test_architecture_policy.py`）：`_materialise` 为 fixture `confflow`
  所有父包目录补空 `__init__.py`，已有文件不覆盖（mutant 明确 init 内容保留）；scenario 语义、
  `source_binding`、SYMLINK 回归、noeditable、fail-closed 原样；不改 tools/policy/runner。
- 新增回归 +1：`test_runtime_fixture_isolated_from_absolute_host_pythonpath`，显式继承宿主绝对
  `PYTHONPATH`（合成宿主 `confflow/remote` 含 lease 污染），用旧 RT-016 clean/mutant fixture 经真实
  `scan_runtime` 证明 clean 不串宿主、mutant 命中 `export_absent` 而非 `source_binding`/ImportError；
  旧逻辑下 clean 报 `export_absent(2)+source_binding(2)+source_binding_path(2)` 失败，新逻辑通过。
- 必要测试（`PYTHONPATH=<本树>/tools/refactor-acc/noeditable:<本树>` 绝对环境）：
  新增回归 + 旧 19 个 `test_runtime_rule_clean_and_mutant` 共 20 passed；
  `runtime_source_binding` 4 passed；ruff/black 通过；collect `309→310`，集成预期 `4664→4665`。
- 未做：全量/golden/整 309 套未重跑，仍待根统一重跑；不称完成。

## L0-ci-scope — 六 unrelated default 扫描 fixture 误触 AP-081 隔离（最小）

- 基点 `86f6dfea4f2ac4788fc74b6173f7a2f8016d4ae1`，分支 `refactor/l0-ci-scope-proto`，工作树
  `/tmp/l0-ci-scope-proto`，输出独占 `/tmp/l0-ci-scope-output`；主仓 main 和 PR 不动；
  不正式提交/push/tag/amend；预演后清回基点干净（不 git clean）。
- 根日志 `/tmp/l0-pr104-python311-fail.log`：`6 failed, 4623 passed, 25 skipped`；
  六失败均为 `tests/v4/test_architecture_policy.py` 无关 default 扫描误触 AP-081
  `require_one`（`expected at least one double file to exist`）：
  trailing_comment / retired_package / relative_import / prefix_boundary / fifteen_patterns / dict_shape。
- 根因：AP-081 `DOUBLE_FILES=["tests/v4/test_v46_cross_repo.py","/opt/jobdesk-v2-v4/..."]`；
  合成 tmp tree 无 `tests/v4/test_v46_cross_repo.py` 且 CI 无绝对 JD 文件，故 `scanned=0` 断言；
  本地真实 JD（绝对文件存在）经 `root/file` 绝对拼接掩盖缺口，属根验收缺口，必须真实记录。
- 白名单仅 `tests/v4/test_architecture_policy.py` 与 `docs/confgen-fix/LOG.md`。
  六测试 default `scan(tmp_path)` 改为 `scan(tmp_path, rule_ids=(其断言规则,))`：
  AP-090×3（trailing_comment/fifteen_patterns/dict_shape）、AP-091×1（retired_package）、
  AP-088×2（relative_import/prefix_boundary）；不改 assert/policy/rules/生产/legacy profile；
  不 skip/bypass/放宽。
- 新增必要回归 +1：`test_l0_isolation_probe_does_not_borrow_host_jd`，monkeypatch 将 AP-081
  files 暂设为纯 fixture 内不存在的相对路径，证明隔离扫描不借主机 JD；同时断言单独
  AP-081 扫描仍拒绝缺失 double（守卫未弱化）。未改真实 JD 目录。
- 复现/验证（外部插件仅六 test 临时 monkeypatch AP-081 files=缺失相对路径，真实源不动）：
  旧版六失败（与 CI 同断言）→ 新版同环境六通过，AP-081 护栏仍拒绝；
  完整 policy 全节点实跑一次 `311 passed`（旧 310 + 新增 1）；ruff/black（--workers 1）必要文件通过；
  测试均 pipefail/tee 全输出并记录真实 pytest 退出码。不跑全量/golden。
- 计数实际变化：policy 模块 `310 → 311`（+1 隔离回归）；CI 六失败在同隔离环境下 `6 failed → 7 passed`（六修复+一新增）。

## B1 — FIX-1A baseline frozen

- L0 PR #104 merged as `1038e4abc705b781aa26f497f777d6b6feb0396e`; candidate `5225cf402a4ba6c81aff50ef633a88341360cf16` has the identical Git tree.
- Two TS1 rounds (three backends) and two engine captures (93 reports) independently matched each other and L0.0 byte for byte. Pin and current JobDesk contracts unchanged.
- Scientific outputs were precollected during CI; freeze occurred after exact merged-tree verification. Root full evidence reused from `/tmp/l0-final-root-run-v3`: 4656 passed, 10 unchanged skips, 4666 nodes, golden true.
- External baseline: `/tmp/fix1a-baseline-final-output/baseline` (104 files); SHA256 manifest: `docs/confgen-fix/checkpoints/B1/MANIFEST.json`. No scientific files added to Git.
- Precollect report authority-path and local-time labels corrected in manifest; minor prose issues did not cause retests. L0 root launch errors (missing --repo, sandbox Gaussian stat restriction, line-list parser) preserved externally and corrected before the successful full/golden run.
- No tags; main stays at the merged L0 SHA while FIX-1A executes on an isolated feature branch.
