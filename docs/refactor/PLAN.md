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
Phase 0  基线冻结            B0.1
Phase 1  ConfFlow 外围删除    C1.1 → C1.2 → C1.3 → C1.4
J1       JD 删除 evaluate_compatibility
Phase 2  JD 删除 V1/V2        J2.1 → J2.2 → J2.3 → J2.4   (J2.5 可选，需用户确认)
Phase 3  边界瘦身（两仓成对） J3.1 → J3.2 → C3.1 → C3.2 → C3.3 → J3.3 → C3.4
IS       输入简化分支改造     IS.1(golden) … IS.0(合入 main) → IS.2 → IS.2b → IS.3 → IS.4(JD) → IS.5(合并到 main，需用户批准)
Phase 4  ConfFlow chain 路径  C4.1 → J4.1 → C4.2 → C4.3 → C4.4
Phase 5  calc/CLI/core 清理   D11 → C5.1(差异报告，需用户确认) → C5.2 → C5.3 → C5.4 → C5.5 → C5.6 → C5.7 → C5.8
逻辑尾部                       L1 (CF pairing 常量) ， L2 (JD token)
```

### 1.2 依赖图

```
J0a ─> J0b ─> B0.1 ──┬──> C1.1 ─> C1.2 ─> C1.3 ─> C1.4 ──────────────┐
       │                                               │
       ├──> J1 ─> J2.1 ─> J2.2 ─> J2.3 ─> J2.4 ────────┤
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

### 2.5 通用禁止事项（每张卡都适用，卡片中以"G1–G10"引用）

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

J0b 起，每张卡由 CodeBuddy 的 `glm-5.3-flash` 以非交互方式执行，一次一张卡。验收方负责发起和验收。以下参数形式经过逐条实测（2026-10-02，判断依据是原始的 `function_call_result`，而不是模型的文字回答）：

```bash
DENY=(); for r in "Bash(unshare:*)" "Bash(mount:*)" "Bash(sudo:*)" "Bash(pip:*)" "Bash(pip3:*)" \
                  "Bash(apt:*)" "Bash(apt-get:*)" "Bash(git push:*)"; do DENY+=(--disallowedTools "$r"); done
cd <卡片所在仓库的执行工作树> && codebuddy -p --model glm-5.3-flash --permission-mode acceptEdits \
  --allowedTools "Bash(*)" "${DENY[@]}" \
  --add-dir /opt/cf-worktrees/exec-cf --add-dir /opt/cf-worktrees/exec-jd --add-dir /opt/cf-worktrees/jd-pin \
  --output-format json "<交接提示词>"
```

实测结论：
- `--allowedTools` 只能写 `"Bash(*)"` 一项。写成空格分隔或逗号分隔的多项（例如 `"Read Edit Write Grep Glob Bash(*)"`）时，所有 Bash 调用都会被拒绝。Read/Edit/Write/Grep/Glob 不需要列出（由 `acceptEdits` 和默认权限覆盖）。
- 禁止规则必须每条单独一个 `--disallowedTools` 参数。写成一个空格分隔的字符串时，规则完全不生效（实测 `unshare`、`mount`、`pip`、`git push --dry-run` 都被执行了）。
- 禁止规则只按命令前缀匹配，**不是安全边界**：`git -C <路径> push` 不被 `Bash(git push:*)` 拦截；`bash -c '…'` 包装可以绕过所有规则；`python3 -c` 调用子进程同理。因此 G10 实际依靠执行模型遵守，以及验收方的审计（ACCEPTANCE §4.2 的 Bash 记录检查，以及每张卡验收时核对远端分支没有变化）。
- 当 Bash 被拒时，`glm-5.3-flash` 曾两次直接编造命令输出（实测）。所以执行模型报告中的任何命令输出都不能作为验收依据，验收方必须自己重跑。

交接提示词必须包含：卡片 ID；PLAN.md 和 ACCEPTANCE.md 的路径；"只执行这一张卡、提交后停止"；G1–G10 全文；"JD 测试只能通过 `run_jd_tests.sh` 运行，禁止直接或间接（`bash -c`、`python3 -c` 等）调用 unshare/mount"；"不得 push，包括 `git -C … push`"；"命令被拒绝或失败时如实报告，不得编造输出"；退回时附上验收方的具体要求，并要求用 `git commit --amend` 重新交付。

同一张卡被退回两次后，不再第三次交给同一个模型，而是报告用户，由用户决定是否换更强的模型。

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
- 具体步骤（@d5a40ae）：
  1. 删除 `confflow/workflow/_retired_runtime.py`（65 行）。已核实 `confflow/` 中只有 `workflow/__init__.py` 的 lazy 表引用它。
  2. `confflow/workflow/__init__.py`：从 `_LAZY_EXPORTS`（L22-37）中删除目标为 `"._retired_runtime"` 的 9 项：L23-26 与 L32-36。删除 docstring 中描述 stub 的那一句（L8-9 的 "The retired runtime names resolve to fail-closed stubs (...)"）。
  3. `confflow/__init__.py`：从 `_LAZY_EXPORTS`（L76-87）中删除 L78 的 `"run_workflow": (".workflow", "run_workflow")`。
  4. `tests/v4/test_architecture_boundaries.py`：删除 `TestRuntimeIsolation::test_importing_workflow_package_stays_lazy`（L952-968）和 `TestRetiredRuntimeBoundary::test_retirement_stubs_fail_closed`（L1045-1052）；删除 `RETIRED_RUNTIME_MODULES` 上方注释中提到 stub 的最后一句（L289-291）。
- 禁止事项：G1–G9；不得改动 `confflow/cli.py` 中的 `run_workflow`（它是 `formal_v4_runner` 的别名，`cli.py:30`，与本卡无关）。
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

### J2.1 — 运行时不再依赖内置 fallback 快照

