# ConfFlow / JobDesk V2 重构执行方案

> 状态：**定稿 v2（2026-10-02）**，已纳入用户对 Q0–Q15、Q0b、Q2c 的答复（§0.1）。没有待答复事项。
> 作者角色：方案与验收（不执行）。执行模型只按本文件和 `ACCEPTANCE.md` 工作。
> 事实基准提交：ConfFlow `main` = `d5a40ae`；ConfFlow `implementation/input-simplification` = `f87da58`；
> JobDesk-v2 `master` = `9beeaf2`；JobDesk-v2 `implementation/input-simplification` = `92d48f1`。
> 文中 `文件:行号` 均注明基于哪个提交；执行时一律先用卡片给出的符号锚点重新定位，行号只作核对。

---

## 0. 决策记录与待确认

### 0.1 用户已确认的决定（2026-10-02）

| # | 事实（附证据） | 用户决定 → 方案中的落点 |
|---|---|---|
| Q0 | ConfFlow `main` d5a40ae 与被 pin 的 JD `9beeaf2` 不兼容。d5a40ae 在 editor manifest 中新增 `confgen.v3.*` 字段，JD 9beeaf2 的 `editor_manifest_from_mapping` 拒绝它（`field 'confgen.v3.rings' declares an item_type but is not an item list`，`application/editor/manifest.py:333`@9beeaf2）。JD 测试绑定 CF main 时有 23 个失败。 | 做 J0：把 92d48f1 中 manifest 解析器的修复移植到 JD master，放在 B0.1 之前；J0 之后 JD 应全绿，B0.1 以此为基线。**写方案时预演发现只移植解析器还剩 9 个失败，见 0.2 的 Q0b。** |
| Q1 | JD 输入简化分支的 ConfGen 表单默认使用 legacy `native.paths`（`gui/new_run/confgen_v3_form.py:548-555`@92d48f1）。 | 确认 → IS.4（白名单已细化）。 |
| Q2a | v3 torsion stage 拒绝没有可测二面角框架的末端原子端点（`science/confgen/torsion/stage.py:96-108`@f87da58），legacy 照样旋转。 | 接受 v3 的拒绝。IS.1 对这类用例比较去重后的几何集合，相同则标记 `LEGACY_DEGENERATE`（不算 `NOT_EQUIVALENT`，但必须证明去重后集合相同）；IS.2 在 intent 编译时报错并指明具体的键，不自动裁剪。 |
| Q2b | legacy bare 声明使用 `_DEFAULT_ANGLE_STEP = 120`（`execution/confgen_executor.py:136`@f87da58）；typed 要求显式写出。 | 确认：bare 声明补 `step: 120`。 |
| Q3 | 现有 token 有 6 个字段；`request_id` 不起区分作用（`presenter.py:931-937`@9beeaf2）。 | **决策 4 修改**：token 字段为 `document_content_digest`、`contract_digest`、`target_identity`；删除 `request_id`、`session_id`、`session_epoch`、`capability_identity` → L2。 |
| Q4 | `ensemble_report` 没有 JSON schema；contract 中只有 `confgen.result_provenance`（`producer/contract.py:396`@d5a40ae）。 | contract 的 `confgen` 段新增 `report_provenance`（JSON pointer 列表）→ C3.3。 |
| Q5 | 现行文档是 README + 7 个。 | 确认；历史文档不动 → C5.7。 |
| Q6 | 删除 boundary 必需成员会让已部署的旧 JD 拒绝新 producer。 | 不升 `BOUNDARY_PROTOCOL_VERSION`；C3.2 的提交说明必须写明"服务器 ConfFlow 与 JD 必须同步升级"。 |
| Q7 | producer 侧 `semantic_identity` 在 JD 停止读取后没有读取方。 | 删除 → C3.2 第 7 步启用。 |
| Q8 | JD Phase 2 不是纯删除。 | 接受 J2.1 的行为变化 (a)(b)。(c) 已核实，结论写进 J2.1。 |
| Q9 | Phase 4 删除 `confgen.native` 会改变 contract。 | 确认：Phase 4 包含此项，成对提交 → J4.1 / C4.2。 |
| Q10 | `preview_paths` 入参是 legacy 形状。 | 本轮保留；记入 §13"移交 PLAN-2"。 |
| Q11 | `blocks/confgen` 的阶段归属。 | 按默认：Phase 5（C5.4）。 |
| Q12 | 删除 `evaluate_compatibility` 后，compatibility 词汇没有业务读取方。 | 按默认：保留。 |
| Q13 | 基线测试状态。 | 按默认：以 B0.1（在 J0 之后运行）的产物为准。 |
| Q14 | `/opt/confjob-coordinator/DECISIONS.md` 不是 git 仓库。 | 按默认：D11 只追加内容，在 LOG 记录前后 sha256。 |
| Q15 | JD 测试把 `/opt/ConfFlow` 和它的 `.venv` 写死为 producer。 | 按默认：用 mount namespace 绑定（§2.3）；改为环境变量记入 §13。 |

### 0.2 第二轮确认（2026-10-02）

| # | 事实 | 用户决定 → 方案中的落点 |
|---|---|---|
| Q0b | 只移植解析器（J0a）后，JD 仍有 8 个失败（测试写死 11 个配方、测试用 `sort_keys` 复算 JCS 签名、vendored boundary fixture 过期），外加 1 个环境问题（`PYTHONPATH`）。 | J0b 纳入 J0，作为 J0a 之后单独的 `test-only` 提交；J0b 通过且 JD 全绿后才做 B0.1。 |
| Q2c | `compile_intent`（`producer/intent.py:1371-1395`@f87da58）没有结构，编译时无法判断末端原子端点。 | 方向 1：由运行时的 v3 拒绝承担，诊断必须指明具体的键 → 新卡 IS.2b。方向 2（在 JD 路径预览阶段报错）记入 §13。 |
| 环境 | JD 的 mypy 需要 `types-PyYAML`（JD `pyproject.toml:34` 的 dev extras），本机原先没有安装。 | 已安装 `types-PyYAML 6.0.12.20260906`，写入 §2.1。 |
| 执行 | 执行模型的选择与权限。 | J0a 由 Opus 子代理执行；J0b 起改由 CodeBuddy `glm-5.3-flash` 执行（§2.8），权限为 `acceptEdits` 加限定工具，不用 bypass；JD 测试一律通过 `docs/refactor/tools/run_jd_tests.sh` 运行（G10）。同一张卡被退回两次时，报告用户，由用户决定是否换更强的模型。 |
| 推送 | — | 用户授权推送 JD `refactor/diet` 和 CF `docs/refactor-plan` 两个工作分支（不动 `master`/`main`）。其他分支和后续推送仍需逐次授权。 |

说明（不需要决定）：TS1 走完整 engine 时，状态使用 engine 的 `TerminalStatus` 词汇（`published_leaf` / `failed_numerical` / …，`science/confgen/model.py:86-101`@d5a40ae），与 `feasibility_spike` 的 REALIZED/UNRESOLVED 不是同一套。基线按 engine 词汇原样记录。

---

## 1. 总览

### 1.1 阶段顺序

```
J0       JD 兼容修复          J0a → J0b
Phase 0  基线冻结            B0.1 → B0.2(工具修复)
Phase 1  ConfFlow 外围删除    C1.1 → C1.2 → C1.3 → C1.4
J1       JD 删除 evaluate_compatibility
Phase 2  JD 删除 V1/V2        J2.1a → J2.1b → J2.2 → J2.3 → J2.1c(修复) → J2.4   (J2.5 可选，需用户确认)
Phase 3  边界瘦身（两仓成对） J3.1 → J3.2 → C3.1 → C3.2 → C3.3 → J3.3 → C3.4
IS       输入简化分支改造     IS.1(golden) → IS.1b(修正比较) … IS.0(合入 main) → IS.2 → IS.2b → IS.3 → IS.4(JD) → IS.5(合并到 main，需用户批准)
Phase 4  ConfFlow chain 路径  C4.1 → J4.1 → C4.2 → C4.3 → C4.4
Phase 5  calc/CLI/core 清理   D11 → C5.1(差异报告，需用户确认) → C5.2 → C5.3 → C5.4 → C5.5 → C5.6 → C5.7 → C5.8
逻辑尾部                       L1 (CF pairing 常量) ， L2 (JD token)
```

### 1.2 依赖图

```
J0a ─> J0b ─> B0.1 ──┬──> C1.1 ─> C1.2 ─> C1.3 ─> C1.4 ──────────────┐
       │                                               │
       ├──> J1 ─> J2.1a ─> J2.1b ─> J2.2 ─> J2.3 ─> J2.4 ────────┤
       │                                               v
       │                     J3.1 ─> J3.2 ─> C3.1 ─> C3.2 ─> C3.3 ─> J3.3 ─> C3.4
       │                                                                      │
       ├──> IS.1 (在 IS 分支上，只读 main，可提前) ────────────┐               │
       │                                                      v               v
       │                                         IS.0 (把 main 合入 IS 分支) ─> IS.2 ─> IS.2b ─> IS.3 ─> IS.4 ─> IS.5
       │                                                                                          │
       │                                                       C4.1 ─> J4.1 ─> C4.2 ─> C4.3 ─> C4.4
       │                                                                                          │
       ├──> D11 (任意时刻)                                                                         v
       ├──> C5.1 (只写报告，可提前) ── 用户确认 ──> C5.2 ─> C5.3 ─> C5.4 ─> C5.5 ─> C5.6 ─> C5.7 ─> C5.8
       │
       └──> L1 (CF，技术上只依赖 C1.4；按用户顺序放在最后)      L2 (JD，依赖 J3.2)
```

### 1.3 可以并行的部分

执行模型一次只做一张卡；"并行"指不同仓库或不同分支之间没有顺序依赖，可以交替进行：

- J0a、J0b 必须最先完成，B0.1 依赖它们。
- CF 的 Phase 1 与 JD 的 J1、Phase 2 互不依赖。
- IS.1（等价 golden）只在 IS 分支上新增文件，可以在 Phase 1–3 期间完成。
- D11、C5.1 只产出文档，可以提前完成；但 C5.3 必须等用户确认 C5.1 的报告。
- L1 只依赖 C1.4。为了遵守"逻辑改动放最后"，它排在最后。

### 1.4 卡片类型

| 类型 | 允许的改动 |
|---|---|
| `delete` | 只删除代码、文件、导出项、测试，以及修复因删除而断掉的 import。允许把被删模块名从"必须存在"清单移到"必须不存在"清单（测试守卫的对应更新）。不得新增任何逻辑分支、函数或类。 |
| `move` | 原样移动代码（文件间或模块间），只改 import 路径和 `__all__`。函数体逐字不变。 |
| `logic` | 行为改变。卡片必须列出每一处预期的行为变化。 |
| `test-only` | 只改 `tests/`（以及 fixture 数据）；被测代码不变。 |
| `ci` | 只改 `.github/workflows/`、pin 常量和与之一致性相关的测试。 |
| `baseline` | 只新增 `docs/refactor/` 下的文件（工具、基线数据）。（新增类型，用户列表中没有。） |
| `doc` | 只改文档。（新增类型。） |
| `merge` | 把一个分支合入另一个分支，冲突只按卡片规定的方向解决。（新增类型。） |

---

## 2. 执行环境与通用规则

### 2.1 仓库与工作树（执行模型第一次开始前创建一次）

```bash
# ConfFlow：执行分支建在 docs/refactor-plan（main + 本方案）之上
git -C /opt/ConfFlow worktree add -b refactor/diet /opt/cf-worktrees/exec-cf docs/refactor-plan
# JobDesk：执行分支建在 master 9beeaf2
git -C /opt/jobdesk-v2-v4 worktree add -b refactor/diet /opt/cf-worktrees/exec-jd 9beeaf2fc52932dbf9ca183898f1aa92c4366d97
# JobDesk pin 检出：ConfFlow 跨仓测试只接受 EXPECTED_JOBDESK_SHA
git -C /opt/jobdesk-v2-v4 worktree add --detach /opt/cf-worktrees/jd-pin 9beeaf2fc52932dbf9ca183898f1aa92c4366d97
# 输入简化分支（IS 阶段才需要）
git -C /opt/ConfFlow worktree add /opt/cf-worktrees/exec-cf-is implementation/input-simplification
git -C /opt/jobdesk-v2-v4 worktree add /opt/cf-worktrees/exec-jd-is implementation/input-simplification
```

`docs/refactor/tools/env.sh`（B0.1 创建）导出：

```bash
export CF=/opt/cf-worktrees/exec-cf
export JD=/opt/cf-worktrees/exec-jd
export JDPIN=/opt/cf-worktrees/jd-pin
export CFIS=/opt/cf-worktrees/exec-cf-is
export JDIS=/opt/cf-worktrees/exec-jd-is
export BASE=$CF/docs/refactor/baseline
export TOOLS=$CF/docs/refactor/tools
export PYTHONDONTWRITEBYTECODE=1
export QT_QPA_PLATFORM=offscreen
```

- 解释器：`/usr/bin/python3`（3.12.3，已安装 pytest、scipy、PySide6、ruff、black、mypy，以及 JD mypy 需要的 `types-PyYAML>=6.0.12`）。系统 Python 受 PEP 668 管理，以上包装在 `/usr/local/lib/python3.12/dist-packages`；执行模型不得自行安装或升级任何包，缺包时停止并报告（G9）。在 `$CF` 中以 `python3 -m …` 运行时，cwd 下的 `confflow` 优先于 `/opt/ConfFlow` 的 editable 安装。
- **禁止**在 `/opt/ConfFlow`、`/opt/jobdesk-v2-v4` 的主工作树中改文件或切分支。
- 不 push、不开 PR、不合并到 `main`/`master`。推送和合并由用户决定（IS.5 例外，它需要用户逐次批准）。

### 2.2 运行 ConfFlow 测试

```bash
source $TOOLS/env.sh
cd $CF && JOBDESK_V2_SRC=$JDPIN/src python3 -m pytest -q -o addopts="" -p no:cacheprovider \
  --junitxml=/tmp/refactor-acc/<CARD>/cf-junit.xml
```

跨仓测试（`-m cross_repo`）要求 `$JDPIN` 的 HEAD 等于 `tests/v4/jobdesk_integration.py:52` 的 `EXPECTED_JOBDESK_SHA`，否则失败（`tests/v4/conftest.py:85-100`）。每次 re-pin 卡完成后执行 `git -C $JDPIN checkout --detach <新 SHA>`。

### 2.3 运行 JD 测试（必须绑定 producer）

JD 测试把 `/opt/ConfFlow` 写死为 producer 的 cwd，并把 `/opt/ConfFlow/.venv/bin/python` 写死为 producer 解释器（Q15；`tests/contract_fixtures.py:38-39`、`tests/application/test_p0_boundary.py:51-52`、`tests/application/test_confflow_v4_workflows.py:69`@9beeaf2）。`docs/refactor/tools/run_jd_tests.sh` 在私有 mount namespace 中把指定的 CF 工作树绑定到 `/opt/ConfFlow`，再把原来的 `.venv` 绑回去，并设置 `PYTHONPATH=<JD>/src`、`QT_QPA_PLATFORM=offscreen`。这个绑定不影响其他进程，也不切换 `/opt/ConfFlow` 的分支：

```bash
$TOOLS/run_jd_tests.sh --cf $CF --jd $JD --junit /tmp/refactor-acc/<CARD>/jd-junit.xml            # 全量
$TOOLS/run_jd_tests.sh --cf $CF --jd $JD -- --collect-only -q                                     # 只收集
$TOOLS/run_jd_tests.sh --cf $CF --jd $JD -- tests/application/test_p0_boundary.py -rf             # 指定测试
```

**执行模型只能通过这个脚本运行 JD 测试，不得直接调用 `unshare` 或 `mount`（G10）。**

脚本解决了两个环境问题：不绑回 `.venv` 时，约 49 个 JD 测试会以 "ConfFlow interpreter unreachable" 跳过；不设 `PYTHONPATH` 时，`tests/application/test_cards_library.py::test_library_modules_import_without_qt` 会失败。

`$CF` 必须是与该 JD 提交配对的 ConfFlow 提交（通常是 `refactor/diet` 当前已验收的 HEAD）。静态检查：`cd $JD && ruff check src tests && ruff format --check src tests && mypy`。

### 2.4 写方案时实测的基线状态（仅供参考，以 B0.1 的产物为准）

- ConfFlow `main` d5a40ae：`--collect-only` 共 4535 项。全量：4516 passed、12 skipped；跨仓 7 项在 `JOBDESK_V2_SRC` 指向 9beeaf2 检出时全部通过（约 17.5 + 1.7 分钟）。
- JD `master` 9beeaf2，按 §2.3 绑定 d5a40ae：2411 项，**23 failed**、2381 passed、7 skipped（约 3.5 分钟），原因见 Q0。
- 预演 J0a+J0b（在 JD master 副本上）：2412 项，2405 passed、7 skipped、0 failed（约 3 分钟）。
- TS1 engine 三种 backend 在本机各约 20–60 秒；engine 报告捕获（`tests/v4/test_confgen_*.py`，274 个测试）约 4 分钟，两次运行结果逐字节相同。
- `confflow v4 contract --json` 和 `v4 boundary --json` 在源码树中运行时字节确定（`producer.commit` 为 `null`），可以跨提交比较。

### 2.5 通用禁止事项（每张卡都适用，卡片中以"G1–G11"引用）

- G1 不得修改白名单以外的任何文件（含格式化工具顺手改的文件）。
- G2 `delete` / `move` 卡不得新增逻辑：不得新增函数、类、条件分支、异常处理、默认值。
- G3 不得为了让测试通过而删除、跳过（`skip`/`skipif`/`xfail`/`importorskip`）或放宽测试断言。被删代码的守护测试可以删除，但必须在卡片中逐条声明。
- G4 不得重命名任何保留下来的符号、文件、测试。
- G5 不得改动 `docs/refactor/baseline/` 的已有文件（Phase 3 / IS.0 / IS.5 卡新增"检查点"文件除外，见各卡）。
- G6 不得改变科学行为（TS1、ring、torsion、stereo、atom ordering）。只要 golden 有差异就停止，在 LOG 中写明差异并升级，不得自行判定为"预期变化"。
- G7 每张卡恰好一个提交；提交前 `git status` 必须干净（没有未跟踪的残留）。
- G8 不得 push、不得改 `main`/`master`、不得切换 `/opt/ConfFlow` 或 `/opt/jobdesk-v2-v4` 主工作树的分支。
- G9 发现卡片与代码不符（锚点找不到、行为与描述不同），或者环境缺包、命令失败时，停止并在提交前报告，不得自行变通。
- G10 不得直接调用 `unshare`、`mount`、`sudo`、`pip`/`apt` 安装命令；JD 测试只通过 `docs/refactor/tools/run_jd_tests.sh` 运行。
- G11 涉及构象比较的夹具必须使用带氢的真实分子（用户 2026-10-02 增补）。没有氢的夹具上，绕含末端原子的键的旋转只是刚体转动，不能用于判断构象是否等价；这类夹具只能作为回归记录，结论不得来自它们。
- G12 对 main/master（含本地）的任何合并、提交、reset，以及任何推送，都必须先报告并等用户明确批准；只有 `refactor/*` 与 `docs/refactor-plan` 可以自行推送。发现已经发生的类似情况，立刻告知用户，不等被问（用户 2026-10-03 增补）。

### 2.6 每张卡的通用验收（卡片中写"标准验收"即指本节）

验收由验收方按 `ACCEPTANCE.md` 执行。执行模型提交前也必须自己跑一遍，并把结果写进提交信息的 `Verification:` 段。

```bash
source $TOOLS/env.sh
CARD=<卡片ID>; mkdir -p /tmp/refactor-acc/$CARD
# 1) 静态检查
#    CF：cd $CF && ruff check . && mypy confflow && git diff --name-only HEAD~1 -- '*.py' | xargs -r ls 2>/dev/null | xargs -r black --check
#    JD：cd $JD && ruff check src tests && ruff format --check src tests && mypy
# 2) 测试清单与结果
python3 $TOOLS/test_inventory.py collect --repo cf --out /tmp/refactor-acc/$CARD/cf-collect.txt   # 或 --repo jd
python3 $TOOLS/test_inventory.py run     --repo cf --out /tmp/refactor-acc/$CARD/cf-outcomes.json
python3 $TOOLS/test_inventory.py diff --prev <上一张已验收卡的 collect 文件> --cur /tmp/refactor-acc/$CARD/cf-collect.txt \
        --declared <卡片声明的删除数>   # 期望：新增 0，删除数 == 声明数，删除的节点全部属于卡片声明的文件/测试
# 3) golden
python3 $TOOLS/golden_check.py --base $BASE --cf $CF --jd-src $JD/src --out /tmp/refactor-acc/$CARD/golden.json
#    期望：TS1 三份、engine 报告（除卡片声明随测试删除的报告外）、contract/boundary sha256、JD contract_key 全部与当前检查点一致
```

