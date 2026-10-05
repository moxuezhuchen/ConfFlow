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

# LOG-APPEND.md — 只新增段 draft（原 LOG 不重写）

> 用法：原 LOG 保持原样不动，将下面整段追加到 LOG 末尾。时间以根验收时间为准；未知写 pending。

---

## [FIX1A 执行澄清追加段] BASE 55b42a3fe6265bbfc67a78ffda50389b991bec9e（fix/confgen-1a，/tmp/fix1a-exec）

- 链：B1 `1e7718c0` → A1 `ad678f4e`（patch `b0fa685e…0601`）→ A2 `97143eb2`（patch `4ebe1c30…`）→ A3 `062a2859`（patch `de76a29c…`）→ CAP-SIG `e10e161d`（patch `b540c9d6…`）→ A4a `f98a50a4` → A4b `47e913a1` → A4c `6182d46` → lazy `ed4be360` → A4d `26476f5d`（patch `b991544c…`）→ A5 `71b742af`（patch `86900a53…`）→ AG1 `65bfd890`（patch `a683e0c0…`）→ AG2 `d5e096cc`（patch `3baab871…`）→ CAP-cleanup `00a798e0`（patch `93f338de…`）→ 集成 `55b42a3fe`。各 formal/stage ROOT-ACCEPTANCE 见 FIX1A-EXECUTION.md §0 外部路径。
- 补卡口径：CAP-SIG 保签名（`run.should_cancel` 公开不变，`engine.py:860` 实测）；`build_typed_graph` 公开可省 registry、私有必传（`planner.py:765/786` 实测，A4a 裁决）；A3 fallback 可达故移动非删除（`ring/component.py:168`、`ring/scope.py:99,139`）；A4d 两模块 PEP562 恰 3 名（`engine.py:87-145`、`__init__.py:79-…`，`_INHERITED_LAZY_NAMES` 三元）+ 通用 ValueError 保源标识；AG1 深 freeze/thaw + 空 paths guard 恢复；AG2 legacy 忽略 custom / generic 跟随 custom + `as_kernel_target` 唯一归 `kernel_records`（去本轮误加 facade 导出）；CAP-cleanup scope 仅报告写出（`run_sharded.py:210-213`），根最终 scope `tests/v4/test_confgen_[pv]*.py` 精确 93 基线，全产品 tests 仍执行，old B1 不动，tool7 待根重算。
- 证据红线：A2 不得称全部 93 重算（仅 keys roundtrip 83/93 域）；A3 旧报告 capture 仅 82/93（11 未覆盖见 stage JSON）；完整 93 / fresh TS1 / contracts 由根最终验收，本轮 stage 接受 ≠ full/golden；A6 与全量 pending。
- 疏漏留痕：A4d root v2 漏 AST 新测 formal 拒停→v3；A5 partial mypy 漏 Any→`cast(float)`；AG1 v1 共享 mutable→深冻、v2 空 paths 托辞根驳回→guard 恢复；AG2 空/改 registry 差异 303 probe 定案 + 新测漏 `schema_version` 后补；根错路径日志保留；CAP 执行器漏 card/proto 清理根补齐。
- 根待填：A6 sha / G13/G14 policy / collect delta / full pass-skip-collect-wall / golden / 最终 93 名字节等 / TS1-contracts / tool7 digest，均为 pending（见 FIX1A-EXECUTION.md §4）。

---

最终补修记录见FIX1A-EXECUTION.md §5；根组合必要检查491 passed，完整mypy228无问题；4859 collect，全量/golden未运行，待填实际结果。

A6正式SHA912e545807f81688236d1214a571e9a6e45ce312；根逐字核对通过。

### 首轮最终验收失败后补修

见FIX1A-EXECUTION.md §6：冻结context测试夹具、继承报告字节恢复、失败诊断保留三项完成阶段验收。根独立1+5回归与28工具测试通过，4实际报告逐字等于B1；首轮失败无manifest，不计验收通过。新测试纯black排版AST一致；待第二轮全量/golden。

## 最终根验收（run-id fix1a-root-final-2；失败/补修历史保留，只新增本段）