- ID：J2.1 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J1
- 目标：让 remote 模式在没有 V1/V2 fallback 的情况下运行：未选服务器时是"无合同"状态，非 V4 服务器报"不可用"，V4 编辑合同不再借用会话中的 fallback recipe catalog。
- 类型：`logic`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/service.py`
  - `src/jobdesk_v2/application/editor/contract/remote_v4.py`
  - `src/jobdesk_v2/application/editor/contract/models.py`（只允许新增"无合同"类型）
  - `src/jobdesk_v2/gui/app.py`
  - `src/jobdesk_v2/gui/new_run/presenter.py`
  - `tests/gui/test_new_run_remote.py`、`tests/gui/test_new_run_v4_contract.py`、`tests/gui/test_contract_integration.py`、`tests/application/test_service.py`（只允许修改/删除断言 fallback 行为的测试，逐条声明）
- 具体步骤（@9beeaf2）：
  1. `models.py`：新增 `NoContract`（实现 `ContractLike` 协议，`models.py:359-395`）：空 `EditorManifest`、空 `RecipeCatalog`、`is_authoritative=False`、`describe_source()` 返回"No server contract"、`contract_key=""`。
  2. `service.py:181-184`：`contract=None` 时使用 `NoContract()`，不再调用 `StableFallbackContractProvider().resolve()`。
  3. `app.py:557-560`：remote 模式起始服务为 `WorkflowEditorService()`（即 `NoContract`）。file mode 分支暂时保留，J2.4 删除。
  4. `remote_v4.py`：`V4ProducerContractProvider.resolve/_fetch`（L263-400）在服务器不支持 V4 或获取失败时，不再 `_degrade` 到 fallback，而是抛出 `ContractParseError`（使用现有诊断码，不新增诊断码），由 presenter 现有的 `_on_contract_failed`（`presenter.py:760-770`）进入 `unavailable` 状态。`_degrade` 与 `fallback`/`fallback_artifacts` 参数在 J2.4 删除。
  5. `presenter.py:703`：`v4_editor_contract(v4, recipe_catalog=…)` 改为传入空 `RecipeCatalog`。
     **Q8(c) 核实结果（写方案时，@9beeaf2）**：V4 会话的配方已经由卡片编辑器的 producer provider 提供，会话 catalog 在 V4 下不可见：
     - V4 contract 生效时，页面安装 `V4ProducerAuthoringProvider`，能力面板渲染的是它的 recipe catalog（`gui/new_run/page.py:242-262`）；
     - 该 provider 直接读 V4 contract 的 `recipe_catalog["recipes"]`（`application/cards/v4_provider.py:128`、`:139`）；
     - 会话的 recipe 选择器在 V4 下被禁用，`create_from_recipe` 被拒绝，由现有测试 `tests/gui/test_new_run_v4_contract.py::test_the_legacy_recipe_entry_is_not_active_under_v4`（L180-203）守护。
     所以传空 catalog 不会让用户失去可见配方。执行时先重新运行这个测试和 `git grep -n "recipe_catalog" -- src/jobdesk_v2/gui src/jobdesk_v2/application/cards`，确认结论仍然成立；不成立就停止（G9）。
  6. 预期行为变化（写入 `Behavior-Change`；(a)(b) 已获用户确认）：(a) 选服务器前编辑区没有字段和配方；(b) 非 V4 服务器的状态从 `restricted`（只读 fallback）变为 `unavailable`；(c) V4 会话中已禁用的旧配方选择器的数据源变为空（用户可见的 V4 配方不变）。
- 禁止事项：G1、G4–G9；不得删除任何 V1/V2 代码（J2.4 才删）；不得改变 V4 contract 的解析、contract_key 或提交路径。
- 验收命令：JD 标准验收；`contract_key` 与基线相同。另外由验收方逐条核对测试改动：每条被修改或删除的测试都必须断言的是上述 (a)(b)(c) 之一。
  期望：0 failed（J0 之后的基线全绿）。
- 提交信息模板：`feat(editor)!: run without the V1/V2 fallback snapshot` + 通用尾部（Behavior-Change 逐条列出）。

### J2.2 — 测试基础设施不再经过 V1/V2 解析

- ID：J2.2 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.1
- 目标：`tests/contract_fixtures.authoritative_contract()` 不再调用 `parse_contract_bytes`（V1/V2），改为直接构造一个使用相同 manifest/catalog 文档的 `ContractLike`。
- 类型：`test-only`
- 允许修改的文件：`tests/contract_fixtures.py`、`tests/application/conftest.py`、`tests/gui/conftest.py`
- 具体步骤（@9beeaf2）：
  1. `contract_fixtures.py:65-74`：`authoritative_contract()` 改为读 `tests/fixtures/contract/producer_v2.json` 中的 `editor_manifest` 和 `recipe_catalog` 两段，用 `editor_manifest_from_mapping` / `recipe_catalog_from_mapping`（两者都保留，V4 也在用）构造，再包进一个测试内定义的 `ContractLike` 数据类：`source="producer"`、`is_authoritative=True`，`contract_key` 的计算方式与 `VerifiedEditorContract.contract_key` 相同，用同样的输入。
  2. `application/conftest.py:90-94` 的 `fallback_contract` fixture 保留，到 J2.4 随 fallback 一起删除。
- 禁止事项：G1–G9；不得修改任何 `test_*.py`；被测代码不得变化。
- 验收命令：JD 标准验收。期望：collect 数不变；失败集合与 J2.1 相同；`git diff HEAD~1 --stat` 只有允许的 3 个文件。
- 提交信息模板：`test(contract): build the authoring fixture contract without the V1/V2 parser` + 通用尾部。

### J2.3 — 把 V4 仍需要的符号移出 `parse.py`

- ID：J2.3 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.2
- 目标：V4 代码只从新模块 `contract/errors.py` 取 `ContractParseError`、`decode_json_object`、`ARTIFACT_CONTRACT`，不再 import `parse.py` / `providers.py`。
- 类型：`move`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/contract/errors.py`（新增）
  - `src/jobdesk_v2/application/editor/contract/parse.py`
  - `src/jobdesk_v2/application/editor/contract/v4.py`
  - `src/jobdesk_v2/application/editor/contract/boundary.py`
  - `src/jobdesk_v2/application/editor/contract/remote_v4.py`
  - `src/jobdesk_v2/application/editor/contract/__init__.py`
- 具体步骤（@9beeaf2）：
  1. 核对 V4 侧依赖：`v4.py:49`（`ARTIFACT_CONTRACT, ContractParseError, decode_json_object`）、`boundary.py:33`（`ContractParseError`）、`remote_v4.py:38-44`（`parse` 与 `providers` 的 import）。
  2. 把 `ContractParseError`（`parse.py:114` 起）、`decode_json_object`（`parse.py:146` 起）及其私有依赖、`ARTIFACT_CONTRACT`（`parse.py:84`）原样剪切到 `errors.py`；`parse.py` 改为从 `errors.py` import 这些名字（`parse.py` 在 J2.4 删除）。
  3. 改 `v4.py`、`boundary.py`、`remote_v4.py` 的 import 路径；`contract/__init__.py` 中这些名字的导出来源改为 `errors`。
- 禁止事项：G1–G9；函数体逐字不变。
- 验收命令：JD 标准验收，另加 `git diff HEAD~1 -U0 | grep '^[-+]' | grep -v '^[-+]\s*\(from\|import\|#\|$\)'`，验收方核对删除行与新增行一一对应（移动）。期望：collect 不变；失败集合不变；contract_key 不变。
- 提交信息模板：`refactor(contract): move V4-shared parse errors into contract.errors` + 通用尾部。