期望结果：

1. 静态检查全部通过。
2. 测试结果：失败集合与上一张已验收卡相同（基线已知失败不得增减），其余全部通过。
3. collect 数量变化 = 卡片声明的删除数，且没有新增节点（`test-only` 与 `logic` 卡另有声明的除外）。
4. golden 不变（Phase 3 卡改用卡内声明的 contract 检查点）。

### 2.7 提交信息格式

```
<type>(<scope>): <一句话目标>

Card: <ID>
Base: <父提交 sha>
Removed-Tests: <N>（逐条列出节点 id 或 "file::*"）
Added-Tests: <N>
Behavior-Change: none | <逐条列出>
Verification:
  static: pass
  tests: <passed>/<failed>/<skipped>（失败集合 == 上一检查点：yes）
  collect: <prev> -> <cur>（差 <N>）
  golden: unchanged | checkpoint <file>
```

### 2.8 执行模型的调用方式

- J0a：Opus 子代理执行。
- J0b 起：**Sonnet 5.5 子代理**（Claude Code 的 Agent 工具，`subagent_type: general-purpose`，`model: sonnet`），由验收方发起，一次一张卡。子代理看不到验收方与用户的对话，只能依据交接提示词、PLAN.md 和 ACCEPTANCE.md 工作，权限与验收方会话相同。
- 交接提示词必须包含：卡片 ID；PLAN.md 和 ACCEPTANCE.md 的路径；"只执行这一张卡、提交后停止"；G1–G10 全文；"JD 测试只能通过 `run_jd_tests.sh` 运行，禁止直接或间接（`bash -c`、`python3 -c` 等）调用 unshare/mount"；"不得 push，包括 `git -C … push`"；"命令被拒绝或失败时如实报告，不得编造输出"；退回时附上验收方的具体要求，并要求用 `git commit --amend` 重新交付。
- 验收方不采信执行模型报告的任何命令输出，一律自己重跑（ACCEPTANCE §4）。
- 同一张卡被退回两次后，不再第三次交给同一个模型，而是报告用户，由用户决定是否换更强的模型。

附：曾经评估过 CodeBuddy `glm-5.3-flash`（2026-10-02），后放弃。实测记录见 LOG.md：`--allowedTools` 只能写单项 `Bash(*)`；禁止规则必须逐条传参，而且只按前缀匹配，可以被 `git -C … push`、`bash -c` 绕过；Bash 被拒时，该模型曾编造命令输出。

---

## 3. J0（JD 兼容修复）与 Phase 0（基线冻结）

J0 的提交在 JD 的 `refactor/diet` 分支上（从 9beeaf2 建立，见 §2.1）。验收时按 §2.3 绑定 `$CF`，`$CF` 此时等于 `docs/refactor-plan` 的 HEAD（main d5a40ae + 本方案文档，producer 代码与 d5a40ae 相同）。

### J0a — 移植 92d48f1 中 manifest 解析器的修复

- ID：J0a ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：用户确认本方案
- 目标：JD master 能解析 CF main 发布的 V4 editor manifest（`confgen.v3.*` 字段的 `item_type: "object"`）。
- 类型：`logic`
- 允许修改的文件：`src/jobdesk_v2/application/editor/manifest.py`
- 具体步骤（@9beeaf2；对照 `git show 92d48f1 -- src/jobdesk_v2/application/editor/manifest.py`）：
  1. L90：`ValueType` 增加 `"object"`。
  2. L97：`_ITEM_EDITORS` 增加 `"json"`，连同 92d48f1 在其上方加的 3 行注释原样移植。
  3. L276：`ItemType` 增加 `"object"`。
  4. `FieldDescriptor` docstring 中关于 `item_type` 的段落（L287-291）可以按 92d48f1 原样更新。
  5. **不得**移植 92d48f1 在同一文件中的其他改动（`is_step_scoped` 对 `{id}` 的支持、`step_selector` 属性），它们属于输入简化功能。
- 禁止事项：G1–G9；不得改任何测试。
- 预期行为变化：manifest 解析接受 `item_type: "object"` 和 `json` 编辑器上的 `item_type`；绑定 CF d5a40ae 时，V4 编辑模型可以建立。
- 验收命令：JD 标准验收（§2.3，含 `PYTHONPATH`）。
  期望：Removed-Tests 0、Added-Tests 0；失败集合**恰好**是以下 8 项（写方案时预演得到；与此不同就升级）：
  ```
  tests/application/test_confflow_v4_contract.py::TestRealBytesRoundtrip::test_parses_real_producer_bytes
  tests/application/test_confflow_v4_contract.py::TestRealBytesRoundtrip::test_every_digest_recomputes_to_claim
  tests/application/test_confflow_v4_contract.py::TestRefreshRace::test_compatible_producer_move_keeps_bytes_identical
  tests/application/test_confflow_v4_contract.py::TestRefreshRace::test_incompatible_schema_change_is_needs_attention_not_edit
  tests/application/test_confflow_v4_e2e.py::TestDoubleDigestAndRecipe::test_double_verifies_real_digests
  tests/application/test_confflow_v4_workflows.py::TestRecipeRoundtrips::test_all_recipes_author_native_documents
  tests/application/test_p0_boundary.py::TestLiveProducerParity::test_vendored_fixture_matches_live_producer[boundary_protocol.json]
  tests/gui/test_confflow_v4_cards.py::TestCapabilityCardsRender::test_all_eleven_cards_render
  ```
  J0a 已于 2026-10-02 按此验收通过（见 LOG）。
- 提交信息模板：`fix(editor): accept object item types published by the V4 producer manifest` + 通用尾部（Behavior-Change 如上，并写明 `Ported-From: 92d48f1 (manifest parser only)`）。

### J0b — 移植测试侧修复并重新 vendor P0 boundary fixture（Q0b 已确认）

- ID：J0b ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J0a（已验收，JD `refactor/diet` 5847bc7）
- 目标：JD 测试与 CF main 的 producer 对齐，JD 全绿。
- 类型：`test-only`
- 允许修改的文件：`tests/application/test_confflow_v4_contract.py`、`tests/application/test_confflow_v4_e2e.py`、`tests/application/test_confflow_v4_workflows.py`、`tests/gui/test_confflow_v4_cards.py`、`tests/fixtures/p0_boundary/PROVENANCE.json`、`tests/fixtures/p0_boundary/boundary_protocol.json`、`tests/fixtures/p0_boundary/compatibility_cases.json`、`tests/fixtures/p0_boundary/jcs_vectors.json`、`tests/fixtures/p0_boundary/named_binding_workflow.json`
- 具体步骤：
  1. 逐字移植 92d48f1 对上述 4 个测试文件的改动：`git -C $JD diff 9beeaf2 92d48f1 -- <4 个文件> | git -C $JD apply`。这些 diff 只包含：`canonical_sha256` 改为 `jcs_sha256` / `jcs_bytes`；配方数 11 改为 12；新增 `test_canonical_bytes_render_floats_as_jcs`；`test_all_eleven_cards_render` 改名为 `test_all_twelve_cards_render`。改名是逐字移植的一部分，作为 G4 的明示例外，便于以后与 IS 分支合并。
  2. 重新 vendor：`cd $JD && python3 scripts/sync_p0_boundary_fixtures.py --source $CF/docs/internal/fixtures/p0_boundary`。`PROVENANCE.json` 的 `source_commit` 会记录 `$CF` 的 HEAD（`docs/refactor-plan` 的提交，它与 d5a40ae 的差别只有 `docs/refactor/`），在提交信息中写明这一点。
- 禁止事项：G1–G3、G5–G9；不得手改 fixture；不得移植 92d48f1 中其他文件的改动。
- 验收命令：JD 标准验收。期望：Removed-Tests 1（`…::test_all_eleven_cards_render`）、Added-Tests 2（`…::test_all_twelve_cards_render`、`…::test_canonical_bytes_render_floats_as_jcs`）；collect 2411 → 2412；**0 failed、7 skipped**。仍有失败 → 停止并升级。
- 提交信息模板：`test(contract): align V4 tests with the ConfFlow d5a40ae producer` + 通用尾部（`Ported-From: 92d48f1 (4 test files)`；`Revendored-From: <CF sha>`）。

## 3a. Phase 0：基线冻结

### B0.1 — 生成全部基线与验收工具

- ID：B0.1 ／ 仓库：ConfFlow ／ 分支：`refactor/diet`（建在 `docs/refactor-plan` 上）／ 前置：J0b 验收通过（JD 全绿）
- 目标：在任何代码改动之前，冻结两仓测试清单与结果、TS1 engine 结果、engine 报告快照、contract/boundary 摘要和 JD contract_key。
- 类型：`baseline`
- 允许修改的文件（全部为新增；`docs/refactor/tools/run_jd_tests.sh` 已随方案提交，本卡不得修改）：
  - `docs/refactor/tools/env.sh`
  - `docs/refactor/tools/ts1_engine.py`
  - `docs/refactor/tools/capture_engine_reports.py`
  - `docs/refactor/tools/contract_digests.py`
  - `docs/refactor/tools/test_inventory.py`
  - `docs/refactor/tools/golden_check.py`
  - `docs/refactor/tools/json_paths_diff.py`
  - `docs/refactor/tools/reachability.py`
  - `docs/refactor/tools/diff_guard.py`
  - `docs/refactor/baseline/README.md`
  - `docs/refactor/baseline/ts1/default.json`、`rigid.json`、`flexible.json`
  - `docs/refactor/baseline/engine_reports/*.json`
  - `docs/refactor/baseline/contract.json`、`contract.full.json`、`boundary.full.json`
  - `docs/refactor/baseline/inventory/cf-collect.txt`、`cf-outcomes.json`、`jd-collect.txt`、`jd-outcomes.json`
- 具体步骤（基于 `docs/refactor-plan` HEAD = d5a40ae + 方案提交；JD 基于 J0b 的提交）：
  1. `env.sh`：内容见 §2.1。
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
  8. `reachability.py --cf DIR`：从 `confflow.main`、`confflow.v4cli`、`confflow.cli`、`confflow.control_worker`、`confflow.fixture_agent`、`confflow.remote.worker`、`confflow.worker_attempt` 出发，按 AST import 闭包计算可达模块（相对 import 要解析；`importlib.import_module("字面量")` 计入；各包 `_LAZY_EXPORTS` 表中的目标计入，前提是有模块以 `from <包> import <名字>` 使用它）。输出不可达模块列表。
  9. `diff_guard.py --repo DIR --base SHA --head SHA --type TYPE --whitelist FILE`：供验收方使用，规则见 `ACCEPTANCE.md` §2。
  10. 运行并写入基线：
      - `ts1_engine.py` 三种 backend 各运行**两次**，两次输出必须逐字节相同，然后写入 `baseline/ts1/`。
      - `capture_engine_reports.py` 运行两次，`diff -r` 相同后写入 `baseline/engine_reports/`。
      - `contract_digests.py --cf $CF --jd-src $JD/src --out $BASE/contract.json`。
      - `test_inventory.py collect/run` 对 CF（`JOBDESK_V2_SRC=$JDPIN/src`）和 JD（按 §2.3 绑定 `$CF`）各运行一次，写入 `baseline/inventory/`。
  11. `baseline/README.md`：记录生成命令、两仓 commit sha、Python 版本、`pip freeze` 中 numpy/scipy/pydantic/rdkit/PySide6 的版本、各文件的 sha256、CF 与 JD 的已知失败列表（逐条 nodeid）。
- 禁止事项：G1–G9。不得改 `confflow/`、`tests/`、`scripts/`、`.github/`。不得在工具中写入任何预期的科学结果。
- 验收命令：
  ```bash
  source $TOOLS/env.sh; cd $CF
  git diff --name-only HEAD~1 | grep -v '^docs/refactor/' && echo FAIL || echo ok
  ruff check docs/refactor/tools && black --check docs/refactor/tools
  for b in default rigid flexible; do python3 $TOOLS/ts1_engine.py --backend $b --out /tmp/refactor-acc/B0.1/$b.json && cmp /tmp/refactor-acc/B0.1/$b.json $BASE/ts1/$b.json; done
  python3 $TOOLS/golden_check.py --base $BASE --cf $CF --jd-src $JD/src
  python3 $TOOLS/test_inventory.py collect --repo cf --out /tmp/refactor-acc/B0.1/cf.txt && diff /tmp/refactor-acc/B0.1/cf.txt $BASE/inventory/cf-collect.txt
  ```
  期望：只新增 `docs/refactor/`；工具通过 ruff/black；重跑结果与基线逐字节相同；collect 清单与基线相同；CF collect 数为 4535（应与 §2.4 一致，不一致就停止报告）。
- 提交信息模板：
  ```
  chore(refactor): freeze phase-0 baseline and acceptance tooling

  Card: B0.1
  Base: <sha>
  Removed-Tests: 0
  Added-Tests: 0
  Behavior-Change: none
  Verification: ...
  ```

### B0.2 — 修复 B0.1 验收时发现的两个工具缺陷

- ID：B0.2 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：B0.1（已验收，eec4e80）
- 目标：`reachability.py` 不再把有可达子模块的包列为不可达；`test_inventory.py diff` 支持声明新增节点数。
- 类型：`baseline`（只改 `docs/refactor/tools/` 下已有的工具文件；不改 `baseline/` 下任何文件）
- 允许修改的文件：`docs/refactor/tools/reachability.py`、`docs/refactor/tools/test_inventory.py`
- 具体步骤：
  1. `reachability.py`：Python 导入一个模块时，会先导入它的所有父包并执行其 `__init__`。因此，凡是可达模块的父包都必须计为可达，并且这些父包 `__init__` 里的 import（包括 `from .x import y`）也要计入可达闭包。修复后，输出中不得出现任何有可达后代的包。输出格式不变（每行一个模块名）。
  2. `test_inventory.py diff`：新增可选参数 `--declared-added N`（默认 0）。当新增节点数不等于 N 时判 FAIL；默认值 0 时行为与现在完全相同（有新增即 FAIL）。删除检查（`--declared`、`--allowed-files`）不变。新增节点清单仍照常打印。
- 禁止事项：G1–G10；不得改 `baseline/`、`run_jd_tests.sh` 及其他工具；不得改变两个工具的现有参数和默认行为。
- 验收命令（验收方运行）：
  ```bash
  cd $CF && python3 $TOOLS/reachability.py --cf $CF > /tmp/refactor-acc/B0.2/reach.txt
  # 期望：不含 confflow.domain / science / workflow / application / producer / programs / remote / analysis 等有可达子模块的包；
  #       叶子模块名单仍包含 workflow._retired_runtime、workflow.helpers、workflow.validation、shared.confgen_params、core.validation、
  #       blocks.viz.report、confts、calc.runner、blocks.refine.processor、blocks.confgen.generator、workflow.composition；
  #       不含 workflow.step_naming、workflow.export、science.torsion、science.confgen.engine、execution.confgen_executor、producer.contract
  A=/tmp/refactor-acc/J0b/acc   # prev.txt 与 cur.txt 是 J0b 前后的 JD collect 清单：删 1 增 2
  python3 $TOOLS/test_inventory.py diff --prev $A/prev.txt --cur $A/cur.txt --declared 1 --declared-added 2 --allowed-files tests/gui/test_confflow_v4_cards.py   # 期望退出码 0
  python3 $TOOLS/test_inventory.py diff --prev $A/prev.txt --cur $A/cur.txt --declared 1 --declared-added 1 --allowed-files tests/gui/test_confflow_v4_cards.py   # 期望非 0
  python3 $TOOLS/test_inventory.py diff --prev $A/prev.txt --cur $A/cur.txt --declared 1 --allowed-files tests/gui/test_confflow_v4_cards.py                       # 期望非 0（与修复前相同）
  ```
- 提交信息模板：`chore(refactor): fix package false positives in reachability and add --declared-added` + 通用尾部（Removed-Tests: 0，Added-Tests: 0）。

---
## 4. Phase 1：ConfFlow 外围删除（contract 与 boundary 字节必须不变）

### C1.1 — 合并两个跨仓 CI 工作流

- ID：C1.1 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：B0.1
- 目标：删除 `paired-jobdesk-compatibility.yml`，把它独有的手动触发并入 `jobdesk-contract.yml`。
- 类型：`ci`
- 允许修改的文件：
  - `.github/workflows/paired-jobdesk-compatibility.yml`（删除）
  - `.github/workflows/jobdesk-contract.yml`
  - `tests/test_release_workflow.py`
- 具体步骤（@d5a40ae）：
  1. 已核实两者的差异：都 pin `JOBDESK_COMPAT_SHA: "9beeaf2…"`，最后一步运行相同的 6 个测试文件（`jobdesk-contract.yml:116-123`、`paired-jobdesk-compatibility.yml:111-119`）。前者多出 push:main 触发和"已安装 wheel 解析 boundary"一步（L66-110）；后者多出 `workflow_dispatch`（带 `confflow_ref`/`jobdesk_ref` 两个输入，L16-28）。后者的 `jobdesk_ref` 必须等于 pin，否则 L97-106 的检查失败，所以去掉这个输入不会损失能力。
  2. 在 `jobdesk-contract.yml` 的 `on:`（L12-15）中加入 `workflow_dispatch:`（不带 inputs）。删除头部注释中"and to the paired workflow"这一句（L8-9）。其余内容不变。
  3. 删除 `paired-jobdesk-compatibility.yml`。
  4. `tests/test_release_workflow.py`：删除 L19 的 `PAIRED_COMPATIBILITY_WORKFLOW` 常量，把 L50 的循环元组改为 `(JOBDESK_CONTRACT_WORKFLOW,)`。断言内容不改。
- 禁止事项：G1–G9；不得改变 pin 值，不得删除 wheel 校验步骤，不得修改其他 workflow。
- 验收命令：标准验收，另加
  ```bash
  cd $CF && python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/jobdesk-contract.yml')); t=d.get(True) or d.get('on'); assert set(t)=={'push','pull_request','workflow_dispatch'}, t"
  test ! -e .github/workflows/paired-jobdesk-compatibility.yml
  grep -rn "paired-jobdesk-compatibility" . --exclude-dir=.git | grep -v '^./docs/refactor/' && echo FAIL || echo ok
  python3 -m pytest -q -o addopts="" tests/test_release_workflow.py
  ```
  期望：Removed-Tests 0；`test_release_workflow.py` 仍为 14 项且全部通过；golden 不变。
- 提交信息模板：`ci(cross-repo): fold the paired JobDesk workflow into jobdesk-contract` + 通用尾部（Card: C1.1，Removed-Tests: 0）。

### C1.2 — 删除 `_retired_runtime` 及其 lazy export

- ID：C1.2 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C1.1
- 目标：删除只为抛出"已退役"错误而存在的 stub 模块和指向它的公开名字。
- 类型：`delete`
- 允许修改的文件：
  - `confflow/workflow/_retired_runtime.py`（删除）
  - `confflow/workflow/__init__.py`
  - `confflow/__init__.py`
  - `tests/v4/test_architecture_boundaries.py`
  - `tests/test_core.py`
- 具体步骤（@d5a40ae）：
  1. 删除 `confflow/workflow/_retired_runtime.py`（65 行）。已核实 `confflow/` 中只有 `workflow/__init__.py` 的 lazy 表引用它。
  2. `confflow/workflow/__init__.py`：从 `_LAZY_EXPORTS`（L22-37）中删除目标为 `"._retired_runtime"` 的 9 项：L23-26 与 L32-36。删除 docstring 中描述 stub 的那一句（L8-9 的 "The retired runtime names resolve to fail-closed stubs (...)"）。
  3. `confflow/__init__.py`：从 `_LAZY_EXPORTS`（L76-87）中删除 L78 的 `"run_workflow": (".workflow", "run_workflow")`。
     `tests/test_core.py::test_confflow_package_exports_current_public_api` 守护这个导出，删除后必须随之修改：**只**删除两行断言，`assert hasattr(confflow, "run_workflow")`（L22）和 `assert "run_workflow" in confflow.__all__`（L28）；该测试函数的其余断言一字不改，测试节点不删除（B0.2 之后的 collect 数量不因这一步变化）。这两行是卡片明确声明的断言删除（ACCEPTANCE W2）。
  4. `tests/v4/test_architecture_boundaries.py`：删除 `TestRuntimeIsolation::test_importing_workflow_package_stays_lazy`（L952-968）和 `TestRetiredRuntimeBoundary::test_retirement_stubs_fail_closed`（L1045-1052）；删除 `RETIRED_RUNTIME_MODULES` 上方注释中提到 stub 的最后一句（L289-291）。