- 被测 `/tmp/fix1a-publish` 分支 `fix/confgen-1a` HEAD `681fac8ea569a04c6aa3f88fc6ec7138dffd6b6c`；`/tmp/fix1a-exec` 为已验证 detached 树，本次绝不动其文件。
- 实际结果（根现场文件，不重跑）：4862 节点，4852 passed / 10 skipped，wall 265s（run.log）；93 报告逐字等于 B1；golden 所有 TS1（default/flexible/rigid）与 contract 5 摘要 ok（golden.json/log）；旧节点结果不变、skip 集合不变（ROOT-NODE-AND-BINDING-VERIFY.json）。
- 104 文件 checkpoint `/tmp/fix1a-final-root-run-v2/baseline`：布局复制 B1；engine_reports/out/current-jd-contract 取最终实际采集；collect.json 从最终 out 节点键排序生成；TS1/contract 复制 B1 字节（根 fresh golden 已现场验证相同，不伪称保存 fresh 临时输出）。发布清单 `docs/confgen-fix/checkpoints/A6/MANIFEST.json` 记录外部绝对路径、每文件 sha、tested SHA681fac8、证据路径、run-id、CF/JDcurrent/JDpin 角色；大 golden 不提交。
- 本最终提交额外差异仅发布文档（A6 MANIFEST + FIX1A-EXECUTION.md + LOG.md）；capture 绑定仍是 `/tmp/fix1a-exec`，不动，不虚称直接绑定新发布提交。
- 数据备注：rpdd.xyz 为 rpdd.gjf 输入的 CREST xTB1 输出，能量默认 Hartree；CREST 只有服务器有，本机不安装/计算；Q9 缺数据只影响 R7，不阻塞其他任务。

## F1 — ring 基准 fixture（test-only）

- 分支 `fix/confgen-1r`，BASE `213b3060a86abc7b036f6ec54bb28a1486d26419`（`git rev-parse HEAD` 已核对一致）。
- 源（只读，未写入源目录）：`/mnt/c/dft/wcm/rpdd.gjf` sha256
  `4929b48cbc4c3a2fa39788f3b68e792471b34c8b36b40f4441b2692c14e5b8a3`（1439 字节）；
  `/mnt/c/dft/wcm/rpdd.xyz` sha256
  `2078aca84ff293021553e3046920b9ceb787f7d04193d1f9efdd433705a1c7b1`（4368 字节）。
- 生产核对（实测）：`confflow/core/gaussian_input.py:43 parse_gaussian_input`
  （文本版 `:53 parse_gaussian_input_text`）得 22 原子、charge 0 mult 1，元素序列
  `C C O O C C O O H H H C C C C H C H C H H H`；
  `confflow/core/io.py:181 read_xyz_file`（`iter_xyz_frames :71`）得目标 3 帧 × 22 原子、
  元素序列一致、坐标有限；`crest_conformers.xyz` 与源逐字节一致；comment 三行原文逐字保留
  （`        -44.25350192` / `        -44.25032157` / `        -44.24850601`），单位按用户确认
  理解为 Hartree，原字节不转换。
- 用户确认：`rpdd.xyz` 为 `rpdd.gjf` 输入的 CREST xTB1 输出；CREST 仅服务器可用，本机不安装、不计算。
- 行号偏差：卡片 L7–L28 实为笔误，L7 为 `0 1`，实际坐标行为 L8–L29（22 行），本卡按实测 L8–L29 转写；
  行尾 CR 去除、前后空白按生产解析器 `strip()` 规范化为 LF 行，行内数值文本逐字不变。
- 科学口径：2 环盆 3 构象为 PLAN §0.3 科学预期占位，F1 不宣称已做 CP 召回；Q9 缺失仅影响 R7；
  脯氨酸/吡喃糖身份待指定；环己烷/甲基环己烷由测试辅助函数生成。
- 容器/来源核对通过；collect 用 `tools/refactor/test_inventory.py collect` 得 4862 节点，
  与 `/tmp/fix1a-final-root-run-v2/baseline/collect.json` 逐字节一致；未跑全量/TS1/golden。
- 新增：`tests/fixtures/confgen/ring/rpdd/{input_ts_fragment.xyz,crest_conformers.xyz,README.md}`、
  `docs/confgen-fix/checkpoints/F1/MANIFEST.json`，另本段 LOG 追加。

## R1 — ring puckering CP coordinates + 38 canonical forms (logic, stage-accepted)

- Commit `70b0d4415134ea3230b0af6b7a81dabf90c43651` on `fix/confgen-1r` (parent F1 `5e455aa0df433cebe8ff5cba0b590b6ce0eb626f`).
- New modules only, unreferenced by production: `confflow/science/confgen/ring/puckering.py` + `tests/v4/test_confgen_ring_puckering.py` (17 tests passed; ruff/black/mypy pass; collect 4862 → 4879, +17/−0, golden unchanged asserted).
- Root note: CP math (Q/θ/φ, traversal shift/reverse covariance, 38 forms = 2C+6B+6TB+12E+12H) verified at stage; full/golden pending at milestone.