### J2.4 — 删除 V1/V2 合同实现、file mode 和内置快照

- ID：J2.4 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.3
- 目标：删除 V1/V2 合同解析、provider、service、内置 manifest/catalog 快照、file mode，以及只守护它们的测试和 fixture。
- 类型：`delete`
- 允许修改的文件：
  - 删除：`src/jobdesk_v2/application/editor/contract/parse.py`、`providers.py`、`service.py`；`src/jobdesk_v2/infrastructure/editor/byte_source.py`；`tests/fixtures/contract/*.json` 中除 `producer_v2.json` 以外只被删除测试使用的文件；`tests/application/test_contract_parsing.py`、`tests/application/test_contract_providers.py`
  - 修改：`src/jobdesk_v2/application/editor/contract/__init__.py`、`models.py`、`remote_v4.py`；`src/jobdesk_v2/application/editor/__init__.py`、`manifest.py`（只删内置快照函数和数据）、`recipes.py`（只删内置快照函数和数据）、`service.py`（editor service 中对 fallback 的引用）、`recipes.py` 中 `EditorContractService` 的引用；`src/jobdesk_v2/infrastructure/editor/__init__.py`；`src/jobdesk_v2/gui/app.py`；`tests/contract_fixtures.py`、`tests/application/conftest.py`；`tests/application/test_architecture.py`、`tests/gui/test_architecture.py`、`tests/application/test_manifest.py`、`tests/application/test_recipes.py`、`tests/application/test_service.py`、`tests/application/test_remote.py`、`tests/gui/test_contract_integration.py`、`tests/application/test_confflow_v4_e2e.py`（只删除守护被删符号的测试或断言）；`README.md`（删除 `JOBDESK_V2_CONTRACT` 一节，README L284 附近）
- 具体步骤（@9beeaf2，结合 J2.1–J2.3 之后的状态）：
  1. 先生成删除清单写入提交信息：`git grep -n` 以下符号，确认生产代码引用都在本卡允许的文件中：`SUPPORTED_CONTRACT_SCHEMAS`、`StableFallbackContractProvider`、`LocalProducerContractProvider`、`EditorContractService`、`ContractRefresh`、`FallbackArtifacts`、`snapshot_contract`、`parse_contract_bytes`、`verified_contract_from_mapping`、`VerifiedEditorContract`、`ContractByteSource`、`EmptyContractByteSource`、`LocalFileByteSource`、`CONTRACT_ENV_VAR`、`build_contract_service`、`build_editor_service`、`editor_manifest_document`、`load_editor_manifest`、`recipe_catalog_document`、`load_recipe_catalog`。
  2. 删除上述符号的定义、导出和全部引用；`gui/app.py` 删除 `CONTRACT_ENV_VAR`（L62）、`build_contract_service`（L120-138）、`build_editor_service`（L497-507）、`file_mode` 分支（L355、L432-433，L183 的 `contract_override`）、`main()` 中的 else 分支（L561-562）。`remote_v4.py` 删除 `_degrade`（L403-416）、`fallback`/`fallback_artifacts` 参数（L241-255）、返回类型中的 `| VerifiedEditorContract`。
  3. `manifest.py` 删除内置快照（`editor_manifest_document` L966 起、`load_editor_manifest` L978 起，以及只被它们使用的常量和数据块，约 L540-1009；执行时用"只被快照函数引用"来界定，解析器部分 L1-539 不动）。`recipes.py` 同理（`recipe_catalog_document` L308、`load_recipe_catalog` L318 以及内置配方数据，含 L239-246 的 `conformer_search` 配方；`RecipeCatalog`/`recipe_catalog_from_mapping` 等模型与解析保留）。
  4. 删除只守护被删符号的测试：整个 `test_contract_parsing.py`（41 项）、`test_contract_providers.py`（27 项），以及其他列出文件中直接 import 被删符号的测试函数（逐条写入 Removed-Tests）。`conftest.py` 中的 `fallback_artifacts`、`fallback_contract` fixture 删除。
- 禁止事项：G1–G9；`EditorManifest`/`RecipeCatalog` 的解析器、`ContractLike`、`ContractDiagnostic`、`DiagnosticCode`、`ContractTarget`、`EditorCapabilities` 保留；不得改任何 V4 代码的行为。
- 验收命令：JD 标准验收，另加
  ```bash
  cd $JD && git grep -nE "SUPPORTED_CONTRACT_SCHEMAS|StableFallback|LocalProducerContractProvider|EditorContractService|FallbackArtifacts|snapshot_contract|parse_contract_bytes|VerifiedEditorContract|JOBDESK_V2_CONTRACT|LocalFileByteSource" -- src tests README.md && echo FAIL || echo ok
  ```
  期望：Removed-Tests 等于提交信息中逐条列出的节点；失败集合 ⊆ J2.3 的失败集合（不得新增）；contract_key 不变。
- 提交信息模板：`refactor(editor)!: delete the V1/V2 contract wire, file mode and bundled snapshot` + 通用尾部。

### J2.5 — （可选）离线编辑：缓存最近一次 V4 contract

- ID：J2.5 ／ 仓库：JobDesk-v2 ／ 前置：J2.4 ／ **只有用户明确要求离线编辑时才执行，默认不执行**
- 类型：`logic`。本方案不展开；需要时另写卡片。

---
## 7. Phase 3：边界瘦身（两仓成对提交）

顺序必须是：JD 先变得"容忍缺失"（J3.1、J3.2）→ CF 把 pin 移到该 JD 提交（C3.1）→ CF 删除 producer 字段（C3.2、C3.3）→ JD 重新 vendor fixture（J3.3）→ CF 再 re-pin（C3.4）。
每个 re-pin 后执行 `git -C $JDPIN checkout --detach <新 SHA>`，再跑 CF 的跨仓测试。

**contract 检查点**：Phase 3 中改变 contract/boundary 字节的卡（C3.2、C3.3）由执行模型新增 `docs/refactor/baseline/checkpoints/<CARD>/`，内容为 `contract_digests.py` 的输出加上 `contract.full.json` / `boundary.full.json`。后续卡的 golden 以最新的已验收检查点为准（`golden_check.py --checkpoint`）。TS1 与 engine 报告始终以 B0.1 为准。

### J3.1 — 删除 presenter 中读取 `capability_identity` 的死分支

