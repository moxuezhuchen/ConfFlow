# 验收协议

> 适用于 `PLAN.md` 中的每一张卡。执行模型每完成一张卡（一个提交）就交给验收方。验收方只读、只运行，不修改执行分支。
> 验收方的工作目录：`/tmp/refactor-acc/<CARD>/`。结论追加到 `docs/refactor/LOG.md`（在 `docs/refactor-plan` 工作树 `/opt/cf-worktrees/refactor-plan` 中维护，不进入执行分支）。

---

## 1. 输入

执行模型交付时必须提供：

1. 卡片 ID、仓库、提交 SHA（`git rev-parse HEAD`）和父提交 SHA。
2. 提交信息，按 `PLAN.md` §2.7 的格式，包含 `Removed-Tests`、`Added-Tests`、`Behavior-Change`、`Verification`。
3. 卡片要求的额外产物（检查点目录、报告文件）。

缺任何一项，直接**退回**。

验收方先准备：

```bash
source /opt/cf-worktrees/exec-cf/docs/refactor/tools/env.sh
CARD=<ID>; ACC=/tmp/refactor-acc/$CARD; mkdir -p $ACC
REPO=<$CF 或 $JD 或 $CFIS 或 $JDIS>
git -C $REPO log -1 --format='%H %P %s' > $ACC/commit.txt
git -C $REPO status --porcelain > $ACC/status.txt    # 必须为空
```

---

## 2. diff 检查（白名单与类型）

```bash
python3 $TOOLS/diff_guard.py --repo $REPO --base HEAD~1 --head HEAD --type <卡片类型> \
        --whitelist $ACC/whitelist.txt > $ACC/diff_guard.txt
```

`whitelist.txt` 由验收方从卡片的"允许修改的文件"逐字抄写，每行一个路径或 glob（`**` 只允许出现在卡片原文写了 `**` 的地方）。

`diff_guard.py` 检查以下规则，验收方再人工复核它标出的每一处：

### 2.1 所有类型

- R1 改动的文件 ⊆ 白名单。超出白名单的任何文件（包括格式化工具顺手改的文件）→ **退回**。
- R2 提交恰好一个，父提交是同一仓库同一分支上上一张已验收卡的提交（`merge` 卡除外）。
- R3 没有改动 `docs/refactor/baseline/` 的已有文件；只有卡片明确要求的检查点目录可以新增。
- R4 没有改动 pytest 配置：`pyproject.toml` 的 `[tool.pytest.ini_options]`、`conftest.py` 中的 `collect_ignore` / `pytest_collection_modifyitems` / `addopts` / `markers` / `filterwarnings`、CI 中的 `--deselect` / `-k` / `--ignore`。卡片白名单包含该文件且步骤明确要求的除外。

### 2.2 按类型

| 类型 | 规则 |
|---|---|
| `delete` | 新增行（`git diff -U0` 中的 `+` 行，去掉首尾空白后）只能是：(a) import 行；(b) 同一文件中某个被删除行的原样副本（反缩进、换行重排）；(c) 空行、注释、docstring 文本；(d) 卡片明确列出的清单项（如 `REMOVED_LEGACY_MODULES` 新条目、re-pin 的 SHA）。新增 `def`、`class`、`if`、`elif`、`else`、`try`、`except`、`raise`、`return <新表达式>`、新赋值，一律视为新增逻辑 → **退回**，除非卡片步骤逐字要求（例如 C4.3 第 2 步的唯一出口）。 |
| `move` | 去掉 import 行和 `__all__` 行后，被删除行与新增行（去空白）的多重集合必须相同。 |
| `test-only` | 只改 `tests/`（及卡片列出的 fixture/脚本）；`src/`、`confflow/` 零改动。 |
| `ci` | 只改 workflow 文件、pin 常量，以及卡片列出的一致性测试。 |
| `baseline` / `doc` | 只新增或修改 `docs/` 下卡片列出的文件。 |
| `logic` | 提交信息的 `Behavior-Change` 必须与卡片"预期行为变化"一一对应：多出的行为变化 → **升级**；少了 → **退回**。 |
| `merge` | 冲突解决只能朝卡片规定的方向；合并提交信息必须逐条列出因删除而去掉的引用。 |