## R2 — ring rigid planar units + local orientation locks (logic, stage-accepted)

- Commit `38b5d4a6443544741bfc9870ac4ad0838adf7c0e` (parent R1).
- New module only: `ring/rigid_units.py` + `test_confgen_ring_rigid_units.py` (27 tests: 18 frozen + 9 root; static pass; collect 4879 → 4906, +27/−0, golden unchanged).
- Root note: planar-unit perception + sp2 audit (no chirality lock on topological sp2) staged; full/golden pending.

## R3 — CP-constrained realization (frozen, stage-accepted)

- Commit `f3c2eeffd06c83a159f5c7d4c21d1418661f31ab` (parent R2): `ring/realization.py` + `tolerances.py` (`phase_defined_q_min` frozen at Q/r̄ ≥ 0.05 per Q13/V17, verify-only afterwards) + `test_confgen_ring_v2_realization.py` (30 passed; ruff/black/mypy pass; frozen patch sha256 `575b5d088e8713dd7134cfeccf52cb99dee274b4a28fccc524845f40dcf84d87`; collect 4906 → 4936, +30/−0).
- Root note: multi-start solver + audit-stamped `failed_geometry` staged; full/golden pending.

## K0 — clash diagnostic: no rescue, K1/K2 to FIX-2 (test-only, stage-accepted)

- Commit `d964ba7e75999b6a7eb4fe48350b19dcdeae925f` (base R3 `f3c2ee…`): `test_confgen_k0_clash_diagnostic.py` + `checkpoints/K0/{COUNTS,MANIFEST,REPORT}.md`.
- Result: 18 ring targets / 84 rows (S1 12 + S2 24 + S3 48), ring-clash-rejected 0, rescued 0/0/0, B-leaf realized 12/24/46 (S3 2 leaf-clash, non-rescue). Evidence `/tmp/fix1r-k0-root-independent-counts.json` + formal `checkpoints/K0/COUNTS.json` (`total_rescuable: 0`, `total_rows: 84`).
- Decision per PLAN: K1/K2 move to FIX-2; R4 proceeds. No production/threshold change.

## R4 — enumeration/perception to CP forms + StateKey `{form,index,anchor,direction}` (science, stage-accepted)

- Commit `27e046c138090e68d54b2d0a711b392c7af0a3d8`; frozen patch sha256 `52f9425141f306bec77298dc1bbeeeae759319c99af9ed8138c4a62491776ed7`; root v3 218 + v4 11 tests passed, collect 4951, evidence `/tmp/fix1r-r4-v4-output/ROOT-STAGE-ACCEPTANCE.json` (process note: initial cmp omitted untracked files, corrected intent-to-add exact cmp).
- Checkpoint (small only, in-repo `checkpoints/R4/MANIFEST.json` + `DIFF.md`; bulk stays at `/tmp/fix1r-r4-root-checkpoint`, MANIFEST self `83353f9ba5c8957f7def878fc694cb38796876db0d71c25a58be74d6aa136168`, 26/26 sha recomputed OK): 26 entries = contract.json `08d51085…` + contract.full.json `2fe92022…` + boundary.full.json `ee811b99…` + collect.txt + DIFF-SUMMARY.json + 21 engine reports.
- 93 classification (`mapping_93.csv` 93 rows; `DIFF-SUMMARY.json` 13 entries; `contract-diff-paths.json` 4 paths): 21 ring-allowed, actual 13 changed / 8 same; 72 non-ring unchanged. Field direction: ring basis template-product → CP-form product; `state_key.rings` `{template,torsions}` → `{form,index}`; evidence/certificate digests follow. contract/full: 4 paths only (forms + hashes); boundary `ee811b99…` unchanged; TS1 default/flexible/rigid unchanged at stage. Final full/golden pending, not final green. R5/R4G add only illegal-input/diagnostic regressions.

## Q9 — reference ensembles frozen (fixture-only, 181 CREST refs)

- Commit `ccf167f227d21322d1a251591440e75f48123538` (20 files, +5375): thf 1 + cyclohexene 2 + methylcyclohexane 6 + N-acetyl-L-proline-methyl-ester 14 + beta-D-glucopyranose 155 + rpdd 3 = 181 (178 Q9 CREST + 3 rpdd). Verified by frame-parse on formal tree.
- Method on record (READMEs): server CREST, authorized; default iMTD-GC/GFN1-xTB, neutral charge (`Molecular charge : 0`), energies Hartree (Eh); unverifiable fields explicitly marked, no fabrication. Q9 arrival unblocks R7 only.