- ID：J3.1 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J2.4
- 目标：决策 4 要求把 `presenter.py:858` 的 getattr bug 单独提交删除。该分支永远取不到值（`ResolvedContract` 是 `slots=True`，没有 `capability_identity` 属性，`contract/remote_v4.py:68-115`@9beeaf2），删除后行为不变。
- 类型：`delete`
- 允许修改的文件：`src/jobdesk_v2/gui/new_run/presenter.py`
- 具体步骤（@9beeaf2 L853-863）：在 `_capability_identity()` 中删除 L858-862（`capability = getattr(...)` 到 `return f"{key}|{digest}"`），函数只剩 `contract = …`、`key = …`、`return key`。函数名保持不变（G4），L2 再重构。
- 禁止事项：G1–G9。
- 验收命令：JD 标准验收。期望：Removed-Tests 0；失败集合不变。
- 提交信息模板：`refactor(new-run): drop the unreachable capability-identity read` + 通用尾部。

### J3.2 — JD 不再要求、不再保存 producer 的无读取方身份字段

- ID：J3.2 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：J3.1
- 目标：`capability_identity`（boundary 与 authoring 回答）、`semantic_identity`、`offered_identity` 不再被解析或保存；`prepared_run_manifest` / `validation_receipt` 两个可选 schema 摘要不再被记录。这样 producer 删除它们之后，JD 仍能接受 contract。
- 类型：`delete`
- 允许修改的文件：
  - `src/jobdesk_v2/application/editor/contract/boundary.py`
  - `src/jobdesk_v2/application/editor/contract/v4.py`
  - `src/jobdesk_v2/application/cards/binding_candidates.py`
  - `tests/application/test_p0_boundary.py`、`tests/application/test_p0_pr2b_named_binding.py`、`tests/application/test_confflow_v4_contract.py`（只删除守护被删字段的测试或断言，逐条声明）
- 具体步骤（@9beeaf2）：
  1. `boundary.py`：`parse_boundary_section` 删除 L236-241 两段解析；`BoundaryProtocol` 删除 `capability_identity`、`semantic_identity` 字段（L266-267）以及 `parse_boundary_section` 末尾构造 `BoundaryProtocol` 时的这两个实参；删除 `_OPTIONAL_SCHEMAS`（L95 起）及其循环（L174 起）；删除 `offered_identity_from_envelope`（L441-491）及其 `__all__` 条目；`_parse_identity`（L127-141）若再无调用方则删除。
  2. `v4.py`：删除 import `offered_identity_from_envelope`（L45）、字段 `offered_identity`（L140-141）、属性 `capability_identity` / `semantic_identity`（L173-183）、构造参数（L336）。
  3. `binding_candidates.py`：删除两个数据类的 `capability_identity` 字段（L118、L141）及其 `__post_init__` 行（L125、L150）、解析（L234-238、L252）、L360 的传参、L446-450 的必需检查和 L456 的输出键。`request_document_digest` 保留（`instantiate.py:308` 在比较它）。
- 禁止事项：G1–G9；`contract_digest`、`result_schema_sha256`、`request_document_digest` 和 compatibility 词汇的解析保留。
- 验收命令：JD 标准验收（绑定当前 `$CF`，此时 producer 仍发布这些字段，JD 必须照常接受）；`contract_key` 不变；`git grep -n "capability_identity\|semantic_identity\|offered_identity" -- src` 只允许命中 `remote/v4_validation.py`、`gui/new_run/presenter.py`（token，L2 处理）。期望：Removed-Tests 按声明。
- 提交信息模板：`refactor(contract): stop parsing producer identity members nobody reads` + 通用尾部。

### C3.1 — re-pin 到 J3.2

- ID：C3.1 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C1.4、J3.2
- 目标：CF 跨仓测试改为针对 J3.2 的 JD 提交。
- 类型：`ci`
- 允许修改的文件：`tests/v4/jobdesk_integration.py`（L52）、`.github/workflows/jobdesk-contract.yml`（`JOBDESK_COMPAT_SHA`）
- 具体步骤：把两处 40 位 SHA 改为 J3.2 的提交 SHA（`git -C $JD rev-parse HEAD`）；把 `jobdesk_integration.py:49-51` 注释中的出处说明改为"refactor/diet J3.2"。然后执行 `git -C $JDPIN checkout --detach <J3.2 SHA>`。
- 禁止事项：G1–G9。
- 验收命令：标准验收（`JOBDESK_V2_SRC=$JDPIN/src`）；`python3 -m pytest -q -o addopts="" -m cross_repo` 全部通过；`tests/test_release_workflow.py` 通过（它校验两处 pin 一致）。期望：Removed-Tests 0；golden 不变。
- 提交信息模板：`chore(cross-repo): pin JobDesk-v2 to <short sha> (J3.2)` + 通用尾部。

### C3.2 — producer 删除无读取方的边界成员与摘要

- ID：C3.2 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C3.1
- 目标：删除只写不读的边界 schema、身份和摘要字段。
- 类型：`delete`
- 允许修改的文件：
  - `confflow/producer/boundary.py`、`confflow/producer/authoring.py`、`confflow/producer/contract.py`、`confflow/producer/run_result.py`、`confflow/producer/__init__.py`
  - `scripts/generate_p0_boundary_fixtures.py`
  - `docs/internal/fixtures/p0_boundary/*`（重新生成；删除 `compatibility_cases.json`）
  - `tests/v4/test_p0_boundary.py`、`tests/v4/test_p0_pr1_authoring.py`、`tests/v4/test_v46_producer_contract.py`（只删除守护被删项的测试或断言，逐条声明）
  - `docs/refactor/baseline/checkpoints/C3.2/*`（新增）