- 禁止事项：G1–G10；除上面声明的 `tests/test_core.py` 两行断言外，不得删除或修改任何断言；不得改动 `confflow/cli.py` 中的 `run_workflow`（它是 `formal_v4_runner` 的别名，`cli.py:30`，与本卡无关）。
- 验收命令：标准验收，另加
  ```bash
  cd $CF && git grep -n "_retired_runtime\|RetiredRuntimeError" -- confflow tests scripts && echo FAIL || echo ok
  python3 -c "from confflow import run_workflow" 2>&1 | grep -q "cannot import name 'run_workflow'" && echo ok
  ```
  期望：Removed-Tests 2（上面两个 nodeid）；golden 不变；contract/boundary sha256 不变。
- 提交信息模板：`refactor(workflow): delete the retired-runtime stubs and their lazy exports` + 通用尾部。

### C1.3 — 删除 `workflow/validation.py`、`workflow/helpers.py`、`shared/confgen_params.py`

- ID：C1.3 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C1.2
- 目标：删除三个从任何入口都不可达、只被测试引用的模块。
- 类型：`delete`
- 允许修改的文件：
  - `confflow/workflow/validation.py`、`confflow/workflow/helpers.py`、`confflow/shared/confgen_params.py`（删除）
  - `confflow/workflow/__init__.py`
  - `tests/test_workflow_helpers.py`、`tests/test_workflow_validation_extra.py`、`tests/test_confgen_params_consistency.py`（删除）
  - `tests/v4/test_architecture_boundaries.py`
- 具体步骤（@d5a40ae）：
  1. 执行前核对：`python3 $TOOLS/reachability.py --cf $CF` 的输出必须包含这三个模块；`git grep -n "workflow.helpers\|workflow.validation\|confgen_params\|from .helpers\|from .validation" -- confflow` 只能命中这三个文件自身和 `workflow/__init__.py`。不满足就停止（G9）。
  2. 删除三个模块（145 + 87 + 201 行）。
  3. `confflow/workflow/__init__.py`：C1.2 之后 `_LAZY_EXPORTS` 只剩 `helpers` 的 4 项和 `validation` 的 1 项，把它们删除。表变空后，删除 `_LAZY_EXPORTS`、`__all__`、`__getattr__` 以及只为它们服务的 `import importlib` / `from typing import Any`。docstring 中关于 helpers/validation 和 lazy 解析的段落一并删除，只保留第一句。
  4. 删除三个测试文件（5 + 10 + 16 = 31 项）。
  5. `tests/v4/test_architecture_boundaries.py`：在 `REMOVED_LEGACY_MODULES`（L240 起）中加入 `"confflow.workflow.helpers"`、`"confflow.workflow.validation"`。它们仍保留在 `FORBIDDEN_LEGACY_MODULES`（L74、L76）中，用于 import 禁令。`test_legacy_module_inventory_is_intentional` 的参数集合是两者的并集，所以节点数不变。
- 禁止事项：G1–G9；不得删除 `confflow/workflow/step_naming.py`、`confflow/workflow/export.py`（`export.py:17` 仍在使用 step_naming，可达）。
- 验收命令：标准验收，另加 `cd $CF && git grep -n "confflow.workflow.helpers\|confflow.workflow.validation\|shared.confgen_params" -- confflow scripts`，只允许命中 0 处。
  期望：Removed-Tests 31（三个文件的全部节点）；golden 不变。
- 提交信息模板：`refactor(workflow): delete unreachable workflow helpers/validation and confgen_params` + 通用尾部。

### C1.4 — 删除 `_analysis_capabilities` 的静默 fallback

- ID：C1.4 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C1.3
- 目标：`confflow.analysis.registry` 一定存在，删除捕获 `Exception` 后返回冻结值的死分支。
- 类型：`delete`
- 允许修改的文件：`confflow/producer/contract.py`
- 具体步骤（@d5a40ae）：
  1. `_analysis_capabilities()`（L71-101）：删除 `try:` 和 `except Exception: … return (… "frozen")`（L79、L92-101），把 try 体（L80-91）反缩进一级，其余逐字不变。返回值中的来源标签仍为 `"registry"`。
  2. docstring（L72-78）删除"otherwise the frozen reaction-profile strings below are declared"这半句。
  3. `ANALYSIS_REACTION_PROFILE_CAPABILITY` / `ANALYSIS_REACTION_PROFILE_CONTRACT` 保留：`producer/__init__.py:14-15` 重新导出，`tests/v4/test_v46_producer_contract.py:255-266` 使用。
- 禁止事项：G1–G9；不得把 `importlib.import_module` 改成直接 import（那是逻辑改动）。
- 验收命令：标准验收。期望：Removed-Tests 0；**contract 与 boundary 的 sha256 与基线逐字节相同**；`tests/v4/test_v46_producer_contract.py` 全部通过。
- 提交信息模板：`refactor(producer): drop the dead analysis-capabilities fallback` + 通用尾部。

---

## 5. J1：JD 删除 `evaluate_compatibility`

### J1 — 删除 JD 侧未被生产代码调用的兼容性求值器

- ID：J1 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet`（JD）／ 前置：B0.1
- 目标：删除 `evaluate_compatibility` 和 `CompatibilityDecision`。生产代码没有调用方，只有测试使用。
- 类型：`delete`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/contract/boundary.py`
  - `tests/application/test_p0_boundary.py`
- 具体步骤（@9beeaf2）：
  1. 核对：`git grep -n "evaluate_compatibility\|CompatibilityDecision" -- src` 只能命中 `contract/boundary.py`。
  2. `boundary.py`：删除 `CompatibilityDecision`（L282-312，含 `@dataclass` 行）、`evaluate_compatibility`（L314-439），以及它们在 `__all__`（L37-47）中的条目。`COMPATIBILITY_STATUSES` / `COMPATIBILITY_REQUIREMENT_KINDS` / `COMPATIBILITY_REASON_CODES` 保留，`_parse_compatibility`（L187-207）仍在使用。`offered_identity_from_envelope` 保留，到 J3.2 再处理。模块 docstring 中"evaluates compatibility with the producer's own rule"的半句和"compatibility decisions are …"一条删除。
  3. `tests/application/test_p0_boundary.py`：删除 import 中的 `CompatibilityDecision`、`evaluate_compatibility`（L27-28）；删除 `TestCompatibilityParity` 的 `test_every_case_matches_producer_decision`、`test_unsupported_is_not_revalidation`、`test_display_only_change_stays_instantiable`。删除后若 `cases` fixture 不再被使用，一并删除。`test_vocabulary_matches_producer_fixture` 和 vendored 的 `compatibility_cases.json` 保留（fixture 由 ConfFlow 生成，PROVENANCE 校验覆盖它，到 J3.3 才随 re-vendor 删除）。
- 禁止事项：G1–G9；不得改动 `tests/fixtures/p0_boundary/*`。
- 验收命令：JD 标准验收（§2.3 绑定 `$CF` = C1.4 已验收的提交）；`contract_digests.py --jd-src $JD/src` 得到的 `contract_key` 与基线相同。
  期望：Removed-Tests 3；失败集合与基线相同。
- 提交信息模板：`refactor(contract): delete the unused consumer compatibility evaluator` + 通用尾部。

---

## 6. Phase 2：JD 删除 V1/V2 合同实现

Phase 2 的目标状态：JD 只认 V4 contract；没有 V1/V2 解析、没有内置 fallback 快照、没有 file mode。
JD contract_key、CF 的 contract/boundary 字节在本阶段都必须不变。

### J2.1a — 会话默认值改为"无合同"，启动不再借用 fallback 快照

- ID：J2.1a ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J1（已验收，fe85b0d）
- 目标：`WorkflowEditorService()` 和 remote 模式的启动会话不再使用内置的 V1/V2 快照，而是空的"无合同"。
- 类型：`logic`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/contract/models.py`
  - `src/jobdesk_v2/application/editor/service.py`
  - `src/jobdesk_v2/gui/app.py`
  - `tests/application/test_service.py`（只允许新增 2 个测试）
- 具体步骤（@fe85b0d；写方案时已在临时副本上原型验证：改动后全量 JD 测试 2402 passed、0 failed，即不需要修改任何已有测试）：
  1. `models.py`：新增 `@dataclass(frozen=True, slots=True) class NoContract`，满足 `ContractLike`：`editor_manifest` 为空的 `EditorManifest`（`schema="confflow.editor-manifest.v1"`、`contract_key=""`、`workflow_schema_version=""`、`fields=()`），`recipe_catalog` 为空的 `RecipeCatalog`（`schema="confflow.recipe-catalog.v1"`、`contract_key=""`、`recipes=()`），`contract_key=""`，`source="none"`，`capabilities=EditorCapabilities(can_edit_fields=False, can_use_recipes=False, blocked_reasons=("No server contract has been resolved yet.",))`，`is_authoritative=False`，`describe_source()` 返回 `"No server contract"`。字段默认值用 `field(default_factory=...)`。`ContractSource` 从 `Literal["producer", "stable-fallback"]` 改为 `Literal["producer", "stable-fallback", "none"]`。在 `__all__` 中导出 `NoContract`。
  2. `service.py`（约 L28、L181-184）：`from .contract import ContractLike, StableFallbackContractProvider` 改为只 import `ContractLike`，另 `from .contract.models import NoContract`；构造函数里 `contract if contract is not None else StableFallbackContractProvider().resolve()` 改为 `... else NoContract()`。
  3. `app.py`（约 L560）：`service = WorkflowEditorService(contract=StableFallbackContractProvider().resolve())` 改为 `service = WorkflowEditorService()`。`StableFallbackContractProvider` 在 `app.py` 里仍被 L443 使用（J2.1b 才处理），import 保留。
  4. `tests/application/test_service.py` 新增 2 个测试：(a) `WorkflowEditorService()` 默认的 `contract` 不是 authoritative、`manifest.fields` 为空、`catalog.recipes` 为空、`contract_key == ""`；(b) 对该默认会话 `create_blank()` 不抛异常。
- 禁止事项：G1–G10；不得修改任何已有测试；不得改 `StableFallbackContractProvider` 及其他 V1/V2 代码。
- 预期行为变化：(a) 远程模式下，选择服务器之前，编辑区没有字段和配方；(b) `WorkflowEditorService()` 不带参数时不再使用内置快照。
- 验收命令：JD 标准验收（`run_jd_tests.sh --cf <只读 CF 参考树> --jd <JD 树>`）。期望：2409 + 2 = 2411 项，2404 passed、7 skipped、0 failed；`contract_key` 与基线相同。
- 提交信息模板：`feat(editor): start sessions without the bundled V1/V2 snapshot` + 通用尾部。

### J2.1b — 非 V4 服务器报"不可用"，V4 会话不再借用会话的配方目录

- ID：J2.1b ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.1a
- 目标：用户确认的行为变化 (b)：读不到 V4 合同的服务器显示"不可用"，不再退回到只读 fallback。同时保留原来显示给用户的具体原因，不泄露远端文本。
- 类型：`logic`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/contract/remote_v4.py`
  - `src/jobdesk_v2/gui/new_run/presenter.py`
  - `src/jobdesk_v2/gui/app.py`
  - `tests/application/test_contract_remote_v4.py`、`tests/application/test_remote.py`、`tests/application/test_contract_providers.py`（只允许改写下面列出的 14 个测试）
- 写方案时的原型结果（@fe85b0d 加 J2.1a；全量 JD 测试）：下面 14 个测试失败，其余全部通过（2388 passed）。GUI 测试不受影响。
  ```
  test_contract_providers.py::TestProviderProtocol::test_the_remote_provider_refuses_rather_than_pretends
  test_contract_remote_v4.py::TestProviderDispatch::test_the_fetch_asks_the_v4_command
  test_contract_remote_v4.py::TestProviderDispatch::test_a_v1_document_fails_closed_to_the_compatibility_fallback
  test_contract_remote_v4.py::TestProviderDispatch::test_a_v2_document_fails_closed_to_the_compatibility_fallback
  test_contract_remote_v4.py::TestProviderDispatch::test_a_degraded_answer_is_not_cached_as_a_contract
  test_contract_remote_v4.py::TestUnknownDocumentFailsClosed::test_an_unknown_schema_is_refused_with_an_explicit_diagnostic
  test_contract_remote_v4.py::TestUnknownDocumentFailsClosed::test_a_document_with_no_schema_at_all_is_refused
  test_contract_remote_v4.py::TestUnknownDocumentFailsClosed::test_invalid_json_is_refused
  test_contract_remote_v4.py::TestUnknownDocumentFailsClosed::test_a_transport_failure_degrades_with_producer_unavailable
  test_contract_remote_v4.py::TestUnknownDocumentFailsClosed::test_a_failed_command_degrades_with_command_failed
  test_contract_remote_v4.py::TestCacheAndService::test_the_service_publishes_a_degraded_answer_as_the_restricted_value
  test_remote.py::TestProductionContractCommand::test_the_provider_asks_config_contract_without_a_version
  test_remote.py::TestRemoteErrorSanitization::test_transport_text_never_reaches_the_contract_diagnostics
  test_remote.py::TestRemoteErrorSanitization::test_remote_stderr_never_reaches_the_contract_diagnostics
  ```
- 具体步骤（@J2.1a 之后）：
  1. `remote_v4.py`：在 `V4ContractRefresh` 之前新增 `class ContractUnavailableError(ValueError)`：构造参数 `diagnostic: ContractDiagnostic`，`super().__init__(diagnostic.message)`，保存为 `self.diagnostic`。
  2. `V4ProducerContractProvider._fetch`（约 L318-380）里所有 `return self._degrade(...)` 改为 `raise ContractUnavailableError(<同一个 ContractDiagnostic>) from exc`（在 `except ... as exc` 里）或不带 `from`（`if not result.succeeded` 分支）。诊断对象（`severity`、`code`、`artifact`）保持不变；其中 message 里的 "The compatibility fallback is in use." 这句话和 `_acquisition_diagnostic` 里 "so only the compatibility fallback is available." 一类提到 fallback 的半句删除，其余文字不变。返回类型改为 `ResolvedContract`，`resolve()` 的返回类型同步。
  3. 删除 `_degrade` 方法、构造函数的 `fallback` 与 `fallback_artifacts` 参数及其赋值、不再使用的 import（`replace`、`FallbackArtifacts`、`StableFallbackContractProvider`、`ProducerContractProvider` 等，以 ruff 报告为准）。
  4. `presenter.py`：(i) `_on_contract_failed`（约 L760-770）里把 `ContractUnavailableError` 加入"允许显示 `str(exc)`"的元组（与 `ProducerValidationError`、`EndpointChangedError` 并列）——这些 message 是 JD 自己写的有界句子，不含远端文本；(ii) `_adopt_v4_field_model`（约 L703）里 `recipe_catalog=self._service.catalog` 改为 `NoContract().recipe_catalog`（V4 的配方由卡片编辑器的 producer provider 提供，会话的配方目录在 V4 下本来就不可见。Q8(c) 的核实证据 @9beeaf2：V4 合同生效时页面安装 `V4ProducerAuthoringProvider`，能力面板渲染的是它的 recipe catalog（`gui/new_run/page.py:242-262`）；该 provider 直接读 V4 合同的 `recipe_catalog["recipes"]`（`application/cards/v4_provider.py:128`、`:139`）；会话的 recipe 选择器在 V4 下被禁用，`create_from_recipe` 被拒绝，由现有测试 `tests/gui/test_new_run_v4_contract.py::test_the_legacy_recipe_entry_is_not_active_under_v4` 守护。执行时先重新运行这个测试和 `git grep -n "recipe_catalog" -- src/jobdesk_v2/gui src/jobdesk_v2/application/cards`，确认结论仍然成立，不成立就停止（G9））。需要的 import：`NoContract`（`...application.editor.contract.models`）、`ContractUnavailableError`（`...application.editor.contract.remote_v4`）。
  5. `app.py`（约 L440-446）：`V4ProducerContractProvider(...)` 调用里删除 `fallback=StableFallbackContractProvider(),` 一行；`StableFallbackContractProvider` 的 import 如果不再被使用就删除。
  6. 改写上面 14 个测试，原则：**断言的严格程度不得降低**。
     - 原来断言"返回降级合同，诊断里有 code X / 消息含 Y"的，改成 `pytest.raises(ContractUnavailableError)`，并断言 `exc.value.diagnostic.code == X`、消息仍含 Y。
     - 原来断言"降级结果不被缓存"的，改成"失败不被缓存，再次调用会重新发起命令"。
     - 两个 `TestRemoteErrorSanitization` 测试守护的是安全属性（传输层文本、远端 stderr 不得出现在用户可见内容中）：必须保留，改为对异常的 `str(exc)` 和 `exc.diagnostic.message` 断言同样的禁止内容（原来断言的是诊断文本里没有 `secret.example`、`alice`、`id_rsa` 等）；不得删除这两个测试，也不得缩小被检查的文本范围。
     - `test_the_service_publishes_a_degraded_answer_as_the_restricted_value`：改成断言 `V4ContractService` 遇到不可用时把异常交给调用方（presenter 的 `_on_contract_failed` 路径），不再发布 restricted 值。
     - 若某个测试在新设计下已没有意义，不要删除，停止并报告。
- 禁止事项：G1–G10；不得删除任何测试；不得改动上面 14 个以外的测试；诊断的 `code` 和 `severity` 不得变化。
- 预期行为变化：(a) 读不到 V4 合同的服务器（连不上、没装 ConfFlow、版本太旧、文档不是 V4、文档损坏）现在显示"不可用 · 禁止提交"并给出具体原因，而不是"兼容性回退 · 只读"；(b) V4 会话中已被禁用的旧配方选择器的数据源变为空（用户可见的 V4 配方不变）。
- 验收命令：JD 标准验收。期望：collect 数与 J2.1a 之后相同（2411）；0 failed、7 skipped；被改写的恰好是上面 14 个测试；`git grep -n "_degrade\|fallback_artifacts" -- src/jobdesk_v2/application/editor/contract/remote_v4.py src/jobdesk_v2/gui` 无输出（`providers.py` 里 `LocalProducerContractProvider` 自己的 `_degrade`/`fallback_artifacts` 留给 J2.4，`runs/confflow_backend.py` 的 `_degrade` 与合同无关，不在本卡范围）。
- 提交信息模板：`feat(contract)!: report a server without a V4 contract as unavailable` + 通用尾部。

### J2.1c — 修复 J2.1a 造成的应用启动崩溃（验收方发现，J2.1a 之后补）

- ID：J2.1c ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.3（c94fcab）
- 背景：J2.1a 让 `WorkflowEditorService()` 默认使用空的 `NoContract`，`gui/app.py::main` 用它构造主窗口。但 `CalculationSection.__init__`（`gui/new_run/calculation_section.py` 约 L139-146）在构造时对 `global.charge`、`global.multiplicity` 调用 `manifest.require_field`，空 manifest 里没有它们，于是应用启动时抛出 `EditorManifestError: unknown field id: 'global.charge'`。验收方在 J2.1a 验收时只运行了测试套件，没有做真实启动路径检查，没有发现这个问题（已记入 LOG）。即使不崩溃，旧代码也只在构造时建一次这两行，选定服务器、绑定 V4 合同之后它们不会重新生成。
- 目标：新建页面在没有合同时可以构造；全局两行跟随当前 manifest 的字段出现和消失。
- 类型：`logic`
- 允许修改的文件：`src/jobdesk_v2/gui/new_run/calculation_section.py`，新增 `tests/gui/test_startup_smoke.py`
- 具体步骤（@c94fcab；验收方已在该提交的干净副本上原型验证：补丁应用后全量 2404 passed，启动冒烟通过）：
  1. 应用 `docs/refactor/handoff/J2.1c-calculation_section.patch`（`git apply`，已验证可干净应用）。补丁的内容：新增 `_GLOBAL_FIELD_IDS` 常量；`CalculationSection` 用一个容器和 `_global_key` 记录当前已画的全局行；新增 `_rebuild_global_rows()`，用 `manifest.field(...)`（找不到返回 `None`）取描述符，字段集合或 `json_pointer` 变化时清空并重画；构造函数和 `refresh()` 开头各调用一次。注意：不要给容器布局调用 `setContentsMargins`——`tests/gui/test_architecture.py::test_pages_never_own_styling` 禁止页面自己设样式，步骤行的容器也是这样做的。
  2. 新增 `tests/gui/test_startup_smoke.py`（标记 `@pytest.mark.gui`，用 `qtbot`），3 个测试：
     - `test_the_main_window_builds_on_a_session_with_no_contract`：`service = WorkflowEditorService()`，`MainWindow(service, WorkflowDraftStore(service))` 构造成功，依次 `show_page` 打开 `new_run`、`workflows`、`workflow_editor`、`files`、`runs`、`settings`，每个都返回 True；
     - `test_global_rows_follow_the_manifest_in_force`：对上面那个窗口的 New Run 页面（`window.stack.widget(0)`），没有合同时 `calculation_section._global_controls` 为空；`service.rebind(authoritative_contract())` 并调用 `page._refresh()` 之后键恰为 `global.charge`、`global.multiplicity`；
     - `test_global_rows_disappear_when_the_contract_is_unbound`：再 `service.rebind(WorkflowEditorService().contract)` 并 `page._refresh()` 后又为空。
