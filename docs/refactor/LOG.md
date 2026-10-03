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

## 2026-10-02 J2.1b — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` 4c5f6ec（父 df25678）；执行者：外部模型
- 验收：已提交的 diff 与验收方在停止期间已验证的未提交补丁逐字节相同（忽略 index 行）。该补丁的验证结果直接适用：ruff / format / mypy ok；全量 2411 项，2404 passed / 0 failed / 7 skipped；collect 清单与 J2.1a 之后逐项相同；14 个测试 nodeid 不变；无 skip/xfail；两个脱敏测试检查范围扩大；更正后的 grep（`remote_v4.py`、`gui/`）无输出
- 远端：`origin/refactor/diet` 仍为 5847bc7（未推送）
- 结论：通过。

## 2026-10-02 C5.1 — 完成，等待用户确认

- 由验收方直接编写：`docs/refactor/REFINE_GAP.md`（`blocks/refine` 与 V4 `transform_executor` refine 的逐项对比）
- 关键发现（已实验证实）：旧 refine 通过图同构做对称感知的去重，V4 refine 只做固定下标比较。同一个丁烷分子，仅把一个甲基的三个等价氢标号循环置换，固定下标 RMSD 0.807 Å；V4 refine 保留 2 个，旧 refine 合并为 1 个。其余差异项为设计上不适用（无能量/频率数据）或可不要（CLI、报告、并行）。
- 待用户决定：Q-R1（对称感知去重是移植到 `confflow/science/` 还是放弃）、Q-R2（ConfGen 声明的拓扑覆盖是否一并移植）、Q-R3（多数拓扑过滤不移植）。C5.3 在这些确认之前不得开始。

## 2026-10-02 J2.2 — 通过（见下）；J2.3 — 执行方按停止条件停下（验收方的卡片遗漏，不计为退回）

- J2.3 情况：改动全部完成并通过其余全部测试（2403 passed），唯一失败是 `tests/application/test_architecture.py::test_only_dedicated_parsers_decode_producer_json`：该守护把 producer JSON 的 `json.loads` 限定在允许名单里，J2.3 把 `decode_json_object` 和 `decode_strict_json` 原样搬到 `contract/errors.py` 与 `editor/jcs.py` 后，这两处不在名单里。该文件不在卡片白名单，执行方按规则停止。
- 验收方复核：违规位置恰好是这两处；`application/` 下 `json.loads` 调用点搬迁前后都是 3 处（parse.py 2、confflow_state.py 1 → errors.py 1、jcs.py 1、confflow_state.py 1），没有新增，所以守护的意图未被削弱。授权只在 `_ALLOWED_JSON_MODULES` 里新增这两项；PLAN J2.3 卡已补入白名单。

## 2026-10-02 J2.2 — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` b1ba08d（父 4c5f6ec）；执行者：外部模型
- 类型：test-only；白名单检查：ok（`tests/contract_fixtures.py`、`tests/application/conftest.py`；没有修改任何 `test_*.py`）
- diff 核对：新增 `FixtureContract` 替身；`authoritative_contract()` 直接用 `producer_v2.json` 的 manifest 与配方目录构造；`conftest.py` 只把类型注解改为 `ContractLike`
- 独立等价核对（验收方）：把 `authoritative_contract()` 与旧路径 `parse_fixture("producer_v2.json")` 逐项比较——`contract_key`、`source`、`is_authoritative`、`level`、`capabilities`、`producer_version`、`diagnostics`、`errors`、`warnings`、`schema`、`workflow_schema_version`、两个 sha256、`describe_source()` 全部相同；`editor_manifest` 与 `recipe_catalog` 对象相等（27 个字段、4 个配方）
- 静态检查：ruff / format / mypy（166）ok
- 测试（验收方在独立 JD 工作树重跑）：见本条下方数字；与 J2.1b 之后集合逐项相同
- 结论：通过