- 具体步骤（@d5a40ae 行号；C1.4 后 `contract.py` 行号整体前移约 12 行，按符号定位）：
  1. `boundary.py` 删除：`compatibility_matrix`（L363-397）、`compare_identities`（L399-413）、`evaluate_compatibility`（L415-561）、`validation_receipt_schema`（L563-626）、`prepared_run_manifest_schema`（L628-709）、`identity_relationships`（L791-841）、`wire_examples`（L862-942）、`capability_identity`（L294-328）；常量 `PREPARED_RUN_MANIFEST_SCHEMA`（L77）、`VALIDATION_RECEIPT_SCHEMA`（L80）；以上在 `__all__`（L36-62）中的条目。`boundary_section`（L944-987）删除 `capability_identity` 成员和 `schemas` 中的 `prepared_run_manifest`、`validation_receipt`；`boundary_document`（L990-1034）删除 `capability_identity`、`compatibility.matrix`、`identity_relationships`、两个 schema、`wire_examples`。`boundary_section`/`boundary_document` 的签名中只为 `capability_identity` 服务的参数删除，调用方（`contract.py` 中 `envelope["boundary"] = boundary_section(…)`、`build_boundary_document`）同步删除对应实参。
  2. `authoring.py`：删除 `_capability_identity_cached` / `_capability_identity`（L144-156）以及回答中的 `"capability_identity"` 键（L253、L528、L820/L894 处的 `identity`），并删除 `authoring_protocol_schema()` 响应 schema 中对应的属性和 required 项。
  3. `contract.py`：删除 `_resources_section` 中 4 个 `"digest_axis"` 键（@d5a40ae L256、L265、L274、L283）；删除 result schema 中的 `"result_digest"` 属性（L443）和 docstring 中的提及（L427）。
  4. `run_result.py`：删除 L222-234 中计算 `result_digest` 的代码块（`identity_digests` 变量只为它服务）。
  5. `scripts/generate_p0_boundary_fixtures.py`：删除 `COMPATIBILITY_CASES_SCHEMA`（L43）、`build_compatibility_cases_fixture`（L254-283）、`emit("compatibility_cases.json", …)`（L324），以及 `evaluate_compatibility` 的 import。运行 `python3 scripts/generate_p0_boundary_fixtures.py` 重新生成 `docs/internal/fixtures/p0_boundary/`，再 `git rm` 其中的 `compatibility_cases.json`。
  6. 测试：删除 `tests/v4/test_p0_boundary.py` 中的 `TestCompatibility` 整类（L189-252）、`TestBoundaryIdentity::test_capability_identity_is_display_inert`（L132-157）、`TestPublishedSchemas::test_wire_examples_conform_to_schemas`（L254-277），并从 `TestFixtureFreshness` 的参数列表中删除 `"compatibility_cases.json"`（L313-317）；其余命中由 `git grep -n "capability_identity\|evaluate_compatibility\|compare_identities\|wire_examples\|identity_relationships\|validation_receipt\|prepared_run_manifest\|digest_axis\|result_digest" -- tests` 找出，逐条处理并声明。
  7. 删除 `semantic_identity`（Q7 已确认）：`boundary.py` 中的 `semantic_identity()`（L330-347）、`_SEMANTIC_CONTRACT_COMPONENTS`（L112 起，若再无使用方）、`boundary_section` / `boundary_document` 中的 `semantic_identity` 成员、`__all__` 条目，以及 `boundary_section` 中只为它服务的 `result_schema_sha256` 参数和调用方实参。
  8. 写检查点：`python3 $TOOLS/contract_digests.py --cf $CF --jd-src $JDPIN/src --out docs/refactor/baseline/checkpoints/C3.2/contract.json`。
- 禁止事项：G1–G9；不得改动 `compatibility_vocabulary`、`canonicalization_document`、`jcs_vectors`、`authoring_protocol_schema` 的请求部分、`diagnostic_envelope`；不得改 `BOUNDARY_PROTOCOL_VERSION`（Q6）。
- 验收命令：标准验收（golden 中 TS1 与 engine 报告对 B0.1，contract 部分对 checkpoint），另加
  ```bash
  python3 $TOOLS/json_paths_diff.py $BASE/contract.full.json  $CF/docs/refactor/baseline/checkpoints/C3.2/contract.full.json
  python3 $TOOLS/json_paths_diff.py $BASE/boundary.full.json  $CF/docs/refactor/baseline/checkpoints/C3.2/boundary.full.json
  ```
  期望：删除路径恰好是步骤 1–7 声明的成员；"修改"路径只能是派生摘要（`contract_digest`、`result_schema_sha256`、`boundary/schemas/*/sha256`、`capability`/`semantic` 之外的 digest 不得变化）；没有新增路径。跨仓测试（`$JDPIN` = J3.2）全部通过。
- 提交信息模板：`refactor(producer)!: delete unread boundary identities, schemas and digests` + 通用尾部（Behavior-Change：列出被删的 wire 成员）。提交说明正文必须包含这一句（Q6）：`服务器 ConfFlow 与 JD 必须同步升级：旧 JD 要求 boundary.capability_identity / semantic_identity，会拒绝本提交之后的 producer；BOUNDARY_PROTOCOL_VERSION 不变。`

### C3.3 — 在 contract 中标注 ConfGen 溯源字段

- ID：C3.3 ／ 仓库：ConfFlow ／ 分支：`refactor/diet` ／ 前置：C3.2
- 目标：决策 5：把 `certificate_digest`、`enumeration.digest`、`input_state_digest`、`input_certificate_digest` 显式标注为科学溯源字段。
- 类型：`logic`（只改 contract 声明，不改任何计算）
- 允许修改的文件：`confflow/producer/contract.py`、`tests/v4/test_confgen_v3_integration.py`、`docs/refactor/baseline/checkpoints/C3.3/*`
- 具体步骤（Q4 已确认）：在 `_confgen_section()`（@d5a40ae L364-410）中，紧接 `"result_provenance"`（L396）新增 `"report_provenance": ["/certificate/digest", "/enumeration/digest", "/input_state_digest", "/input_certificate_digest"]`（JSON pointer 相对于 `ensemble_report`；字段位置已核实：`execution/confgen_executor.py:632-654`@d5a40ae）。在 `TestProducerWiring::test_contract_confgen_section_tracks_registries`（L1253 起）中加一条对应断言（Added-Tests 0，断言 +1）。写检查点 `checkpoints/C3.3/`。
- 禁止事项：G1–G9；不得改 `confgen_executor.py`、`science/`；不得改变任何报告内容。
- 验收命令：标准验收；`json_paths_diff.py` 对比 C3.2 检查点：只新增 `/confgen/report_provenance`（及派生摘要变化）。engine 报告与 TS1 不变。
- 提交信息模板：`feat(producer): declare confgen report provenance fields` + 通用尾部。

### J3.3 — JD 重新 vendor P0 boundary fixture

- ID：J3.3 ／ 仓库：JobDesk-v2 ／ 分支：`refactor/diet` ／ 前置：C3.3
- 目标：JD 的 `tests/fixtures/p0_boundary/` 与 CF C3.3 生成的 fixture 一致。
- 类型：`test-only`
- 允许修改的文件：`tests/fixtures/p0_boundary/*`、`scripts/sync_p0_boundary_fixtures.py`（只改 `FIXTURE_NAMES`）、`tests/application/test_p0_boundary.py`
- 具体步骤：
  1. `scripts/sync_p0_boundary_fixtures.py`：从 `FIXTURE_NAMES` 中删除 `"compatibility_cases.json"`。
  2. `python3 scripts/sync_p0_boundary_fixtures.py --source $CF/docs/internal/fixtures/p0_boundary`，然后核对 `PROVENANCE.json` 的 `source_commit` = CF C3.3 的提交 SHA；`git rm tests/fixtures/p0_boundary/compatibility_cases.json`。
  3. `test_p0_boundary.py`：删除 `TestCompatibilityParity::test_vocabulary_matches_producer_fixture`（它读取已删除的 fixture）以及整个空类；从文件头的 fixture 名单（L58 附近）中删除 `compatibility_cases.json`。
