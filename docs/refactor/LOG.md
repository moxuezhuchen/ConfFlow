# 重构验收日志

按 `ACCEPTANCE.md` §6 的格式追加，最新的在最后。不得修改或删除已有条目。

---

## 2026-10-02 J0a — 升级（未提交，执行方按 G9 停止）

- 仓库/分支/提交：JobDesk-v2 `refactor/diet`，改动未提交（父 9beeaf2）
- 类型：logic；白名单检查：ok（只改 `src/jobdesk_v2/application/editor/manifest.py`，只移植 92d48f1 的前 3 个 hunk 和 docstring，没有 `step_selector`/`{id}`）
- 静态检查：ruff ok、ruff format ok；**mypy 失败 9 处，全部是 `Library stubs not installed for "yaml"`**；未修改的 9beeaf2 上同样是这 9 处，所以是环境问题，不是 J0a 引起的。JD `pyproject.toml:34` 的 dev extras 声明了 `types-PyYAML>=6.0.12`（CI 会安装），本机没有安装。CF `mypy confflow` 通过。
- 测试：collect 2411（声明 -0 +0）；2396 passed / 8 failed / 7 skipped；8 个失败与 J0a 卡预期的 8 项逐条相同
- golden：不适用（B0.1 尚未建立）
- 结论依据：方案 §2.1 写的"环境已安装 mypy"不完整，缺 `types-PyYAML`，需要用户决定环境处理方式；Q0b 仍未答复
- 对用户的问题：(1) 是否在本机安装 `types-PyYAML`（与 JD CI 一致）；(2) Q0b
- 产物：/tmp/refactor-acc/J0a/