## R5 — `distorted_input` into report (report-only, stage-accepted)

- Commit `10c7ffe0caf504be50228684a9b64e484b92fdeb`; frozen patch sha256 `a113bd59a8d933ed60efb43d84452ab1324c920cd15f303ad516cbbee8284408`; root 15 tests passed, 16 existing nodes, 23 captured reports 0 different, collect 4951 → 4966 (+15/−0), `Golden-Changed: []`. Evidence `/tmp/fix1r-r5-v2-output/ROOT-REVIEW.json` + `checkpoints/R5/{MANIFEST,DIFF}.json`.
- Scope: report/diagnostics only (Q6 report-only); existing golden unchanged; final full/golden pending.

## R4G — unsupported-topology scope guards fail-closed (science guard, stage-accepted)

- Commit `45ff3578129c39dc95572a276dafc304ad80a45c` (parent R5 `10c7ffe…`); root exact patch cmp true, combined 30 tests passed, 16 existing nodes, 23 reports byte-equal. Evidence `/tmp/fix1r-r4g-final-output/ROOT-ACCEPTANCE.json` + `checkpoints/R4G/{MANIFEST,DIFF}.json`.
- Scope: fail-closed guards + new illegal-input regressions; existing golden unchanged; final full/golden pending.

## R6 — retire Cartesian template realization (delete, stage-accepted)

- Commit `4e5e36a51da3a4e554aed1d2afee27bcea52ac66` (parent R4G); root patch cmp true, 187 tests, 16 capture nodes 23 equal, collect 4979. Evidence `/tmp/fix1r-r6-final-output/ROOT-STAGE-ACCEPTANCE.json` (+ `REPORT.md`/`MAPPING.md`).
- Deleted only `ring/templates.py` + Cartesian paths; removed tests exactly `test_bond_length_nominal_documented` + `test_templates_have_exact_closure_and_distinct_states`; format-only changes AST-equivalent (`/tmp/fix1r-r6-final-format-equivalence.json`). Golden equals R5 checkpoint; final full/golden pending.

## R7 — benchmark published CP seeds vs reference ensembles (verify-only, stage-accepted)

- Commit `71cf6e241ebaccf0c4c914f616463fe4c77e1217` (base R6 `4e5e36a…`, 5 files +1460): `tools/ring_benchmark.py` + `tools/match_after_opt.py` (user-side only) + `test_confgen_ring_benchmark.py` (11 tests) + `checkpoints/R7/{MANIFEST.json,DIFF.md}`.
- Root: final 11 tests passed, mch 38 published 6/6 full audits, 3-case R6 re-verify (rpdd-crest-default 3/3, mch-crest-explicit 6/6, synchx-crest-default 2/2, all audit-complete 0 gaps), 195 external artifacts hashed, `R7-v2.patch` cmp consistent (sha256 `75a32dcd9d29c78ee1ec63c1b8917fb28948616274b69c44f2099355ea1ff32a`), collect 4990 = 4979+11 (collect-only). Evidence `/tmp/fix1r-r7-final-output/ROOT-STAGE-ACCEPTANCE.json` + `/tmp/fix1r-r7-v3-root-all-systems/ROOT-SUMMARY.json` (24 manifests verified).
- Mode rule: explicit + crest-first must be declared for any 100% claim. Required recalls pass: rpdd 3/3, synthetic-cyclohexane 2/2, mch crest-first explicit 6/6. Measured recalls: mch crest-first default 5/6 / explicit 6/6; mch original-input default 3/6 / explicit 4/6 (evidence `/tmp/fix1r-r7-tool-v3-output/SUMMARY.json`). Limits: glucose crest-default 113/155 → explicit 127/155; the remaining 28 is a measured CP-recall gap, not individually attributed (no relaxation, no 100% claim on sugar); other original-input shortfalls are likewise recorded as measured recall shortfalls without causal attribution. `phase_defined_q_min` 0.05 verify-only (never retuned here). Post-seed RMSD recall/redundancy is user-side counting only (`match_after_opt.py`), not performed here. Full milestone full/golden running at root, pending — this LOG does not claim final green.

## FIX-1R 最终根独立验收

