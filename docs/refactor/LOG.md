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

## 2026-10-02 D11 — 完成（由验收方直接执行，不经执行模型）

- 文件：/opt/confjob-coordinator/DECISIONS.md（非 git 仓库）；只在文末追加 D011，未改动已有条目
- sha256 前：f0f6540b40b23e54ae4c36c1458cdd6f055cac069a1bd5bd7446e254726c1136
- sha256 后：aba1dacc78dcc812ab91ea5c23b9ea86f4131e65f2e4c4e953a8fa6bb5263a9e
- 内容依据：用户决定"D010 被取代：confts / confgen / confrefine 从未使用"；D010 要求的"新的可达性与使用审计"以用户的使用说明加静态导入闭包报告为证据

## 2026-10-02 C1.3 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` 445d4f5（父 8eda87f）；执行者：外部模型
- 类型：delete；白名单检查：ok（删除 `workflow/validation.py`、`workflow/helpers.py`、`shared/confgen_params.py` 与对应 3 个测试文件；修改 `workflow/__init__.py`、`tests/v4/test_architecture_boundaries.py`）
- diff 核对：`tests/v4/test_architecture_boundaries.py` 只在 `REMOVED_LEGACY_MODULES` 增加 `confflow.workflow.helpers`、`confflow.workflow.validation` 两项；`workflow/__init__.py` 只剩 shebang、第一段 docstring 与 `from __future__ import annotations`。执行方额外删除了 `__dir__()`，它依赖被删除的 `__all__`，否则 NameError；这是 PLAN 卡片遗漏，已认可并在此记录
- 静态检查（验收方重跑）：ruff / black / mypy（253）ok；仓库内无被删模块引用；保留的 `step_naming`、`export`、`composition` 仍可导入
- 测试（验收方在独立工作树、显式 `--cf` 重跑）：4502 项，4490 passed / 0 failed / 12 skipped；相对基线恰好少 33 个节点（C1.2 的 2 个 + 本卡 31 个：`test_confgen_params_consistency.py` 16、`test_workflow_helpers.py` 5、`test_workflow_validation_extra.py` 10），无新增，无结果变化
- golden：TS1、83 份 engine 报告、contract 五项摘要全部不变
- 结论：通过
- 备注：执行方指出验收提示词里的 grep 模式 `.` 被当作通配符，命中了几处普通英文 "workflow validation"；之后的提示词改用 `git grep -F`

## 2026-10-02 J1 — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` fe85b0d（父 ee0eabb）；执行者：外部模型
- 类型：delete；白名单检查：ok（`contract/boundary.py`、`tests/application/test_p0_boundary.py`）
- diff 核对：删除 `CompatibilityDecision`（含装饰器）、`evaluate_compatibility`、两者的 `__all__` 条目、孤立的 `Sequence` import、docstring 中对应半句、三个测试与孤立的 `cases` fixture；没有新增逻辑；`test_vocabulary_matches_producer_fixture` 与 fixture 保留
- 静态检查（验收方重跑）：ruff / format / mypy（166）ok
- 测试（验收方在独立 JD 工作树重跑，CF 用只读参考树 `cf-for-jd`@445d4f5）：2409 项，2402 passed / 0 failed / 7 skipped；相对基线恰好少 3 个声明的节点，无新增，无结果变化
- contract：JD `contract_key` 与全部摘要和基线相同
- 结论：通过

## 2026-10-02 C1.4 — 通过

- 仓库/分支/提交：ConfFlow `refactor/diet` b8e85a3（父 445d4f5）；执行者：外部模型
- 类型：delete；白名单检查：ok（只改 `confflow/producer/contract.py`，+13/-25）
- diff 核对：`_analysis_capabilities()` 删除 `try/except Exception` 与 "frozen" 分支，`importlib.import_module("confflow.analysis.registry")` 调用原样保留，来源标签仍为 `"registry"`；docstring 去掉相应半句；常量保留；没有改任何测试
- 静态检查（验收方重跑）：ruff / black / mypy（253）ok
- 测试（验收方在独立工作树、显式 `--cf` 重跑）：4502 项，4490 passed / 0 failed / 12 skipped；与 C1.3 之后的集合相同（相对基线少 33 个节点，无新增，无结果变化）
- golden：TS1、83 份 engine 报告、contract 五项摘要全部不变（contract / boundary 字节不变）
- 结论：通过。Phase 1 完成。

## 2026-10-02 验收加速