---

## 3. 测试是否被削弱

对执行分支上**保留下来**的每个测试文件（`git diff --name-only --diff-filter=M HEAD~1 -- tests`），逐项检查：

- W1 新增了 `pytest.mark.skip`、`skipif`、`xfail`、`pytest.skip(`、`pytest.xfail(`、`importorskip`、`@pytest.mark.flaky` → **退回**。
  `git diff -U0 HEAD~1 -- tests | grep -nE '^\+.*(mark\.skip|skipif|xfail|pytest\.skip\(|importorskip|flaky)'`
- W2 删除了 `assert` 行，但它所在的测试函数没有被整个删除，而且卡片也没有声明删除这条断言 → **退回**。
  `git diff -U0 HEAD~1 -- tests | grep -nE '^-\s*assert\b'`，与 Removed-Tests 和卡片步骤逐条比对。
- W3 修改了断言（同一 hunk 中删 `assert` 再加 `assert`）：逐条人工判断是否放宽。以下一律视为放宽 → **退回**：`==` 改为 `in`/`>=`/`<=`/`!=`；`pytest.approx` 的容差变大或新增 approx；预期集合或列表的元素变少；`pytest.raises(X)` 改为更宽的基类或去掉 `match=`；`assert x` 改为 `assert x is not None` 这类更弱的形式。
- W4 删除的测试节点必须全部在 `Removed-Tests` 中声明，并且属于卡片允许删除的文件或函数：
  `python3 $TOOLS/test_inventory.py diff --prev <上一张卡的 collect> --cur $ACC/collect.txt --declared N --allowed-files <...>`
- W5 新增测试节点只允许出现在 `logic`、`test-only`、`baseline` 卡，并且必须在 `Added-Tests` 中声明。
- W6 被删除的测试如果守护的不是本卡删除的代码（看它 import 或调用的符号），→ **退回**。

---

## 4. 运行验收命令并与基线比对

### 4.1 静态检查

CF：`cd $CF && ruff check . && mypy confflow && git diff --name-only HEAD~1 -- '*.py' | xargs -r ls 2>/dev/null | xargs -r black --check`
JD：`cd $JD && ruff check src tests && ruff format --check src tests && mypy`

任何失败 → **退回**。

### 4.2 测试

按 `PLAN.md` §2.2 / §2.3 运行全量测试，输出 junit，再转成 `{nodeid: outcome}`：

```bash
python3 $TOOLS/test_inventory.py collect --repo <cf|jd> --out $ACC/collect.txt
python3 $TOOLS/test_inventory.py run     --repo <cf|jd> --out $ACC/outcomes.json
```

规则（"上一检查点"指上一张已验收的同仓卡；第一张卡对应 `baseline/inventory/`）：

- T1 collect 数量变化 = `Removed-Tests` − `Added-Tests`，且 W4、W5 成立。否则 → **退回**。
- T2 失败集合（`failed` + `error`）必须等于上一检查点的失败集合减去本卡删除的节点。
  - 出现新的失败 → **退回**。
  - 已知失败（例如 J0a 之后预演得到的 8 项；B0.1 之后两仓基线都应为 0 失败）消失或变化，而卡片没有预言 → **升级**（说明行为发生了预期之外的变化）。
- T3 `skipped` 集合不得增加（随本卡删除的节点除外）。增加 → **退回**（属于 W1 的变体，例如条件 skip 被触发）。
- T4 CF 跨仓测试（`-m cross_repo`）必须实际运行，不能是 skip：`$JDPIN` 的 HEAD 必须等于 `tests/v4/jobdesk_integration.py` 的 `EXPECTED_JOBDESK_SHA`。如果发生 skip，先修正环境再验收，不算执行模型的责任。

### 4.3 golden

```bash
python3 $TOOLS/golden_check.py --base $BASE --cf <对应 CF 提交的工作树> --jd-src <对应 JD 的 src> \
        [--checkpoint <最新已验收检查点>/contract.json] [--removed-nodes $ACC/removed_nodes.txt] > $ACC/golden.txt
```