- 禁止事项：G1–G9；不得手改 fixture 内容（只能由同步脚本写入）。
- 验收命令：JD 标准验收（绑定 `$CF` = C3.3）；`TestFixtureProvenance` 通过。期望：Removed-Tests 1；失败集合不变；`contract_key` 与 C3.3 检查点一致。
- 提交信息模板：`test(fixtures): re-vendor P0 boundary fixtures from ConfFlow <short sha>` + 通用尾部。

### C3.4 — re-pin 到 J3.3

- 同 C3.1，SHA 换成 J3.3 的提交。前置：J3.3。验收：跨仓测试全部通过，`TestLiveProducerParity`（JD 侧）在 §2.3 绑定下通过。

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

### IS.0 — 把 Phase 1–3 后的 main 合入 IS 分支

- ID：IS.0 ／ 仓库：ConfFlow（以及 JD：IS.0-JD）／ 前置：C3.4；**用户先把 `refactor/diet` 合入 `main`**（该操作由用户执行）
- 类型：`merge`
- 步骤：`git -C $CFIS merge --no-ff main`。冲突一律保留 main 的删除；IS 新增的代码如果引用了被删符号（例如 `capability_identity`），按 main 的删除去掉引用，并在合并提交信息中逐条列出。JD 同理：`git -C $JDIS merge --no-ff master`。
- 验收：两仓标准验收；engine 报告与 TS1 必须等于 B0.1（IS 分支新增测试产生的新报告记录为新的报告检查点 `checkpoints/IS.0/engine_reports/`，由验收方核对：已有报告不得变化）。

### IS.2 — intent 编译器始终输出 `schema_version: 3`，paths 走 v3

- ID：IS.2 ／ 仓库：ConfFlow ／ 分支：IS ／ 前置：IS.0，且 IS.1 中没有 `NOT_EQUIVALENT`
- 目标：`_wire_confgen` 对带 paths 的 legacy native 生成 typed v3 块，而不是透传。
- 类型：`logic`
- 允许修改的文件：`confflow/producer/intent.py`、`tests/v4/` 下与 intent 编译相关的测试文件（逐条声明）
- 具体步骤（@f87da58 `intent.py:917-951`）：
  1. intent 的 confgen 步骤如果 `native` 不是 v3，且只包含 `paths` / `angle_step` / `bond_scale` / `strict_path_bond_check`，就按 IS.1 第 2 步的映射规则生成 `{"schema_version": 3, "index_base": 1, "paths": […], …}`；L948-950 的步骤级 `paths` / `strict_path_bond_check` 同样并入 v3 块。映射规则必须与 IS.1 的工具逐字一致（从同一函数导入或复制并注明来源）。
  2. bare 声明补 `step: 120`（Q2b）。
  3. 末端原子端点（Q2a、Q2c 方向 1）：intent 编译器不做检查（编译时没有结构），也不得自动把端点换成邻居；路径原样编译成 v3，由运行时的 v3 拒绝承担，诊断里的具体键由 IS.2b 负责。
- 禁止事项：G1–G9；不得修改 `confgen_executor.py`、`science/`。
- 验收命令：标准验收（在 `$CFIS` 上）；IS.1 中每个 `EQUIVALENT` 用例再通过 intent 编译 → 执行，输出必须与 IS.1 中的 v3 输出逐项相同；每个 `LEGACY_DEGENERATE` 用例必须失败，且不得被自动裁剪后执行。
- 预期行为变化：intent 中的 legacy paths 不再以 legacy native 执行，而以 typed v3 执行；bare 声明显式变为 `step: 120`；末端原子端点的路径在运行时被 v3 拒绝（诊断见 IS.2b）。
- 提交信息模板：`feat(producer)!: compile ConfGen intent paths into typed v3 scopes` + 通用尾部。

### IS.2b — v3 拒绝末端原子端点时，诊断指明具体的键

- ID：IS.2b ／ 仓库：ConfFlow ／ 分支：IS ／ 前置：IS.2
- 目标：Q2c 方向 1。v3 因"端点没有可测二面角框架"拒绝路径时，错误信息指明是哪条路径声明的哪个键，例如 `confgen.paths[1].end (atom 7) is a terminal atom with no measurable dihedral frame`。
- 类型：`logic`（只改诊断文本和为其传递来源键所需的参数，不改任何判定）
- 允许修改的文件：`confflow/science/confgen/torsion/stage.py`、`confflow/science/confgen/torsion/paths.py`、`confflow/science/confgen/planner.py`（只允许把路径来源键传到 axis 上），以及断言该错误文本的测试（逐条声明）
- 具体步骤（@f87da58，IS.0 合并后按符号定位）：
  1. 找到抛出点：`torsion/stage.py` 中"has no measurable dihedral frame (terminal pair)"（L96-108）；找到 typed `paths` 解析成 torsion axis 的位置（`torsion/paths.py` 的 `resolve_paths` / `parse_path_declarations`，以及 `planner.py` 中对 `paths` 的处理，L494-520）。
  2. 解析时把每条路径的来源键（`paths[<j>]` 与具体端点字段 `start`/`end`，以及 1-based 原子号）记录到由该路径产生的 axis 上（只增加一个只读的来源描述字段，不参与任何计算、排序、去重或 state key）。
  3. 拒绝时，如果 axis 带有来源描述，就把它写进错误信息；没有来源描述的 axis（`torsions` 声明产生的）错误信息保持原样。
- 禁止事项：G1–G10；不得改变哪些输入被接受或拒绝；来源字段不得进入 state key、报告的 `enumeration`/`certificate` 或任何 digest。
- 验收：标准验收（在 `$CFIS`）。**TS1 与 engine 报告必须逐字节不变**（来源字段一旦进入报告就是违规）；IS.1 中每个 `LEGACY_DEGENERATE` 用例经 intent 编译后执行，失败信息必须包含对应的 `paths[<j>].<start|end>` 键。
- 预期行为变化：只有错误信息文本变化。
- 提交信息模板：`fix(confgen): name the offending path key when a terminal endpoint is refused` + 通用尾部。

### IS.3 — `_wire_confgen` 拒绝非 v3 native

