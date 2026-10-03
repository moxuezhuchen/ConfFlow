# IS.2c 与 IS.2b 补充说明（ConfFlow 输入简化分支，两张卡连做，各一个提交）

- 工作树 /opt/cf-worktrees/exec-cf-is，分支 implementation/input-simplification，HEAD 应为 2e0295a 开头（IS.2 的提交），工作树干净。不符就停止报告。
- 每张卡的自检都要用 run_sharded.py（`--jdpin /opt/cf-worktrees/jd-pin`），不要直接 pytest；它报告收集阶段错误就停止。

## IS.2c（先做）
1. `git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/IS.2c-waypoint-hint.patch && git apply` 同一路径；git status 应有 2 个文件（intent.py、tests/v4/test_intent_legacy_paths_to_v3.py）。
2. 自检：ruff/mypy/black；全量期望 `{"passed": 5139, "skipped": 12}`；collect 5150 → 5151，`comm -13` 恰好是 `tests/v4/test_intent_legacy_paths_to_v3.py::test_waypoint_is_refused_with_the_torsions_hint`，`comm -23` 为空。
3. 提交：`fix(producer): point waypoint users to explicit torsions in the compile-time refusal`，Added-Tests 1（名字逐字复制）。

## IS.2b（IS.2c 提交之后）
1. 应用 handoff/IS.2b-src-tests.patch；git status 应有 2 个文件（torsion/stage.py 修改，tests/v4/test_terminal_endpoint_diagnostic.py 新增）。
2. 自检：ruff/mypy/black；全量期望 `{"passed": 5145, "skipped": 12}`；collect 5151 → 5157，`comm -13` 与 handoff/IS.2b-added-tests.txt 逐行相同（6 项），`comm -23` 为空；
   golden：`PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:. python3 docs/refactor/tools/golden_check.py --base docs/refactor/baseline --cf . --jd-src /opt/cf-worktrees/jd-pin/src --checkpoint docs/refactor/baseline/checkpoints/IS.0/contract.json --out /tmp/refactor-acc/IS.2b/golden.json`：contract 五项 ok、ts1 三种 ok、engine_reports added 5 / different 1；再用 capture_engine_reports 重新生成那 6 份报告（命令与 IS.2 的报告一致）并与 checkpoints/IS.0/engine_reports/ 逐字节比较，必须 6 份全部相同。
3. 提交：`fix(confgen): name the offending path key when a terminal endpoint is refused`，Added-Tests 6（名字逐字复制）。

## 通用
补丁无法应用或数字不符，停止报告，不要自己修。不要 push（该分支不在推送授权内）；不要 sudo/pip/apt/unshare/mount。每张卡一个提交，两张做完统一报告（分卡写）：提交 SHA 与完整提交信息、git show --stat HEAD、自检原始输出、执行过的全部命令。不要编造输出。