## 2026-10-02 J2.3 — 通过

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` c94fcab（父 b1ba08d）；执行者：外部模型
- 类型：move；白名单检查：ok（13 个文件：新增 `contract/errors.py`；改 `parse.py`、`jcs.py`、`contract/__init__.py`、`v4.py`、`boundary.py`、`remote_v4.py`、`binding_candidates.py`、`v4_validation.py`、`v4_results.py`；测试侧 `test_p0_boundary.py`、`test_confflow_v4_contract.py` 只改 import；`test_architecture.py` 只在 `_ALLOWED_JSON_MODULES` 增加两项并补注释，经验收方授权）
- 搬迁核对（验收方独立重做）：对 `git diff -U0` 做"删除行 vs 新增行"多重集合比较（忽略 import 与括号行）——函数体、类体逐字抵消；剩余差异只有新增/删除的文档字符串、注释和 `__all__` 条目；`application/` 下 `json.loads` 调用点仍为 3 处（errors.py、jcs.py、confflow_state.py）
- 静态检查（验收方重跑）：ruff / format / mypy（167）ok
- 测试（验收方在独立 JD 工作树重跑）：2411 项，2404 passed / 0 failed / 7 skipped；与 J2.2 之后逐项相同，无新增无删除无变化
- contract：JD `contract_key` 与全部摘要和基线相同
- 结论：通过

## 2026-10-02 推送（用户授权"下轮一起推送"）

- JobDesk-v2 `refactor/diet`：5847bc7 → c94fcab（J0b、J1、J2.1a、J2.1b、J2.2、J2.3 共 6 个提交）
- ConfFlow `refactor/diet`：新建，b8e85a3（B0.1、B0.2、C1.1–C1.4）
- ConfFlow `docs/refactor-plan`：1ccf347 → 4917e86（本日志推送前的最后一个提交）
- 未推送：`master`、`main`、`implementation/input-simplification`（IS.1、IS.1b 的提交仍只在本地的 `exec-cf-is` 工作树里；远端的输入简化分支仍为 f87da58）。此条日志本身在推送之后写入，下次推送一并带上。
- 合并提醒：CF `refactor/diet` 上的 `docs/refactor/PLAN.md`、`ACCEPTANCE.md` 是早期版本（随 B0.1 的父提交带入），`docs/refactor-plan` 上是最新版本。把 `refactor/diet` 合入 `main` 时，这两个文件以 `docs/refactor-plan` 的版本为准。

## 2026-10-02 J2.1a 验收疏漏 — 已推送的 c94fcab 无法启动应用（验收方发现，新增 J2.1c 修复）

- 发现经过：为 J2.4 做删除原型时，`NewRunPage` 在空 `NoContract` 上构造失败（`EditorManifestError: unknown field id: 'global.charge'`）。验收方随后直接在已提交的 c94fcab 上复现应用的启动路径：`MainWindow(WorkflowEditorService(), store)` 在 `CalculationSection.__init__` 抛出同样的异常。`gui/app.py::main` 正是这样构造主窗口的，所以 J2.1a 之后（含已推送的 J2.1b、J2.2、J2.3）的 JobDesk 在没有合同的状态下无法启动。
- 原因：J2.1a 把会话默认值换成空的 `NoContract`，但新建页面在构造时要求 manifest 里有 `global.charge` / `global.multiplicity`。J2.1a 的全套测试都显式传入合同，没有测试覆盖"没有合同的默认会话 + 真实页面"。验收方在 J2.1a 验收和原型阶段都只运行了测试套件，没有做启动路径检查。
- 影响范围：只影响工作分支 `refactor/diet`（JD 远端 c94fcab），`master` 没有受影响（它仍是 9beeaf2）。
- 处理：新增卡 J2.1c（`CalculationSection` 的全局行改为跟随当前 manifest 构建并随变化重建，附启动冒烟测试）；验收协议新增 §4.2b（对改变会话默认值、合同解析或组合根的 JD logic 卡，必须运行 `tools-acc/startup_smoke.py`）。验收方的启动冒烟脚本在 c94fcab 上失败、在修复版上通过。

## J2.1c — 验收通过（2026-10-02）
- 执行：外部模型 glm-5.3-flash；JD `refactor/diet` 13b6f55（父 c94fcab），1 次提交，未退回。
- 验收人独立重跑（accjd 工作树）：src 改动与 handoff/J2.1c-calculation_section.patch 逐行一致，无 setContentsMargins；仅 2 个文件（calculation_section.py、tests/gui/test_startup_smoke.py）；ruff/format/mypy 通过；全量 JD 2407 passed / 7 skipped（与期望一致）；startup_smoke.py 输出 `startup smoke: ok`。
- 用户授权推送（"下轮一起推送"）：JD refactor/diet、CF refactor/diet、CF docs/refactor-plan。

## J2.4 — 第一次 G9 停止（2026-10-02，规划方疏漏，不计退回）
- 执行模型自检 5（git grep）命中 3 处残留：`test_architecture.py:410` 的示例字符串、`test_confflow_v4_e2e.py:16/470` 的过时注释。其余自检全部通过（2325/7、collect 2332、冒烟 ok、82 项被删测试逐行一致）。
- 原因：规划方定稿时的 grep 没覆盖这三处。已修补丁（36 个文件，新增 `test_confflow_v4_e2e.py` 两处注释，`test_architecture.py` 示例串改名），验收方实测 grep 输出 ok。

## J2.4 — 验收通过（2026-10-02）
- 执行：外部模型 glm-5.3-flash；JD `refactor/diet` fedac08（父 13b6f55）。第一次 G9 停止属规划方疏漏，第二次一次通过。
- 验收人独立重跑：提交相对 13b6f55 的 diff 与 handoff/J2.4-src-tests.patch 字节一致；ruff/format/mypy 通过；全量 JD 2325 passed / 7 skipped；startup_smoke ok；PLAN 的 git grep 输出 ok；被删测试 82 项由执行模型对照清单（验收方补丁生成时已核）。
- 用户授权推送（"下轮一起推送"）：JD refactor/diet。

## J3.1 + J3.2 — 验收通过（2026-10-02）
- 执行：外部模型 glm-5.3-flash；JD `refactor/diet` 26c5ccb（J3.1）、1ca4052（J3.2），一次通过。
- 验收人独立：两提交 diff 与 handoff/J3.1-presenter.patch、J3.2-src-tests.patch 字节一致；ruff/format/mypy 通过；全量 JD 2306 passed / 7 skipped；startup_smoke ok。
- 附：发现验收工具盲区（venv 可编辑安装钩子会从 /opt/ConfFlow 找回被删模块），已加 tools-acc/noeditable 并让 run_sharded.py 默认使用；对已验收的 CF refactor/diet b8e85a3 重跑 4490 passed / 0 failed，此前验收结论不变。
- 用户授权推送（"下轮一起推送"）：JD refactor/diet。

## 2026-10-02 用户决定与验收工具补充
- export：用户同意作为 C5.2 之后的独立 delete 卡（C5.2b）处理，前提：JD 不依赖 `confflow export`（验收方已 grep 确认：JD 的 export 仅为工作流 YAML 导出，无 results.db 依赖）；V4 结果出口为 run_result_manifest（JD parse_result_bytes）；用户确认没有需保留的旧 results.db 后才执行。
- 验收工具：新增 tools-acc/deleted_modules_check.py；JD 测试经 run_jd_tests.sh 的 PYTHONPATH 解析到被测 JD 树（已实测）；删除类卡片附加检查写入 ACCEPTANCE。对已验收的 J2.4/J3.2（accjd@1ca4052）补查：被删模块全部 ModuleNotFoundError。
- C3.1/C3.2 已备补丁（验收方原型）；C3.2 实测路径差异与计划一致，jd_contract_key 不变；已知并行分片下 test_cancel_trap 偶发失败（单独运行稳定）。

## C5.2（侧分支 refactor/diet-c5）— 验收通过（2026-10-02）
- 执行：外部模型 glm-5.3-flash；CF `refactor/diet-c5` 2f95dc1（父 b8e85a3），一次通过。
- 验收人独立：提交与 handoff/C5.2-src-tests.patch 字节一致；deleted_modules_check 通过（calc、calc.runner、confts、workflow.composition、blocks.viz 均 ModuleNotFoundError，包解析到 cf-c5）；ruff/mypy 通过；run_sharded 4118 passed / 12 skipped。执行模型自报 golden ok、被删测试 376 项逐行一致、新增 4 项。
- 环境教训：共享 jd-pin 随主线推进到 J3.2 后，侧分支（pin 仍为 9beeaf2）需专用 pin：/opt/cf-worktrees/jd-pin-cf（9beeaf2）。侧分支验收一律 `--jdpin /opt/cf-worktrees/jd-pin-cf`；合回主线后改用 jd-pin。
- 侧分支未合并；已在远端（用户 2026-10-02 确认保留）。授权范围更新为：refactor/* 与 docs/refactor-plan 可推送，main/master 一律不推。合并时机：C3.4 之后，由验收方做 merge 并复验。

## 2026-10-02 用户答复 Q-R1/2/3
- Q-R1 移植，拆两张卡：C5.3a（move：图同构映射原样搬入 science/，函数体不改，旧测试随迁，并保留预算与剪枝、加性能测试）、C5.3b（logic：V4 refine 调用；独立对照 RDKit GetBestRMS；丁烷、叔丁基、苯环翻转、非对称不得误合并）。
- Q-R2 一起移植，但须先写明 V4 中 refine 获取拓扑的来源，来源不明则不开始：验收方调查结论写入 PLAN C5.3c（现状无来源；候选 A 结构元数据 / B refine 自带键 / C 读 ConfGen 报告），等待用户决定。
- Q-R3 同意不移植；PLAN-2 记一条（优化后键连接变化的构象 V4 是否有检查）。
- 推送授权改为：refactor/* 与 docs/refactor-plan 可推送；main/master 一律不推。

## 2026-10-02 用户答复 Q-R2 = B 与 C5.3a 要求
- Q-R2 选 B：refine 步骤新增 `topology_bonds`（带 COVALENT/COORDINATION/FORMING 类型、index_base 与 ConfGen 一致、校验原子数、映射保持边类型、无参数时旧行为）；intent 编译器从上游 ConfGen 复制；不改 ConfGen 发布内容与 contract。已写入 PLAN C5.3c-1/-2。待确认小点：ConfGen 还有 BREAKING 边类型，默认四种都接受。
- 验收方核实：`perceive_adjacency` 是 `core.bonding.build_adjacency` 的薄封装，12000 例输出逐位相同，仅错误处理不同 → C5.3a 保留对 `build_adjacency` 的调用；C5.5 须保留 core/bonding.py、core/data.py。
- C5.3a 性能测试按节点预算与结果固定，不依赖耗时。等 C3 报告回来后再做 C5.3a 补丁。

## 2026-10-02 用户答复（C5.3c 细节）
- 边类型四种（含 BREAKING）都接受。PLAN-2 新增：core/bonding.py、core/data.py 最终移入 science/。
- 验收方核对 ConfGen 构图（planner.build_typed_graph）后发现与"声明边覆盖同一原子对、其余几何感知补全"不一致，已回头请用户裁定（见对话）；裁定前 C5.3c-1 不写补丁。

## 2026-10-02 用户裁定（C5.3c 构图规则）
- 选 A：refine 复用 build_typed_graph；topology_bonds = ConfGen 的 topology + index_base，coordination 与 tolerances.bond_scale 由编译器复制；声明了拓扑时用 ConfGen 的 bond_scale（默认 1.15），未声明时 1.2 保持旧行为；接受与旧 AddBond/DelBond 的差异并写文档。
- C5.3c-2：沿数据流向上找最近 ConfGen 步骤（不要求紧邻）；多个上游来源且声明不一致 → 报错并要求显式指定。
- PLAN-2：统一 refine 与 ConfGen 的默认 bond_scale。

## C3.1 + C3.2 — 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF refactor/diet 65c4e0c（C3.1）、e5c3032（C3.2）；两提交与 handoff 补丁字节一致；run_sharded 4481 passed / 12 skipped；ruff/mypy 通过；golden（检查点）由执行模型自报 ok，验收方在同一补丁上已验。
- 执行模型自述：C3.2 首次提交的 Removed-Tests 正文测试名写错，已 amend 修正（仅提交信息）。已在后续卡片补一条"提交信息中的测试名必须从清单逐字复制"。
- 跨仓验证（验收方）：用新 producer（exec-cf）跑 JD 全量，3 个失败正是预期的 fixture 未同步类（两项指纹、一项删除 producer 已不发布的键），由 J3.3 修复；J3.3 补丁已在 CF=C3.3 原型上实测 2303 passed / 7 skipped。
- 推送：按 2026-10-02 授权（refactor/*），推送 CF refactor/diet。

## C5.3a 补丁已备（2026-10-03，验收方原型）
- 基点 2f95dc1。搬迁等价：topology 21 个定义与 rmsd_engine 的 7 个定义 AST 逐一相同（tools-acc/move_identity_check.py）。全量 4125 passed / 12 skipped，collect 4137；golden ok。
- 发现：被搬的 compare_frames 依赖 core.constants.HARTREE_TO_KCALMOL，C5.5 须保留 core/constants.py（并入 PLAN-2 的 core→science 迁移）。

## C3.3 + J3.3 — 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF refactor/diet 8d968dc（C3.3）、JD refactor/diet edb068a（J3.3），一次通过。
- 验收人独立：C3.3 提交与 handoff/C3.3-src-tests.patch 字节一致；J3.3 代码部分与 J3.3-code.patch 一致，fixture 与 CF 源字节一致，PROVENANCE.source_commit = 8d968dc；CF 全量 4481 passed / 12 skipped；JD（accjd@edb068a，绑定 exec-cf）2303 passed / 7 skipped；ruff/mypy/冒烟通过。
- C3.4 补丁已备（pin 到 edb068a；验收方实测 4481 passed、release workflow 测试 14 passed）；jd-pin 已移到 edb068a。
- 推送：refactor/diet（CF、JD）按 2026-10-02 授权推送。

## 2026-10-03 C5.2b 补丁已备
- 用户确认旧 results.db 不需保存、不需 README；已生成的导出文件原样保留。C5.2b 排入侧分支。补丁在 C5.2（4101 passed/collect 4113）与 C5.3a 之上（4108/4120）均实测通过，golden ok，被删 17 项。

## C3.4 验收通过；Phase 3 完成；refactor/diet 合入主分支（2026-10-03）
- C3.4：CF refactor/diet baeb451，与补丁字节一致；4481 passed / 12 skipped；release workflow 测试通过。
- 用户授权验收方自行合并（"你直接合并就行，你自己决定怎样合并分支"）。合并方式：独立工作树（不动用户检出的 research/realization-handoff 与 implementation/input-simplification），`--no-ff`，只更新本地 main / master，**未推送 main/master**（遵守"main/master 一律不推"）。
- CF main d5a40ae → 8291fb9（合并 refactor/diet baeb451）；JD master 9beeaf2 → 2f11b49（合并 refactor/diet edb068a）。两个合并结果的树与 refactor/diet 完全相同。
- 合并后复验：CF 4481 passed / 12 skipped；JD（绑定合并后的 CF）2303 passed / 7 skipped。
- 合并工作树：/opt/cf-worktrees/merge-cf（main）、/opt/cf-worktrees/merge-jd（master）。

## IS.0（CF + JD）— 验收方亲自合并并验收通过（2026-10-03）
- CF：`implementation/input-simplification` ← main，合并提交 b0e8c64，无冲突。验证：ruff/mypy 通过，全量 4853 passed / 12 skipped（main 的 4481 + IS 新增）。
- JD：`implementation/input-simplification` ← master，合并提交 bcdea5b，4 处冲突全部按 master 的删除解决（fixture、editor/__init__.py、test_confflow_v4_contract.py）；IS 代码里对已删符号的引用逐条去掉：intent/compiler.py 与 preview.py（ContractParseError 改自 contract.errors；producer 回答不再需要 capability_identity）、3 个测试文件（load_recipe_catalog → fixture_catalog）、2 个测试文件假回答里的 capability_identity、10 处 yaml `# type: ignore`；fixture 从合并后的 CF IS 重新 vendor（多出 compile_intent / preview_paths 两个 authoring 操作）。验证：ruff/format/mypy 通过，JD 全量（绑定合并后的 CF IS）2465 passed / 7 skipped。
- engine 报告：B0.1 基线未变。golden_check 报告 6 项（5 项新增 + 1 项 different），均记入 `docs/refactor/baseline/checkpoints/IS.0/engine_reports/`：1 项 different 是 `test_pinned_coordination_ring_damage_is_drift`，原因是 IS 分支的 99283a7 改了**测试输入**（把对称打平的正方形种子换成蝴蝶形环）；验收方证据：IS 的引擎用**旧测试输入**逐字节复现基线报告，故引擎行为未变，不是科学行为变化。
- 未推送：IS 分支（implementation/*）不在推送授权内，main/master 一律不推。
- 发现并修复我自己的错误：C5.2b 卡的 PLAN 编辑误删第 8–10 节，已从 ff9f23c 恢复（见前一提交）。

## C5.3a + C5.2b（侧分支）— 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF refactor/diet-c5：da047c0（C5.3a）、a261bbf（C5.2b），一次通过。
- 验收人独立：两提交与 handoff 补丁字节一致；move_identity_check：topology 21 个、frame_compare 7 个定义 AST 相同；deleted_modules_check（calc、workflow.export）通过；ruff/mypy 通过；全量 4106 passed + 2 个负载敏感失败（test_v4_runtime_cutover 的两个端到端测试，单独运行均通过），执行模型自报 4108 passed / 12 skipped；golden 由执行模型自报 ok。
- 已知负载敏感测试（并行分片下偶发，单独稳定）：test_v44_worker::test_cancel_trap…、test_v4_runtime_cutover 的 test_worker_runs_v4_end_to_end / test_plain_cli_runs_v4_application。
- 推送 refactor/diet-c5（授权：refactor/*）。

## C5.3b 补丁已备（2026-10-03，验收方原型）
- 基点 a261bbf；3 个文件；全量 4121 passed + 2 个已知负载敏感测试（单独通过）= 4123，collect 4135（+15），golden ok；RDKit GetBestRMS 交叉验证四个对称体系一致、两个构象对一致不合并。
- 发现并处理：直接在顶层导入 science 模块会令 worker 导入闭包载入 confflow.core（test_worker_run_import_closure_is_compiler_free 失败），改为懒加载。
- 裁定：严格小于（用户默认）；阈值 0 不再合并；记入卡片。

## IS.0 追加修复与 IS.2 补丁已备（2026-10-03）
- 工具漏洞：run_sharded.py 以前会悄悄漏掉“收集阶段就报错”的测试文件；已修复（现在直接报错退出）。据此发现 IS.0 合并后 tests/v4/test_producer_authoring_boundary_coverage.py（IS 分支新增）仍 import 已删的 compare_identities / evaluate_compatibility，已在 IS 分支追加提交 fe2acf0 删除只守护已删函数的 4 个测试与未用辅助函数。此前所有已验收卡片的 collect 数量都与期望一致，未受该漏洞影响。
- IS.2 补丁（基点 fe2acf0）：全量 5138 passed，collect +53，golden：contract 与 IS.0 检查点一致；engine 报告与 IS.0 检查点同批同字节。
- 发现：legacy 路径的 `waypoint` 在 typed v3 中没有对应形式，IS.2 之后 intent 里带 waypoint 的路径在编译期被拒绝（见 PLAN IS.2 与 PLAN-2）。

## C5.3b（侧分支）— 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF refactor/diet-c5 efbaecd（父 a261bbf），一次通过。与 handoff/C5.3b-src-tests.patch 字节一致；ruff/mypy 通过；全量 4123 passed / 12 skipped；新测试 15 passed。已推送（refactor/*）。

## C5.3c-1 补丁已备（2026-10-03，验收方原型）
- 基点 efbaecd；5 个文件；全量 4144 passed，collect +21；golden ok。复用 build_typed_graph；映射保持边类型（Graph.typed_edges + MappingSearch 边类型检查），无类型边时搜索节点数/判定与 C5.3a 固定测试逐项不变；同一声明经 ConfGen 与 refine 两条路径边集合相同（5 组）；反应键异位不合并（对照：无类型边时合并）。验收方裁定：native bond_scale 与 topology_bonds 同时给出视为冲突报错。文档已写入 docs/architecture/WORKFLOW_V4.md。

## 2026-10-03 用户裁定 waypoint；IS.2c 补丁已备
- waypoint 不在 v3 补支持；IS.2 保持编译期拒绝，错误信息补"改用显式 torsions 声明"；PLAN-2 记触发条件（首次遇到 PATH_AMBIGUOUS：带 add_bond 的 TS、金属配合物）；IS.5 不受影响。
- IS.2 已被执行模型提交（2e0295a，与 handoff/IS.2-src-tests.patch 字节一致）；提示语变更作为小卡 IS.2c（基点 2e0295a，+1 测试）单独发出，不改 IS.2。

## IS.2 — 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF implementation/input-simplification 2e0295a（父 fe2acf0），一次通过。提交与 handoff/IS.2-src-tests.patch 字节一致；验收方独立：ruff 通过，run_sharded 5138 passed / 12 skipped；提交信息里的 53 个测试名与 handoff/IS.2-added-tests.txt 逐行相同。
- 说明：执行报告正文里复述的测试名有两个与提交里的真实名字不同（报告写 `…_rejected_at_compile_time`），属报告正文笔误；提交信息本身正确（验收方逐行对照）。
- 未推送（该分支不在推送授权内）。

## IS.2b 补丁已备（2026-10-03，验收方原型）
- 基点 2e0295a（可与 IS.2c 任意顺序；文件互不重叠，两者都已验证可应用）。实现与原卡不同：stage 只读已存在的 paths_resolved 审计来措辞，不增加任何字段，所以不可能进入 digest/报告；全量 5144 passed（+6），golden：contract 与 TS1 ok，6 份 IS.0 引擎报告逐字节不变。测试文件名避开 test_confgen_ 前缀，以免被 golden 的引擎捕获规则多收一份报告。

## C5.3c-1（侧分支）— 验收通过（2026-10-03）
- 执行：外部模型 glm-5.3-flash；CF refactor/diet-c5 6521581（父 efbaecd），一次通过。与补丁字节一致；提交信息 21 个测试名与清单逐行相同；ruff/mypy 通过；全量 4144 passed / 12 skipped。已推送（refactor/*）。