- ID：IS.3 ／ 仓库：ConfFlow ／ 分支：IS ／ 前置：IS.2
- 目标：IS.2 映射之后仍然不是 v3 的 native（例如 `chains`），直接 `_fail`，不再透传。
- 类型：`logic`
- 允许修改的文件：`confflow/producer/intent.py`、相关测试（逐条声明）
- 具体步骤：`intent.py:941-951` 的透传分支改为 `raise _fail("step …: ConfGen intent requires a typed schema_version 3 scope", step_id=…)`；`_passthrough_legacy`（L269-290，legacy V4 文档的透传）不在本卡范围，保持不变。
- 验收命令：标准验收。期望：只有断言"非 v3 native 透传"的测试被修改，逐条声明。

### IS.4 — JD 输入简化分支改用 typed v3（Q1 已确认）

- ID：IS.4 ／ 仓库：JobDesk-v2 ／ 分支：`implementation/input-simplification`（已完成 IS.0-JD 合并）／ 前置：IS.3
- 目标：JD 不再写出 legacy `native.paths`；新的 ConfGen 步骤和路径编辑一律使用 typed v3。
- 类型：`logic`
- 允许修改的文件：
  - `src/jobdesk_v2/gui/new_run/confgen_v3_form.py`
  - `src/jobdesk_v2/gui/new_run/page.py`
  - `src/jobdesk_v2/application/intent/model.py`
  - `tests/application/test_intent_model.py`、`tests/gui/test_intent_live_gui.py`、`tests/gui/test_confgen_v3_editor.py`、`tests/application/test_confgen_v3_jobdesk.py`（只修改断言 legacy 默认值或 legacy 表示的测试，逐条声明）
- 具体步骤（@92d48f1 行号；IS.0-JD 合并后按符号定位）：
  1. `confgen_v3_form.py`：路径表示选择器（L548-555）删除 `("legacy", "legacy native.paths (frame-free, default)")` 选项，只保留 typed，默认值 `"typed"`；`_paths_mode` 为 `"legacy"` 的分支（L783-803 读取 `native.paths`、L897、L962-975 写回 native）中，写回 legacy native 的分支删除；读取已有文档中 legacy `native.paths` 的分支保留为只读展示，并显示"legacy paths 需转换为 typed v3"的提示（提示文本写入提交信息）。
  2. `page.py`：新 confgen 步骤的初始块（L486-491）从 `{"confgen": {"native": {}}}` 改为 `{"confgen": {"schema_version": 3}}`。
  3. `intent/model.py`：`_intent_confgen_step`（L919-977）的 `has_legacy` 分支改为 `raise IntentNotRepresentable(...)`，消息指明具体键 `confgen.native`，并说明需改用 typed v3；模块 docstring（L84、L663-666）中关于 producer 包装 legacy paths 的描述同步删除。
  4. 不改：`confgen.native` 字段行（`confgen_v3_form.py:661`，J4.1 处理）；`intent/preview.py` 的 `build_preview_native`（Q10，保留）；`intent/sampling.py` 对已有文档中 `native.paths` 的读取。
- 预期行为变化：(a) 新建 ConfGen 步骤是 typed v3；(b) 表单不能再写出 legacy paths；(c) 含 legacy native 的已有文档不能再走 intent 编译，会得到指明 `confgen.native` 的错误。
- 禁止事项：G1–G9。
- 验收命令：JD 标准验收（§2.3 绑定 `$CFIS` 的 IS.3 提交）。期望：失败集合为空；被修改的测试只断言 (a)(b)(c)。
- 提交信息模板：`feat(new-run)!: author ConfGen paths as typed v3 only` + 通用尾部。

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
- 验收：标准验收。期望：collect 不变；使用默认值的 6 个文件（`test_repair_capabilities.py`、`test_p0_pr1_authoring.py`、`test_compiler.py`、`test_native_definition_validation.py`、`test_execution_critical_validation.py`、`test_digest_axes.py`）全部通过，且没有任何断言被修改。如果有测试只能靠改断言才能通过，停止并升级（说明它依赖 legacy 行为）。

### J4.1 — JD 不再使用 `confgen.native` 字段（Q9 已确认）

- ID：J4.1 ／ 仓库：JobDesk-v2 ／ 前置：C4.1
- 类型：`delete`
- 范围：IS.4 合入 JD master 之后，`git grep -n "confgen.native\|CONFGEN_NATIVE_FIELD" -- src tests` 的全部命中。如果没有命中，本卡记为"无改动"，写入 LOG 并跳过。

### C4.2 — re-pin 到 J4.1（J4.1 无改动时跳过）

- 同 C3.1。

### C4.3 — 删除 legacy native 执行路径与 schema

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

### C5.2 — 删除 `calc/`、`confts.py`、`workflow/composition.py`、`blocks/viz/`

- ID：C5.2 ／ 仓库：ConfFlow ／ 前置：D11、C4.4
- 类型：`delete`
- 允许修改的文件：
  - 删除：`confflow/calc/**`、`confflow/confts.py`、`confflow/workflow/composition.py`、`confflow/blocks/viz/**`
  - 修改：`confflow/__init__.py`（删除 L79-81 的 `CalcStepRunner`/`CalcStepRequest`/`CalcStepResult` lazy export）、`confflow/blocks/refine/result.py`（它 import calc；如果整个模块只为 calc 服务则删除）、`pyproject.toml`（删除 L79 `confts` 入口）、`scripts/architecture_metrics.py`、`scripts/v4_arch_scan.py`（只删除对被删模块的列举）、`tests/v4/test_architecture_boundaries.py`（把被删模块加入 `REMOVED_LEGACY_MODULES`）
  - 删除的测试：第 1 步得到的测试文件清单（写方案时按 import 核对，约 30 个文件，例如 `tests/test_calc*.py`、`tests/test_policies*.py`、`tests/test_rescue*.py`、`tests/test_confts_*.py`、`tests/test_viz_report.py`、`tests/test_ts_acceptance.py`、`tests/test_task_acceptance.py`）
- 步骤：
  1. 执行前：`python3 $TOOLS/reachability.py --cf $CF` 必须把以上模块列为不可达或只从 legacy 入口可达；用 `git grep -ln "confflow.calc\|from \.\.calc\|from \.calc\|confflow.confts\|blocks.viz\|workflow.composition" -- tests` 得到测试清单。**只引用被删模块的测试文件整个删除；同时引用保留模块的文件（例如 `tests/test_core.py`、`tests/test_dependency_boundaries.py`、`tests/test_small_adapters_extra.py`、`tests/v4/test_v42_adapters.py`、`tests/test_worker_supervision.py`）只删除引用被删模块的测试函数**，逐条列入 Removed-Tests。
  2. 删除模块和导出。