- 禁止事项：G1–G11；不得改动任何已有测试；不得改 `app.py`、`presenter.py`、`manifest.py`。
- 验收命令：JD 标准验收，另加验收方的启动冒烟 `docs/refactor/tools-acc/startup_smoke.py`（在 c94fcab 上必须失败、在本卡提交上必须通过）。期望：collect 2411 + 3 = 2414；2407 passed、7 skipped、0 failed；`contract_key` 不变。
- 提交标题：`fix(new-run): build the global rows from the manifest in force so the app starts without a contract`

### J2.2 — 测试夹具不再经过 V1/V2 解析器

- ID：J2.2 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.1b（已验收，4c5f6ec）
- 目标：`tests/contract_fixtures.authoritative_contract()` 不再调用 `parse_contract_bytes`，改为直接用 `producer_v2.json` 里的 manifest 和配方目录构造一个测试内的 `ContractLike` 替身，为 J2.4 删除 V1/V2 解析器和 `VerifiedEditorContract` 做准备。
- 类型：`test-only`
- 允许修改的文件：`tests/contract_fixtures.py`、`tests/application/conftest.py`、`tests/gui/conftest.py`
- 具体步骤（@4c5f6ec）：
  1. `contract_fixtures.py`（约 L65-77）：定义一个 frozen dataclass `FixtureContract`，提供与 `VerifiedEditorContract` 相同的**使用面**：`editor_manifest`、`recipe_catalog`（用 `editor_manifest_from_mapping` / `recipe_catalog_from_mapping` 从 `tests/fixtures/contract/producer_v2.json` 的 `editor_manifest`、`recipe_catalog` 段构造；这两个函数 J2.4 之后依然保留）、`source="producer"`、`is_authoritative=True`、`capabilities=EditorCapabilities(can_edit_fields=True, can_use_recipes=bool(recipes))`、`diagnostics=()`、`contract_key`（格式与 `VerifiedEditorContract.contract_key` 完全相同：`f"{schema}/{source}/manifest:{manifest_sha256[:12]}/recipes:{recipe_sha256[:12]}"`，输入取自 `producer_v2.json` 的 `schema` 与两个 sha256 字段，保证 key 字符串不变）、`describe_source()`（返回与原来相同的 `"Using ConfFlow contract <producer_version or '(unversioned)'>"`）。其余测试用到而上面没有的属性，按需要补上，补多少就在提交信息里列多少。
  2. `authoritative_contract()` 与 `_authoritative_contract_cached()` 返回 `FixtureContract`；`authoring_service()` 不变。`fallback_artifacts()` 和 `parse_fixture()` 保留（还有用 V1/V2 解析器的测试在用，J2.4 才删）。
  3. `application/conftest.py` 的 `contract` fixture（约 L98-110）和 `gui/conftest.py` 的 `service` fixture 已经调用 `authoritative_contract()`，如不需要改动就不动。
- 禁止事项：G1–G11；不得修改任何 `test_*.py`；被测代码不得变化；`fallback_contract` / `fallback_artifacts` fixture 这一卡不删。若发现必须改动 `test_*.py` 才能通过（例如某个测试断言 `isinstance(contract, VerifiedEditorContract)`），停止并报告，不要变通。
- 验收命令：JD 标准验收。期望：collect 2411 不变；结果与 J2.1b 之后逐项相同（2404 passed / 7 skipped / 0 failed）；`git diff --stat` 只涉及白名单内的文件。
- 提交标题：`test(contract): build the authoring fixture contract without the V1/V2 parser`

### J2.3 — 把 V4 仍需要的符号移出 `parse.py`

- ID：J2.3 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.2
- 目标：V4 代码和测试不再 import `contract.parse`；通用的错误类型和 JSON 解码原语移到新模块和 `jcs.py`，`parse.py` 只剩 V1/V2 的内容（J2.4 删除）。
- 类型：`move`
- 允许修改的文件：
  - 新增 `src/jobdesk_v2/application/editor/contract/errors.py`
  - `src/jobdesk_v2/application/editor/contract/parse.py`、`__init__.py`、`v4.py`、`boundary.py`、`remote_v4.py`
  - `src/jobdesk_v2/application/editor/jcs.py`
  - `src/jobdesk_v2/application/cards/binding_candidates.py`、`src/jobdesk_v2/application/remote/v4_validation.py`、`src/jobdesk_v2/application/runs/v4_results.py`
  - `tests/application/test_p0_boundary.py`、`tests/application/test_confflow_v4_contract.py`，以及任何其他 import 了被移动符号的 `tests/` 文件（**只允许改 import 语句**）
  - `tests/application/test_architecture.py`：**只允许**在 `_ALLOWED_JSON_MODULES`（约 L144）里新增两项 `Path("application") / "editor" / "contract" / "errors.py"` 和 `Path("application") / "editor" / "jcs.py"`，并在其上方注释补一句说明它们是从 `parse.py` 原样搬来的专门解码模块。这条守护的意图（解码 producer JSON 的 `json.loads` 只能在专门的解析模块里）不变；搬迁前后 `application/` 下的 `json.loads` 调用点数量不变（验收方已核对，同为 3 处：parse.py 两处→errors.py 与 jcs.py，confflow_state.py 一处）。J2.4 删除 `parse.py` 时再把 `_PARSER_MODULE` 一项移除。除此之外不得改这个文件。
- 具体步骤（@J2.2 之后；移动的函数体、类体逐字不变）：
  1. 新增 `errors.py`：从 `parse.py` 原样剪切 `ARTIFACT_CONTRACT`（约 L84）、`ContractParseError`（约 L114-143）、`decode_json_object`（约 L146-184）。依赖的 import（`json`、`Any`、`ContractDiagnostic`、`DiagnosticCode`）按需带过去。
  2. 移到 `application/editor/jcs.py` 末尾（同样原样剪切）：`_DuplicateKeyError`、`_NonFiniteConstantError`、`_strict_object_pairs`、`_reject_constant`、`decode_strict_json`（约 L187-238）、`canonicalize_text`（约 L241-258）。依赖的 `json`、`hashlib`、`JcsError`、`reason_code_for`、`jcs_bytes` 在 jcs.py 里本来就有或按需 import。这两个函数在源码里只有 parse.py 自己用，只有 `test_p0_boundary.py` 里的 JCS 一致性测试在用；它们守护的是 JD 与 producer 的 JCS 接受规则一致，所以保留而不删除。
  3. `parse.py`：从 `errors` 和 `jcs` import 上述名字（它自己内部还在用）；`parse.py` 自己不再定义它们。
  4. 把全部 import 方改成新位置：`v4.py`（`ARTIFACT_CONTRACT, ContractParseError, decode_json_object` ← `.errors`）、`boundary.py`（`ContractParseError` ← `.errors`）、`remote_v4.py`、`contract/__init__.py`（这些名字的再导出改为来自 `errors` 与 `jcs`）、`binding_candidates.py`、`v4_validation.py`、`v4_results.py`、以及上面列出的测试文件里的 import 语句。`jcs.py` 开头 docstring 里提到 "`contract.parse.decode_strict_json`" 的地方改成本模块。
  5. 完成后 `git grep -n "contract.parse\|from .parse\|from ..parse" -- src tests` 的命中只允许出现在 `contract/` 内的 V1/V2 模块自己（`parse.py` 内部、`providers.py`）和 J2.4 才删除的测试里（`test_contract_parsing.py`、`test_contract_providers.py` 等 V1/V2 专属测试）。
- 禁止事项：G1–G11；函数体、类体逐字不变；不得重命名；不得改任何测试断言。
- 验收命令：JD 标准验收，另加 `git diff HEAD~1 -U0 | grep '^[-+]' | grep -v '^[-+][-+]'` 由验收方核对（删除行和新增行除 import、`__all__`、空行、注释外一一对应）。期望：collect 2411 不变；结果与 J2.2 之后逐项相同；`contract_key` 不变。
- 提交标题：`refactor(contract): move V4-shared errors and JSON primitives out of parse.py`

### J2.4 — 删除 V1/V2 合同实现、file mode 和内置快照

- ID：J2.4 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.1c（基点 13b6f55）
- 目标：删除 V1/V2 合同解析、provider、service、内置 manifest/catalog 快照、file mode，以及只守护它们的测试。
- 类型：`delete`（带必要的测试改写）
- 做法：验收方已在 13b6f55 上做出并通过全量验证的原型，导出为 `handoff/J2.4-src-tests.patch`（36 个文件，含修正 G9 停止后补入的 `test_confflow_v4_e2e.py`）。执行模型**应用补丁、复核、自检、提交**，不重新设计。
- 允许修改的文件：与补丁涉及的文件完全一致（删除 7 个、修改 27 个、删除脚本 2 个）；补丁之外任何文件的改动都算越界。
- 具体步骤：
  1. `git apply --check` 再 `git apply` 补丁；`git status` 的文件集合必须等于补丁的文件集合。
  2. 复核补丁：删除了 `contract/parse.py`、`providers.py`、`service.py`、`infrastructure/editor/*`、`manifest.py` 与 `recipes.py` 中的内置快照、`models.py` 中的 `VerifiedEditorContract`/`ContractLevel`/`ArtifactSource`、`app.py` 的 file mode、`diagnostics.py` 的 Contract override 行、README 的 `JOBDESK_V2_CONTRACT` 与降级说明、`scripts/screenshots_phase_g.py`、`scripts/gen_editor_field_matrix.py`；没有任何 V4 行为代码被改。
  3. 被删除的测试恰好是 `handoff/J2.4-removed-tests.txt` 的 82 项（逐行对照，不多不少）。
- 禁止事项：G1–G9；不得手工再改补丁内容；若补丁无法应用或自检与期望不符，停止报告，不要自行修补。
- 验收命令：JD 标准验收，另加
  ```bash
  cd $JD && git grep -nE "StableFallback|VerifiedEditorContract|JOBDESK_V2_CONTRACT|parse_contract_bytes|LocalFileByteSource|build_contract_service" -- src tests scripts README.md && echo FAIL || echo ok
  ```
  （验收方已在补丁上实测输出 ok。）期望：0 failed、7 skipped、2325 passed；collect 2332（2414−82，无新增）；`startup_smoke.py` 输出 `startup smoke: ok`；ruff/format/mypy 通过。
- 提交信息模板：`refactor(editor)!: delete the V1/V2 contract wire, file mode and bundled snapshot` + 通用尾部（Removed-Tests 写 82 并附清单文件名，Behavior-Change 写：无合同时编辑被禁用，提示改为 "Configuration editing is disabled until a server contract is resolved."；JOBDESK_V2_CONTRACT 环境变量不再有效）。

### J2.5 — （可选）离线编辑：缓存最近一次 V4 contract

- ID：J2.5 ／ 仓库：JobDesk-v2 ／ 前置：J2.4 ／ **只有用户明确要求离线编辑时才执行，默认不执行**
- 类型：`logic`。本方案不展开；需要时另写卡片。

---
## 7. Phase 3：边界瘦身（两仓成对提交）

顺序必须是：JD 先变得"容忍缺失"（J3.1、J3.2）→ CF 把 pin 移到该 JD 提交（C3.1）→ CF 删除 producer 字段（C3.2、C3.3）→ JD 重新 vendor fixture（J3.3）→ CF 再 re-pin（C3.4）。
每个 re-pin 后执行 `git -C $JDPIN checkout --detach <新 SHA>`，再跑 CF 的跨仓测试。

**contract 检查点**：Phase 3 中改变 contract/boundary 字节的卡（C3.2、C3.3）由执行模型新增 `docs/refactor/baseline/checkpoints/<CARD>/`，内容为 `contract_digests.py` 的输出加上 `contract.full.json` / `boundary.full.json`。后续卡的 golden 以最新的已验收检查点为准（`golden_check.py --checkpoint`）。TS1 与 engine 报告始终以 B0.1 为准。

### J3.1 — 删除 presenter 中读取 `capability_identity` 的死分支

- ID：J3.1 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.4（基点 fedac0850964f0bacc720018bae802cef83456c4）
- 目标：`presenter.py` 的 `_capability_identity()` 里 `getattr(contract, "capability_identity", None)` 永远取不到值（`ResolvedContract` 是 `slots=True`，没有该属性），删除后行为不变。
- 类型：`delete`。做法：应用 `handoff/J3.1-presenter.patch`（1 个文件，−5 行），不重新设计。
- 允许修改的文件：`src/jobdesk_v2/gui/new_run/presenter.py`
- 禁止事项：G1–G9；函数名保持不变（L2 再重构）。
- 验收命令：JD 标准验收。期望：2325 passed、7 skipped、collect 2332（Removed-Tests 0）；ruff/format/mypy 通过。
- 提交信息模板：`refactor(new-run): drop the unreachable capability-identity read` + 通用尾部。

### J3.2 — JD 不再要求、不再保存 producer 的无读取方身份字段

- ID：J3.2 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J3.1
- 目标：`capability_identity`（boundary 与 authoring 回答）、`semantic_identity`、`offered_identity` 不再被解析或保存；`prepared_run_manifest` / `validation_receipt` 两个可选 schema 摘要不再被记录。这样 producer 删除它们之后，JD 仍能接受 contract。
- 类型：`delete`。做法：应用 `handoff/J3.2-src-tests.patch`（4 个文件）。验收方已在其上跑通全量。
- 允许修改的文件：`application/editor/contract/boundary.py`、`application/editor/contract/v4.py`、`application/cards/binding_candidates.py`、`tests/application/test_p0_boundary.py`。
- 被删测试恰好是 `handoff/J3.2-removed-tests.txt` 的 19 项（`test_future_only_digests_are_recorded_when_present` 1 项，加 `test_malformed_digest_entries_are_refused` 对 `prepared_run_manifest`/`validation_receipt` 的 18 个参数化节点）；另有 2 个测试被改写但节点不变：`test_missing_required_member_is_refused`（不再要求 `capability_identity`/`semantic_identity`）、`test_real_contract_bytes_carry_boundary`（去掉对这两个身份和 `offered_identity` 的断言）。
- 不动：`remote/v4_validation.py` 与 `presenter.py` 的 token（L2 处理）；`tests/fixtures/p0_boundary/*`（vendored producer fixture，J3.3 再同步）；compatibility 词汇里的 `capability_identity_changed`。
- 禁止事项：G1–G9；不得手改补丁。
- 验收命令：JD 标准验收（此时 producer 仍发布这些字段，JD 必须照常接受，由 `TestLiveProducerParity` 真实字节测试证明）。期望：2306 passed、7 skipped、collect 2313（2332−19）；`git grep -n "capability_identity\|semantic_identity\|offered_identity" -- src` 只命中 `boundary.py` 的 `capability_identity_changed`、`remote/v4_validation.py`、`gui/new_run/presenter.py`。
- 提交信息模板：`refactor(contract): stop parsing producer identity members nobody reads` + 通用尾部（Removed-Tests 19，附清单文件名）。

### C3.1 — re-pin 到 J3.2

- ID：C3.1 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C1.4、J3.2（JD 提交 1ca4052a7715421f70fcb40889aec482a35d9d6b）
- 类型：`ci`。做法：应用 `handoff/C3.1-pin.patch`（2 个文件）。
- 内容：`tests/v4/jobdesk_integration.py` 与 `.github/workflows/jobdesk-contract.yml` 的 40 位 SHA 改为 J3.2；`jobdesk_integration.py` 里 `ContractParseError` 的 import 改自 `contract.errors`（J2.1b 起它不再在 `contract.parse`，J2.4 删除了 `parse`）。执行后 `git -C /opt/cf-worktrees/jd-pin checkout --detach 1ca4052a7715421f70fcb40889aec482a35d9d6b`（验收方已执行）。
- 验收：标准验收（`JOBDESK_V2_SRC=$JDPIN/src`）；跨仓测试通过；`tests/test_release_workflow.py` 通过。期望 Removed-Tests 0，golden 不变。
- 提交信息模板：`chore(cross-repo): pin JobDesk-v2 to 1ca4052 (J3.2)` + 通用尾部。

### C3.2 — producer 删除无读取方的边界成员与摘要

- ID：C3.2 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C3.1
- 类型：`delete`（wire 内容变化）。做法：应用 `handoff/C3.2-src-tests.patch`（12 个文件），不重新设计。验收方已在其上跑通全量测试与 golden，并核对 contract/boundary 的路径差异。
- 内容：`boundary.py` 删除 `capability_identity`、`semantic_identity`、`compatibility_matrix`、`compare_identities`、`evaluate_compatibility`、`validation_receipt_schema`、`prepared_run_manifest_schema`、`identity_relationships`、`wire_examples` 及相关常量；`boundary_section()`/`boundary_document()` 不再带参数；`authoring.py` 的响应不再带 `capability_identity`；`contract.py` 删除 `digest_axis` 与 `result_digest` schema 属性；`run_result.py` 不再写每步 `result_digest`；`generate_p0_boundary_fixtures.py` 与 `docs/internal/fixtures/p0_boundary/` 重新生成并删除 `compatibility_cases.json`；`tests/v4/test_p0_boundary.py`、`test_p0_pr1_authoring.py` 删除对应测试/断言；新增检查点 `docs/refactor/baseline/checkpoints/C3.2/`。
- 被删测试：`handoff/C3.2-removed-tests.txt` 的 9 项；新增 0 项。
- 路径差异（验收方实测，期望完全一致）：contract 删除 9 条（`/boundary/capability_identity`、`/boundary/semantic_identity`、两个 schema 摘要、四个 `digest_axis`、`result_digest` 属性），修改 3 条（`/boundary/schemas/authoring_response/sha256`、`/contract_digest`、`/result_schema_sha256`）；boundary 删除 9 条、修改 2 条（`required` 下标移位）；`jd_contract_key` 不变。
- 禁止事项：G1–G9；不得手改补丁；不得改 `BOUNDARY_PROTOCOL_VERSION`（Q6）。
- 自检期望：全量 `{"passed": 4481, "skipped": 12}`，collect 4493（4502−9）；`golden_check.py --checkpoint` ok；contract 与 boundary 的 `json_paths_diff` 与上面一致；ruff/mypy 通过；**用 `tools-acc/noeditable` 的 run_sharded.py**。注意 `tests/v4/test_v44_worker.py::TestCancellation::test_cancel_trap_yields_cancelled_without_rescue` 在并行分片下偶发超时（与本卡无关，单独运行稳定通过）；若只有它失败，单独重跑三次，三次都过则记入报告，不算失败。
- 提交信息模板：`refactor(producer)!: delete unread boundary identities, schemas and digests` + 通用尾部；正文必须包含：`服务器 ConfFlow 与 JD 必须同步升级：旧 JD 要求 boundary.capability_identity / semantic_identity，会拒绝本提交之后的 producer；BOUNDARY_PROTOCOL_VERSION 不变。`

### C5.2b — 删除 `confflow export`（侧分支；用户 2026-10-03 确认旧 results.db 不需保存，直接排入）

