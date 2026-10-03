# IS.3（CF）与 IS.4（JD）补充说明——先 IS.3，再 IS.4

## 顺序
IS.3 先做并提交；**IS.4 只能在 IS.3 提交之后开始**（IS.4 的测试要绑定 CF 的 IS.3 提交）。两张卡可以由两个执行模型分别做，也可以同一个模型连做；报告各写各的。

## IS.3（见 handoff/IS.3.md）
工作树 /opt/cf-worktrees/exec-cf-is，HEAD 应为 0a9f28d 开头；应用 handoff/IS.3-src-tests.patch；期望 5149 passed / 12 skipped，collect 5157 → 5161（−9 +13）。细节与自检见 handoff/IS.3.md。

## IS.4（JD）
- 工作树 `/opt/jobdesk-v2-v4`（输入简化分支的检出），分支 implementation/input-simplification，HEAD 应为 bcdea5b 开头，工作树干净。不符就停止报告。**开工前再确认** `git -C /opt/cf-worktrees/exec-cf-is log -1 --format=%s` 是 `feat(producer)!: reject non-v3 ConfGen native in intent compilation`，不是就停止报告（IS.3 还没提交）。
- `git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/IS.4-src-tests.patch && git apply` 同一路径；git status 应有 6 个文件（confgen_v3_form.py、page.py、intent/model.py、tests/application/test_intent_model.py、tests/gui/test_intent_live_gui.py、tests/gui/test_intent_panels.py）。
- 自检（原始输出；JD 用 ruff format，不要用 black，也不要对 src 整体 `ruff format`，只检查 `ruff format --check src tests`）：
  1. `cd /opt/jobdesk-v2-v4 && ruff check src tests scripts && ruff format --check src tests && mypy`
  2. `/opt/cf-worktrees/refactor-plan/docs/refactor/tools/run_jd_tests.sh --cf /opt/cf-worktrees/exec-cf-is --jd /opt/jobdesk-v2-v4 --junit /tmp/refactor-acc/IS.4/jd-junit.xml -- -rf` 期望 `2467 passed, 7 skipped`。
  3. collect（同脚本 `-- --collect-only`，不要 -q，应用前后各存排序列表）：2472 → 2474；`comm -23` 与 handoff/IS.4-removed-tests.txt 逐行相同（3 项）；`comm -13` 与 handoff/IS.4-added-tests.txt 逐行相同（5 项）。
  4. `cd /opt/jobdesk-v2-v4 && QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3 /opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/startup_smoke.py` 期望 `startup smoke: ok`。
- 提交：`feat(new-run)!: author ConfGen paths as typed v3 only`，Removed-Tests 3、Added-Tests 5（名字逐字复制），提示文本写进提交信息。不要 push（该分支不在推送授权内）；补丁无法应用或数字不符，停止报告。