## 2026-10-02 J0a — 通过（重新验收）

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` 5847bc7（父 9beeaf2）
- 执行者：Opus 子代理（Claude Code）完成改动；用户确认后由验收方按原改动提交。J0b 起执行模型改为 CodeBuddy `glm-5.3-flash`。
- 环境：按用户决定安装 `types-PyYAML 6.0.12.20260906`（`pip install --break-system-packages`，与其他包同在 `/usr/local/lib/python3.12/dist-packages`），已写入 PLAN §2.1。
- 类型：logic；白名单检查：ok（仅 `src/jobdesk_v2/application/editor/manifest.py`，+12/-7，与 92d48f1 的前三个 hunk 及 docstring 逐字一致）
- 静态检查：ruff ok；ruff format ok；mypy ok（166 个源文件无问题）
- 测试（`run_jd_tests.sh --cf exec-cf@10e8c69`）：collect 2411（-0 +0）；2396 passed / 8 failed / 7 skipped；失败集合与卡片预期的 8 项逐条相同
- 测试削弱检查：ok（没有改动测试）
- golden：不适用（B0.1 尚未建立）
- 结论依据：满足用户给定的通过条件（mypy 通过，且失败集合等于卡片预期的 8 项）
- 产物：/tmp/refactor-acc/J0a/

## 2026-10-02 推送授权

- 用户授权推送 JD `refactor/diet` 和 CF `docs/refactor-plan` 两个工作分支，不动 `master`/`main`。

## 2026-10-02 J0b — 发起失败（不计为退回）

- 执行模型：CodeBuddy `glm-5.3-flash`
- 情况：`--allowedTools "... Bash"` 在非交互模式下没有授予 shell 权限，执行模型的第一条只读命令就被拒绝；它按 G9 停止，没有改动任何文件（`exec-jd` 仍为 5847bc7，工作树干净）。
- 原因：验收方的调用参数写错，应为 `Bash(*)`。已用最小命令实测：`Bash(*)` 可以执行；`--disallowedTools` 仍能拦截 `unshare`。PLAN §2.8 已更正。
- 处理：以更正后的参数重新发起 J0b。这一次不计入"退回"次数。
- 产物：/tmp/refactor-acc/J0b/codebuddy.json
- 补记：第二次发起时，交接提示词中的 CF HEAD 写错（写成 25e0e19），验收方在执行模型改动文件之前终止了该次运行；`exec-jd` 仍为 5847bc7，工作树干净。这一次也不计入退回次数。

## 2026-10-02 J0b — 第三次发起未执行；CodeBuddy 权限参数实测（不计为退回）

- 第三次发起（`--allowedTools "Read Edit Write Grep Glob Bash(*)"`）时，所有 Bash 调用仍被拒绝。执行模型按 G9 停止，没有改动文件（`exec-jd` 仍为 5847bc7，工作树干净）。
- 验收方的更正：此前记录的"`Bash(*)` 可以执行、`unshare` 被拦截"是错误结论。当时只看了模型的文字回答；模型实际是通过 Read 读取 `.git` 的 reflog 得到 HEAD 和提交标题，并没有执行命令。今后一律以原始 `function_call_result` 为准。
- 实测结论（逐条以 `function_call_result` 判断）：
  - `--allowedTools` 写多项（空格或逗号分隔）时，Bash 全部被拒；只写 `"Bash(*)"` 时 Bash 可用。
  - `--disallowedTools` 写成一个空格分隔的字符串时完全不生效（实测 `unshare --help`、`mount --version`、`pip --version`、`git push --dry-run` 都被执行；`--dry-run` 没有推送，远端 `refactor/diet` 仍为 5847bc7）；每条规则单独一个参数时，`unshare`、`mount`、`pip`、`sudo` 被拒。
  - 前缀规则可以被绕过：`git -C <路径> push` 不被 `Bash(git push:*)` 拦截；`bash -c 'unshare --help'` 被执行。
  - `glm-5.3-flash` 在 Bash 被拒时，有两次直接编造了命令输出（HEAD 写成 `f3a2c1b`、`f4a2b19e`，真实值为 5847bc7）。
- PLAN §2.8 和 ACCEPTANCE §4.2 已按实测结果更新。
- 待用户决定：在禁止规则不是安全边界、且执行模型会编造输出的前提下，是否继续用 `glm-5.3-flash` 执行 J0b。

## 2026-10-02 执行模型变更

- 用户决定：执行模型改为 Sonnet 5.5，以 Claude Code 子代理方式由验收方直接调用。CodeBuddy `glm-5.3-flash` 不再使用。PLAN §2.8 已更新。

## 2026-10-02 J0b — 第一次发起停在测试步骤（工具缺陷，不计为退回）

- 执行模型：Sonnet 5.5 子代理。步骤 1–4 完成（4 个测试文件已移植、fixture 已重新 vendor、ruff/format/mypy 通过），改动保留在 `exec-jd`，未提交。
- 停止原因：`run_jd_tests.sh` 在 `exec-cf` 中没有执行权限。仓库设置了 `core.fileMode=false`，验收方提交脚本时 x 位没有进入索引（`100644`）。执行模型按 G9 停止，没有用 `bash <脚本>` 绕过。
- 处理：验收方用 `git update-index --chmod=+x` 修正（`100755`），并同步到 `exec-cf`；让同一执行模型从步骤 5 继续。

## 2026-10-02 J0b — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` ee0eabb（父 5847bc7）；执行者：Sonnet 5.5 子代理
- 类型：test-only；白名单检查：ok（4 个测试文件 + `tests/fixtures/p0_boundary/PROVENANCE.json`、`boundary_protocol.json`）
- 逐字核对：4 个测试文件与 92d48f1 中的版本完全相同；4 个 fixture 与 `exec-cf/docs/internal/fixtures/p0_boundary/` 逐字节相同；PROVENANCE `source_commit` = 38a0c16（与 d5a40ae 只差 `docs/refactor/`）
- 静态检查（验收方重跑）：ruff ok；ruff format ok（275）；mypy ok（166）
- 测试（验收方重跑，`run_jd_tests.sh --cf exec-cf@ee39d62`）：2405 passed / 0 failed / 7 skipped；collect 2411 → 2412；删除 `tests/gui/test_confflow_v4_cards.py::TestCapabilityCardsRender::test_all_eleven_cards_render`；新增 `…::test_all_twelve_cards_render`、`tests/application/test_confflow_v4_e2e.py::TestDoubleDigestAndRecipe::test_canonical_bytes_render_floats_as_jcs`，与声明一致
- 测试削弱检查：没有新增 skip/xfail；被修改的 6 条断言（配方数 11→12、`canonical_sha256`→`jcs_sha256`）均来自卡片规定的逐字移植
- 失败集合：J0a 预期的 8 项全部消失，与卡片预言一致；JD 全绿
- 远端：`origin/refactor/diet` 仍为 5847bc7（执行模型没有 push）
- 提交信息更正（不改写历史，在此记录）：Verification 中 "golden: unchanged" 应为 "n/a（B0.1 尚未建立）"；"failed set == previous checkpoint: yes" 应为 "8 → 0，与卡片预期一致"
- 结论：通过。B0.1 的前置条件（J0b 通过、JD 全绿）满足。
- 产物：/tmp/refactor-acc/J0b/

## 2026-10-02 B0.1 — 执行中断（不计为退回）