- ID：C5.2b ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5` ／ 前置：C5.2（可在 C5.3a 之前或之后；补丁已验证两种顺序都能应用） ／ 类型：`delete`。做法：应用 `handoff/C5.2b-src-tests.patch`（10 个文件）。
- 已核实（验收方）：JD 源码、测试、脚本、README 中没有对 `confflow export` 或 `results.db` 的依赖；V4 的结果出口是 `confflow v4 run` 产出的 `confflow.run_result_manifest.v1`，由 JD 的 `parse_result_bytes` 读取。
- 内容：删除 `confflow/workflow/export.py`；`cli.py` 删除 `--export`、`--format` 参数与对应分支和 import，`--output` 的帮助改为只描述 `--rerun-failed`；删除 `tests/test_export.py`、`tests/results_db_fixture.py`；`tests/test_cli.py` 删除 4 个 export/format 测试及一处断言；`tests/test_contract.py` 删除 `test_export_uses_contract_filenames`；`tests/test_provenance_metadata.py` 删除 3 个依赖 `ResultsDB`/export 的测试（保留 `test_refine_output_keeps_ts_metadata`）；`tests/v4/test_architecture_boundaries.py` 把 `confflow.workflow.export` 从 `FORBIDDEN_LEGACY_MODULES` 移到 `REMOVED_LEGACY_MODULES`；`docs/ARCHITECTURE.md`、`docs/TESTING.md` 删除 export 的相关行（其余文档由 C5.7 统一处理；`COMMAND_REFERENCE.md` 无 export 章节）。
- 被删测试：`handoff/C5.2b-removed-tests.txt` 的 17 项（test_export 9、test_provenance_metadata 3、test_cli 4、test_contract 1），新增 0 项。
- 自检期望：在 C5.2 之上单独应用：`{"passed": 4101, "skipped": 12}`，collect 4113；在 C5.3a 之上应用：`{"passed": 4108, "skipped": 12}`，collect 4120（验收方两种顺序都实测）。`deleted_modules_check.py cf --tree <树> confflow.workflow.export` 退出码 0；`golden_check.py` ok；`--jdpin /opt/cf-worktrees/jd-pin-cf`。
- 提交信息模板：`refactor!: delete the confflow export command and its results.db reader` + 通用尾部（Removed-Tests 17，附清单文件名；测试名从清单逐字复制）。
- Behavior-Change：`confflow --export`、`--format` 不再存在；没有任何生产代码再写 `results.db`。

### C3.3 — 在 contract 中标注 ConfGen 溯源字段

- ID：C3.3 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C3.2（e5c3032）
- 类型：`logic`（只改 contract 声明，不改任何计算）。做法：应用 `handoff/C3.3-src-tests.patch`（5 个文件，含检查点）。
- 内容：`_confgen_section()` 在 `result_provenance` 之后新增 `"report_provenance": ["/certificate/digest", "/enumeration/digest", "/input_state_digest", "/input_certificate_digest"]`（JSON pointer 相对于 `ensemble_report`；验收方已核实 `certificate.digest`、`enumeration.digest` 在引擎报告中存在）；`test_contract_confgen_section_tracks_registries` 加一条断言；新增检查点 `docs/refactor/baseline/checkpoints/C3.3/`。
- 路径差异（验收方实测）：相对 C3.2 检查点，contract 只新增 `/confgen/report_provenance`、修改 `/contract_digest`；boundary 无任何变化；`result_schema_sha256` 与 `jd_contract_key` 不变。
- 自检期望：全量 `{"passed": 4481, "skipped": 12}`，collect 4493（Removed 0，Added 0）；`golden_check.py --checkpoint …/checkpoints/C3.3/contract.json` ok；`json_paths_diff` 对 C3.2 检查点与上面一致；ruff/mypy/black 通过。
- 提交信息模板：`feat(producer): declare confgen report provenance fields` + 通用尾部。

### J3.3 — JD 重新 vendor P0 boundary fixture

- ID：J3.3 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：C3.3 提交（需要知道其 SHA，fixture 由 CF 的 `docs/internal/fixtures/p0_boundary` 同步）
- 类型：`test-only`。做法：先应用 `handoff/J3.3-code.patch`（2 个文件），再运行同步脚本（`PROVENANCE.json` 的 `source_commit` 只能由脚本写入，不能放进补丁），最后 `git rm` 已退役的 fixture。
- 补丁内容：`scripts/sync_p0_boundary_fixtures.py` 的 `FIXTURE_NAMES` 去掉 `compatibility_cases.json`；`tests/application/test_p0_boundary.py`：去掉 `compatibility_cases.json` 名单项，删除 `TestCompatibilityParity` 整类，删除 `test_real_envelope_without_future_digests_is_accepted`（它删除 producer 已不再发布的键，且被 `test_real_contract_bytes_carry_boundary` 覆盖），`_compact_section` 不再读取 `semantic_identity`/`capability_identity`/`matrix`，`workflow_schema_id` 取自 `V4_WORKFLOW_SCHEMA_ID`，schemas 只含两个 authoring schema。
- 被删测试：`handoff/J3.3-removed-tests.txt` 的 3 项（`test_vocabulary_matches_producer_fixture`、`test_real_envelope_without_future_digests_is_accepted`、`test_vendored_fixture_matches_live_producer[compatibility_cases.json]`）。
- 执行后期望：JD 全量（绑定 `--cf` = C3.3 之后的 `exec-cf`）`2303 passed, 7 skipped`，collect 2310（2313−3），ruff/format/mypy 通过，`TestFixtureProvenance` 通过，`PROVENANCE.json` 的 `source_commit` = C3.3 提交 SHA，`tests/fixtures/p0_boundary/` 中不再有 `compatibility_cases.json`；`boundary_protocol.json` 与 CF `docs/internal/fixtures/p0_boundary/boundary_protocol.json` 字节一致。
- 附注（验收方发现）：在 C3.2 之后、J3.3 之前，用新 producer 跑 JD 全量会有 3 个失败（两项 fixture 指纹未同步、一项删除 producer 已不发布的键）；这是预期的，J3.3 修复。
- 提交信息模板：`test(fixtures): re-vendor P0 boundary fixtures from ConfFlow <short sha>` + 通用尾部。

### C3.4 — re-pin 到 J3.3

- ID：C3.4 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：J3.3（JD 提交 edb068ac258447f26eed2184d58e34554c323a18）
- 类型：`ci`。做法：应用 `handoff/C3.4-pin.patch`（2 个文件，SHA 两处 + 注释）。`jd-pin` 已由验收方移到该提交。
- 验收：跨仓测试全部通过；全量 `{"passed": 4481, "skipped": 12}`，collect 4493；`tests/test_release_workflow.py` 14 项通过；JD 侧 `TestLiveProducerParity` 在 §2.3 绑定下通过（J3.3 已验）。
- 提交信息模板：`chore(cross-repo): pin JobDesk-v2 to edb068a (J3.3)` + 通用尾部。**这是 Phase 3 的最后一张卡。**

---

## 8. 输入简化分支改造（IS）

IS 阶段在 `implementation/input-simplification`（ConfFlow，`$CFIS`）与 JD 的同名分支（`$JDIS`）上进行。基准：CF `f87da58`，JD `92d48f1`。
IS 分支上没有 `docs/refactor/`。各卡如需工具，从 `$TOOLS` 运行，不复制进 IS 分支（IS.1 的 golden 数据除外）。

### IS.1 — legacy paths 与 v3 paths 等价 golden（改造之前）

- ID：IS.1 ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification` ／ 前置：B0.1（可提前）
- 目标：在改动之前，记录每一个 legacy paths 用例在 legacy 执行路径和对应 typed v3 声明下的输出，并判断是否等价。
- 类型：`baseline`
- 允许修改的文件（新增）：`docs/refactor/paths_equivalence/run_equivalence.py`、`docs/refactor/paths_equivalence/cases.json`、`docs/refactor/paths_equivalence/result.json`、`docs/refactor/paths_equivalence/README.md`
- 具体步骤（@f87da58）：
  1. 用例来源：(a) 运行 `tests/v4/test_confgen_paths_phase0.py`（44 项）和 `tests/v4/test_confgen_paths_audit.py`（26 项），包装 `ConfgenExecutor._run_legacy_paths`（`execution/confgen_executor.py:1475`）记录每次调用的 `(records, native)`，去重后写入 `cases.json`；(b) 显式加入 Q2 的两类用例：端点是末端原子（只有一个邻居）的路径；bare `{start, end, move}`（不给 `angles`/`step`）的路径。分子用 `tests/v4/test_repair_executors.py` 中的 `_butane` 等现成构造器。
  2. 每个用例执行两次，都通过公开入口 `ConfgenExecutor().execute(item, ctx)`，构造方式与 `tests/v4/test_confgen_paths_audit.py:34-39` 相同：
     - legacy：`native` 原样；
     - v3：`{"schema_version": 3, "index_base": 1, "paths": [...]}`，每条声明的 `start/end/move/id` 原样；有 `angles`/`step` 的原样；bare 声明补 `step: 120`（= `_DEFAULT_ANGLE_STEP`，见 Q2）；`angle_step`、`bond_scale`、`strict_path_bond_check` 按 v3 schema 中对应字段映射，没有对应字段的写进 `result.json` 的 `unmapped` 列表。
  3. 比较并写入 `result.json`：状态（completed/failed 及失败原因）；构象数；按网格序号排序后每个构象的原子顺序和坐标（逐原子最大偏差，阈值 1e-6 Å）；每个转子的角度集合。每个用例给出三种标记之一：
     - `EQUIVALENT`：以上全部相同。
     - `LEGACY_DEGENERATE`（Q2a）：只适用于 v3 因"末端原子端点没有可测二面角框架"而拒绝的用例。此时另构造一个对照声明：把末端端点换成它唯一的邻居原子，其余不变，用 v3 执行；再把 legacy 的输出按几何去重（两个构象逐原子最大偏差 ≤ 1e-6 Å 视为同一个，不做叠合，因为两者在同一坐标系中）。去重后的 legacy 集合与对照声明的 v3 集合**完全相同**时，才标记为 `LEGACY_DEGENERATE`，并在 `result.json` 中写明被替换的端点、去重前后数量和两边的集合摘要。替换后 `start == end` 或声明非法时，标记为 `NOT_EQUIVALENT`。
     - `NOT_EQUIVALENT`：其他任何差异。
- 禁止事项：G1–G9；不得修改 `confflow/`、`tests/`。
- 验收命令：`cd $CFIS && python3 docs/refactor/paths_equivalence/run_equivalence.py --check`（重跑并与 `result.json` 逐字节比较）。
  期望：可重复。`LEGACY_DEGENERATE` 不阻塞，但验收方要逐个核对去重证明。**只要有一个 `NOT_EQUIVALENT`，验收结论就是"升级"，IS.2 不得开始。**
- 提交信息模板：`test(confgen): record legacy-vs-v3 paths equivalence golden` + 通用尾部。

### IS.1b — 修正 IS.1 的比较方法并补充含氢用例（IS.1 验收后新增，用户已确认规则）

- ID：IS.1b ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification` ／ 前置：IS.1（提交 9340601）
- 目标：IS.1 的比较沿用了 PLAN 的"不做叠合"，这是方案本身的错误（无氢夹具上绕末端键的旋转只是刚体转动）。本卡改用只允许真旋转的叠合比较，通过三个自检后才可用于下结论，并补充带氢的真实分子用例。
- 类型：`baseline`
- 允许修改的文件：`docs/refactor/paths_equivalence/` 下已有的 `run_equivalence.py`、`cases.json`、`result.json`、`README.md`，以及新增的同目录文件（如 `fixtures_h.json`）。不碰其他目录。
- 用户已确认的规则：
  1. 叠合只允许真旋转（行列式 +1），禁止镜像。`confflow.science.cluster.kabsch_rmsd` 已实现"无镜像 Kabsch"（`cluster.py:27-42`），可以直接使用，但必须先通过下面的自检。
  2. 比较工具先通过三个自检才可用于出结论，自检结果写入 README 和 result.json 的 `selfcheck` 节；任何一个失败，工具以非零退出，不生成 result.json：
     - SC1 刚体旋转 → 相同：取一个带氢的非平面结构，施加随机真旋转和平移，RMSD ≤ 1e-5；
     - SC2 镜像 → 不同：取一个手性分子（带氢，例如 CHFClBr 或 2-丁醇，坐标来自确定性构造并保存在 `fixtures_h.json` 中），做镜像（x → −x），RMSD > 1e-5（并记录实际值）；
     - SC3 真实二面角变化 → 不同：取带氢的丁烷（或 1-丙醇），绕一个 C–C 键把一端转 60°，RMSD > 1e-5（并记录实际值）。
  3. 阈值统一为 1e-5 Å（所有比较，包括原来的逐原子比较），在 README 和 result.json 里写明。
  4. 用例须带氢，且同时覆盖对称甲基（CH3）和非对称端基（OH、NH2）：至少 n-丁烷（两端甲基）、1-丙醇（OH 端基，另一端甲基）、丙胺（NH2 端基，另一端甲基），端点在末端原子上、路径经过内部键，angles 用 [0,120,240]；各配一个 bare 声明版本（不写 angles/step）；再配内部键、两端都是非末端原子的对照路径（应为 EQUIVALENT）。分子坐标用 RDKit 的确定性嵌入（`AllChem.EmbedMolecule(mol, randomSeed=7)` 加 MMFF 优化）生成一次后**把坐标直接存入 `fixtures_h.json`**，之后各步骤都从文件读取，不再依赖 RDKit 版本。
  5. `LEGACY_ONLY_TERMINAL_ROTOR` 的期望是 0：对每个端点在末端原子上的含氢用例，统计"legacy 结构（用叠合不变的度量去重后）中，在 v3 对照集合里找不到对应结构的个数"。该个数大于 0 就标记 `LEGACY_ONLY_TERMINAL_ROTOR` 并如实记录两边的集合大小。**结果大于 0 时，验收结论为升级**（执行方不要试图让它变成 0，也不要改判定）。对 CH3 和 NH2/OH 分别记录，另外对端基氢原子做置换的版本也各记录一份（`symmetry_aware_legacy_only`，只置换同一个端基重原子上的氢），便于区分"标号不同的等价构象"和"真正不同的构象"。
  6. 新增标记：`BOTH_REJECT`（legacy 与 v3 都以非 COMPLETED 结束，仅原因不同，记录两边原因）；`V3_EMPTY_DEGENERATE`（legacy 产出结构，v3 或对照声明 COMPLETED 但发布 0 个结构，且输入链所有原子共线，任意三原子构成的叉积范数 < 1e-9；记录共线判定数据）。这两个标记都不是失败，不阻塞 IS.2。
  7. 原有的 28 个无氢用例保留，在新度量下重新判定并写入 result.json 的 `no_hydrogen_regression` 节，**不计入结论**（G11）；结论计数只来自 `fixtures_h.json` 中的含氢用例和 `hydrogen_cases` 节。
  8. `--check` 连续两次逐字节相同（沿用 IS.1）。
- 禁止事项：G1–G11；不得改 `confflow/`、`tests/`；不得为了让某个标记计数变成期望值而改判定或阈值。
- 验收命令（验收方运行）：`run_equivalence.py --check`；检查 README 里三个自检的数值；逐个用例核对含氢用例的标记。
- 提交标题：`test(confgen): compare terminal-endpoint paths up to proper rotation with hydrogen-bearing cases`

### IS.0 — 把 Phase 1–3 后的 main 合入 IS 分支

- ID：IS.0 ／ 仓库：ConfFlow（以及 JD：IS.0-JD）／ 前置：C3.4；**用户先把 `refactor/diet` 合入 `main`**（该操作由用户执行）
- 类型：`merge`
- 步骤：`git -C $CFIS merge --no-ff main`。冲突一律保留 main 的删除；IS 新增的代码如果引用了被删符号（例如 `capability_identity`），按 main 的删除去掉引用，并在合并提交信息中逐条列出。JD 同理：`git -C $JDIS merge --no-ff master`。
- 验收：两仓标准验收；engine 报告与 TS1 必须等于 B0.1（IS 分支新增测试产生的新报告记录为新的报告检查点 `checkpoints/IS.0/engine_reports/`，由验收方核对：已有报告不得变化）。

### IS.2 — intent 编译器把 legacy paths 编译成 typed v3（始终输出 `schema_version: 3`）

- ID：IS.2 ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification`（工作树 `/opt/cf-worktrees/exec-cf-is`，基点 fe2acf0） ／ 前置：IS.0 完成、IS.1b 通过且含氢用例无 `NOT_EQUIVALENT`、`LEGACY_ONLY_TERMINAL_ROTOR` = 0（均已满足） ／ 类型：`logic`。做法：应用 `handoff/IS.2-src-tests.patch`（5 个文件），不重新设计。
- 内容：`confflow/producer/intent.py` 新增 `_legacy_paths_to_v3`：当 confgen 步骤的 `native` 只含 `paths`/`angle_step`/`bond_scale`/`strict_path_bond_check` 时，按 IS.1 golden 的 `map_native` 规则逐字映射成 `{"schema_version": 3, "index_base": 1, "paths": […], …}`（bare 声明补 `step`：优先 `angle_step`，否则 120；`bond_scale` → `tolerances.bond_scale`；`strict_path_bond_check` → v3 顶层标志）；步骤 `seed`/`overrides` 照旧附加。`native` 含其他键（如 `chains`）的仍原样透传 legacy（IS.3 再拒绝）。路径声明含映射表之外的键 → 编译期报错（`unsupported keys`，`waypoint` 另附提示），**不静默丢弃**。末端原子端点原样编译，不检查、不换成邻居（交给运行时 v3，诊断见 IS.2b）。
- 发现（验收方，需用户知晓）：**legacy 路径声明支持 `waypoint`（多端点路径），typed v3 不支持（`torsion/paths.py` 明确 "deferred"）。** IS.1 golden 的 `case_0024` 在 legacy 上能跑、v3 无对应声明。IS.2 之后，intent 里带 `waypoint` 的路径会在编译期被拒绝并指明原因。这是 Q2 "paths 一律走 v3" 的直接后果；是否要给 v3 补 `waypoint` 另议（PLAN-2 记一条）。
- 另一个发现：步骤级 `paths`/`strict_path_bond_check` 并不是 intent 的合法步骤成员（`_STEP_KEYS` 不含），原卡"并入"的分支不可达，所以未实现。
- 同卡带入的 IS.0 检查点：`docs/refactor/baseline/checkpoints/IS.0/contract*.json`、`boundary.full.json`（IS 分支合并 main 后、IS.2 之前的 contract/boundary 基线；它相对 C3.3 检查点的大量差异来自 IS 分支自带的 intent/authoring 改动，不是本卡造成）。
- 新增测试 `tests/v4/test_intent_legacy_paths_to_v3.py`（53 项）：所有 golden 用例用 golden 自己的 `map_native` 对拍（可映射的逐项相等；含未知路径键的被拒；含 `chains` 等的保持 legacy）；bare 步长 120、`angle_step`、`bond_scale`、strict 标志、`seed`/`overrides`；9 个含氢 `EQUIVALENT` 用例经**编译后的块**执行，输出与 golden 记录的 v3 结果逐项相同（状态、诊断、转子、构象坐标 1e-9）。
- 禁止事项：G1–G9；不得修改 `confgen_executor.py`、`science/`。
- 自检期望：全量 `{"passed": 5138, "skipped": 12}`；collect 5097 → 5150（+53，Removed 0）；`golden_check.py --checkpoint docs/refactor/baseline/checkpoints/IS.0/contract.json` 的 contract 五项全 ok、ts1 三种 ok，engine 报告 added 5 / different 1 且与 `checkpoints/IS.0/engine_reports/` 逐字节相同（见 handoff）；`mypy`/`ruff`/`black` 通过。
- 预期行为变化：intent 中的 legacy paths 不再以 legacy native 执行，而以 typed v3 执行；bare 声明显式变为 `step: 120`（或 `angle_step`）；含 `waypoint` 或其他未知路径键的声明在编译期被拒绝。
- 提交信息模板：`feat(producer)!: compile ConfGen intent paths into typed v3 scopes` + 通用尾部（Added-Tests 53，测试名从清单逐字复制；Behavior-Change 含上面三条，并写明 `waypoint` 不再可用）。

### IS.2c — waypoint 的编译期提示补半句（用户 2026-10-03 裁定）

- ID：IS.2c ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification` ／ 前置：IS.2（2e0295a） ／ 类型：`logic`（只改错误信息文本，加 1 个测试）。做法：应用 `handoff/IS.2c-waypoint-hint.patch`（2 个文件）。
- 用户裁定：`waypoint` 用户从未使用，不在 v3 补支持；IS.2 保持编译期拒绝，错误信息补半句"改用显式 torsions 声明"。IS.5 不受影响，不需要等待。错误信息变为：`… carries unsupported keys: waypoint (waypoint paths have no typed v3 form; use explicit torsions declarations instead)`。
- 自检期望：全量 `{"passed": 5139, "skipped": 12}`，collect 5150 → 5151（+1：`tests/v4/test_intent_legacy_paths_to_v3.py::test_waypoint_is_refused_with_the_torsions_hint`），ruff/mypy/black 通过。
- 提交信息模板：`fix(producer): point waypoint users to explicit torsions in the compile-time refusal` + 通用尾部。

### IS.2b — v3 拒绝末端原子端点时，诊断指明具体的键

- ID：IS.2b ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification` ／ 前置：IS.2c（基点 2e0295a 之上再加 IS.2c） ／ 类型：`logic`（只改拒绝时的错误信息措辞，不改任何判定）。做法：应用 `handoff/IS.2b-src-tests.patch`（2 个文件）。
- 实现（验收方核对后的方案，与原卡不同）：`TorsionStage.__init__` 本来就收到完整的 resolved spec，其中的 `paths_resolved`（`declared_paths` 的 `source`/`start`/`end` 与 `rotors` 的 `id`/`sources`）已由 planner 附上。因此 stage 只需**只读**这份审计，在 `_frame_for` 抛出"terminal pair"之前，按 axis id 查到来源路径，判断终端原子等于该路径的 `start` 还是 `end`，把 `confgen.paths[<j>].<start|end> (atom N) is a terminal atom with no measurable dihedral frame; ` 放在原句前面。**没有给 `TorsionAxis`、torsion 条目或 spec 增加任何字段**，所以不可能进入 state key、报告的 `enumeration`/`certificate` 或任何 digest。原卡"改 `paths.py`/`planner.py`"不需要。
- 非 `paths` 产生的 axis（`torsions` 声明）错误信息保持原样；原句 `has no measurable dihedral frame (terminal pair)` 仍在，旧断言不受影响。
- 新增测试 6 项（`tests/v4/test_terminal_endpoint_diagnostic.py`；文件名特意不以 `test_confgen_` 开头，避免被 golden 的引擎报告捕获规则收入）：start 端点、end 端点、两端都是末端时先报 start；多路径时指明具体的 `paths[1]`；`torsions` 轴文本不变；被接受的声明仍被接受。
- 自检期望（在 IS.2c 之后应用）：全量 `{"passed": 5145, "skipped": 12}`；collect 5151 → 5157（+6，Removed 0，清单 `handoff/IS.2b-added-tests.txt`）；golden：contract 五项 ok、ts1 三种 ok、engine 报告 added 5 / different 1 且 6 份与 `checkpoints/IS.0/engine_reports/` 逐字节相同（**TS1 与 engine 报告必须逐字节不变**）；ruff/mypy/black 通过。
- 预期行为变化：只有错误信息文本变化。
- 提交信息模板：`fix(confgen): name the offending path key when a terminal endpoint is refused` + 通用尾部（Added-Tests 6，测试名从清单逐字复制）。