- 禁止事项：G1–G9；不得删除 `blocks/refine`（C5.3）、`blocks/confgen`（C5.4）、`core/`、`shared/`（C5.5）。
- 验收命令：标准验收；`python3 -c "import confflow; confflow.CalcStepRunner"` 必须抛 `AttributeError`；`git grep -n "confflow.calc\|confflow.confts" -- confflow scripts pyproject.toml` 无命中。golden 不变。
- 提交信息模板：`refactor!: delete the legacy calc tooling and the confts CLI` + 通用尾部。

### C5.3 — 删除 `blocks/refine/`（前置：用户确认 C5.1）

- ID：C5.3 ／ 前置：C5.2、用户确认 REFINE_GAP.md
- 类型：`delete`
- 允许修改的文件：`confflow/blocks/refine/**`（删除）、`pyproject.toml`（L81 `confrefine`）、`tests/v4/test_architecture_boundaries.py`、引用 `blocks.refine` 的测试（`tests/test_refine*.py`、`tests/test_processor_hotspots.py`、`tests/test_rmsd_engine_hotspots.py`、`tests/test_bonding_consistency.py` 等，按 grep 结果逐条声明）
- 验收：标准验收；golden 不变。

### C5.4 — 删除 `blocks/confgen/`（Q11）

- ID：C5.4 ／ 前置：C5.3
- 类型：`delete`
- 允许修改的文件：`confflow/blocks/confgen/**`、`confflow/blocks/__init__.py`（blocks 空了就删除整个包）、`pyproject.toml`（L80 `confgen`）、引用的测试（`tests/test_confgen.py`、`tests/test_collision*.py`、`tests/test_confgen_validator.py`、`tests/test_mapping.py`、`tests/test_confgen_refine_fallbacks.py`、`tests/test_optional_numba.py`、`tests/v4/test_confgen_scientific_regressions.py` 等，按 grep 逐条声明）
- 验收：标准验收。写方案时核实：B0.1 的 engine 报告捕获中没有来自 `test_confgen_scientific_regressions.py` 的报告，删除它不影响 golden。

### C5.5 — 删除 `core/` 和 `shared/` 中只被 legacy 使用的部分

- ID：C5.5 ／ 前置：C5.4
- 类型：`delete`
- 允许修改的文件：`confflow/core/*.py`、`confflow/core/__init__.py`、`confflow/shared/config_coercion.py`、`tests/v4/test_architecture_boundaries.py`、`scripts/v4_arch_scan.py`、只引用被删模块的测试
- 步骤：重新运行 `reachability.py`。写方案时（C5.2–C5.4 之前）的候选是 `core/validation.py`（333 行）、`core/chem_validation.py`、`core/cli_base.py`、`core/constants.py`、`core/keyword_rewrite.py`、`core/models.py`、`core/pairs.py`、`shared/config_coercion.py`，**以重新运行的结果为准**，只删除不可达的模块。`core/__init__.py` 的 `_LAZY_EXPORTS`（L23-50）中指向被删模块的条目一并删除；`TestFacadeLazyIsolation::test_core_public_surface_still_importable`（`test_architecture_boundaries.py` 约 L1610-1625）中 import 被删名字的断言删除，其余断言保留。
- 验收：标准验收；golden 不变。

### C5.6 — `cli.py` 进程识别不再包含退役 CLI 名

- ID：C5.6 ／ 前置：C5.5 ／ 类型：`delete`
- 允许修改的文件：`confflow/cli.py`、对应测试
- 步骤：`cli.py:418`（@d5a40ae）的集合从 `{"confflow", "confts", "confgen", "confrefine", "confcalc"}` 改为 `{"confflow"}`；删除 L411-416 关于 `confcalc` 的注释。
- 验收：标准验收；只允许修改断言这些名字被识别的测试。

### C5.7 — 文档（Q5 已确认）

- ID：C5.7 ／ 前置：C5.6 ／ 类型：`doc`
- 允许修改的文件：`README.md`、`docs/KEYWORD_REFERENCE.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT.md`、`docs/COMMAND_REFERENCE.md`、`docs/TESTING.md`、`docs/USAGE.md`、`docs/architecture/WORKFLOW_V4.md`
- 步骤：删除描述 `confts`/`confgen`/`confrefine` CLI、`confflow.calc`、`blocks/*` 的章节和句子。`KEYWORD_REFERENCE.md` 如果全文都是 confts 关键字，整个删除，并删除其他文档中指向它的链接。历史文档不动。
- 验收：`git grep -nE "\bconfts\b|\bconfrefine\b|confflow\.calc|blocks/refine|CalcStepRunner" -- README.md docs ':!docs/archive' ':!docs/internal' ':!docs/rfc' ':!docs/refactor'` 无命中；`tests/test_release_workflow.py` 中检查 README 的测试通过。

### C5.8 — 架构测试收尾

- ID：C5.8 ／ 前置：C5.7 ／ 类型：`delete`
- 允许修改的文件：`tests/v4/test_architecture_boundaries.py`、`scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py`
- 步骤：只删除如今恒为真的守护项：`FORBIDDEN_IMPORT_PREFIXES`（L42-62）中对应包已不存在的条目，以及只针对已删包的测试函数。`REMOVED_LEGACY_MODULES` 与"模块必须不存在"的测试保留。
- 验收：标准验收；Removed-Tests 逐条声明。

---

## 11. 逻辑尾部

### L1 — `_allowed_pairings_by_kind` 改为读常量

- ID：L1 ／ 仓库：ConfFlow ／ 前置：C5.8（技术上只依赖 C1.4）
- 类型：`logic`（输出必须字节相同）
- 允许修改的文件：`confflow/producer/contract.py`、`confflow/execution/contracts.py`（只允许把 `_STRUCTURE_PORT_PAIRINGS` / `_VALUE_PORT_PAIRINGS` 改为公开名或增加只读访问函数）
- 步骤（@d5a40ae）：`contract.py:180-202` 的"构造 `PortSpec` 试探"改为直接按 `execution/contracts.py:110-117` 的规则从这两个常量生成表：`structure` kind 用 `_STRUCTURE_PORT_PAIRINGS`，其他 kind 用 `_VALUE_PORT_PAIRINGS`，列表顺序与 `Pairing` 枚举顺序一致。
- 验收：标准验收；**contract 与 boundary 的 sha256 与最新检查点逐字节相同**。

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
| J2.1 | 高 | GUI 行为变化（Q8）；已知失败集合（Q0）可能变化；presenter 状态机进入 `unavailable` 的路径与原 `restricted` 不同。 | 单提交 `git revert`；J2.2–J2.4 依赖它，须按逆序一并回滚。 |
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
3. **Q2c 方向 2**：在 JD 的路径预览阶段（有结构）提前报出末端原子端点，指明具体的键。本轮由运行时拒绝承担（IS.2b）。

