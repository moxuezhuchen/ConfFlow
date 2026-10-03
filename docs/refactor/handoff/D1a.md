# D1a — 删除无人引用的死夹具并同步 TESTING.md（ConfFlow，refactor/cleanup-d1；零行为）

- 工作树 /opt/cf-worktrees/d1，分支 `refactor/cleanup-d1`（从 main `afdf9df` 建立），HEAD 应为 `afdf9df` 开头，工作树干净。**提交前再核对 `cd /opt/cf-worktrees/d1 && git branch --show-current` 输出 `refactor/cleanup-d1`。**每条命令显式 `cd`。
- 类型：`delete`（来自分支 `refactor/v4-diet-pr10-repository-hygiene` 的提交 4997907 中真正的删除部分；该提交里对 `tests/v4/test_v42_adapters.py`、`tests/v4/test_v42_coverage_render.py` 的两处**测试合并改写不带过来**）。
- 白名单（恰 26 个文件）：删除 25 个——`tests/fake_orca.sh`、`tests/fixtures/durable/v2/**`（20 个）、`tests/input/sial1..4-r-ml-lla-ts2.gjf`（4 个）；修改 1 个——`docs/TESTING.md`。
- 应用：`cd /opt/cf-worktrees/d1 && git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/D1a.patch && git apply …`（"has type 100644, expected 100755" 的警告可忽略）；`git status --short` 恰 25 个 `D` 加 1 个 `M`。

## 零引用证据（验收方，@afdf9df；执行器须重跑并贴原始输出，结果必须是"无输出"）
```
cd /opt/cf-worktrees/d1 && git grep -nE "fixtures/durable|durable/v2|fake_orca\.sh|sial[0-9]-r-ml-lla|sentinel-search|sentinel-output|signature-(future|malformed|v1)|state-(legacy|malformed|v1|v99)|manifest-(malformed|v1|v999)|alias-default|tests/input" -- . ':!docs/refactor' ':!docs/internal' ':!tests/fixtures/durable' ':!tests/input' ':!tests/fake_orca.sh' ':!docs/TESTING.md'
cd /opt/cf-worktrees/d1 && git grep -nE "iterdir|rglob|listdir|scandir" -- tests scripts confflow | grep -iE "tests/input|durable|fixtures/durable"
git -C /opt/jobdesk-v2-v4 grep -nE "fixtures/durable|durable/v2|fake_orca\.sh|sial[0-9]-r-ml-lla|tests/input" master --
```
- 第 1 条在 `docs/TESTING.md` 之外应无命中（`docs/TESTING.md` 里原有 3 处引用：`tests/fake_orca.sh`、`durable 存储`、`tests/input/` 行，本卡一并同步）；`tests/v4/fakes/fake_g16.py` 里的 `glob("*.gjf")` 作用在运行目录上，与 `tests/input/` 无关；`tests/v4/test_v42_adapters.py:403` 的 `fake_orca.shift_coordinates` 是 Python 模块 `fake_orca.py`，不是 `fake_orca.sh`。
- JobDesk master（`3addb94`）整树无命中。

## `docs/TESTING.md` 同步（3 处，纯文档）
1. 假可执行文件那句去掉"和 `tests/fake_orca.sh`"。
2. `tests/fixtures/` 一行去掉"durable 存储"。
3. 删除 `tests/input/` 一整行（该目录删除后不再存在）。

## 自检（原始输出）
1. 上面三条 grep。
2. `cd /opt/cf-worktrees/d1 && git status --short | awk '{print $1}' | sort | uniq -c`：`25 D`、`1 M`。
3. `cd /opt/cf-worktrees/d1 && ruff check confflow tests scripts`；collect 4483 不变。
4. 全量：`cd /opt/cf-worktrees/d1 && PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:. python3 /opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/run_sharded.py --cf . --out /tmp/acc2/D1a-exec/out.json --jdpin /opt/cf-worktrees/jd-pin`（先 `mkdir -p /tmp/acc2/D1a-exec`；`/opt/cf-worktrees/jd-pin` 是 JobDesk 9de35d6 的只读检出），期望 `{"passed": 4473, "skipped": 10}`。
5. **golden_check（不得省略）**：`cd /opt/cf-worktrees/d1 && B=/tmp/acc2/D1a-exec/base && rm -rf $B && cp -r docs/refactor/baseline $B && rm -rf $B/checkpoints && cp docs/refactor/baseline/checkpoints/IS.5/engine_reports/* $B/engine_reports/ && cp docs/refactor/baseline/checkpoints/C4.3/engine_reports/* $B/engine_reports/ && printf 'tests/v4/test_confgen_v3_integration.py::TestLegacyRegressions::test_v3_filenames_vs_legacy_compat\n' > /tmp/acc2/D1a-exec/removed.txt && PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:. python3 docs/refactor/tools/golden_check.py --base $B --checkpoint docs/refactor/baseline/checkpoints/C4.3/contract.json --cf . --jd-src /opt/cf-worktrees/jd-pin/src --removed-nodes /tmp/acc2/D1a-exec/removed.txt --out /tmp/acc2/D1a-exec/golden.json`，期望 `ok: true`。
- 提交：`test: delete unreferenced durable-v2 fixtures, fake_orca.sh and legacy gjf inputs`，Removed-Tests 0，Behavior-Change: none。不 push；不动 main/master；补丁无法应用、grep 有命中或数字不符，停止报告，不要自行修复。