固定测试提交 `71cf6e241ebaccf0c4c914f616463fe4c77e1217`；后续收尾提交 `046c3eac01f1393d7f89edc0b9b5b6a83995827f` 仅文档，生产/测试/工具/脚本 diff 为空。根独立全量 4980 passed / 10 skipped（4990 节点，282s），capture complete、93 报告；golden true，added/different/missing 均空，TS1 default/rigid/flexible 和五契约摘要全部 ok。ruff/mypy（230 文件）/black（32 改动文件）通过，验收工具 25 passed。证据 `/tmp/fix1r-final-root-run/ROOT-ACCEPTANCE.json`；104 文件基线及逐文件 SHA 见 checkpoints/FIX1R-FINAL/MANIFEST.json。

R7 glucose 127/155 的 28 项召回缺口与未做种子优化后 RMSD 验证如实保留；不调整科学门槛。根摘要脚本首次错误地把行清单 collect.json 当 JSON 解析，未写出验收结论即修正。最终验收通过，发布/合并尚待实际执行，其他里程碑仍未完成。

## D1 — sigma-image retry (science, preview only — NOT submitted)

- 基点 `9cf3f7c`（`fix/confgen-1d` D0）；预演分支 `refactor/fix1d-d1-proto`，正式线未动；冻结后精确清理回基点干净。证据 `/tmp/fix1d-d1-output/`（v2 件；v1 卡/提示词/补丁保留作未完成证据）。
- 改动：`coordination/realization.py`（`initial_coordinates` 仅改 solver 起点，终审锚点仍为真正 parent；`None` 旧路径逐字保持）+ `coordination/stage.py`（`retry_solve`：首遍全 input-only 后按 ordinal 对失败 target 重试；完整原子 witness 经只读 `validate_full_witness` 的 `authority_valid` 门控；π 映 binding placement，坐标 σ 像仅初始化；顺序 source ordinal、witness 固定顺序，去重，预算 `1+|σ像|`；成功加 `retry_start=sigma_image:<id>`，失败 hook None；显式 rigid 不暗换 flexible；D2/D3 未实现 fail-closed）+ 新测试 17 节点 + `checkpoints/D1/{MANIFEST.json,DIFF.md}`。`symmetry`/容差/fixtures/registry/kernel 未碰；`component.py` 未需改动。
- 根纠错：默认路径曾无条件写 `sigma_start_applied: False`（改原记录字节），已修正为显式起点才加字段，双树完整 outcome 字典 sha256 `7eaee718…` 字节一致；共享构建器甲基实验曾使科学基线 30→19 叶，已恢复单甲基体系，控制流多 witness 夹具隔离（`_witness_variants` 双位点作用），未放宽容差；轴向交换 witness 因改变位点作用无效，改用 x<->-y 反射（不同位点作用，构成真实多源顺序证据）。
- 验证：新 17 passed；旧版基点跑新测试 14 failed/3 passed（失败 proof）；D0 20 passed；旧 coord witness/authority 子集 5 passed；ruff/mypy（生产两文件）/black 通过；TS1 三后端与基线字节一致（该 spec 无 runtime 完整 witness，诚实零变化；恢复证明由合成反射体系承担：000017<-000018，rigid 000017+000024）。
- Golden 方向：26 候选恢复方向（首遍成功不变，仅 FAILED→REALIZED 新增）；67 + contract/boundary 不变；collect/out +17 节点精确对账，不称逐字不变。
- 报告缺口：`sigma_skip_diagnosis` 仅进程内测试 pin，非运行时报告；按根排期决议（`/tmp/fix1d-reporting-deferred-note.md`）留 D3 随 `start_statistics` 接入，D3 allowlist 须含诊断/报告变更；本卡不宣称报告完成。D2 需 D1 全σ后第二 retry 槽位（本卡无 D2 码）。

## D2 — sibling-start retry (science, preview only — NOT submitted)