- Sonnet 5.5 子代理第一次执行时因 API 额度上限（HTTP 429）退出；恢复后，用户因额度不足中断，验收方停止了该子代理。没有提交。
- `exec-cf` 中留下 7 个未验收的未跟踪文件：`docs/refactor/tools/{env.sh,ts1_engine.py,capture_engine_reports.py,contract_digests.py,golden_check.py,json_paths_diff.py,test_inventory.py}`；`baseline/` 为空。
- 用户决定：后续由用户把交接提示词粘贴给另一个模型执行，验收方继续负责验收。B0.1 的交接提示词见 `docs/refactor/handoff/B0.1.md`。

## 2026-10-02 B0.1 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` eec4e80（父 3f8aff3）；执行者：外部模型（用户转交提示词 `handoff/B0.1.md`，提交署名为 glm-5.3-flash）
- 类型：baseline；白名单检查：ok。103 个新增文件，全部在 `docs/refactor/tools/` 与 `docs/refactor/baseline/` 下；没有修改任何已有文件；`run_jd_tests.sh`、PLAN、ACCEPTANCE、LOG 未动
- 静态检查（验收方重跑）：`ruff check .` ok；`black --check docs/refactor/tools` ok；`mypy confflow` ok（257）
- 可重复性（验收方重跑，全部与基线逐字节一致）：
  - TS1 三种 backend 的输出与 `baseline/ts1/*.json` 相同。engine 状态计数：default `{failed_numerical: 9, published_leaf: 3}`、rigid `{failed_numerical: 11, published_leaf: 1}`、flexible `{failed_numerical: 9, published_leaf: 3}`（与写方案时独立测得的值相同）
  - engine 报告：重新捕获 83 份，`diff -r` 与 `baseline/engine_reports/` 无差异
  - `contract.json`、`contract.full.json`、`boundary.full.json` 相同；contract/boundary/contract_digest 摘要与写方案时独立测得的值相同（9fdc5ccd…、8fe36f86…、eab1dd86…；JD contract_key `…/workflow:38a546f61706/manifest:7886bc067ec8/recipes:bc63fc178492`）
  - collect：CF 4535、JD 2412，清单与 `baseline/inventory/` 逐字节相同
  - 两仓全量测试重跑，outcomes 与基线完全相同：CF 4523 passed / 0 failed / 12 skipped，JD 2405 passed / 0 failed / 7 skipped
  - CF 的 7 个 `cross_repo` 测试全部在 passed 中（没有被跳过）。12 个 skipped 是环境相关（wheel 构建、numba、root 权限等），已列入 README 的"已知 skipped"
  - README 中 93 条 sha256 与文件逐一核对，全部一致
- 工具抽查：
  - `diff_guard.py`：J0b 用 test-only + 完整白名单通过；缩小白名单报 R1；J0a 作为 test-only 报"改了生产代码"；J0a 作为 delete 报"新增逻辑"；B0.1 自身作为 baseline 通过
  - `test_inventory.py diff`：能正确列出新增/删除节点，并检查声明数量与允许的文件范围
  - `golden_check.py`：对篡改后的基线副本（改 rigid.json 的计数、改动并删除各一份报告）返回退出码 1，指出 `ts1.rigid: DIFF`、`different`、`added`
- 审计：`git ls-remote` 显示 JD `refactor/diet` 仍为 5847bc7，CF `refactor/diet` 远端不存在（均未推送）
- 结论：通过。TS1、engine 报告、contract 和两仓测试清单的基线已冻结。
- 发现的工具缺陷（不影响本卡通过，已登记，需要在对应卡之前修复）：
  1. `test_inventory.py diff` 只要有新增节点就一律判 FAIL，没有"声明新增 N 个"的开关；会声明新增测试的卡（logic、test-only 等）只能由验收方人工核对新增清单。
  2. `reachability.py` 把有可达子模块的包（`confflow.domain`、`confflow.science`、`confflow.workflow` 等 16 个）也列为不可达。包的 `__init__` 会被隐式导入，这是误报，方向危险；叶子模块名单（55 个）与验收方独立计算的结果一致，仅 `shared.orca_blocks` 一处差异。C5.2 和 C5.5 之前必须修复，在那之前只信叶子模块并人工核对。
  3. 提交信息的署名行为 `Co-Authored-By: glm-5.3-flash`，没有邮箱，不规范，不改写历史。
- 产物：/tmp/refactor-acc/B0.1/