### IS.3 — `_wire_confgen` 拒绝非 v3 native

- ID：IS.3 ／ 仓库：ConfFlow ／ 分支：`implementation/input-simplification` ／ 基点 0a9f28d（IS.2b） ／ 前置：IS.2、IS.2b、IS.2c ／ 类型：`logic`。做法：应用 `handoff/IS.3-src-tests.patch`（2 个文件）。
- 内容：`confflow/producer/intent.py::_wire_confgen` 的 legacy 透传分支（含已不可达的步骤级 `paths`/`strict_path_bond_check` 循环）改为 `raise _fail("step … ConfGen intent requires a typed schema_version 3 scope; the legacy native vocabulary (<键>) has no typed form here")`。IS.2 映射之后仍不是 v3 的 native（`chains`/`chain_angles`/`rotate_side`/`no_rotate`，以及只有 `angle_step`/`bond_scale`/`strict_path_bond_check` 而没有 `paths` 的）都会在编译期被拒绝，并在消息里列出具体键。`_passthrough_legacy`（legacy V4 文档的透传）不在本卡范围，不变。
- 实测（验收方）：整个测试集中**只有**IS.2 新增的那组"范围之外的 native 保持 legacy"的 9 个测试断言了透传，没有任何其他测试依赖透传；它们被改写为"必须被拒绝"（9 个节点改名），并新增 4 个参数化节点（只有 `angle_step`、只有 `bond_scale`、只有 strict 标志、`chains`+`chain_angles` 都被拒绝且消息指名这些键）。
- 被删（改名）测试：`handoff/IS.3-removed-tests.txt` 的 9 项；新增 `handoff/IS.3-added-tests.txt` 的 13 项（含 9 个改名后的节点）。
- 自检期望：全量 `{"passed": 5149, "skipped": 12}`；collect 5157 → 5161（−9 +13）；golden（`--checkpoint …/checkpoints/IS.0/contract.json`）：contract 五项 ok、ts1 三种 ok、engine 报告 added 5 / different 1（IS.0 同批，6 份与 `checkpoints/IS.0/engine_reports/` 逐字节相同）；ruff/mypy/black 通过。
- 预期行为变化：legacy ConfGen native（`chains` 等）在 intent 编译期被拒绝，不再透传到 legacy 执行路径。
- 提交信息模板：`feat(producer)!: reject non-v3 ConfGen native in intent compilation` + 通用尾部（Removed-Tests 9、Added-Tests 13，测试名从清单逐字复制）。

### IS.4 — JD 输入简化分支改用 typed v3（Q1 已确认）

- ID：IS.4 ／ 仓库：JobDesk-v2 ／ 分支：`implementation/input-simplification`（工作树就是 `/opt/jobdesk-v2-v4`，HEAD bcdea5b，须干净） ／ 前置：IS.3 已提交（**IS.4 的测试要绑定 CF 的 IS.3 提交**：`--cf /opt/cf-worktrees/exec-cf-is`，其 HEAD 提交标题必须是 `feat(producer)!: reject non-v3 ConfGen native in intent compilation`，否则先停下报告） ／ 类型：`logic`。做法：应用 `handoff/IS.4-src-tests.patch`（6 个文件），不重新设计。
- 内容：
  1. `confgen_v3_form.py`：删除"路径表示"选择器（legacy / typed）及所有 legacy 写回分支，表单只写 typed v3 `paths`。含 legacy `native.paths` 的已有文档：加载到同一组路径编辑器并显示提示 "This document carries legacy native.paths. Legacy paths must be converted to typed v3: edit any path to convert them."；**只加载不写入**；用户一旦编辑，就写出 typed `paths` 并从 `native` 中只删掉 `paths`（其余 native 成员保留）。（验收方对原卡"只读展示"的调整：原卡的只读控件没有现成实现，改为"加载+提示+编辑即转换"，行为更友好，不会产生两种表示并存。）
  2. `page.py`：新建 ConfGen 步骤的初始块从 `{"confgen": {"native": {}}}` 改为 `{"confgen": {"schema_version": 3}}`。
  3. `intent/model.py`：`_intent_confgen_step` 对非空 `confgen.native` 一律抛 `IntentNotRepresentable`，消息指明 `confgen.native` 并说明改用 typed v3；删除"mixes representations"分支和 legacy 展开；同步更新 docstring。GUI 对 `IntentNotRepresentable` 的既有处理是回落到直接 V4 路径（状态栏 "Direct V4 path (unvalidated by intent): …"）。
  4. 不改：`confgen.native` 字段行（J4.1 处理）；`intent/preview.py` 的 `build_preview_native`（Q10 保留）；`intent/sampling.py` 对已有文档中 `native.paths` 的读取。
- **实测发现（需知晓）：** 改成 typed 之后，Normal 里编辑的 ConfGen 路径是**全枚举，编译结果不再带推导出的种子**（legacy native 路径以前总是带一个 producer 推导的整数种子）。依赖"必有种子"的 3 个实机 GUI 测试已改为断言 `seed is None`；"改路径后种子随科学身份变化"的测试改写为"编译结果随路径变化且无种子"。
- 测试改动：删除/改名 3 项（`test_confgen_hybrid_representation_refuses`、`test_confgen_hybrid_representation_refuses_fast`、`test_second_run_rederives_seed_after_path_change`，清单 `handoff/IS.4-removed-tests.txt`）；新增 5 项（`handoff/IS.4-added-tests.txt`：legacy native 一律拒绝并指名、实机 GUI 的拒绝、编译随路径变化且无种子、legacy native.paths 的提示与编辑即转换、表单无 legacy 切换）；另有 4 个实机/面板测试被改写但节点名不变（去掉 legacy 模式、断言 typed 路径与 `schema_version == 3`）。
- 自检期望：JD 全量（绑定 IS.3 之后的 CF）`2467 passed, 7 skipped`；collect 2472 → 2474；ruff/format/mypy 通过；`startup_smoke.py` 输出 ok。
- 预期行为变化：(a) 新建 ConfGen 步骤是 typed v3；(b) 表单不能再写出 legacy paths；(c) 含 legacy native 的已有文档不能再走 intent 编译（得到指明 `confgen.native` 的提示并回落到直接 V4 路径）。
- 提交信息模板：`feat(new-run)!: author ConfGen paths as typed v3 only` + 通用尾部（Removed-Tests 3、Added-Tests 5，测试名从清单逐字复制；提示文本写入提交信息）。

### IS.5 — 把 IS 分支合入 main（需要用户批准）

- 由用户执行或明确授权。合并后运行两仓标准验收，记录新的 engine 报告检查点 `checkpoints/IS.5/`；已有报告必须与 B0.1 相同。

---

## 9. Phase 4：删除 ConfFlow chain / legacy native 路径

基准：IS.5 之后的 `main`。下文行号 @f87da58，执行时以符号为准。

### C4.1 — 测试构造器默认改为 v3

- ID：C4.1 ／ 仓库：ConfFlow ／ 分支：`refactor/diet`（从 IS.5 后的 main 重新建立）／ 前置：IS.5
- 类型：`test-only`
- 允许修改的文件：`tests/v4/_builders.py`
- 步骤：`confgen_step()`（@d5a40ae L233-257）的默认值从 `{"native": {"chains": ["1-2-3"]}}`（L243）改为最小合法的 v3 块，例如 `{"schema_version": 3, "torsions": [{"id": "t1", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [0, 120, 240], "treatment": "enumerate"}]}`（与 `producer/recipes.py:109-120` 的配方相同，原子号按该 builder 默认结构调整）。显式传入 `native=` 的调用不变。
- **修订（用户 2026-10-03，选 A）：** 验收方原型发现 `test_compiler.py::TestCapabilityVocabulary::test_confgen_requires_explicit_seed` 与 `test_repair_capabilities.py::TestGoatSeedValidation::test_confgen_seed_rules_preserved` 固定的是旧 native 的 `seed_required` 规则（v3 全枚举无需种子；加 `sampling.cap` 而无种子则变成 schema 错误），任何 v3 默认块都无法在不改断言时通过。故白名单增加这两个文件，只在它们的 3 处 `confgen_step(...)` 调用中显式传 `native={"chains": ["1-2-3"]}`（等同旧默认），断言/测试名/其它代码不动。补丁 handoff/C4.1.patch。
- 验收：标准验收。期望：collect 不变；使用默认值的 6 个文件（`test_repair_capabilities.py`、`test_p0_pr1_authoring.py`、`test_compiler.py`、`test_native_definition_validation.py`、`test_execution_critical_validation.py`、`test_digest_axes.py`）全部通过，且没有任何断言被修改。如果有测试只能靠改断言才能通过，停止并升级（说明它依赖 legacy 行为）。

### J4.1 — JD 不再使用 `confgen.native` 字段（Q9 已确认）

- ID：J4.1 ／ 仓库：JobDesk-v2 ／ 前置：C4.1
- 类型：`delete`
- 范围：IS.4 合入 JD master 之后，`git grep -n "confgen.native\|CONFGEN_NATIVE_FIELD" -- src tests` 的全部命中。如果没有命中，本卡记为"无改动"，写入 LOG 并跳过。
- **前置检查结果（2026-10-03，@2c7e121）：有命中，不能跳过。** `application/editor/confgen_v3.py`（`CONFGEN_NATIVE_FIELD` 定义与字段表）、`gui/new_run/confgen_v3_form.py`（"Native input (legacy)" 表单控件）、`tests/gui/test_intent_live_gui.py`、`tests/gui/test_intent_panels.py`；`application/intent/model.py` 的拒绝提示文字是 IS.4 的行为，保留。

### C4.2 — re-pin 到 J4.1（J4.1 无改动时跳过）

- 同 C3.1。

### C4.3 — 删除 legacy native 执行路径与 schema

- **登记（用户 2026-10-03）：** `TestCapabilityVocabulary::test_confgen_requires_explicit_seed` 与 `TestGoatSeedValidation::test_confgen_seed_rules_preserved` 固定的是旧 native 的 `seed_required` 规则（C4.1 中改为显式 legacy native）。C4.3 删除旧路径时必须处理：改写为 v3 对应规则，或删除（删除需用户批准）。同时须确认「v3 全枚举无需种子」是预期行为（设计依据：种子是唯一的随机性权威，全枚举无随机性；`PRODUCER_INTENT.md` 同此），并报告用户。

- ID：C4.3 ／ 仓库：ConfFlow ／ 前置：C4.2
- 目标：删除 `native.chains` / `native.paths` 的执行、解析和 contract 字段。
- 类型：`delete`
- 允许修改的文件：
  - `confflow/execution/confgen_executor.py`
  - `confflow/science/confgen/planner.py`、`confflow/science/confgen/__init__.py`、`confflow/science/confgen/torsion/__init__.py`、`confflow/science/confgen/torsion/legacy.py`（删除）
  - `confflow/workflow/v4/confgen_schema.py`、`confflow/workflow/v4/schema.py`、`confflow/workflow/v4/parser.py`、`confflow/workflow/v4/document.py`
  - `confflow/producer/manifest.py`、`confflow/producer/contract.py`
  - `docs/refactor/baseline/checkpoints/C4.3/*`
  - 测试：由第 1 步的 grep 结果确定，逐条声明
- 具体步骤（@f87da58）：
  1. 执行前列出调用方：对 `confgen_executor.py` 中 `_run_legacy`（L812）、`_working_adjacency`（L1058）、`_prepare_path_rotors`（L1100）、`_realize_grid_members`（L1281）、`_build_conformer_members`（L1324）、`_complete_legacy_grid`（L1382）、`_run_legacy_paths`（L1475）、`_path_resolution_payload`（L1592）、`_as_str_list`（L1661）、`_bond_set`（L1674）、`_bond_pair`（L1683）、`_check_index`（L1692）、`_write_report`（L1696）、`_GridCancelled`（L147）、`ALLOWED_NATIVE_KEYS`（L117）、`_DEFAULT_*`（L136-138）、`_LEGACY_MAX_DECLARED_STATES`（L144），运行 `grep -n "<名字>\b" confflow/execution/confgen_executor.py`，只删除调用方全部在被删集合内的项。写方案时核实，v3 路径仍在使用 `_resolved_path_diagnostics`（v3 调用于 L407）、`_warning_diagnostics`（L408）、`_thaw_path_resolution`（L718）、`_driving`（L247）、`_fail`、`_cancelled`，这些保留。
  2. `_run`（L190-206）中 `schema_version != 3` 的分支改为直接 `_fail("confgen requires a typed schema_version 3 scope")`。这是删除分支后剩下的唯一出口，不算新增逻辑；在提交信息中说明。
  3. `planner.py`：删除 `normalize_executor_native`（L707-852）；删除 `torsion/legacy.py` 及 `science/confgen/__init__.py`、`torsion/__init__.py` 中的再导出。
  4. `confgen_schema.py`：删除 `LegacyPathDeclarationModel`（L339-…）以及 step 级 legacy `native` 的 schema 和解析合并逻辑（`parser.py:433-440` 中的 `native` 回退）。用 `git grep -n "LegacyPathDeclarationModel\|native\[\"paths\"\]\|legacy native"` 核对。
  5. `manifest.py:633-634` 的 `confgen.native` 字段、`contract.py:324` 的 `confgen.native` 块删除。写检查点 `checkpoints/C4.3/`。
- 禁止事项：G1–G9；`science/torsion.py` 不在本卡（C4.4）；不得改动 v3 路径的任何代码行。
- 验收命令：标准验收；`json_paths_diff.py` 对比上一检查点，删除的只能是 `confgen.native` 相关路径；**TS1 与 engine 报告与 B0.1 / IS.5 检查点逐字节相同**（随测试删除的报告除外）。
- 提交信息模板：`refactor(confgen)!: delete the legacy native chains/paths path` + 通用尾部。

### C4.4 — 删除 `science/torsion.py` 中只被 legacy 使用的函数

- ID：C4.4 ／ 仓库：ConfFlow ／ 前置：C4.3
- 类型：`delete`
- 允许修改的文件：`confflow/science/torsion.py`、`confflow/science/__init__.py`、对应测试（逐条声明）
- 步骤：对 `science/torsion.py` 中每个顶层函数运行 `git grep -n "\b<名字>\b" -- confflow`，只删除再无调用方的函数。写方案时核实，v3 torsion stage 依赖 `clashes`、`edge_in_cycle`、`rotate_atoms_around_bond`、`rotating_side`、`topological_distance_matrix`（`science/confgen/torsion/stage.py:41-47`@d5a40ae），必须保留；`science/confgen/torsion/paths.py`（IS 新增）的依赖也必须保留。
- 验收：标准验收；golden 不变。

---

## 10. Phase 5：calc / 遗留 CLI / core 清理

基准：C4.4 之后的 `refactor/diet`。行号 @d5a40ae（IS 分支没有改动这些文件）。

### D11 — 在 DECISIONS.md 追加取代 D010 的决策

- ID：D11 ／ 位置：`/opt/confjob-coordinator/DECISIONS.md`（不是 git 仓库，Q14）／ 前置：无
- 类型：`doc`
- 步骤：按文件头规定的格式（L9-17）在文末追加：
  ```
  ## D011 — calc/confts/confgen/confrefine legacy tooling is retired (supersedes D010)
  Date: <执行日期>
  Status: ACCEPTED
  Decision: confts, confgen and confrefine have never been used. confflow/calc, confflow/confts.py, confflow/blocks/{confgen,refine,viz}, the legacy-only parts of confflow/core, confflow/shared/config_coercion.py, their console scripts and lazy exports are deleted. blocks/refine is deleted only after the refine gap report (refactor card C5.1) is confirmed by the owner.
  Reason: Owner confirmation that the CLIs have no users; static reachability shows these modules are reachable only from the legacy console scripts.
  Consequences: D010 is superseded. Public CLI commands confts/confgen/confrefine and the confflow.CalcStepRunner/CalcStepRequest/CalcStepResult exports disappear.
  ```
  不得修改已有条目。在 LOG.md 记录追加前后文件的 sha256。

### C5.1 — refine 差异报告（结论需要用户确认）

- ID：C5.1 ／ 仓库：ConfFlow ／ 前置：B0.1（可提前）
- 类型：`doc`
- 允许修改的文件：`docs/refactor/REFINE_GAP.md`（新增）
- 步骤：逐项对比 `confflow/blocks/refine/`（`processor.py` 878 行、`rmsd_engine.py` 797 行、`topology.py` 660 行，入口 `blocks/refine/__init__.py:main`）与 V4 的 refine（`confflow/execution/transform_executor.py`；preset `refine_default`，在 IS 合并后的 `producer/presets.py` 中）。至少覆盖：去重判据（RMSD 算法、对称性/原子置换处理、阈值）、能量窗口、拓扑一致性检查、输入/输出格式、溯源元数据（`input_sha256`/`output_sha256`）、性能路径（numba）。每项给出"blocks/refine 有 / V4 有 / 差异与证据（文件:行号）"。缺失项单独列在文首。
- 验收：只新增该文件。**结论固定为"升级给用户"**，等用户逐项确认后，C5.3 才能开始。

### C5.2 — 删除 `calc/`、`confts.py`、`workflow/composition.py`、`blocks/viz/`（侧分支）