- 基点 `51246ce`（`fix/confgen-1d` D1+D0.2）；预演树 `/tmp/fix1d-d2-proto`，正式线未动；冻结后精确清理回基点干净（只删本卡新建文件，不 `git clean`）。证据 `/tmp/fix1d-d2-output/`（00 基线冻结、01 callback、02 sigma 隔离、03 sibling、04 TS1 差分、05 D0、D2.patch；既有失败日志与 `ts1-default-new.json` 原样保留）。
- 改动：`coordination/stage.py` 唯一生产文件（`retry_phases()->("sigma","sibling")`，`retry_solve_phase` 分发：sigma 沿用旧 `retry_solve` + 永久 `first_pass`，sibling 用冻结 `phase_snapshot` 审计源含 sigma 恢复者、按 source ordinal 至多 3、无 witness 要求；各源坐标仅 `initial_coordinates` 起点，锚点仍真 parent；显式 rigid 不转 flexible；成功 `retry_start="sibling:<id>"`，失败 None；取消在候选间传播；`_realize_with_sibling` 与 `_realize_with_sigma` 共享 `_realize_from_start` 同求解路径；`sibling_skip_diagnosis` 仅进程内 pin，D3 才接报告）+ `tests/v4/test_confgen_retry_sibling.py` 新 16 节点 + `tests/v4/test_confgen_retry_sigma.py` 窄 helper 隔离（`_SigmaOnlyStage` + `_run` 显式 sigma-only + `_MockSigmaStage` sigma-only，17 节点 ID 与断言全保留）+ `checkpoints/D2/{MANIFEST.json,DIFF.md}`。`realization.py` 复用 D1 接口未改；kernel/model/fixtures/schema/symmetry 未碰；无 Molassembler。
- 根纠错三件：callback 自身抛取消变 `UnboundLocalError`（两 loop 改函数内无条件 lazy import 置回调前，取消重抛、非取消 policy 保持；根 probe 通过 exit 0）；D2 使 6/17 D1 失败（sibling 恢复方向）→ helper 隔离后 17/17（`engine.run()` 显式 stage 会进无 retry 转发的 `LegacyStageAdapter`，故 `_run` 走公开 `run_kernel` + 同 `project_v3`，已核实）；sibling 字节证明从重复运行补强为 sigma-only 真 input-only 基线对照（29 叶 id/坐标/metadata + 记录 status/reason 全 exact，+1 sibling 叶）。
- 验证：sibling 新 16 passed；sigma 旧 17 passed（断言未弱化）；D0 batch 31 passed；ruff/black/mypy 三文件通过；TS1 default 实测 3→4 REALIZED（仅 `coordination:000001` FAILED→REALIZED，余 11 项 status/reason/evidence 全同，旧 3 叶 coords 全留，+1 新叶）；flexible/rigid TS1 与 93 报告 capture 未重跑（根 full/golden 拥有；候选方向沿根 preallowlist 26 恢复-only/67 不变）。
- 报告缺口：`sibling_skip_diagnosis` 非运行时报告，D3 随 `start_statistics` 接入（D3 allowlist 须含）；本卡不宣称报告完成，不混入 D3。
## L-D3 — generic retry telemetry logic seam (logic, preview only — NOT submitted)

- 基点 `51246ced0c190c2325a1a72b4e59f9965bcd645c`（`fix/confgen-1d`）；预演分支 `refactor/fix1d-ld3-proto`，正式线未动；冻结后精确清理回基点干净。证据 `/tmp/fix1d-ld3-output/`（卡片/正式提示词/原始日志/报告/冻结补丁；`ROOT-REVIEW.md` 与 root 探针为根侧文件，原样保留）。
- 改动（与 D2 生产文件不相交，`coordination/*`、registry/accounting/schema/容差未碰）：`kernel_records.py` 新增 frozen `TelemetryError`/`TelemetryRow`/`BoundTelemetryEvent`/`RetryResult(+success_index)`；`model.py` 新增 `GenerationStage.report_statistics` 缺省 `None` 及 `retry_solve_phase` 返回注解；`engine.py` 当次 `_RunState` 局部 ledger、真实 solve 门计数（input 先入账、wrapper 解包选定行）、`finish()` 只读快照按 component 分组、`scope.component_statistics` 纯 additive 合并；`wire_v3.py` 仅 `LegacyStageAdapter.report_statistics` 必要转发（旧缺省 `None`，无新 dispatch）；新测试 13 节点 + `checkpoints/L-D3/{MANIFEST.json,DIFF.md}`。
- 根冻结裁决落实：`RetryResult(outcome optional, telemetry frozen tuple)` 明确识别，legacy plain outcome/`None` 与旧 `retry_solve` 直接 API 保持；kernel 只验证合法性与绑定身份，不解释 sigma/sibling，不聚合轴名；`geometry-success` 与 `accepted` 分列；payload 失败 start（含 outcome `None`）留遥测，`accepted` 仅归属返回成功 start（无成功 payload 则 generic legacy 事件）；`component_statistics` 仅非空 hook 返回才写入，默认无键，现有 golden 逐字节不变；取消沿现有语义无 partial；遥测错误经独立 `TelemetryError` 显式传播。
- 根 blocker 修复（本修订）：删除全局-last `_mark_retry_accepted`，改为 solve 返回时同步记录 own span + selected 行、终审按索引 marking 并重验身份与 `attempts>0/solve_successes>0`（后代事件不可 steals，尾随零尝试诊断永不 accepted；`success_index` 缺省时唯一成功行校验，歧义 loud 拒绝）；input 调用门改为进入前先入账 `attempt=1`、返回后更新 success，抛异常保留 `success=0` 且 `stage_error` 记录语义逐字不变。
- 验证：新 13 passed；D0.2 batch 31 passed；collect 5051 = 5038 + 13 精确对账；默认报告 sha256 `fafd89af…d10af2` 与基点逐字节一致；root 探针三场景（own-parent/trailing-diag/identity-mismatch）通过；ruff/mypy/black（白名单 5/4/5 文件）通过。不跑全量整仓/TS1/完整 golden，root 里程碑最终统一。