- 新增验收方工具 `docs/refactor/tools-acc/run_sharded.py`（分片并行运行 CF 测试）与 `weights.json`（每个测试文件的实测耗时）。
- 在 C1.4（b8e85a3）上验证：分片运行结果与串行运行逐项一致（4502 项，4490 passed / 12 skipped）；全量 21 分钟 → 3.5 分钟（14 个分片，下限由两个各约 3 分钟的测试文件决定）。
- ACCEPTANCE §4.2a 记录了新规则：CF 每卡仍跑一次完整测试但改用分片；完整 golden 只对触及 science/execution/domain/persistence/workflow/v4/remote 的卡和阶段收尾运行，其余卡只做摘要检查。

## 2026-10-02 J2.1a — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` df25678（父 fe85b0d）；执行者：外部模型
- 类型：logic；白名单检查：ok（`contract/models.py`、`editor/service.py`、`gui/app.py`、`tests/application/test_service.py`）
- diff 核对：新增 `NoContract`（空 manifest / 空配方 / `source="none"` / 不 authoritative / 带 `blocked_reasons`），`ContractSource` 增加 `"none"`；`WorkflowEditorService()` 默认使用 `NoContract()`；`app.py` 远程模式的启动会话改为 `WorkflowEditorService()`；`StableFallbackContractProvider` 的 import 保留（J2.1b 处理）；`test_service.py` 只新增 2 个测试，没有删除任何已有行
- 静态检查（验收方重跑）：ruff / format / mypy（166）ok
- 测试（验收方在独立 JD 工作树重跑，CF 用只读参考树）：2411 项，2404 passed / 0 failed / 7 skipped；相对 J1 之后恰好新增声明的 2 个节点，无删除，无结果变化
- contract：JD `contract_key` 与全部摘要和基线相同
- 结论：通过。与写方案时的原型结果一致（不需要改任何已有测试）。

## 2026-10-02 IS.1 — 升级（交付物完整，需要用户对结果作决定）

- 仓库/分支/提交：ConfFlow `implementation/input-simplification` 9340601（父 f87da58）；执行者：外部模型
- 类型：baseline；白名单检查：ok（只新增 `docs/refactor/paths_equivalence/` 下 5 个文件）；`--check` 连续两次逐字节复现（执行方报告，验收方抽查见下）
- 结果（执行方）：28 个用例，EQUIVALENT 4、LEGACY_DEGENERATE 1、NOT_EQUIVALENT 14、OUT_OF_SCOPE 9
- 验收方复核（只读复核，未改任何文件）：
  - 12 个 NOT_EQUIVALENT 的共同原因是 PLAN 规定的"LEGACY_DEGENERATE 比较不做叠合"本身有缺陷：butane 夹具没有氢，legacy 绕含末端原子的键旋转，只是整个分子的刚体转动，是同一个构象，却因坐标不同被判成不同（例：case_0020 的两个结构是绕 C1–C2 轴的刚体转动）。用 `kabsch_rmsd`（阈值 1e-6）重新比较，case_0009、0015、0020、0021、0022、extra_terminal_endpoint、extra_bare_path、extra_bare_path_angle_step 共 8 个的集合完全相同（去重后 2/2、1/1、3/3、6/6）。
  - case_0010、0011、0016、0017：输入是完全共线的链（所有原子在一条直线上），绕中间键的转动是恒等操作、框架无定义。legacy 保留输入结构（去重后 1 个），v3（对照声明）COMPLETED 但发布 0 个结构（"published 0 leaf structures (2 raw targets)"）。这是真实的行为差异，但只在退化几何下出现。
  - case_0023、0024：legacy 与 v3 都拒绝，只是原因不同（legacy：声明 13824 个状态超过预几何上限 10000 / 未知键 waypoint；v3：末端端点）。
  - 夹具全部不含氢，所以"端点是甲基碳"这个最有意义的真实场景（绕 C–CH3 键的旋转会真正改变构象）没有被覆盖。
- 方案缺陷：PLAN §8 IS.1 的"不叠合"规则错误（验收方的失误）；已新增卡 IS.1b 修正（`docs/refactor-plan` 5811ece）。
- 结论：升级给用户（IS.1 的规则：只要有 NOT_EQUIVALENT 就不放行 IS.2）。

## 2026-10-02 J2.1b — 执行方按 G9 停止（验收方的卡片缺陷，不计为退回）

