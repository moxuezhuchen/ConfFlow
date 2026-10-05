# 通用规则（L0.1 迁入）

> 本文件由 L0.1 从 `docs/refactor/PLAN.md` 的 §1.4（卡片类型）、§2.5（G1–G10）、§2.6（标准验收）、§2.7（提交信息格式）、§2.8（执行模型的调用方式）**逐字搬运**而成；工具与文档路径改为新位置（`tools/refactor/`、`docs/process/ACCEPTANCE.md`）。v3.3 方案对这些规则的修订（G5'/G6'、G11–G14 等）以 `docs/confgen-fix/PLAN.md` §2.2 为准。

### 1.4 卡片类型

| 类型 | 允许的改动 |
|---|---|
| `delete` | 只删除代码、文件、导出项、测试，以及修复因删除而断掉的 import。允许把被删模块名从"必须存在"清单移到"必须不存在"清单（测试守卫的对应更新）。不得新增任何逻辑分支、函数或类。 |
| `move` | 原样移动代码（文件间或模块间），只改 import 路径和 `__all__`。函数体逐字不变。 |
| `logic` | 行为改变。卡片必须列出每一处预期的行为变化。 |
| `test-only` | 只改 `tests/`（以及 fixture 数据）；被测代码不变。 |
| `ci` | 只改 `.github/workflows/`、pin 常量和与之一致性相关的测试。 |
| `baseline` | 只新增仓库外检查点（`$CKPT/<里程碑>/<卡>/`）；仓内仅提交对应 manifest/DIFF 与 LOG。（新增类型，用户列表中没有。） |
| `doc` | 只改文档。（新增类型。） |
| `merge` | 把一个分支合入另一个分支，冲突只按卡片规定的方向解决。（新增类型。） |

---


### 2.5 通用禁止事项（每张卡都适用，卡片中以"G1–G10"引用）

- G1 不得修改白名单以外的任何文件（含格式化工具顺手改的文件）。
- G2 `delete` / `move` 卡不得新增逻辑：不得新增函数、类、条件分支、异常处理、默认值。
- G3 不得为了让测试通过而删除、跳过（`skip`/`skipif`/`xfail`/`importorskip`）或放宽测试断言。被删代码的守护测试可以删除，但必须在卡片中逐条声明。
- G4 不得重命名任何保留下来的符号、文件、测试。
- G5' 不得修改 `$CKPT` 下已有的检查点文件；每张卡只能新增自己的检查点目录。（原 G5 保护仓内 `docs/refactor/baseline/`；该目录已随 architecture-diet-1 归档，仓内检查点记录 `docs/confgen-fix/checkpoints/` 由 diff_guard R3 保护只增不改。）
- G6 不得改变科学行为（TS1、ring、torsion、stereo、atom ordering）。只要 golden 有差异就停止，在 LOG 中写明差异并升级，不得自行判定为"预期变化"。
- G7 每张卡恰好一个提交；提交前 `git status` 必须干净（没有未跟踪的残留）。
- G8 不得 push、不得改 `main`/`master`、不得切换 `/opt/ConfFlow` 或 `/opt/jobdesk-v2-v4` 主工作树的分支。
- G9 发现卡片与代码不符（锚点找不到、行为与描述不同），或者环境缺包、命令失败时，停止并在提交前报告，不得自行变通。
- G10 不得直接调用 `unshare`、`mount`、`sudo`、`pip`/`apt` 安装命令；JD 测试只通过 `tools/refactor/run_jd_tests.sh` 运行。

### 2.6 每张卡的通用验收（卡片中写"标准验收"即指本节）

验收由验收方按 `docs/process/ACCEPTANCE.md` 执行。执行模型提交前也必须自己跑一遍，并把结果写进提交信息的 `Verification:` 段。

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
- J0b 起：**Sonnet 5.5 子代理**（Claude Code 的 Agent 工具，`subagent_type: general-purpose`，`model: sonnet`），由验收方发起，一次一张卡。子代理看不到验收方与用户的对话，只能依据交接提示词、PLAN.md（或现行方案文件）和 docs/process/ACCEPTANCE.md 工作，权限与验收方会话相同。
- 交接提示词必须包含：卡片 ID；现行方案文件和 docs/process/ACCEPTANCE.md 的路径；"只执行这一张卡、提交后停止"；G1–G10 全文；"JD 测试只能通过 `run_jd_tests.sh` 运行，禁止直接或间接（`bash -c`、`python3 -c` 等）调用 unshare/mount"；"不得 push，包括 `git -C … push`"；"命令被拒绝或失败时如实报告，不得编造输出"；退回时附上验收方的具体要求，并要求用 `git commit --amend` 重新交付。
- 验收方不采信执行模型报告的任何命令输出，一律自己重跑（docs/process/ACCEPTANCE.md §4）。
- 同一张卡被退回两次后，不再第三次交给同一个模型，而是报告用户，由用户决定是否换更强的模型。

附：曾经评估过 CodeBuddy `glm-5.3-flash`（2026-10-02），后放弃。实测记录见 LOG.md：`--allowedTools` 只能写单项 `Bash(*)`；禁止规则必须逐条传参，而且只按前缀匹配，可以被 `git -C … push`、`bash -c` 绕过；Bash 被拒时，该模型曾编造命令输出。

---