## D3 — coordination start_statistics + skip wiring (science, preview only — NOT submitted)

- 基点 `29f1ac2`（`fix/confgen-1d` D2+L-D3）；预演分支 `refactor/fix1d-d3-proto`，正式线未动；证据 `/tmp/fix1d-d3-output/`（00 允变清单、before/after TS1、D3-CARD、正式提示词、REPORT、原始日志、D3.patch；LEGACY-FIX 证据保留）。
- 改动：`coordination/stage.py` 唯一生产文件（`report_statistics` 只读聚合 `component_statistics.coordination`；`retry_solve_phase` sigma/sibling 仪器化单次求解+`RetryResult`+`success_index`，legacy override 探测后走旧 plain 路径单次求解不编数，`report_statistics` 见 legacy `retry` 相返 `None`；`retry_solve` 直接 API plain 不变；`retry_phases` 不变）+ 新测试 10 节点 + `checkpoints/D3/{MANIFEST.json,DIFF.md}`。kernel/model/wire/accounting/registry/容差/witness/fixtures 未碰；未新增 `retry_statistics.py`。
- 根纠错：sigma 相曾绕过 `self.retry_solve` 致旧 `_MockSigmaStage.first_pass_seen` 为 None（5 失败）；已改 MRO 探测保留旧调用语义、不 double-solve、不塞假值；legacy 统计不可观测不以 sigma 0 伪装（返 `None`），内置统计完整。
- 验证：新 10 passed；旧 77 passed（batch31+sigma17+sibling16+telemetry13）；collect 5077=5067+10；ruff/mypy/black（2 白名单文件，`--workers 1`）通过；TS1 default 单采 after（4 叶/8 失败不变，input 12/3/3、sigma 0、sibling 25/1/1、skip 17=9 sigma no-witness+8 sibling exhausted），strip `component_statistics` 后全部 target/leaves/certificate/report 字节等于冻结 before，contract/boundary 不变。

## F-ledger — 记账缺口定点分析+修复预演（proto preview — NOT submitted）

- 基点 `c9bbc9f8cc0ae94875aaaab180c8488f7ea81f25`（`fix/confgen-1d`）；预演树 `/tmp/fix1d-ledger-proto`（detached），正式线未动；证据 `/tmp/fix1d-ledger-output/`。冻结后精确恢复回父干净（只 `checkout` 本卡改动文件 + 删本卡新建文件，不过 `git clean`/rm 整树）。
- 反例：真实根 `..._test_suppression_positive_control_real_stage_fewer_attempts__0.json` input 20 vs ledger 18/12（差 2 issued 后抑制）；spy kernel 直调 3 issued→ledger 2/1（差 1）复现，调用链 `run_level→_expand_target（先抑制后 input 门）→_retry_level（再抑制）→_supersede_failure` 删旧失败。
- 改动（白名单）：`accounting.py`（`attempt_ledger_counts(..., issued_history=None)`，真 skip vs issued 后抑制分离，条件 `suppressed_after_issue` 键）+ `engine.py`（`_issued_history` 通用集，input 门 + retry 非 None 入账，`finish()` 传入并按真 skip 重算 `realization_attempts`）+ 新测试 5 节点 + `checkpoints/F-ledger/{DESIGN,CARD,DIFF,MANIFEST}.md`；`kernel_records.py`/组件实现/容差/fixtures 未动。
- 验证：新 5 passed；旧实现跑新测试 2 failed/3 passed；`retry_statistics` 10 passed；core 抑制子集 10 passed；C2 阳性 1 passed；真实差分 issued 18→20/skipped 12→10/after 2/without 3→5/attempts 18→20，leaf 15/3/12 与 cert/count/terminal 全同；无重试 sha `6f64f6a49…` 逐字相同；ruff/mypy/black 通过。
## L1-C0 — intent 包化制卡预演（move, executor self-check；根验收另行）