- G-TS1 `ts1/{default,rigid,flexible}.json` 与 `baseline/ts1/` 逐字节相同。**任何差异 → 升级**（科学行为），不论卡片类型、不论执行模型如何解释。
- G-ENG `engine_reports/` 与 B0.1（IS.0/IS.5 之后：与对应检查点）逐文件逐字节相同。缺失的文件只有在其 nodeid 属于本卡删除的测试时才允许。**任何内容差异 → 升级**。新增文件 → 只有 IS.0/IS.5 卡允许，并记录为新检查点。
- G-CON contract 与 boundary：
  - Phase 0/1/2、J1、L1：`contract_cli_sha256`、`boundary_cli_sha256`、`contract_bytes_sha256`、`contract_digest` 与基线（或最新检查点）相同，JD `contract_key` 相同。不同 → **退回**。
  - Phase 3、C4.3：卡片必须新增检查点。用 `json_paths_diff.py` 对比上一检查点，删除/新增的 JSON 路径必须恰好等于卡片声明的集合，"修改"路径只能是派生摘要。多删、少删或出现未声明的修改 → **退回**；出现卡片未声明的新增路径 → **升级**。
- G-ATOM atom ordering：TS1 与 engine 报告中的 `leaves[].atoms` 包含在 G-TS1/G-ENG 的逐字节比较中；单独列出只为强调：顺序变化同样 → **升级**。

### 4.4 卡片自带的额外验收命令

逐条运行卡片"验收命令"中的额外命令，结果与卡片"期望"逐条比对。

---

## 5. 结论

结论只有三种。判定顺序：先看是否需要升级，再看是否退回，最后才是通过。

| 结论 | 触发条件（任一即成立） |
|---|---|
| **升级给用户** | G-TS1 / G-ENG / G-ATOM 有任何差异；ring、torsion、stereo、coordination、atom ordering 的行为有任何变化（包括测试结果的变化暗示了这些变化）；卡片标注了 BLOCKED-Qn 而用户尚未答复；`logic` 卡出现卡片未列出的行为变化；已知失败意外消失；contract 出现未声明的新增路径；IS.1 有 `NOT_EQUIVALENT`；C5.1（固定升级）；执行模型报告 G9（卡片与代码不符）；需要改变已定决策才能继续。 |
| **退回** | R1–R4、类型规则、W1–W6、T1–T3、G-CON（声明不符）、静态检查或卡片额外验收命令中任何一项不满足；提交信息缺项；工作树不干净。退回时必须写明：哪条规则、证据（命令 + 输出摘录或 文件:行号）、要求执行模型做什么。执行模型修正时用 `git commit --amend` 重新交付同一张卡（仍是一个提交）。 |
| **通过** | 以上都不成立。通过后，该提交成为下一张卡的"上一检查点"。 |

"升级"时验收方停止整个流水线中依赖该卡的后续卡片，把问题、证据和可选项写给用户，等用户答复后再继续。不得以"预期变化"放行科学行为差异。

---

## 6. LOG.md 记录格式

每次验收（包括退回后的重新验收）在 `docs/refactor/LOG.md` 末尾追加一条：

```
## <YYYY-MM-DD HH:MM> <CARD> — 通过 | 退回 | 升级

- 仓库/分支/提交：<repo> <branch> <sha>（父 <sha>）
- 类型：<type>；白名单检查：ok | <违规文件>
- 静态检查：ok | <失败摘要>
- 测试：collect <prev> -> <cur>（声明 -N +M）；结果 <passed>/<failed>/<skipped>；失败集合：与上一检查点相同 | <差异>
- golden：TS1 ok | diff；engine 报告 ok | diff <文件>；contract ok | checkpoint <路径> | diff
- 测试削弱检查：ok | <W 规则 + 位置>
- 结论依据：<一到三句>
- 对执行模型的要求（退回时）：<逐条>
- 对用户的问题（升级时）：<逐条，附证据>
- 产物：/tmp/refactor-acc/<CARD>/
```

非 git 位置的改动（D11）同样记录前后文件的 sha256。