- 情况：J2.1b 的全部改动已完成并通过自检，唯有 handoff 第 4 条 `git grep "_degrade|fallback_artifacts" -- src` 按原文无法满足。命中来自白名单之外、先于本卡存在的代码：`providers.py`（`LocalProducerContractProvider` 自己的降级机制，J2.4 删除）和 `runs/confflow_backend.py`（与合同无关的私有方法）。执行方没有提交，如实报告。
- 验收方复核（把执行方未提交的 diff 应用到独立 JD 工作树 df25678 之上）：源码改动与卡片逐条一致；ruff / format / mypy ok；全量 2411 项，2404 passed / 0 failed / 7 skipped；collect 清单与 J2.1a 之后逐项相同（14 个测试的 nodeid 不变，没有删除、没有新增）；没有新增 skip/xfail；两个 `TestRemoteErrorSanitization` 测试保留，检查范围由 `diagnostics` 的 message 扩大为 `str(exc)` 加 `diagnostic.message`，同一组 CANARIES；合同解析失败经 `_spawn(on_error=deliver_error)` 进入 `_on_contract_failed`，提交在状态不是 `ready` 时仍被禁用（presenter L817、L977）。
- 观察（记录，不阻塞）：解析失败时 presenter 只把状态改为 `unavailable`，上一台服务器已采纳的 V4 合同与字段模型仍留在内存里供卡片编辑器显示，提交被状态拦住。之前的降级路径会把它换成 fallback。这是预期内的行为变化的一部分，但切换到一台坏服务器后界面上仍显示上一台的字段，J2.4 或 PLAN-2 可以考虑清空。
- 处理：PLAN 与 handoff/J2.1b.md 的 grep 范围已更正为 `remote_v4.py` 与 `gui/`；让同一执行方按现状提交。

## 2026-10-02 IS.1b — 通过（IS.1 的升级随之解除）

- 仓库/分支/提交：ConfFlow `implementation/input-simplification` e21277d（父 9340601）；执行者：外部模型
- 类型：baseline；白名单检查：ok（只改 `docs/refactor/paths_equivalence/` 下已有的 README / cases / result / run_equivalence，新增 `fixtures_h.json`、`make_fixtures_h.py`）；未触碰 `confflow/`、`tests/`
- 静态与可重复性（验收方重跑）：ruff / black ok；`--check` 连续两次逐字节相同
- 工具自检（执行方记录，验收方复核方法）：SC1 刚体旋转 RMSD 1.3e-15；SC2 镜像 1.231；SC3 二面角 60° 0.578；阈值 1e-5；`proper_rotation_rmsd` 包装 `confflow.science.cluster.kabsch_rmsd`，验收方读过源码，带行列式修正（`cluster.py:27-42`）
- 验收方独立复核（不使用执行方的比较代码，直接调用 `ConfgenExecutor`，阈值 1e-5，`kabsch_rmsd`）：n-丁烷（1→4）、1-丙醇（O4→C1）、丙胺（N4→C1），angles=[0,120,240]：
  - legacy 与 v3 都 COMPLETED，各 27 个结构，旋转去重后 27 个互不相同；
  - 两个集合互相完全覆盖；
  - v3 确实走 v3 路径（产出 `ensemble_report.json`，legacy 产出 `confgen_report.json`），消息为 "published 27 leaf structures (27 raw targets)"；
  - 反向对照（v3 换成角度 [0,90,180]）不会覆盖 legacy 的结构，说明比较有区分能力。
- 含氢结果：9 个用例全部 EQUIVALENT；`LEGACY_ONLY_TERMINAL_ROTOR` = 0（达到用户的期望值），`symmetry_aware_legacy_only` = 0；`BOTH_REJECT` 0、`V3_EMPTY_DEGENERATE` 0（含氢用例中没有出现）
- 关键发现（纠正验收方此前的预测）：带氢分子上 v3 **不会**拒绝末端端点声明。C–H 键提供了可测的二面角框架，"no measurable dihedral frame" 只出现在没有任何取代基的末端（如无氢夹具）。因此用户此前接受的"v3 拒绝末端原子端点"在含氢真实分子上并没有造成构象损失。
- 已知局限（不阻塞，不计入结论）：
  - 无氢回归部分（`no_hydrogen_regression`，EQUIVALENT 5 / NOT_EQUIVALENT 14 / OUT_OF_SCOPE 9）没有再走对照声明流程，14 个 "NOT_EQUIVALENT" 实为 "v3 拒绝，发布 0 个结构"，信息量低于 IS.1 的原判。按 G11 这些夹具不能用于结论，所以未要求返工。
  - 尚未覆盖"末端重原子上没有任何取代基"的真实场景（C–F、C–Cl、C=O 的 O、腈基的 N）：v3 对此类端点仍会以 "no measurable dihedral frame" 拒绝，legacy 则会产生刚体转动的重复结构。IS.2b 的诊断（指明具体的键）仍然有用。PLAN-2 可以补一个含此类端基的含氢用例。
- 结论：通过。IS.2 的前置条件之一（含氢用例无 NOT_EQUIVALENT，`LEGACY_ONLY_TERMINAL_ROTOR` 为 0）已满足；其他前置条件（IS.0，即 Phase 3 完成并合并到 main）仍待满足。