- 基点 CF `c7728447162ee76877e38966f96e0417e779f39c`；工作树 `/tmp/l1-c0-proto`
  分支 `refactor/l1-c0-proto`；输出 `/tmp/l1-c0-output`。AGENTS 缺失，
  以 `docs/process/RULES.md` + `docs/process/ACCEPTANCE.md` 为准。JD 未动。
- 白名单实测：删 `confflow/producer/intent.py`（2000 行）；
  改 `confflow/producer/intent/compiler.py`（2000 行，18+/18- 仅 import 层级，
  17 处）；新 `intent/__init__.py`（47 行，兼容 facade，四公开同 `__all__` 顺序
  + 实际私有 `_recipe_cards_to_role_cards`，同对象 `is`，`__module__` 保持旧名，
  root 裁决机械兼容元数据）；新 `tests/v4/test_l1_intent_package_compat.py`
  （117 行，6 用例）；新检查点 `docs/confgen-fix/checkpoints/L1-C0/`。
  `tools/architecture_policy.py` 与 `tests/v4/test_architecture_policy.py`
  零改（目录 rglob 已覆盖，无 `intent.py` 字面规则）。
- 自检（非根验收）：新 6 passed；producer_intent+regressions 64；
  boundary_coverage+legacy_paths 290；checkpoints+terminal 75；
  architecture_policy 333；boundaries+g13g14 30。
  原/新真实编译产物 + catalog 逐字节等（sha256 `33240346…`）；
  contract/boundary 四摘要原新一致；collect 4990 -> 4996（+6/−0）。
  ruff/black/mypy 按单文件口径通过（compiler.py 保留基线 1056 长行原样）。
- 冻结：`git diff --binary HEAD` 见 `/tmp/l1-c0-output/L1-C0-proto.diff`
  （new file mode x2 起，rename 相似度 98%；sha 见同目录 `SHA256SUMS`）。
  未提交/未推送/未合并/未打 tag；共享树与冻结基线未动。

## L1-C1 — intent 通用 bindings/resources 机械拆分预演（move, executor self-check；根验收另行）

- 基点正式 C0 `78ab3d5773c304a11f71f06775b9ef136cf80700`；工作树 `/tmp/l1-c1-proto`
  分支 `refactor/l1-c1-proto`；输出 `/tmp/l1-c1-output`。三位置前检不存在通过；
  不基于未冻结活跃 proto。PLAN L1 + L1-DESIGN-v2：v1 主循环改 resolve 属 C2，
  C1 不引入 dispatch/registry/descriptor，不做能力 science 行为。
- 白名单实测：改 `confflow/producer/intent/compiler.py`（1666 行，10+/344-，
  仅新轻 imports + 删 8 已搬定义）；新 `intent/common.py`（48 行，仅
  `IntentCompilationError`+`_fail`，真实依赖见 MANIFEST，root 审阅边界）、
  `intent/bindings.py`（129 行，`_registry_input_ports`+`_auto_bindings`）、
  `intent/resources.py`（236 行，`_wire_resources`+`_wire_scheduler`+
  `_apply_machine_profile`+`_apply_checkpoints`通用编排，Gaussian 细节留 G1）；
  新 `tests/v4/test_l1_intent_helpers_compat.py`（156 行，5 用例）；
  新检查点 `docs/confgen-fix/checkpoints/L1-C1/`。`__init__.py`、
  `tools/architecture_policy.py`、`tests/v4/test_architecture_policy.py`、
  其他生产文件/oldtests 零改；policy scope 冲突无（333+7+25 通过），未扩大
  science 豁免；helper 无回 import compiler。
- 自检（非根验收）：新 5 + 旧 compat 6（11）；producer_intent 等 130；
  authoring_boundary 等 433；policy 333+7+25。原/新真实编译产物 + catalog
  逐字节等（`6ed711d1…`）；contract/boundary 全量与摘要原新一致；collect
  4996 -> 5001（+5/−0）；AST 8 符号等价；ruff/black/mypy 按单文件口径通过；
  旧导出 `is` 同一、无新 wrapper、签名等、`__module__`/pickle 兼容。
- 冻结：`git diff --binary HEAD` 见 `/tmp/l1-c1-output/L1-C1-proto.diff`
  （含 new/modes；sha 见同目录 `SHA256SUMS`）。未提交/未推送/未合并/未打 tag；
  共享树与冻结基线未动。