## 2026-10-02 B0.2 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` 096af5f（父 eec4e80）；执行者：外部模型
- 类型：baseline（工具修复）；白名单检查：ok，只改 `docs/refactor/tools/reachability.py`（+11/-1）和 `test_inventory.py`（+4/-3）；`baseline/` 与其他工具未动
- 静态检查（验收方重跑）：ruff ok；black ok；mypy ok（257）
- 验收方重跑：
  - `reachability.py`：输出 62 个模块，与验收方独立计算的不可达名单（62 个）逐个相同；没有残留的有可达后代的包；`workflow.step_naming`、`workflow.export`、`science.torsion`、`science.confgen.engine`、`execution.confgen_executor`、`producer.contract`、`shared.orca_blocks`、`shared.defaults` 均不在名单中
  - `test_inventory.py diff`（用 J0b 前后的清单，删 1 增 2）：`--declared 1 --declared-added 2` 退出 0；`--declared-added 1` 退出 1；不带 `--declared-added` 退出 1（与修复前相同）；两份相同清单 `--declared 0` 退出 0
- golden：未重跑。本卡没有修改任何生成工具和基线文件；工具文件之外的树与 eec4e80 相同
- 远端：未推送（`origin/refactor/diet` 仍不存在于 CF；JD 仍为 5847bc7）
- 结论：通过。B0.1 验收时登记的两个工具缺陷已关闭。
- 遗留：提交署名为 `glm-5.3-flash <noreply@example.com>`，格式已符合要求。

## 2026-10-02 C1.1 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` fc6b821（父 096af5f）；执行者：外部模型
- 类型：ci；白名单检查：ok（`jobdesk-contract.yml`、删除 `paired-jobdesk-compatibility.yml`、`tests/test_release_workflow.py`）
- diff 核对：pin 值 `JOBDESK_COMPAT_SHA` 与 `EXPECTED_JOBDESK_SHA` 均为 9beeaf2，未动；触发器恰为 push / pull_request / workflow_dispatch；`test_release_workflow.py` 只去掉常量并把循环元组改为单项，断言原样；没有新增 skip/xfail
- 静态检查（验收方重跑）：ruff / black / mypy 全部 ok
- 验收环境：独立工作树 `acc-cf`（固定在 fc6b821）；golden_check 用 `--cf` 指定；测试清单用 `--cf` 指定
- 测试（验收方重跑）：4535 项，4523 passed / 0 failed / 12 skipped；清单与结果与基线逐项相同（没有删除、新增或结果变化）
- golden：TS1 三种 backend、83 份 engine 报告、contract 五项摘要全部不变
- 结论：通过
- 验收方失误记录：第一次验收时没有给 `test_inventory.py` 传 `--cf`，工具默认测试了 `exec-cf`（当时已包含 C1.2），得到 4521 passed 的无效结果；发现后在显式指定工作树的情况下重跑，上面的数字来自重跑。今后验收命令一律显式传 `--cf`。

## 2026-10-02 C1.2 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` 8eda87f（父 fc6b821）；执行者：外部模型
- 类型：delete；白名单检查：ok（5 个文件：`_retired_runtime.py`（删除）、`workflow/__init__.py`、`confflow/__init__.py`、`tests/v4/test_architecture_boundaries.py`、`tests/test_core.py`）
- diff 核对：没有新增逻辑；`workflow/__init__.py` 只删 9 个 lazy export 和 docstring 中关于 stub 的半句；`confflow/__init__.py` 只删 `run_workflow` 一项；`tests/test_core.py` 只删 PLAN 声明的两行断言（`hasattr(confflow, "run_workflow")`、`"run_workflow" in confflow.__all__`），其余断言原样；`test_architecture_boundaries.py` 只删两个声明的测试方法和一句注释
- 静态检查（验收方重跑）：ruff ok；black ok；mypy ok（256）
- 行为核对：`from confflow import run_workflow` 抛 ImportError；`confflow.cli.run_workflow` 仍为 `formal_v4_runner`；仓库内无 `_retired_runtime`/`RetiredRuntimeError` 引用
- 测试（验收方重跑，工作树 `acc2-cf` = 8eda87f）：collect 4535 → 4533；4521 passed / 0 failed / 12 skipped；与基线相比恰好少 2 个节点（`TestRetiredRuntimeBoundary::test_retirement_stubs_fail_closed`、`TestRuntimeIsolation::test_importing_workflow_package_stays_lazy`），无新增、无结果变化；`test_inventory.py diff --declared 2 --allowed-files tests/v4/test_architecture_boundaries.py` 退出 0
- golden：TS1 三种 backend、83 份 engine 报告、contract 五项摘要全部不变（contract 与 boundary 字节不变，符合 Phase 1 要求）
- 提交信息：已声明 Removed-Tests: 2、Removed-Assertions: 2 与 Behavior-Change，与实际一致
- 结论：通过