- ID：C5.2 ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5`（从 `refactor/diet` 的 b8e85a3 分出，**侧分支**，与 JD/Phase 3 的主线并行；Phase 3 结束后由验收方合回 `refactor/diet`）／ 前置：D11
- 类型：`delete`（带必要的测试改写）。做法：应用 `handoff/C5.2-src-tests.patch`（73 个文件），**不重新设计**。验收方已在其上跑通全量测试与 golden。
- 内容：删除 `confflow/calc/**`、`confts.py`、`workflow/composition.py`、`blocks/viz/**`；`blocks/refine/result.py` 自带 `RefineResult`（不再 import calc）；删除 `confflow/__init__.py` 的 CalcStep* 导出、`pyproject.toml` 的 `confts` 入口；`scripts/architecture_metrics.py` 去掉被删模块；架构测试的 `REMOVED_LEGACY_MODULES` 加入四个被删模块；删除只测试被删模块的测试文件，混合文件只删或改写相关测试；新增 `tests/results_db_fixture.py`（`workflow/export.py` 仍读取旧 `results.db`，其测试需要一个最小写入器，见下"发现"）。
- 被删测试：`handoff/C5.2-removed-tests.txt` 的 376 项（含 `tests/test_viz_report.py::TestCoreTypesRetired::test_core_types_module_is_gone`，它被原样搬到 `tests/test_core_types_retired.py`，新增节点共 4 个：这一个加三个 `test_legacy_module_inventory_is_intentional[confflow.blocks.viz|confflow.calc|confflow.confts]`）。
- **发现（需用户之后决定，不阻塞本卡）**：`confflow/workflow/export.py` 与 `confflow export` 命令读取 calc 产生的 `results.db`；本卡之后没有任何生产代码再写该文件。本卡保留 export 及其测试（用最小写入器造库），是否整体删除 export 另议。
- 禁止事项：G1–G9；不得手改补丁；不得运行 `ruff format` 于整个仓库（只允许 `ruff format --check` 于被改文件）。
- 验收命令（**必须用屏蔽可编辑安装钩子的环境**，否则被删模块会从 `/opt/ConfFlow` 里被找到而假通过）：
  ```bash
  A=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc
  python3 $A/run_sharded.py --cf $CF --out /tmp/refactor-acc/C5.2/out.json --jdpin /opt/cf-worktrees/jd-pin
  ```
  期望：`{"passed": 4118, "skipped": 12}`，0 failed；collect 4130（4502−376+4）；`golden_check.py` 全部 ok；`PYTHONPATH=$A/noeditable:. python3 -c "import confflow; confflow.CalcStepRunner"` 抛 `AttributeError`；`git grep -nE "(from|import) +confflow\.(calc|confts)|from +\.+(calc|confts)" -- confflow scripts` 无输出；ruff check、mypy 通过。
- 提交信息模板：`refactor!: delete the legacy calc tooling and the confts CLI` + 通用尾部（Removed-Tests 376 附清单文件名，Added-Tests 4）。

### C5.3a — 把图同构映射原样搬入 `confflow/science/`（move；用户 2026-10-02 决定 Q-R1 移植）

- ID：C5.3a ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5`（侧分支，基点 2f95dc1，即 C5.2 提交） ／ 前置：C5.2 ／ 类型：`move`（函数体一字不改，旧测试随迁）。做法：应用 `handoff/C5.3a-src-tests.patch`（8 个文件），不重新设计。
- 内容：
  1. `blocks/refine/topology.py` → `science/topology_mapping.py`（git mv）；只改文件头的 import 行（`core.bonding.build_adjacency`、`core.data` 直接导入，不再经 `_compat` 的回退加载）；`blocks/refine/topology.py` 变为 20 个名字的纯重导出垫片（C5.3d 一并删除）。
  2. `rmsd_engine.py` 中的 `kabsch_rmsd`（旧版，含 999.9 哨兵值，**不是** `science/cluster.kabsch_rmsd`）、`PairVerdict`、`_frame_graph`、`_effective_cutoff`、`_element_distance_fingerprint`、`_build_candidate_priority`、`compare_frames` 原样搬到新文件 `science/frame_compare.py`（`ENERGY_RMSD_SCALE_FACTOR` 随迁），`rmsd_engine.py` 从新位置重导入，其余内容不动。
  3. 随迁测试：`tests/test_refine_graph_rmsd.py` 中只依赖被搬代码的 8 个测试及其辅助函数搬到 `tests/science/test_topology_mapping.py`（测试体不变，仅 import 路径改为新模块）。
  4. 新增性能/行为固定测试（不测耗时，只固定节点数、映射数、剪枝数与判定），数据为带氢分子的字面坐标 `tests/science/data/molecules_h.json`：丁烷（甲基氢置换）(14,1,0)、叔丁醇（甲基互换+氢循环）(15,1,0)、苯环重标号（恒等映射）、甲苯邻/间位互换（需图映射）(15,1,0)、反式/邻位交叉丁烷（不得合并，`distinct`，(576,0,246,完整)）、预算 5 → `unresolved`（节点 6）；另固定 `DEFAULT_MAPPING_NODE_BUDGET == 1000`。
- **必须保留的上限与剪枝**（验收方已读源码，被搬代码原样保留，由上面的固定测试守护）：
  1. 节点预算 `DEFAULT_MAPPING_NODE_BUDGET = 1000`（每对结构、每次搜索），超过 → `unresolved`（两帧都保留，绝不合并）。
  2. 前置不变量过滤：原子数、元素计数、度序列、4 轮 Weisfeiler-Lehman 着色。
  3. 候选按（原子序数，度）分桶，并逐点检查邻接一致性。
  4. 候选优先级 `_build_candidate_priority` 按局部几何距离排序：第一批完整映射接近最优，是 1000 节点预算在高对称体系上仍可用的前提。
  5. 成对距离下界剪枝：仅全原子比较启用，`heavy_only=True` 时关闭。
  6. 恒等映射优先，`seen_projections` 去重；返回第一个 RMSD < 阈值的见证映射，不是全局最小。
  7. 预算耗尽 / 无合法映射 / 所有映射都高于阈值 → `unresolved` / `different_topology` / `distinct`，不得改变。
- **已核实：** `science/bonds.perceive_adjacency` 只是 `core.bonding.build_adjacency` 的薄封装（12000 例逐位相同，仅错误处理不同），故搬迁不替换函数体，保留 `build_adjacency` 调用。结果：`science/` 依赖 `core/bonding.py`、`core/data.py`、**`core/constants.py`**（`HARTREE_TO_KCALMOL`），C5.5 必须保留这三个文件（PLAN-2：最终移入 `science/`）。
- 验收：
  - `python3 tools-acc/move_identity_check.py <树> HEAD confflow/blocks/refine/topology.py confflow/science/topology_mapping.py '*'` 输出 21 个 identical、退出码 0；同工具对 `rmsd_engine.py → science/frame_compare.py` 的 7 个名字全部 identical。
  - 全量（`run_sharded.py --jdpin /opt/cf-worktrees/jd-pin-cf`）`{"passed": 4125, "skipped": 12}`；collect 4137（4130 − 8 + 8 + 7）；被删节点恰为 `handoff/C5.3a-removed-tests.txt` 的 8 项（搬走的旧 id），新增节点恰为 `handoff/C5.3a-added-tests.txt` 的 15 项（8 项搬入 + 7 项新固定测试）；`golden_check.py` ok；ruff/mypy/black 通过。
- 提交信息模板：`refactor(science): move graph isomorphism mapping and frame comparison out of blocks/refine` + 通用尾部（Removed-Tests 8、Added-Tests 15，附清单文件名；测试名从清单逐字复制）。

### C5.3b — V4 refine 调用对称映射去重（logic；Q-R1）

- ID：C5.3b ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5` ／ 前置：C5.3a、C5.2b（基点 a261bbf） ／ 类型：`logic`（科学行为变化：V4 refine 将合并仅原子标号对称置换的构象）。做法：应用 `handoff/C5.3b-src-tests.patch`（3 个文件），不重新设计。
- 内容：`TransformExecutor._refine` 改用 `science/frame_compare.compare_frames`（懒加载，避免 worker 导入闭包载入 `confflow.core`，该约束由 `test_worker_run_import_closure_is_compiler_free` 守护）；每条记录用 `perceive_adjacency`（`bond_scale`，错误语义不变）建图后做合法映射下的 RMSD 比较；新增 native 键 `mapping_budget`（非负整数，默认 1000 = `DEFAULT_MAPPING_NODE_BUDGET`），映射搜索预算耗尽 → 两帧都保留，notes 写明 "unresolved"。科学分组（含元素顺序）、`max_structures`、`heavy_only` 语义不变。
- **裁定（用户默认，写入此处）：** 阈值比较用旧 refine 的严格小于（`rmsd < threshold`），与搬来的 `compare_frames` 及其测试一致；因此 `rmsd_threshold_angstrom = 0` 不再合并任何结构（旧 V4 的 `<=` 在 0 时会合并完全相同的复制品）。notes 文本从 `<=` 改为 `<`。
- 独立对照与回归（新增 15 项，带氢的真实分子）：
  1. RDKit `rdMolAlign.GetBestRMS` 交叉验证：丁烷（甲基氢置换）、叔丁醇（甲基互换+氢循环）、甲苯（邻/间位互换）、苯环（环旋转），V4 合并 ⇔ GetBestRMS < 0.25；其中除苯环外固定下标 RMSD 都大于阈值，所以合并只能来自合法映射搜索。
  2. 不得误合并：反式/邻位交叉丁烷、反式/邻位交叉 2-丁醇（GetBestRMS > 0.25，两者都保留）；对称置换的副本不得掩盖真正不同的构象。
  3. 输入顺序不影响结果；预算耗尽 → 保留并注明；`mapping_budget` 校验；阈值 0 不合并；`REFINE_DEFAULT_MAPPING_BUDGET == DEFAULT_MAPPING_NODE_BUDGET`。
- 限制（已核实，不在本卡改变）：科学分组键含元素顺序，只在同一元素序列内比较；ConfGen 同一输入产生的构象元素序列相同，所以这是相关情形。
- 自检期望：`{"passed": 4123, "skipped": 12}`（含两个负载敏感测试，见下），collect 4135（4120 + 15），Removed 0、Added 15（清单 `handoff/C5.3b-added-tests.txt`）；`golden_check.py` ok（TS1、engine 报告不涉及 refine）；ruff/mypy/black 通过。**已知负载敏感：** `tests/v4/test_v4_runtime_cutover.py` 的 `test_worker_runs_v4_end_to_end`、`test_plain_cli_runs_v4_application` 在并行分片且机器忙时偶发失败（单独运行稳定通过）；若只有它们失败，单独重跑，通过则在报告里如实写出。
- 提交信息模板：`feat(execution)!: refine merges symmetry-equivalent relabellings through the legal-mapping search` + 通用尾部（Added-Tests 15，测试名从清单逐字复制；Behavior-Change 写明严格小于与阈值 0）。

### C5.3c — refine 的 `topology_bonds` 参数（Q-R2，用户 2026-10-02 选方案 B）

拆为两张卡：**C5.3c-1（CF，logic）** 与 **C5.3c-2（编译器自动复制，IS 分支）**。

**拓扑来源（已定）：** refine 步骤新增 native 参数 `topology_bonds`；由 intent 编译器从上游 ConfGen 步骤自动复制。不改 ConfGen 的发布内容，不改 contract（验收方已核实：transform 的 native 键不在 contract 中，`rmsd_threshold` 等均未出现在 `contract.full.json`；`REFINE_NATIVE_KEYS` 只在 `transform_executor.py` 的运行时校验里）。

**C5.3c-1（CF，前置：C5.3b；类型 `logic`）——用户 2026-10-02 裁定后的规则（补丁已备，基点 efbaecd）：** 做法：应用 `handoff/C5.3c-1-src-tests.patch`（5 个文件），不重新设计。
1. **构图：refine 直接复用 `science/confgen/planner.build_typed_graph`**（经 `normalize_spec` 规范化，与 ConfGen 同一路径），规则与 ConfGen 完全相同：声明了 `bonds` 时声明边完全胜出、不做几何感知；否则几何感知后依次应用 `add_bond`（非共价类型替换同一对感知出的共价边；COVALENT 为新增）、`del_bond`（只能删共价对，不存在的对静默忽略）、配位范围叠加；同一对给出矛盾类型报错。
2. **参数 `topology_bonds`**（`REFINE_NATIVE_KEYS` 新增）：成员 `index_base`（0|1，默认 1）、`bonds`、`add_bond`、`del_bond`、`atoms`、`coordination`、`bond_scale`；类型词汇四种（COVALENT/COORDINATION/FORMING/BREAKING），取自 `science/confgen/graph.py::EdgeType`。`ValueError` 一律转成 `DomainError`（`refine topology_bonds is invalid` / `does not fit <id>`）；索引越界、与结构原子数不符、矛盾类型都报错。
3. **`bond_scale`：** 声明了拓扑时用 `topology_bonds.bond_scale`（缺省为 ConfGen 的 1.15）；未声明时仍用 refine 的 1.2，行为不变；**同时给出 native `bond_scale` 与 `topology_bonds` 视为冲突，报错**（验收方裁定，避免两个口径悄悄并存）。
4. **映射保持边类型：** `science/topology_mapping.Graph` 新增可选字段 `typed_edges`（非共价类型边，默认空）与 `with_typed_edges()`；`MappingSearch` 在候选检查中比较已赋值顶点对的边类型，`fixed_index_isomorphism`、`graphs_may_be_isomorphic`（按类型计数）、`validate_mapping` 同步；没有类型边的图，搜索节点数、剪枝数与判定逐项不变（C5.3a 的固定测试原样通过）。边的 `bond_order`、`atoms` 的 label/role/stereo 不参与映射（文档写明）。
5. **新增测试 21 项**：同一份声明经 ConfGen 路径（`normalize_spec`+`build_typed_graph`）与经 refine 路径，边集合（含类型）完全相同（`bonds`+FORMING、`bonds`+BREAKING 且 `index_base: 0`、`add_bond`+`del_bond`、配位范围、显式 `bond_scale` 共 5 组）；声明的类型确实出现；`index_base` 0/1 等价；**几何相同、反应键连在不同物理原子上 → 不合并**（对照：无类型边时合并），反应键在对称不变的位置时仍合并；无参数时与旧行为一致；非法声明、原子数不符、`bond_scale` 冲突的报错；`science` 层的类型边单元测试。
6. **文档（已写）：** `docs/architecture/WORKFLOW_V4.md` 新增 refine 章节：去重判据（合法映射、严格小于、预算与 unresolved）、**ConfGen 带标号状态数与 refine 后物理构象数是两个口径（σ 相关结构会被合并）**、TS1 的 refine 结果仅作信息性记录不作通过条件、`topology_bonds` 语义、与旧 `AddBond/DelBond` 的三处差异。
7. **与旧 `AddBond/DelBond` 的差异（用户已接受，文档已写明）：** 同一对同时出现在 add 与 del 时，旧 refine 先 del 后 add（add 胜），ConfGen 先 add 后 del（del 胜），新规则取 ConfGen；非法条目旧为静默忽略，新为报错；声明了拓扑时 `bond_scale` 由 1.2 变 1.15；来源由"帧注释"改为步骤参数。
- 自检期望：全量 `{"passed": 4144, "skipped": 12}`；collect 4135 → 4156（+21，Removed 0，清单 `handoff/C5.3c-1-added-tests.txt`）；`golden_check.py` ok；ruff/mypy/black 通过。
- 提交信息模板：`feat(execution)!: refine accepts a declared typed topology and preserves typed edges in its mappings` + 通用尾部（Added-Tests 21，测试名从清单逐字复制）。

**C5.3c-2（编译器自动复制，IS 分支）**。

**拓扑来源（已定）：** refine 步骤新增 native 参数 `topology_bonds`；由 intent 编译器从上游 ConfGen 步骤自动复制。不改 ConfGen 的发布内容，不改 contract（验收方已核实：transform 的 native 键不在 contract 中，`rmsd_threshold` 等均未出现在 `contract.full.json`；`REFINE_NATIVE_KEYS` 只在 `transform_executor.py` 的运行时校验里）。

**C5.3c-1（CF，前置：C5.3b；类型 `logic`）——用户 2026-10-02 裁定后的规则：**
1. **构图：refine 直接复用 `science/confgen/planner.build_typed_graph(structure, topology, resolved)`**，规则与 ConfGen 完全相同：声明了 `bonds` 时声明边完全胜出、不做几何感知；否则几何感知后依次应用 `add_bond`（非共价类型替换同一对感知出的共价边；COVALENT 为新增）、`del_bond`（只能删共价对，不存在的对静默忽略）、配位范围叠加；同一对给出矛盾类型报错。
2. **参数形状：** `topology_bonds` = ConfGen 的 `topology`（`bonds` 或 `add_bond`/`del_bond`，带类型，外加 `atoms`），加 `index_base`（0|1，默认与 ConfGen 文档一致为 1）；`coordination` 范围与 `tolerances.bond_scale` 由编译器一并复制到 refine 步骤（`topology_bonds` 内以 `coordination`、`bond_scale` 两个成员携带）。类型词汇四种（COVALENT/COORDINATION/FORMING/BREAKING），取自 `science/confgen/graph.py::EdgeType`。
3. **`bond_scale`：** 声明了拓扑时用复制来的 ConfGen 值（默认 1.15）；未声明时仍用 refine 自己的 1.2，保持旧行为。refine 在内部构造 `resolved = {"tolerances": {"bond_scale": ...}, "coordination": ...}` 传给 `build_typed_graph`，不改 ConfGen 代码。
4. **校验：** `index_base` 与 ConfGen 一致；转换成 0 起始后，索引越界、原子数不符、矛盾类型 → `DomainError`（`build_typed_graph` 抛出的 `ValueError` 统一转换）。
5. **映射保持边类型：** 在 C5.3a 搬来的映射搜索上增加边类型标签（对已搬代码的 logic 修改，只发生在本卡）；几何相同但反应键（FORMING 等）连接的原子对不同 → **不得合并**。
6. **测试：** 沿用 C5.3b 全部对照；新增：反应键异位不合并；`index_base` 0/1 等价；原子数不符报错；无参数时与旧行为逐位一致；**同一份声明分别经 ConfGen 构图（`build_typed_graph` 经 ConfGen 路径）与经 refine 构图，边集合（含类型）完全相同**（`bonds` 模式与 `add_bond/del_bond` 模式各一组，含 `coordination` 一组）。
7. **与旧 `AddBond/DelBond` 的差异（用户已接受，文档必须写明）：** 同一对同时出现在 add 与 del 时，旧 refine 先 del 后 add（add 胜），ConfGen 先 add 后 del（del 胜），新规则取 ConfGen；非法条目旧为静默忽略，新为报错；感知 `bond_scale` 旧为 refine 的 1.2，新（声明了拓扑时）为 ConfGen 的 1.15；来源由"帧注释"改为步骤参数。
8. **文档（本卡必须写）：** ConfGen 的带标号状态计数与 refine 之后的物理构象数是两个口径（σ 相关结构会被合并）；TS1 的 refine 结果作为信息性记录，不作为通过条件；第 7 条的差异。

**C5.3c-2（编译器，前置：IS 合并进 main 之后；类型 `logic`）：** intent 编译器在生成 refine 步骤时，**沿结构数据流向上找最近的 ConfGen 步骤**复制 `topology`（含 `index_base`）、`coordination` 范围与 `tolerances.bond_scale` 到 `topology_bonds`，**不要求紧邻**（中间可隔着优化等步骤）。若沿数据流存在多个上游 ConfGen 来源且它们的拓扑声明不一致 → 不自动复制，报错并要求用户显式指定；声明一致则可复制；上游没有 ConfGen 或无拓扑声明则不写该参数（保持旧行为）。编译器位置（IS.0 后核实）：ConfFlow 的 `confflow/producer/intent.py`（`implementation/input-simplification` 分支，IS.5 合并进 main 之后）；JD 的 `application/intent/` 只是调用方，不需要改动。

### C5.3d + C5.4 — 删除 `blocks/refine/` 与 `blocks/confgen/`（合并为一张 delete 卡；前置：C5.3a/b/c-1 通过）

- ID：C5.3d+C5.4 ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5` ／ 基点 6521581（C5.3c-1） ／ 类型：`delete`。做法：应用 `handoff/C5.3d-4-src-tests.patch`（35 个文件），**随后 `rm -rf confflow/blocks`**（`git rm` 之后目录里还残留未跟踪的 `__pycache__`，会让 `confflow.blocks` 仍能作为命名空间包被 import，`deleted_modules_check` 会报错）。
- 前提已满足：对称映射去重已搬入 `science/`（C5.3a）并接入 V4 refine（C5.3b）；声明拓扑与类型边已接入（C5.3c-1，替代 `blocks/confgen` 里的 `AddBond`/`DelBond` 覆盖逻辑）；`blocks` 在生产代码里没有任何 import（只有 `pyproject.toml` 的两个入口）。C5.3c-2（编译器自动复制拓扑）在 IS 合并后单独做，不阻塞本卡。
- 内容：删除 `confflow/blocks/**`；`pyproject.toml` 删除 `confgen`、`confrefine` 两个入口；`scripts/architecture_metrics.py` 的 `CALC_PUBLIC_TOOLING_ROOTS` 清空；架构测试把 `confflow.blocks`、`confflow.blocks.refine`、`confflow.blocks.confgen` 加入 `REMOVED_LEGACY_MODULES`（新增 3 个参数化节点）；删除只测试被删模块的测试文件 16 个（另删 `TestLegacyToolingBoundary` 中两个在子进程里 import 被删包的守护测试：`test_confgen_tooling_does_not_load_calc_at_all`、`test_confrefine_tooling_does_not_load_calc_execution_runtime`）（`test_collision*`、`test_confgen`、`test_confgen_validator`、`test_confgen_refine_fallbacks`、`test_mapping`、`test_optional_numba`（其两个 numba 测试在本环境为 skipped，但都 import `blocks`）、`test_processor_hotspots`、`test_provenance_metadata`、`test_refine`、`test_refine_graph_rmsd`、`test_rmsd_engine_hotspots`、`test_topology_provenance`、`v4/test_confgen_scientific_regressions`）；混合文件只删相关测试（`test_bonding_consistency` 删 hash-worker 测试并把 `build_graph` 改自 `science.topology_mapping`；`test_core`、`test_dependency_boundaries`、`test_small_adapters_extra` 各删守护已删包的测试）。
- 被删测试：`handoff/C5.3d-4-removed-tests.txt` 的 214 项；新增 `handoff/C5.3d-4-added-tests.txt` 的 3 项。B0.1 的 engine 报告捕获没有来自被删文件的报告（golden 实测 `missing_allowed_removed` 为空、`different` 为空）。
- 自检期望：全量 `{"passed": 3935, "skipped": 10}`；collect 4156 → 3945（−214 +3）；`deleted_modules_check.py cf --tree <树> confflow.blocks confflow.blocks.refine confflow.blocks.confgen` 退出码 0（**必须先 rm -rf confflow/blocks**）；golden `--removed-nodes` 清单后 ok；ruff/mypy/black 通过。
- 已知负载敏感测试（并行分片下偶发，单独稳定）：`test_v4_runtime_cutover` 的两个端到端测试、`test_v44_worker::test_cancel_trap…`、`test_terminal_arbitration_recheck::TestRealControlCancel::test_concurrent_control_cancel_and_completion_stay_consistent`；若只有它们失败，单独重跑，通过则如实报告。
- 提交信息模板：`refactor!: delete the legacy refine and confgen blocks and their CLIs` + 通用尾部（Removed-Tests 214 附清单文件名，Added-Tests 3；测试名从清单逐字复制）。

### C5.4 — （已并入上一卡）

### C5.5 — 删除 `core/` 和 `shared/` 中只被 legacy 使用的部分

- ID：C5.5 ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5` ／ 基点 deadf46（C5.3d+C5.4） ／ 类型：`delete`。做法：应用 `handoff/C5.5-src-tests.patch`（14 个文件），不重新设计。
- 依据：在 C5.3d+C5.4 之后重新运行 `reachability.py`，`core/` 与 `shared/` 里不可达的模块恰为 `core/chem_validation.py`、`cli_base.py`、`keyword_rewrite.py`、`models.py`、`pairs.py`、`validation.py`、`shared/config_coercion.py`（并已 grep 确认生产代码没有 import）；`core/bonding.py`、`data.py`、`constants.py` 被 `science/` 使用，保留（PLAN-2：最终移入 `science/`）。
- 内容：删除上述 7 个模块；`core/__init__.py` 的 `_LAZY_EXPORTS` 删除 `TaskContext` 与全部 `validate_*`；删除只测试它们的测试文件（`test_keyword_rewrite`、`test_models`、`test_validation`、`test_small_adapters_extra`）；`test_bonding_consistency` 去掉依赖 `chem_validation` 的 RDKit 加载器那一段（保留 `build_adjacency` 与 `science` 图层的一致性断言）；架构测试把 7 个模块加入 `REMOVED_LEGACY_MODULES`，把 `confflow.core.models` 从 `FORBIDDEN_LEGACY_MODULES` 移走，并从 `test_core_public_surface_still_importable` 去掉对已删符号的断言。
- 被删测试：`handoff/C5.5-removed-tests.txt` 的 86 项；新增 `handoff/C5.5-added-tests.txt` 的 6 项（6 个"必须已删除"的参数化节点；`core.models` 的节点本来就在）。
- 另发现（**不在本卡**）：`reachability.py` 还报出 `analysis.pes`、`application.execution.memory`、`release_dependencies`、`workflow.step_naming` 四个从 CLI 入口不可达的模块，计划里没有对应的卡；很可能只被测试或打包脚本使用，不动，留给用户决定。
- 自检期望：**在 `rm`/删除全部完成之后**跑全量：`{"passed": 3855, "skipped": 10}`；collect 3945 → 3865（−86 +6）；`deleted_modules_check.py cf --tree <树> confflow.core.models confflow.core.validation confflow.core.chem_validation confflow.core.cli_base confflow.core.keyword_rewrite confflow.core.pairs confflow.shared.config_coercion` 退出码 0；golden（`--removed-nodes`）ok；ruff/mypy/black 通过。
- 提交信息模板：`refactor!: delete the legacy-only core and shared modules` + 通用尾部（Removed-Tests 86、Added-Tests 6，测试名从清单逐字复制）。

### C5.6 + C5.7 + C5.8 — 进程识别、文档与架构测试收尾（合并为一张卡）

- ID：C5.6+C5.7+C5.8 ／ 仓库：ConfFlow ／ 分支：`refactor/diet-c5` ／ 基点 da44bf5（C5.5） ／ 类型：`delete`（含文档）。做法：应用 `handoff/C5.6-8-src-tests.patch`（11 个文件），不重新设计。
- **C5.6：** `confflow/cli.py` 的进程识别集合改为 `{"confflow"}`，并删除关于 `confcalc` 的历史注释；没有测试断言过这些名字（全量无失败）。
- **C5.7（文档）：** README 删除 `confgen`/`confrefine`/`confts` 三行命令表与 `confgen` 示例及 Keyword Reference 链接；`COMMAND_REFERENCE.md` 删除 `confgen`/`confrefine`/`confts` 三节并把说明里的命令列表改为 `confflow`；`USAGE.md` 删除工具总览中的三个已删命令及第 4（confgen）、5（confrefine）、6（Calc step 与 TS 救援）节；`ARCHITECTURE.md`、`DEVELOPMENT.md`、`TESTING.md` 删除 `blocks/`、`calc/` 两棵目录树与对应章节、指向已删测试文件的条目和已删 CLI 的说明；`WORKFLOW_V4.md` 把对已删符号的名字引用改为"已删除的旧 calc 运行器"等中性措辞（含上一张卡新增的那一句）；**整个删除 `docs/KEYWORD_REFERENCE.md`**（全文只有已删的 `confgen`/`confrefine` 关键字和已退役的旧 `confflow input.xyz -c …` 调用形式；README、ARCHITECTURE、DEVELOPMENT 中指向它的链接/条目已一并删除）。历史文档（`docs/archive`、`docs/internal`、`docs/rfc`、`docs/refactor`）不动。**说明：** 这几份文档整体上仍大量描述旧架构的其他部分（例如 calc step 与 TS 救援、工作流引擎），不在本卡范围；本卡只做计划规定的"删除描述已删 CLI/包的章节和句子"。
- **C5.8：** `tests/v4/test_architecture_boundaries.py` 与 `scripts/v4_arch_scan.py` 的 `FORBIDDEN_IMPORT_PREFIXES` 去掉对已不存在的包（`confflow.calc`、`confflow.blocks`、`confflow.confts`）的条目；删除整个 `TestLegacyToolingBoundary` 类（它唯一剩下的测试对已不存在的包恒为真，6 个参数化节点）。`REMOVED_LEGACY_MODULES` 与"模块必须不存在"的测试全部保留。
- 被删测试：`handoff/C5.6-8-removed-tests.txt` 的 6 项；新增 0 项。
- 自检期望：全量 `{"passed": 3849, "skipped": 10}`；collect 3865 → 3859（−6）；`git grep -nE "\bconfts\b|\bconfrefine\b|confflow\.calc|blocks/refine|CalcStepRunner|KEYWORD_REFERENCE" -- README.md docs ':!docs/archive' ':!docs/internal' ':!docs/rfc' ':!docs/refactor'` 无输出（退出码 1）；`tests/test_release_workflow.py` 通过；golden（`--removed-nodes`）ok；ruff/mypy/black 通过。
- 提交信息模板：`refactor!: finish the legacy CLI retirement (process names, docs, architecture guards)` + 通用尾部（Removed-Tests 6，测试名从清单逐字复制）。

---

## 11. 逻辑尾部

### L1 — `_allowed_pairings_by_kind` 改为读常量

- ID：L1 ／ 仓库：ConfFlow ／ 前置：C5.8（技术上只依赖 C1.4）
- 类型：`logic`（输出必须字节相同）
- 允许修改的文件：`confflow/producer/contract.py`、`confflow/execution/contracts.py`（只允许把 `_STRUCTURE_PORT_PAIRINGS` / `_VALUE_PORT_PAIRINGS` 改为公开名或增加只读访问函数）
- 步骤（@d5a40ae）：`contract.py:180-202` 的"构造 `PortSpec` 试探"改为直接按 `execution/contracts.py:110-117` 的规则从这两个常量生成表：`structure` kind 用 `_STRUCTURE_PORT_PAIRINGS`，其他 kind 用 `_VALUE_PORT_PAIRINGS`，列表顺序与 `Pairing` 枚举顺序一致。
- 验收：标准验收；**contract 与 boundary 的 sha256 与最新检查点逐字节相同**。
- **实现（验收方原型，2026-10-03）：** 常量保持私有，新增只读函数 `allowed_port_pairings(kind)`（`execution/contracts.py`），`producer/contract.py` 调用它；表与旧探测结果相同；分支 `refactor/diet-l1`（基点 c6b88ff），补丁 handoff/L1.patch。

### L2 — validation token 字段改为 document_content_digest、contract_digest、target_identity（Q3：决策 4 已修改）

- ID：L2 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J3.3
- 目标：异步验证结论的过期判断只依赖"判的是哪份内容、按哪份合同、针对哪个目标"三项；删除 `request_id`、`session_id`、`session_epoch`、`capability_identity`。
- 类型：`logic`
- 允许修改的文件：`src/jobdesk_v2/application/remote/v4_validation.py`、`src/jobdesk_v2/gui/new_run/presenter.py`、`tests/application/test_p0_pr2_identity_resource.py`，以及 `git grep -n "session_epoch\|request_id\|ValidationRequestIdentity\|validation_request_identity" -- tests` 命中的其他测试文件（只修改断言被删字段的测试，逐条声明）
- 具体步骤（@9beeaf2；J3.1 之后 presenter 行号略有前移，按符号定位）：
  1. `v4_validation.py`：
     - `VALIDATION_IDENTITY_FIELDS`（L275-282）改为 `("document_content_digest", "contract_digest", "target_identity")`；同步更新 L256-273 的说明注释。
     - `ValidationRequestIdentity`（L302 起）：删除 `session_id`、`session_epoch`、`request_id`、`capability_identity` 字段，新增 `contract_digest: str`；`__post_init__` 的必填检查改为 `document_content_digest`、`contract_digest`（`target_identity` 允许为空字符串，与现在相同）；`stale_reasons` 只比较这三项。
     - `ValidationRequestGate.request_id` 属性（L366-368）删除。
     - `validation_request_identity` / `validation_request_identity_from_digest`（L375-417）：参数改为 `document`/`document_digest`、`contract_digest`、`target_identity`。
  2. `presenter.py`：
     - 删除 `_capability_identity`（L853-863，J3.1 之后只剩返回 contract key 的三行）。
     - `_refresh_edit_snapshot`（L889-903）中 `_edit_snapshot` 只保留 digest（不再保存 `session_id`、`session_epoch`）。
     - `_current_validation_identity`（L905-914）不再接收 `request_id`，改为传入 `contract_digest=<当前生效 V4 合同的 contract_digest>`（`VerifiedV4Contract.contract_digest`，`contract/v4.py:130`；`ResolvedContract` 通过 `.v4` 取得）和 `target_identity=self._target_identity()`。
     - `_begin_validation_request`（L916-929）删除 `request_id` 参数；L937 改为 `gate.check(self._current_validation_identity())`；L988 不再生成 `uuid.uuid4().hex`。如果 `uuid` 再无其他用途，删除 `import uuid`（L39）。
     - 当前没有生效的 V4 合同时 `contract_digest` 为空，`__post_init__` 拒绝构造，与现在"未标识的请求被拒"的语义一致。
     - 更新 L840-846 的注释（"binds session id, session epoch, request id, …" 改为三项）。
  3. 预期行为变化（写入 `Behavior-Change`）：
     - (a) 内容、合同和目标都没变时，即使 `session_id` 或 `session_epoch` 变了（例如重新打开同一份草稿、撤销/重做回到相同内容），已返回的验证结论仍然有效，不再被丢弃。
     - (b) 合同变化时，旧结论会被丢弃。之前在生产中实际比较的是 `contract_key`（`capability_identity` 那一支永远取不到值，见 J3.1），现在比较的是 `contract_digest`：`contract_digest` 覆盖整个 envelope，而 `contract_key` 只取 schema/manifest/recipes 三个摘要的前 12 位，所以 producer 只改了其他 section 时，旧结论现在也会被丢弃。
     - (c) 目标（服务器 + 远端目录）变化时，旧结论仍然被丢弃（与现在相同）。
     - (d) 不再有 request 级别的标识；同一份内容、合同、目标下，先发出的请求的结论也会被接受（结论是关于同一内容的，不影响提交安全，因为提交还受 `ValidatedSubmission.validated_sha256` 字节绑定的约束，`v4_validation.py:206-235`）。
- **实现差异（验收方原型，2026-10-03）：** 非 V4 的回退合同没有 `contract_digest`，若为空即拒绝会让 16 个测试失败（`ValueError: requires contract_digest`）；实现在非 V4 时改用合同自身的 `contract_key`（同变更前，回退合同按其 key 判过期）。分支 JD `refactor/diet-p4`（基点 2c7e121），补丁 handoff/L2.patch；`test_new_run_remote.py::test_an_edit_during_validation_discards_the_stale_answer` 因预期行为变化 (a) 去掉 undo，`test_p0_pr2_identity_resource.py` 的 epoch 测试改名并改写。
- 禁止事项：G1–G9；不得改 `bind_validated_submission`、`check_submission_bytes`、`submission_identity`。
- 验收命令：JD 标准验收。期望：失败集合为空；被修改的测试只断言 (a)–(d) 涉及的字段；`git grep -n "session_epoch" -- src/jobdesk_v2/application/remote src/jobdesk_v2/gui/new_run/presenter.py` 在 token 相关代码中无命中（store 自身的 `session_epoch` 不在本卡范围）。
- 提交信息模板：`refactor(validation)!: bind validation answers to content, contract and target only` + 通用尾部（Behavior-Change 列出 (a)–(d)）。

---

## 12. 风险登记（中高风险任务）

| 卡 | 风险等级 | 可能出问题的地方 | 回滚 |
|---|---|---|---|
| J0a / J0b | 中 | J0a 之后的失败集合若与预演的 8 项不同，说明本机环境或 CF 工作树与预演时不同；J0b 改名一个测试（G4 的明示例外），以后与 IS 分支合并时由同一改名吸收。 | `git revert`；B0.1 必须在 J0b 之后重做。 |
| C1.1 | 中 | GitHub Actions 无法在本地运行，触发器合并错误要到下一次 PR 才会暴露。 | `git revert`；本地以 yaml 解析断言和 `test_release_workflow.py` 兜底。 |
| C1.4 | 中 | registry 的返回值与冻结值不同会改变 contract 字节。 | contract sha256 必须不变，否则不提交；`git revert`。 |
| J2.1a | 低 | 远程模式下选择服务器之前编辑区为空（用户已确认）。原型验证不需要改任何已有测试。 | `git revert`。 |
| J2.1b | 高 | GUI 行为变化（用户已确认）；14 个测试需要改写，其中 2 个守护"远端文本不泄露"的安全属性，改写时严格度不得降低；presenter 进入 `unavailable` 的路径与原 `restricted` 不同。 | `git revert`；J2.2–J2.4 依赖它，须按逆序一并回滚。 |
| J2.4 | 高 | 删除量大，`manifest.py` / `recipes.py` 的快照边界判断失误会误删解析器；34 个使用 `service` fixture 的测试文件依赖 J2.2 的替代 fixture。 | `git revert J2.4`；J2.2 的 fixture 保证测试仍可运行。 |
| J3.2 / C3.2 | 高 | 删除 boundary 必需成员会让已部署的旧 JD 拒绝新 producer（Q6）；`result_digest` 在 result schema 中是 `additionalProperties: false` 下的可选属性（`contract.py:436-443`），如果有代码用新 schema 校验旧运行目录里的 manifest，旧 manifest 会被拒。执行时需 `git grep -n "result_schema" -- confflow` 确认没有这种读取路径，有则停止并升级。 | 按 C3.4 → J3.3 → C3.3 → C3.2 → C3.1 → J3.2 的逆序 revert，并把 `$JDPIN` 切回上一 pin。 |
| C3.3 | 中 | provenance 标注位置需用户确认（Q4）。 | revert；只影响 contract。 |
| IS.1 / IS.2 | 高 | 科学行为：typed v3 拒绝末端原子端点、bare 声明默认步长（Q2）。任何不等价都意味着用户可见的构象集合会变。 | IS.1 只新增文件；IS.2 revert 即恢复透传。 |
| IS.0 / IS.5 | 高 | 合并冲突中 IS 新代码引用被删符号；engine 报告可能因 IS 改动而变化。 | `git merge --abort`（未提交前）或 `git revert -m 1 <merge>`。 |
| C4.1 | 中 | 依赖 legacy 默认值的测试被静默改变含义。 | 卡片禁止修改断言；revert。 |
| C4.3 | 高 | 误删 v3 共享的私有方法（例如 `_resolved_path_diagnostics`）；删除 `confgen.native` 改变 contract 并影响 JD（Q9）。 | 按 C4.3 → C4.2 → J4.1 逆序 revert。 |
| C5.2–C5.5 | 中 | 删除公开 API（`confflow.CalcStepRunner`、`confflow.core.validate_*`、console scripts）；局部删除测试函数时误删保留模块的测试。 | 每卡单提交 `git revert`。 |
| L2 | 中 | 去掉 session/epoch/request 后，内容、合同、目标都相同时旧结论会被接受；合同比较改用 `contract_digest`，会比现在更频繁地丢弃结论（见 L2 预期行为变化）。 | revert。 |

---

## 13. 移交 PLAN-2（本轮不做）

以下事项经用户确认留到下一轮方案，本轮任何卡片都不得顺带实现：

1. **`preview_paths` 改收 v3 声明**（Q10）：`producer/path_preview.py:27-95`@f87da58 目前接收 legacy 形状 `native: {paths, angle_step, bond_scale, strict_path_bond_check}`；JD 的 `application/intent/preview.py` 的 `build_preview_native` 生成这一形状。PLAN-2 将改为接收 typed v3 的 `paths` 声明，两仓成对修改。
2. **JD 测试的 producer 路径改为环境变量**（Q15）：`tests/contract_fixtures.py:38-39`、`tests/application/test_p0_boundary.py:51-52`、`tests/application/test_confflow_v4_workflows.py:69`、`tests/application/test_confflow_v4_contract.py:49-50`、`tests/application/test_confflow_v4_tspes_chain.py:62-76`@9beeaf2 把 `/opt/ConfFlow` 和 `/opt/ConfFlow/.venv/bin/python` 写死。改为统一读取环境变量（例如 `CONFFLOW_CWD`、`CONFFLOW_PYTHON`）后，§2.3 的 mount namespace 绑定就可以取消。
3. **v3 发布 0 个结构时应失败或明确告警，不得 completed**（用户 2026-10-02 增补，来自 IS.1 的共线链用例）：v3 执行器在 `confgen v3 published 0 leaf structures (2 raw targets)` 的情况下仍返回 COMPLETED。需要改为失败或带明确的告警诊断。同时必须核实并记录下游步骤收到 0 个结构时的行为。写方案时的初步核实（@d5a40ae，读代码，未运行）：
   - `transform`（refine / deduplicate）：`transform_executor.py:198-204` 对空的 `structure` 输入集抛 `DomainError("transform requires a non-empty 'structure' input set ...")`，即在下游失败得很明确；
   - 计算步骤（calculation，按 `each_entity`/per-structure 绑定）：收到 0 个结构时会产生 0 个 work item 还是报错，**尚未核实**，PLAN-2 需要用最小工作流实际运行确认并记录；
   - 绑定基数：`workflow/v4/graph.py:56-62` 允许绑定把必需端口放宽为 `many`（零个或多个），所以 0 个结构不一定在编译期被拒绝。
4. **Q2c 方向 2**：在 JD 的路径预览阶段（有结构）提前报出末端原子端点，指明具体的键。本轮由运行时拒绝承担（IS.2b）。

N. **优化后键连接变化的构象（Q-R3，用户 2026-10-02）：** 旧 `blocks/refine` 的多数拓扑过滤会删除少数拓扑构象；V4 refine 不过滤，各自保留。待核实：V4 对"优化后键连接发生变化的构象"是否有检查。用最小工作流实际运行确认；没有则记为缺口，交用户决定。

N+1. **`core/bonding.py`、`core/data.py` 最终移入 `science/`（用户 2026-10-02）：** 使 `core/` 可整体删除。当前 `science/bonds.py` 与 C5.3a 搬入的映射代码依赖它们，C5.5 暂时保留。

N+2. **统一 refine 与 ConfGen 的默认 `bond_scale`（用户 2026-10-02）：** 目前 refine 默认 1.2，ConfGen 默认 1.15。统一是行为变化，本轮不做；C5.3c-1 只在声明了拓扑时复制 ConfGen 的值。

N+4. **`analysis/pes.py` 的产品定位（用户 2026-10-03）：** 没有生产代码导入，只有测试使用（test_v46_pes、test_v45_tspes、test_v46_tspes_e2e、test_v46_debt 清单）。留待决定。本轮不动。`application/execution/memory.py`（测试替身）与 `release_dependencies.py`（scripts 使用）保留。

N+5. **可达性工具补扫 `scripts/`（用户 2026-10-03）：** 用单独的小卡，不与删除卡混合。

N+3. **v3 的 `waypoint`（多端点路径）（IS.2 发现；用户 2026-10-03 裁定）：** 用户从未使用，本轮不在 v3 补支持，IS.2 保持编译期拒绝并提示改用显式 `torsions` 声明。**触发条件：**首次实际遇到 `PATH_AMBIGUOUS`（带 `add_bond` 的 TS、金属配合物）时，再为 v3 设计 `waypoint`。IS.5 不受影响。
